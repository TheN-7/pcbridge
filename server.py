"""
PC Bridge - local file server
Lets your phone browse, upload, and download files on this PC over your
local network (or a Tailscale network) via a simple web app.

Run:  python server.py
Then open the printed URL on your phone's browser.
"""

import json
import mimetypes
import os
import platform
import secrets
import shutil
import socket
import sys
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Query, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent


def app_dir() -> Path:
    """Where config.json should live.

    Normally this is just BASE_DIR (next to this script). But when this
    server is launched from inside a PyInstaller-frozen EXE (see
    pcbridge_app.py --server), __file__ points into a temporary
    extraction folder that gets deleted after the process exits -- if
    config.json lived there, the PIN/folder settings would silently reset
    every single run. sys.executable, on the other hand, is the real path
    to the running EXE, so config.json ends up next to it and survives.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return BASE_DIR


def ensure_certificate() -> tuple:
    """Generates a self-signed TLS certificate + private key the first time
    this runs, and reuses the same one on every later run.

    This is what the "secure connection" between your phone/PC actually is:
    real HTTPS, but signed by nobody -- a real certificate authority won't
    issue a certificate for a private LAN IP. Instead, each client app
    remembers (pins) this exact certificate's fingerprint the first time it
    connects, the same trust model SSH uses for host keys. If cert.pem/
    key.pem are ever deleted (so a new certificate gets generated), every
    app that already trusted the old one will refuse to connect until you
    re-add this PC there -- that's the point, not a bug: it's the same
    protection SSH gives you when a host key suddenly changes.
    """
    cert_path = app_dir() / "cert.pem"
    key_path = app_dir() / "key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise SystemExit(
            "Missing dependency: 'cryptography' is required to generate PC "
            "Bridge's TLS certificate. Run: pip install -r requirements.txt"
        )

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PC Bridge")])
    now = datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def cert_fingerprint(cert_path: Path) -> str:
    """SHA-256 fingerprint of the certificate, formatted like AA:BB:CC:...
    Client apps pin this on first connect; it's also printed in the startup
    banner so you can double-check it by eye if you want to be extra sure."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    digest = cert.fingerprint(hashes.SHA256())
    return digest.hex(":").upper()


CONFIG_PATH = app_dir() / "config.json"
PHONES_PATH = app_dir() / "phones.json"


def load_phones() -> list:
    """Known phones this PC can push files to (see /api/register-phone and
    pcbridge_app.py's "Send to Phone" feature, which reads this same file).
    Mirrors pcbridge_app.py's load_phones() exactly -- both processes share
    one phones.json next to this PC's config/cert files."""
    if PHONES_PATH.exists():
        try:
            return json.loads(PHONES_PATH.read_text())
        except Exception:
            pass
    return []


def save_phones(phones: list):
    PHONES_PATH.write_text(json.dumps(phones, indent=2))


def load_config():
    default = {
        "root_dir": str(Path.home()),
        "port": 8000,
        "pin": None,
    }
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            default.update({k: v for k, v in data.items() if v is not None})
        except Exception:
            pass

    changed = False
    if not default.get("pin"):
        default["pin"] = f"{secrets.randbelow(1000000):06d}"
        changed = True
    if changed or not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(default, indent=2))
    return default


CONFIG = load_config()
ROOT_DIR = Path(CONFIG["root_dir"]).expanduser().resolve()
PIN = str(CONFIG["pin"])
PORT = int(CONFIG.get("port", 8000))
# A second, plain-HTTP listener purely for opening this PC's web UI in a
# regular browser. Browsers have no way to trust-on-first-use pin a
# self-signed certificate the way the native phone/PC apps do (see
# ensure_certificate's docstring) -- they just show a security warning
# every time, and older/stricter browsers can refuse the connection
# outright. Native apps are completely unaffected: pcbridge_app.py's
# _connect_phone_any() and the Android app both only ever talk to PORT
# (HTTPS, pinned), never this one. The tradeoff: anything sent to this
# port -- including the PIN, on every request -- travels in cleartext
# over your LAN. Fine for a private home network; worth knowing if you're
# on a network you don't fully trust.
HTTP_PORT = int(CONFIG.get("http_port") or PORT + 1)

if not ROOT_DIR.exists():
    raise SystemExit(f"root_dir does not exist: {ROOT_DIR}")

CERT_PATH, KEY_PATH = ensure_certificate()
CERT_FINGERPRINT = cert_fingerprint(CERT_PATH)

app = FastAPI(title="PC Bridge")

# Lets your phone's browser talk to *other* PC Bridge servers on your
# network while a page is open from *this* one (needed for the multi-PC
# device switcher). Every real action still requires the correct PIN --
# this only allows the cross-origin request to happen at all.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- helpers ----------

def check_pin(request: Request):
    supplied = request.headers.get("x-pin") or request.query_params.get("pin")
    if supplied != PIN:
        raise HTTPException(status_code=401, detail="Invalid or missing PIN")


