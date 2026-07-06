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
from datetime import datetime
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


CONFIG_PATH = app_dir() / "config.json"


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

if not ROOT_DIR.exists():
    raise SystemExit(f"root_dir does not exist: {ROOT_DIR}")

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
    """
    import uvicorn

    ip = lan_ip()
    print("=" * 50)
    print(" PC Bridge is starting")
    print(f" Serving folder: {ROOT_DIR}")
    print(f" PIN: {PIN}")
    print(f" Open on your phone:  http://{ip}:{PORT}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()