def resolve_safe(rel_path: str) -> Path:
    """Resolve a relative path against ROOT_DIR, blocking any escape via .. or symlinks."""
    rel_path = (rel_path or "").lstrip("/")
    candidate = (ROOT_DIR / rel_path).resolve()
    if candidate != ROOT_DIR and ROOT_DIR not in candidate.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    return candidate


def entry_info(p: Path):
    try:
        st = p.stat()
    except OSError:
        return None
    return {
        "name": p.name,
        "is_dir": p.is_dir(),
        "size": st.st_size if p.is_file() else None,
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
    }


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def fmt_bytes(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    val = float(n)
    while val >= 1024 and i < len(units) - 1:
        val /= 1024
        i += 1
    return f"{val:.1f} {units[i]}" if i > 0 else f"{int(val)} {units[i]}"


# ---------- API ----------

@app.get("/api/list")
def list_dir(request: Request, path: str = Query("")):
    check_pin(request)
    target = resolve_safe(path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    entries = []
    for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        info = entry_info(child)
        if info:
            entries.append(info)

    rel = target.relative_to(ROOT_DIR)
    rel_str = "" if str(rel) == "." else str(rel).replace(os.sep, "/")
    parent = None
    if target != ROOT_DIR:
        parent_rel = target.parent.relative_to(ROOT_DIR)
        parent = "" if str(parent_rel) == "." else str(parent_rel).replace(os.sep, "/")

    return {"path": rel_str, "parent": parent, "entries": entries}


@app.get("/api/download")
def download_file(request: Request, path: str = Query(...)):
    check_pin(request)
    target = resolve_safe(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    mime, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, filename=target.name, media_type=mime or "application/octet-stream")


@app.get("/api/download-zip")
def download_zip(request: Request, path: str = Query("")):
    check_pin(request)
    target = resolve_safe(path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(target):
            for f in files:
                fp = Path(root) / f
                zf.write(fp, arcname=str(fp.relative_to(target)))
    buf.seek(0)
    name = target.name or "root"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )


@app.post("/api/upload")
async def upload_files(request: Request, path: str = Form(""), files: list[UploadFile] = File(...)):
    check_pin(request)
    target_dir = resolve_safe(path)
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(status_code=404, detail="Target directory not found")

    saved = []
    for f in files:
        # relative_path lets folder uploads (webkitdirectory) preserve structure
        rel_name = f.filename or "unnamed"
        dest = (target_dir / rel_name).resolve()
        if target_dir not in dest.parents and dest != target_dir:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(rel_name)

    return {"saved": saved}


@app.post("/api/mkdir")
async def make_dir(request: Request, path: str = Form(""), name: str = Form(...)):
    check_pin(request)
    parent = resolve_safe(path)
    new_dir = resolve_safe(str(Path(path) / name))
    if new_dir.exists():
        raise HTTPException(status_code=409, detail="Already exists")
    new_dir.mkdir(parents=True)
    return {"ok": True}


@app.post("/api/delete")
async def delete_entry(request: Request, path: str = Form(...)):
    check_pin(request)
    target = resolve_safe(path)
    if target == ROOT_DIR:
        raise HTTPException(status_code=400, detail="Cannot delete root")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True}


@app.post("/api/rename")
async def rename_entry(request: Request, path: str = Form(...), new_name: str = Form(...)):
    check_pin(request)
    target = resolve_safe(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    dest = target.parent / new_name
    if dest.exists():
        raise HTTPException(status_code=409, detail="Already exists")
    target.rename(dest)
    return {"ok": True}


@app.get("/api/whoami")
def whoami(request: Request):
    check_pin(request)
    return {"root_dir": str(ROOT_DIR)}


@app.get("/api/ping")
def ping():
    """Unauthenticated, deliberately minimal: lets a phone check 'is a PC
    Bridge server here and reachable' for the device list's online dots,
    without needing (or leaking) the PIN or anything behind it."""
    return {"ok": True, "app": "pcbridge", "hostname": socket.gethostname()}


@app.get("/api/cert-fingerprint")
def cert_fingerprint_endpoint():
    """Also unauthenticated -- the certificate itself is handed to anyone
    who connects during the TLS handshake anyway, so its fingerprint isn't
    a secret. This just lets an app (or you, by eye) double-check it
    without needing the PIN first."""
    return {"fingerprint": CERT_FINGERPRINT}


@app.post("/api/register-phone")
async def register_phone(
    request: Request,
    name: str = Form(...),
    address: str = Form(...),
    addresses: str = Form(""),
    phone_pin: str = Form(...),
    cert_fingerprint: str = Form(...),
):
    """Lets a phone register itself as a "send to" target the moment it
    successfully adds this PC (see the Android app's Add PC flow, and its
    "Allow receiving files" toggle) -- so you don't have to separately run
    "Add Phone" on this PC too. Authenticated with this PC's own PIN (the
    same one the phone just used for /api/whoami) -- phone_pin here is a
    *different* PIN, the phone's own receive-server one, which this PC
    will present back to the phone later when actually sending a file.

    [address] is the phone's single primary address (LAN-preferred),
    kept for backward compatibility with older phone apps that only ever
    sent one. [addresses], comma-joined, is the fuller list (LAN +
    Tailscale, whichever the phone currently has) -- pcbridge_app.py's
    _connect_phone_any() tries every address in this list in order on
    every connect, so it no longer matters which network either device
    happens to be on: whichever address works, works. A phone's own
    NetworkInterface enumeration order isn't stable across app restarts
    (see NetworkUtil.kt's getLocalAddresses doc comment), which used to
    mean the exact same phone could silently register a different
    "primary" address every time -- storing the whole list here, and
    matching on any overlap below, is what stops that from creating
    duplicate/stale entries or timing out on a stale single address.

    Writes to the same phones.json the desktop tray app's "Send to Phone"
    feature already reads fresh every time that dialog opens, so a newly
    registered phone shows up there immediately with no extra plumbing."""
    check_pin(request)
    addr_list = [a.strip() for a in addresses.split(",") if a.strip()] or [address]
    addr_set = {a.lower() for a in addr_list}

    phones = load_phones()

    def matches(p: dict) -> bool:
        known = {a.lower() for a in p.get("addresses") or [p.get("address", "")]}
        return bool(known & addr_set)

    existing_index = next((i for i, p in enumerate(phones) if matches(p)), None)
    entry = {
        "id": phones[existing_index]["id"] if existing_index is not None else secrets.token_hex(8),
        "name": name,
        "address": address,
        "addresses": addr_list,
        "pin": phone_pin,
        "cert_fingerprint": cert_fingerprint,
    }
    if existing_index is not None:
        phones[existing_index] = entry
    else:
        phones.append(entry)
    save_phones(phones)
    return {"ok": True}


@app.get("/api/stats")
def stats(request: Request):
    check_pin(request)
    total, used, free = shutil.disk_usage(ROOT_DIR)
    return {
        "hostname": socket.gethostname(),
        "lan_ip": lan_ip(),
        "port": PORT,
        "platform": f"{platform.system()} {platform.release()}",
        "root_dir": str(ROOT_DIR),
        "storage": {
            "total": total,
            "used": used,
            "free": free,
            "total_human": fmt_bytes(total),
            "used_human": fmt_bytes(used),
            "free_human": fmt_bytes(free),
        },
    }


@app.get("/api/download-selected")
def download_selected(request: Request, path: list[str] = Query(...)):
    """Zip an arbitrary set of selected files/folders together for one-shot download."""
    check_pin(request)
    targets = [resolve_safe(p) for p in path]
    targets = [t for t in targets if t.exists()]
    if not targets:
        raise HTTPException(status_code=404, detail="Nothing to download")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in targets:
            if t.is_file():
                zf.write(t, arcname=t.name)
            else:
                for root, _dirs, files in os.walk(t):
                    for f in files:
                        fp = Path(root) / f
                        zf.write(fp, arcname=str(Path(t.name) / fp.relative_to(t)))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="pcbridge-selected.zip"'},
    )


# ---------- static frontend ----------
app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")


def run():
    """Start serving, blocking, exactly like `python server.py` always has.

    Pulled out into its own function so pcbridge_app.py (the tray app) can
    call it directly from its `--server` worker subprocess, instead of
    re-invoking this file by path -- the latter doesn't work once this
    project is packaged into a single frozen EXE, since there's no
    separate server.py file sitting on disk to point at anymore.

    Runs two listeners concurrently on the same app: the original pinned
    HTTPS one on PORT (unchanged -- this is the only one native phone/PC
    apps ever talk to), plus a plain-HTTP one on HTTP_PORT for opening the
    web UI in a regular browser without a certificate warning getting in
    the way. uvicorn.Server().serve() is just a coroutine, so both run
    side by side under one asyncio event loop in this one process/thread
    -- no need for a second subprocess or port-forwarding trick.
    """
    import asyncio

    import uvicorn

    ip = lan_ip()
    print("=" * 50)
    print(" PC Bridge is starting")
    print(f" Serving folder: {ROOT_DIR}")
    print(f" PIN: {PIN}")
    print(f" Open in a browser:   http://{ip}:{HTTP_PORT}")
    print(f" Phone/PC apps use:   https://{ip}:{PORT}")
    print(f" Certificate fingerprint: {CERT_FINGERPRINT}")
    print(" (apps trust this automatically the first time they connect --")
    print("  this is only here if you want to double-check it by eye)")
    print("=" * 50)

    https_server = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=PORT,
        ssl_certfile=str(CERT_PATH), ssl_keyfile=str(KEY_PATH),
    ))
    http_server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=HTTP_PORT))

    async def serve_both():
        await asyncio.gather(https_server.serve(), http_server.serve())

    asyncio.run(serve_both())


if __name__ == "__main__":
    run()
