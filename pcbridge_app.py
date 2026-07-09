"""
PC Bridge - desktop control app

A small system tray app for running the PC Bridge server without ever
opening a terminal. Click the tray icon to open a tiny window that shows
whether the server is running, its address, and the PIN your phone needs
-- with buttons to start/stop it and change the PIN or shared folder.

Dev:      python pcbridge_app.py
Packaged: see README-DESKTOP.md for the PyInstaller command that turns
          this into a single portable PCBridge.exe.

How this file is used, in two different ways:
  1. Normal launch (no arguments) -- shows the tray icon + window.
  2. `pcbridge_app.py --server` -- skips all of that and just runs the
     actual FastAPI/uvicorn server, blocking, exactly like `python
     server.py` would. The tray app launches *this exact file* with that
     flag as a subprocess, so Start/Stop can cleanly start/kill it. A
     packaged single-file EXE has no separate server.py sitting on disk
     to relaunch, so re-invoking itself with a flag is the standard way
     to give a frozen app a "worker" mode.

Auto-update: checks a GitHub repo's latest Release for a newer version
and, if you confirm, downloads the new PCBridge.exe and swaps it in --
see the "auto-update" section below and README-DESKTOP.md for how to
configure and publish releases.
"""

import hashlib
import http.client
import http.server
import json
import os
import secrets
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.sax.saxutils as saxutils
from email.utils import formatdate
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# This dev-mode default is only used for local/unpackaged runs. Real
# releases get their version baked in automatically at build time (see
# .github/workflows/build-release.yml's "Bake version into the build"
# step, which generates app_version.py from the git tag before PyInstaller
# runs) -- this used to be a manually-bumped literal, which silently never
# got bumped for a real release, causing the app to keep reporting its old
# version and auto-update to loop forever redownloading "the latest" build.
APP_VERSION = "1.4.0"
try:
    from app_version import APP_VERSION  # noqa: F811 -- overrides dev default when baked in
except ImportError:
    pass

# Baked-in update credentials -- generated fresh by the CI build (see
# .github/workflows/build-release.yml) right before PyInstaller runs, so
# a release built via GitHub Actions already knows its own repo/token and
# needs no manual config.json edit to enable auto-update. This file does
# not exist in the repo itself and isn't created for local/dev runs, so
# it falls back to empty strings -- use config.json's "update_repo"/
# "update_token" instead for anything you build yourself.
try:
    from update_secrets import UPDATE_REPO, UPDATE_TOKEN
except ImportError:
    UPDATE_REPO, UPDATE_TOKEN = "", ""

BASE_DIR = Path(__file__).resolve().parent

# Midnight theme -- matches static/style.css, so the desktop control
# window doesn't look like a completely different product from the app.
BG = "#16191d"
CARD = "#1c1f24"
BORDER = "#2a2f36"
TEXT = "#f4f5f7"
MUTED = "#8b93a1"
BLUE = "#38bdf8"
ON_BLUE = "#0c2733"
GREEN = "#4ade80"
RED = "#f87171"


def app_dir() -> Path:
    """Where config.json lives -- the real EXE folder, not the temp folder
    a frozen single-file build extracts itself into. Mirrors server.py's
    app_dir() so both processes agree on where settings are stored."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return BASE_DIR


CONFIG_PATH = app_dir() / "config.json"


def load_config() -> dict:
    default = {
        "root_dir": str(Path.home()),
        "port": 8000,
        "pin": None,
        # Auto-update settings -- see README-DESKTOP.md. Both must be set
        # (baked in by a CI build, or set here) for update checks to run
        # at all; left unset, the feature is a silent no-op so this
        # doesn't break setups that don't want it. A value in config.json
        # always wins over the baked-in one, so you can still point a
        # given install at a different repo/token if you ever need to.
        "update_repo": UPDATE_REPO or None,   # "yourusername/pcbridge"
        "update_token": UPDATE_TOKEN or None,  # fine-grained PAT, read-only, this repo only
        "auto_check_updates": True,
    }
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            default.update({k: v for k, v in data.items() if v is not None})
        except Exception:
            pass
    if not default.get("pin"):
        default["pin"] = f"{secrets.randbelow(1000000):06d}"
    return default


def save_config(config: dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ---------- server subprocess management ----------

class ServerProcess:
    """Owns the actual server subprocess. Runs it as a separate process
    (rather than importing server.py in-thread) so Stop is a clean kill
    with no need to reach into uvicorn's internals to shut it down."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.log_path = app_dir() / "pcbridge.log"
        self._log_file = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.running:
            return

        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--server"]
        else:
            cmd = [sys.executable, str(BASE_DIR / "pcbridge_app.py"), "--server"]

        creationflags = 0
        if sys.platform == "win32":
            # Stops a console window from flashing up behind the tray app.
            creationflags = subprocess.CREATE_NO_WINDOW

        self._log_file = open(self.log_path, "a", buffering=1, encoding="utf-8")
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(app_dir()),
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

    def stop(self):
        if not self.running:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None
        if self._log_file:
            self._log_file.close()
            self._log_file = None


server = ServerProcess()


# ---------- auto-update ----------
#
# Checks a GitHub repo's latest Release (private repo -> needs a token)
# for a version newer than APP_VERSION, downloads the "PCBridge.exe"
# asset attached to that release, and swaps it in for the running exe.
#
# To publish an update: bump APP_VERSION above, build a new PCBridge.exe
# (see README-DESKTOP.md), create a GitHub Release tagged e.g. "v1.2.0",
# and attach the exe as an asset named exactly "PCBridge.exe". Existing
# installs pick it up next time they check.

GITHUB_API = "https://api.github.com"
UPDATE_ASSET_NAME = "PCBridge.exe"


def _parse_version(v: str) -> tuple:
    v = (v or "").strip().lstrip("vV")
    parts = []
    for chunk in v.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def fetch_latest_release(repo: str, token: str) -> dict:
    """repo is 'owner/name'. Raises urllib.error.* / OSError on failure."""
    req = urllib.request.Request(
        f"{GITHUB_API}/repos/{repo}/releases/latest",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "PCBridge-Updater",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_asset(release: dict, name: str = UPDATE_ASSET_NAME):
    for asset in release.get("assets", []):
        if asset.get("name", "").lower() == name.lower():
            return asset
    return None


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    """Prevents urllib from auto-following the redirect GitHub sends for
    a private-repo release asset (a presigned, unauthenticated S3 URL).
    We need to strip the GitHub Authorization header before that second
    request -- S3 rejects the request with it still attached."""

    def http_response(self, request, response):
        return response

    https_response = http_response


def download_release_asset(repo: str, token: str, asset_id, dest_path: Path):
    url = f"{GITHUB_API}/repos/{repo}/releases/assets/{asset_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/octet-stream",
            "User-Agent": "PCBridge-Updater",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    resp = opener.open(req, timeout=15)
    try:
        if resp.status in (301, 302, 303, 307, 308):
            redirect_url = resp.headers.get("Location")
            resp.close()
            with urllib.request.urlopen(redirect_url, timeout=120) as final, \
                    open(dest_path, "wb") as f:
                shutil.copyfileobj(final, f)
        elif resp.status == 200:
            with open(dest_path, "wb") as f:
                shutil.copyfileobj(resp, f)
        else:
            raise RuntimeError(f"unexpected status {resp.status} downloading asset")
    finally:
        resp.close()


def _write_updater_script(target_exe: Path, new_exe: Path, pid: int, expected_size: int) -> Path:
    """A PowerShell script that waits for this process to exit, then
    swaps the downloaded exe into place and relaunches it. Windows won't
    let a running exe overwrite itself, so the app has to hand off to
    something else for the actual file swap.

    Rewritten from an earlier plain-batch version after a real failure:
    the batch version would blindly `move` then `start` with no error
    checking, and a user hit a corrupted-looking relaunch (PyInstaller's
    "pyi_rth_inspect" / missing base_library.zip -- almost certainly
    antivirus quarantining/altering the freshly-downloaded, freshly-
    launched-by-another-program exe, which is exactly the pattern
    heuristic AVs flag hardest). This version retries the move a few
    times (rides out a transient lock), verifies the moved file's size
    against what was actually downloaded before ever launching it, and
    logs every step to pcbridge_update.log next to config.json so a
    failure like that is diagnosable instead of just a cryptic crash
    dialog with nothing to go on."""
    ps1_path = target_exe.parent / "pcbridge_update.ps1"
    log_path = target_exe.parent / "pcbridge_update.log"
    script = f"""$ErrorActionPreference = "Stop"
$logPath = "{log_path}"
function Log($msg) {{
    Add-Content -Path $logPath -Value "$(Get-Date -Format o)  $msg"
}}

try {{
    Wait-Process -Id {pid} -ErrorAction SilentlyContinue

    $target = "{target_exe}"
    $newFile = "{new_exe}"
    $expectedSize = {expected_size}

    $moved = $false
    for ($i = 0; $i -lt 10 -and -not $moved; $i++) {{
        try {{
            Move-Item -Path $newFile -Destination $target -Force
            $moved = $true
        }} catch {{
            Log "Move attempt $i failed: $_"
            Start-Sleep -Seconds 1
        }}
    }}

    if (-not $moved) {{
        Log "Giving up: could not move update into place after 10 attempts."
        exit 1
    }}

    $actualSize = (Get-Item $target).Length
    if ($expectedSize -gt 0 -and $actualSize -ne $expectedSize) {{
        Log "Size mismatch after move: expected $expectedSize, got $actualSize -- not launching a possibly-corrupt exe."
        exit 1
    }}

    Start-Process -FilePath $target
    Log "Update applied successfully ($actualSize bytes), relaunched."
}} catch {{
    Log "Updater script failed: $_"
}}

Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""
    ps1_path.write_text(script)
    return ps1_path


# ---------- send to phone ----------
#
# The PC can push files/folders straight onto a phone's storage -- the
# mirror image of the phone pulling files by browsing: the phone runs a
# small HTTPS server of its own (see pcbridge-android's ReceiveServer.kt)
# and the PC connects out to it as a client, pinning its self-signed
# certificate's fingerprint the same trust-on-first-use way the phone app
# already pins this PC's certificate (see README-DESKTOP.md).
#
# Known phones live in phones.json (gitignored, like config.json -- it
# holds real PINs and fingerprints): a list of {"id", "name",
# "address" ("host:port"), "pin", "cert_fingerprint"}.

PHONES_PATH = app_dir() / "phones.json"


def load_phones() -> list:
    if PHONES_PATH.exists():
        try:
            return json.loads(PHONES_PATH.read_text())
        except Exception:
            pass
    return []


def save_phones(phones: list):
    PHONES_PATH.write_text(json.dumps(phones, indent=2))


class PhoneCertMismatch(Exception):
    """Raised when a phone presents a different certificate than the one
    pinned for it. Could mean the app was reinstalled -- or that something
    is intercepting the connection. Never silently trusted; the caller
    always has to explicitly decide to re-pin (see _send_paths_to_phone)."""

    def __init__(self, expected: str, actual: str):
        self.expected = expected
        self.actual = actual
        super().__init__(f"Certificate fingerprint mismatch: expected {expected}, got {actual}")


def _parse_address(address: str) -> tuple:
    if ":" in address:
        host, port = address.rsplit(":", 1)
        return host, int(port)
    return address, 8000


def _insecure_ssl_context() -> ssl.SSLContext:
    """No CA can vouch for a phone's self-signed certificate, so standard
    chain validation is off here -- the actual security comes from every
    caller below checking the real fingerprint by hand right after
    connecting (see _connect_phone), the same TOFU model SSH uses for
    host keys, not from this context on its own."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _peer_fingerprint(conn: "http.client.HTTPSConnection") -> str:
    der = conn.sock.getpeercert(binary_form=True)
    return hashlib.sha256(der).digest().hex(":").upper()


def _connect_phone(address: str, expected_fingerprint: str = None, timeout: int = 10):
    """Opens a connection and returns (conn, fingerprint). Raises
    PhoneCertMismatch if expected_fingerprint is given and doesn't match --
    the caller decides whether to abort or ask the user to trust the new
    one."""
    host, port = _parse_address(address)
    conn = http.client.HTTPSConnection(host, port, context=_insecure_ssl_context(), timeout=timeout)
    conn.connect()
    fingerprint = _peer_fingerprint(conn)
    if expected_fingerprint and fingerprint.upper() != expected_fingerprint.upper():
        conn.close()
        raise PhoneCertMismatch(expected_fingerprint, fingerprint)
    return conn, fingerprint


def connect_and_learn_fingerprint(address: str, pin: str) -> str:
    """Used by Add Phone: connects without an expected fingerprint (there's
    nothing pinned yet), verifies the PIN is right, and returns the
    fingerprint to pin from now on."""
    conn, fingerprint = _connect_phone(address)
    try:
        query = urllib.parse.urlencode({"pin": pin})
        conn.request("GET", f"/api/ping?{query}")
        resp = conn.getresponse()
        resp.read()
        if resp.status == 401:
            raise RuntimeError("That PIN was rejected by the phone.")
        if resp.status != 200:
            raise RuntimeError(f"Phone returned status {resp.status}.")
    finally:
        conn.close()
    return fingerprint


def ping_phone(phone: dict) -> bool:
    """Used only for the Send to Phone list's online/offline dot -- a
    generous timeout on purpose. A phone address in the 100.64.0.0/10
    range is a Tailscale IP, which sometimes relays through a DERP server
    instead of a direct connection and can add real latency; the previous
    5s timeout was tight enough to flag a genuinely-online phone as
    offline fairly often. This is separate from Android's own Doze/App
    Standby battery restrictions, which can also delay or drop an
    incoming connection to a backgrounded phone with the screen off for a
    while -- no timeout on this side fixes that, it's a phone-side
    background-execution limit, not a network one."""
    try:
        conn, _fp = _connect_phone(phone["address"], phone.get("cert_fingerprint"), timeout=8)
    except Exception:
        return False
    try:
        query = urllib.parse.urlencode({"pin": phone["pin"]})
        conn.request("GET", f"/api/ping?{query}")
        resp = conn.getresponse()
        resp.read()
        return resp.status == 200
    except Exception:
        return False
    finally:
        conn.close()


def collect_send_targets(paths: list) -> list:
    """paths: a list of file/folder paths the user picked. Returns
    (absolute_path, relative_path) pairs -- relative_path is where the
    phone recreates it under its "Received from PC" folder: just the
    filename for a loose file, "<folder name>/<subpath>" for anything
    inside a chosen folder, so folder structure survives the trip."""
    targets = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for root, _dirs, files in os.walk(p):
                for name in files:
                    full = Path(root) / name
                    rel = f"{p.name}/{full.relative_to(p)}".replace(os.sep, "/")
                    targets.append((full, rel))
        elif p.is_file():
            targets.append((p, p.name))
    return targets


def send_file_to_phone(phone: dict, local_path: Path, rel_path: str, on_progress=None):
    """Streams one file to the phone's /api/receive. Raises
    PhoneCertMismatch (caller's job to handle) or RuntimeError on any
    other failure."""
    conn, _fp = _connect_phone(phone["address"], phone.get("cert_fingerprint"), timeout=30)
    try:
        size = local_path.stat().st_size
        query = urllib.parse.urlencode({
            "pin": phone["pin"],
            "relpath": rel_path,
            "filename": local_path.name,
        })
        conn.putrequest("POST", f"/api/receive?{query}")
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        conn.endheaders()

        sent = 0
        with open(local_path, "rb") as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                conn.send(chunk)
                sent += len(chunk)
                if on_progress:
                    on_progress(sent, size)

        resp = conn.getresponse()
        resp.read()
        if resp.status == 401:
            raise RuntimeError("The phone rejected the PIN.")
        if resp.status != 200:
            raise RuntimeError(f"Phone returned status {resp.status}.")
    finally:
        conn.close()


# ---------- browse phone (full file access) ----------
#
# Mirrors server.py's /api/list, /api/download, /api/upload -- but talking
# to the *phone's* small HTTPS server (pcbridge-android's ReceiveServer.kt)
# instead of a PC's, and rooted at the phone's real filesystem rather than
# just its Downloads folder. Requires the phone to have granted itself
# "All files access" (see that repo's DeviceSidebar "Browse this phone"
# section); every call below surfaces that server's own explanation if it
# hasn't been granted, rather than a generic connection error.


def _phone_error(body: bytes, status: int) -> str:
    try:
        message = json.loads(body.decode("utf-8")).get("error", "")
    except Exception:
        message = ""
    return message or f"Phone returned status {status}."


def list_phone_dir(phone: dict, path: str = "") -> dict:
    conn, _fp = _connect_phone(phone["address"], phone.get("cert_fingerprint"), timeout=15)
    try:
        query = urllib.parse.urlencode({"pin": phone["pin"], "path": path})
        conn.request("GET", f"/api/browse/list?{query}")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(_phone_error(body, resp.status))
        return json.loads(body.decode("utf-8"))
    finally:
        conn.close()


def download_file_from_phone(phone: dict, remote_path: str, dest_path: Path, on_progress=None):
    conn, _fp = _connect_phone(phone["address"], phone.get("cert_fingerprint"), timeout=30)
    try:
        query = urllib.parse.urlencode({"pin": phone["pin"], "path": remote_path})
        conn.request("GET", f"/api/browse/download?{query}")
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError(_phone_error(resp.read(), resp.status))
        total = int(resp.getheader("Content-Length") or 0)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        received = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                if on_progress:
                    on_progress(received, total)
    finally:
        conn.close()


def upload_file_to_phone(phone: dict, local_path: Path, remote_dir: str, on_progress=None):
    conn, _fp = _connect_phone(phone["address"], phone.get("cert_fingerprint"), timeout=30)
    try:
        size = local_path.stat().st_size
        query = urllib.parse.urlencode({
            "pin": phone["pin"],
            "path": remote_dir,
            "filename": local_path.name,
        })
        conn.putrequest("POST", f"/api/browse/upload?{query}")
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        conn.endheaders()

        sent = 0
        with open(local_path, "rb") as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                conn.send(chunk)
                sent += len(chunk)
                if on_progress:
                    on_progress(sent, size)

        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(_phone_error(body, resp.status))
    finally:
        conn.close()


def open_phone_download_stream(phone: dict, remote_path: str):
    """Like download_file_from_phone, but hands back (conn, resp) instead
    of writing to a local file -- the caller reads resp in chunks and is
    responsible for closing conn when done. Used by the WebDAV bridge's
    GET handler (see PhoneWebDavHandler) to stream a file straight through
    to Explorer's request instead of buffering the whole thing to disk
    first."""
    conn, _fp = _connect_phone(phone["address"], phone.get("cert_fingerprint"), timeout=30)
    query = urllib.parse.urlencode({"pin": phone["pin"], "path": remote_path})
    conn.request("GET", f"/api/browse/download?{query}")
    resp = conn.getresponse()
    if resp.status != 200:
        body = resp.read()
        conn.close()
        raise RuntimeError(_phone_error(body, resp.status))
    return conn, resp


def stream_upload_to_phone(phone: dict, remote_dir: str, filename: str, size: int, read_source) -> None:
    """Like upload_file_to_phone, but pulls bytes from any read(n)-style
    source (e.g. a WebDAV PUT request's rfile) instead of a local file --
    lets the WebDAV bridge relay a PUT straight through without buffering
    the whole upload to a temp file first. size must be known upfront
    (Explorer's WebDAV PUT requests always send Content-Length)."""
    conn, _fp = _connect_phone(phone["address"], phone.get("cert_fingerprint"), timeout=60)
    try:
        query = urllib.parse.urlencode({
            "pin": phone["pin"],
            "path": remote_dir,
            "filename": filename,
        })
        conn.putrequest("POST", f"/api/browse/upload?{query}")
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        conn.endheaders()

        remaining = size
        while remaining > 0:
            chunk = read_source.read(min(256 * 1024, remaining))
            if not chunk:
                break
            conn.send(chunk)
            remaining -= len(chunk)

        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(_phone_error(body, resp.status))
    finally:
        conn.close()


def mkdir_on_phone(phone: dict, path: str) -> None:
    conn, _fp = _connect_phone(phone["address"], phone.get("cert_fingerprint"), timeout=15)
    try:
        query = urllib.parse.urlencode({"pin": phone["pin"], "path": path})
        conn.request("POST", f"/api/browse/mkdir?{query}")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(_phone_error(body, resp.status))
    finally:
        conn.close()


def delete_on_phone(phone: dict, path: str) -> None:
    conn, _fp = _connect_phone(phone["address"], phone.get("cert_fingerprint"), timeout=30)
    try:
        query = urllib.parse.urlencode({"pin": phone["pin"], "path": path})
        conn.request("POST", f"/api/browse/delete?{query}")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(_phone_error(body, resp.status))
    finally:
        conn.close()


def rename_on_phone(phone: dict, path: str, new_name: str) -> None:
    conn, _fp = _connect_phone(phone["address"], phone.get("cert_fingerprint"), timeout=15)
    try:
        query = urllib.parse.urlencode({"pin": phone["pin"], "path": path, "new_name": new_name})
        conn.request("POST", f"/api/browse/rename?{query}")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(_phone_error(body, resp.status))
    finally:
        conn.close()


def _fmt_bytes(n) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    val = float(n or 0)
    while val >= 1024 and i < len(units) - 1:
        val /= 1024
        i += 1
    return f"{val:.1f} {units[i]}" if i > 0 else f"{int(val)} {units[i]}"


# ---------- WebDAV bridge (browse a phone as a real Explorer drive) ----------
#
# "Browse phone..." used to only open a custom black listbox window --
# functional, but not what a real file explorer looks or feels like, and
# Windows has no way to natively browse an arbitrary HTTPS API. This
# bridges the gap: a tiny WebDAV server (RFC 4918, just the handful of
# verbs Explorer actually uses) listening on 127.0.0.1 only, translating
# every request into a call against the phone's own /api/browse/*
# endpoints (see pcbridge-android's ReceiveServer.kt). Windows' built-in
# WebDAV client (the "WebClient" service, aka the Mini-Redirector) is
# then pointed at it via `net use`, so the phone shows up as an honest
# mapped drive letter in Explorer.
#
# Deliberately plain HTTP, not HTTPS: this server is loopback-only and
# never reachable from the network, so there's nothing to encrypt here --
# the real PC<->phone hop underneath still goes over the existing pinned
# HTTPS connection exactly as everywhere else in this file. Using HTTPS
# for this local bridge would only add the headache of getting Windows'
# WebDAV client to trust a self-signed certificate, for no actual gain in
# security.
#
# NOTE: built and reviewed carefully, but never exercised against a real
# instance of Windows Explorer or the WebClient service (this assistant
# has no Windows environment to test against) -- Windows' WebDAV client
# has a real-world reputation for being finicky (the service is
# Manual-start and often not running by default, and it's historically
# picky about exact header/XML formatting). If mapping the drive fails,
# this falls back to the original custom browser window automatically,
# so "Browse phone..." should never be a dead end even if the WebDAV side
# doesn't cooperate on your machine.

_webdav_bridges: dict = {}  # phone id -> {"server", "thread", "port"}


class PhoneWebDavServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, phone: dict):
        self.phone = phone
        super().__init__(address, PhoneWebDavHandler)


class PhoneWebDavHandler(http.server.BaseHTTPRequestHandler):
    """Implements just enough of WebDAV (OPTIONS/PROPFIND/GET/HEAD/PUT/
    MKCOL/DELETE/MOVE, plus no-op LOCK/UNLOCK) for Explorer's own use of
    it -- not a general-purpose WebDAV server."""

    protocol_version = "HTTP/1.1"
    server_version = "PCBridgeWebDAV/1.0"

    @property
    def phone(self) -> dict:
        return self.server.phone

    def log_message(self, format, *args):
        pass  # keep this quiet; failures still reach Explorer via real status codes

    def _remote_path(self) -> str:
        return urllib.parse.unquote(urllib.parse.urlparse(self.path).path).lstrip("/")

    def _send_error_body(self, code: int, message: str):
        body = message.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _empty_response(self, code: int):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- basic verbs ----

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("DAV", "1,2")
        self.send_header("MS-Author-Via", "DAV")
        self.send_header("Allow", "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, MKCOL, MOVE, LOCK, UNLOCK")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self._handle_get(send_body=False)

    def do_GET(self):
        self._handle_get(send_body=True)

    def _handle_get(self, send_body: bool):
        remote_path = self._remote_path()
        if not remote_path:
            self._send_error_body(404, "Not a file")
            return
        try:
            conn, resp = open_phone_download_stream(self.phone, remote_path)
        except Exception as e:
            self._send_error_body(502, str(e))
            return
        try:
            length = resp.getheader("Content-Length")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            if length is not None:
                self.send_header("Content-Length", length)
            self.end_headers()
            if send_body:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        finally:
            conn.close()

    def do_PUT(self):
        remote_path = self._remote_path()
        if not remote_path:
            self._send_error_body(400, "No filename")
            return
        size = int(self.headers.get("Content-Length") or 0)
        remote_dir = remote_path.rsplit("/", 1)[0] if "/" in remote_path else ""
        filename = remote_path.rsplit("/", 1)[-1]
        try:
            stream_upload_to_phone(self.phone, remote_dir, filename, size, self.rfile)
        except Exception as e:
            self._send_error_body(502, str(e))
            return
        self._empty_response(201)

    def do_MKCOL(self):
        remote_path = self._remote_path()
        if not remote_path:
            self._send_error_body(400, "No folder name")
            return
        try:
            mkdir_on_phone(self.phone, remote_path)
        except Exception as e:
            self._send_error_body(409, str(e))
            return
        self._empty_response(201)

    def do_DELETE(self):
        remote_path = self._remote_path()
        if not remote_path:
            self._send_error_body(400, "Cannot delete root")
            return
        try:
            delete_on_phone(self.phone, remote_path)
        except Exception as e:
            self._send_error_body(502, str(e))
            return
        self._empty_response(204)

    def do_MOVE(self):
        """Explorer sends MOVE for both "rename" and "move to another
        folder." Destination is a full URL or an absolute path depending
        on the client; either way only its path component matters here.
        A same-folder move (a plain rename, by far the common case) goes
        straight to the phone's one rename call. A genuine cross-folder
        move falls back to download+upload+delete through this bridge,
        since the phone's own rename endpoint (mirrors server.py's
        /api/rename) only ever moves within the same folder."""
        remote_path = self._remote_path()
        destination = self.headers.get("Destination", "")
        dest_remote = urllib.parse.unquote(urllib.parse.urlparse(destination).path).lstrip("/")
        if not remote_path or not dest_remote:
            self._send_error_body(400, "Missing source or destination")
            return

        src_dir = remote_path.rsplit("/", 1)[0] if "/" in remote_path else ""
        dst_dir = dest_remote.rsplit("/", 1)[0] if "/" in dest_remote else ""
        dst_name = dest_remote.rsplit("/", 1)[-1]

        try:
            if src_dir == dst_dir:
                rename_on_phone(self.phone, remote_path, dst_name)
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    local_tmp = Path(tmp) / dst_name
                    download_file_from_phone(self.phone, remote_path, local_tmp)
                    upload_file_to_phone(self.phone, local_tmp, dst_dir)
                delete_on_phone(self.phone, remote_path)
        except Exception as e:
            self._send_error_body(502, str(e))
            return
        self._empty_response(201)

    # ---- LOCK/UNLOCK: faked but successful. Some WebDAV-aware Windows
    # components probe for lock support before writing a file; plain
    # Explorer drag/drop and Save As generally don't require it, but a
    # real-looking (if meaningless) lock response costs nothing and
    # avoids surprises with anything that does check. ----

    def do_LOCK(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        token = f"urn:uuid:{uuid.uuid4()}"
        body = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>'
            "<D:locktype><D:write/></D:locktype>"
            "<D:lockscope><D:exclusive/></D:lockscope>"
            "<D:depth>0</D:depth>"
            "<D:timeout>Second-3600</D:timeout>"
            f"<D:locktoken><D:href>{token}</D:href></D:locktoken>"
            "</D:activelock></D:lockdiscovery></D:prop>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", 'text/xml; charset="utf-8"')
        self.send_header("Lock-Token", f"<{token}>")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_UNLOCK(self):
        self._empty_response(204)

    # ---- PROPFIND: directory listing / file metadata ----

    def do_PROPFIND(self):
        remote_path = self._remote_path()
        depth = self.headers.get("Depth", "1")
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)  # a <propfind> body asking for specific props -- ignored, see below

        try:
            if remote_path:
                # PROPFIND on a single file/folder: list its *parent* and
                # pick out the matching entry, since /api/browse/list only
                # describes a directory's contents, not one item's own
                # stat in isolation.
                parent = remote_path.rsplit("/", 1)[0] if "/" in remote_path else ""
                name = remote_path.rsplit("/", 1)[-1]
                result = list_phone_dir(self.phone, parent)
                matches = [e for e in result.get("entries", []) if e["name"] == name]
                if not matches:
                    self._send_error_body(404, "Not found")
                    return
                entry = matches[0]
                responses = [self._entry_xml(remote_path, entry)]
                if entry["is_dir"] and depth != "0":
                    child_result = list_phone_dir(self.phone, remote_path)
                    for child in child_result.get("entries", []):
                        responses.append(self._entry_xml(f"{remote_path}/{child['name']}", child))
            else:
                responses = [self._collection_xml("")]
                if depth != "0":
                    root_result = list_phone_dir(self.phone, "")
                    for child in root_result.get("entries", []):
                        responses.append(self._entry_xml(child["name"], child))
        except Exception as e:
            self._send_error_body(502, str(e))
            return

        body = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<D:multistatus xmlns:D="DAV:">\n' + "\n".join(responses) + "\n</D:multistatus>"
        ).encode("utf-8")
        self.send_response(207)
        self.send_header("Content-Type", 'text/xml; charset="utf-8"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _href(self, remote_path: str, is_dir: bool) -> str:
        href = "/" + urllib.parse.quote(remote_path)
        if is_dir and not href.endswith("/"):
            href += "/"
        return href

    def _collection_xml(self, remote_path: str) -> str:
        href = saxutils.escape(self._href(remote_path, True))
        return (
            f"  <D:response><D:href>{href}</D:href>"
            "<D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype>"
            "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
        )

    def _entry_xml(self, remote_path: str, entry: dict) -> str:
        is_dir = entry["is_dir"]
        href = saxutils.escape(self._href(remote_path, is_dir))
        name = saxutils.escape(entry["name"])
        modified_ms = entry.get("modified") or 0
        last_modified = formatdate(modified_ms / 1000 if modified_ms else 0, usegmt=True)
        if is_dir:
            return (
                f"  <D:response><D:href>{href}</D:href><D:propstat><D:prop>"
                f"<D:displayname>{name}</D:displayname>"
                "<D:resourcetype><D:collection/></D:resourcetype>"
                f"<D:getlastmodified>{last_modified}</D:getlastmodified>"
                "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
            )
        size = entry.get("size") or 0
        return (
            f"  <D:response><D:href>{href}</D:href><D:propstat><D:prop>"
            f"<D:displayname>{name}</D:displayname>"
            "<D:resourcetype/>"
            f"<D:getcontentlength>{size}</D:getcontentlength>"
            f"<D:getlastmodified>{last_modified}</D:getlastmodified>"
            "<D:getcontenttype>application/octet-stream</D:getcontenttype>"
            "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
        )


def _find_free_port(start: int = 47990, tries: int = 20) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Couldn't find a free local port for the WebDAV bridge")


def start_webdav_bridge(phone: dict) -> int:
    """Starts (or reuses) a loopback-only WebDAV server bridging Explorer
    to one phone's /api/browse/* endpoints. Kept running for the rest of
    this app's lifetime once started (a daemon thread, so it never blocks
    exit) -- reused on every later "Browse phone..." click for the same
    phone rather than starting a duplicate server each time."""
    existing = _webdav_bridges.get(phone["id"])
    if existing is not None:
        return existing["port"]

    port = _find_free_port()
    httpd = PhoneWebDavServer(("127.0.0.1", port), phone)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _webdav_bridges[phone["id"]] = {"server": httpd, "thread": thread, "port": port}
    return port


# ---------- tray icon ----------

def build_tray_image():
    from PIL import Image

    icon_path = BASE_DIR / "static" / "icons" / "icon-192.png"
    try:
        return Image.open(icon_path)
    except Exception:
        # Bundled icon missing for some reason -- fall back to a flat
        # square in the app's accent color rather than crashing the tray.
        return Image.new("RGB", (64, 64), BLUE)


def build_tray_icon(on_open, on_toggle, on_send_to_phone, on_check_updates, on_quit):
    import pystray

    return pystray.Icon(
        "pcbridge",
        build_tray_image(),
        "PC Bridge",
        menu=pystray.Menu(
            pystray.MenuItem("Open PC Bridge", on_open, default=True),
            pystray.MenuItem(
                lambda item: "Stop server" if server.running else "Start server",
                on_toggle,
            ),
            pystray.MenuItem("Send to Phone...", on_send_to_phone),
            pystray.MenuItem("Check for updates", on_check_updates),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        ),
    )


# ---------- window ----------

class App:
    def __init__(self):
        self.config = load_config()
        if not self.config.get("pin"):
            self.config["pin"] = f"{secrets.randbelow(1000000):06d}"
            save_config(self.config)

        self.root = tk.Tk()
        self.root.title("PC Bridge")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        self._build_ui()
        self.icon = build_tray_icon(
            self.on_tray_open, self.on_tray_toggle, self.on_tray_send_to_phone,
            self.on_tray_check_updates, self.on_quit
        )
        threading.Thread(target=self.icon.run, daemon=True).start()

        self.refresh()

        if self.config.get("auto_check_updates", True):
            self.root.after(2000, lambda: self.check_for_updates(interactive=False))

    # ---- layout ----

    def _section(self, parent):
        frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="x", padx=16, pady=(0, 12))
        return frame

    def _build_ui(self):
        tk.Label(
            self.root, text="PC Bridge", bg=BG, fg=TEXT, font=("Segoe UI", 15, "bold")
        ).pack(anchor="w", padx=16, pady=(16, 10))

        status_frame = self._section(self.root)
        row = tk.Frame(status_frame, bg=CARD)
        row.pack(fill="x", padx=12, pady=12)
        self.status_dot = tk.Label(row, text="●", bg=CARD, fg=RED, font=("Segoe UI", 12))
        self.status_dot.pack(side="left")
        self.status_label = tk.Label(
            row, text="Stopped", bg=CARD, fg=TEXT, font=("Segoe UI", 11, "bold")
        )
        self.status_label.pack(side="left", padx=(6, 0))

        self.address_label = tk.Label(
            status_frame, text="", bg=CARD, fg=BLUE, font=("Segoe UI", 10), cursor="hand2"
        )
        self.address_label.pack(anchor="w", padx=12, pady=(0, 10))
        self.address_label.bind("<Button-1>", self.on_copy_address)

        self.toggle_btn = tk.Button(
            status_frame,
            text="Start server",
            command=self.on_toggle_clicked,
            bg=BLUE,
            fg=ON_BLUE,
            activebackground=BLUE,
            activeforeground=ON_BLUE,
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=6,
        )
        self.toggle_btn.pack(anchor="w", padx=12, pady=(0, 12))

        pin_frame = self._section(self.root)
        tk.Label(
            pin_frame, text="PIN your phone enters", bg=CARD, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", padx=12, pady=(12, 2))
        pin_row = tk.Frame(pin_frame, bg=CARD)
        pin_row.pack(fill="x", padx=12, pady=(0, 12))
        self.pin_var = tk.StringVar(value=self.config["pin"])
        self.pin_entry = tk.Entry(
            pin_row,
            textvariable=self.pin_var,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Consolas", 12),
            width=10,
        )
        self.pin_entry.pack(side="left", ipady=4)
        tk.Button(
            pin_row, text="Randomize", command=self.on_randomize_pin,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 9),
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            pin_row, text="Save", command=self.on_save_pin,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 9),
        ).pack(side="left", padx=(6, 0))

        folder_frame = self._section(self.root)
        tk.Label(
            folder_frame, text="Shared folder", bg=CARD, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", padx=12, pady=(12, 2))
        self.folder_label = tk.Label(
            folder_frame, text=self.config["root_dir"], bg=CARD, fg=TEXT,
            font=("Segoe UI", 9), wraplength=280, justify="left",
        )
        self.folder_label.pack(anchor="w", padx=12, pady=(0, 8))
        tk.Button(
            folder_frame, text="Change folder...", command=self.on_change_folder,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 9),
        ).pack(anchor="w", padx=12, pady=(0, 12))

        send_frame = self._section(self.root)
        tk.Label(
            send_frame, text="Send files to a phone", bg=CARD, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w", padx=12, pady=(12, 2))
        tk.Label(
            send_frame,
            text="Push files or a whole folder straight onto a phone's storage.",
            bg=CARD, fg=MUTED, font=("Segoe UI", 8), wraplength=280, justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 8))
        tk.Button(
            send_frame, text="Send to Phone...", command=self.on_send_to_phone,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 9),
        ).pack(anchor="w", padx=12, pady=(0, 12))

        version_row = tk.Frame(self.root, bg=BG)
        version_row.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(
            version_row, text=f"v{APP_VERSION}", bg=BG, fg=MUTED, font=("Segoe UI", 8)
        ).pack(side="left")
        tk.Button(
            version_row, text="Check for updates",
            command=lambda: self.check_for_updates(interactive=True),
            bg=BG, fg=BLUE, activebackground=BG, activeforeground=BLUE,
            relief="flat", font=("Segoe UI", 8, "underline"), bd=0, cursor="hand2",
        ).pack(side="left", padx=(8, 0))

        self.hint_label = tk.Label(
            self.root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=300, justify="left"
        )
        self.hint_label.pack(anchor="w", padx=16, pady=(0, 14))

    # ---- state / actions ----

    def refresh(self):
        """The recurring poll loop -- reschedules itself. Call this once,
        from __init__, and never again; anything that wants an immediate
        UI update after an action should call _update_display() instead,
        or this would stack up an extra parallel polling loop every time."""
        self._update_display()
        self.root.after(1500, self.refresh)

    def _update_display(self):
        running = server.running
        self.status_dot.config(fg=GREEN if running else RED)
        self.status_label.config(text="Running" if running else "Stopped")
        self.toggle_btn.config(text="Stop server" if running else "Start server")
        if running:
            self.address_label.config(
                text=f"https://{lan_ip()}:{self.config.get('port', 8000)}"
            )
        else:
            self.address_label.config(text="Not reachable while stopped")

    def on_toggle_clicked(self):
        if server.running:
            server.stop()
        else:
            server.start()
            # Give it a moment, then check it actually stayed up (a bad
            # config or a port already in use makes it exit immediately).
            self.root.after(800, self._check_started)
        self._update_display()

    def _check_started(self):
        if not server.running:
            messagebox.showerror(
                "PC Bridge",
                "The server didn't stay running -- it may have exited "
                f"immediately. Check {server.log_path} for details (e.g. "
                "the port may already be in use).",
            )
        self._update_display()

    def on_copy_address(self, event=None):
        text = self.address_label.cget("text")
        if not text.startswith("https://"):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.hint_label.config(text="Address copied to clipboard.")

    def on_randomize_pin(self):
        self.pin_var.set(f"{secrets.randbelow(1000000):06d}")

    def on_save_pin(self):
        new_pin = self.pin_var.get().strip()
        if len(new_pin) < 4:
            messagebox.showwarning("PC Bridge", "PIN should be at least 4 characters.")
            return
        self.config["pin"] = new_pin
        save_config(self.config)
        self._maybe_offer_restart()

    def on_change_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.config["root_dir"])
        if not chosen:
            return
        self.config["root_dir"] = chosen
        save_config(self.config)
        self.folder_label.config(text=chosen)
        self._maybe_offer_restart()

    def _maybe_offer_restart(self):
        if not server.running:
            self.hint_label.config(text="Saved. Starts using this the next time you start the server.")
            return
        if messagebox.askyesno(
            "PC Bridge", "The server is running. Restart it now so this change takes effect?"
        ):
            server.stop()
            server.start()
            self.root.after(800, self._check_started)
        else:
            self.hint_label.config(text="Saved. Restart the server for this to take effect.")
        self._update_display()

    # ---- send to phone ----

    def on_send_to_phone(self):
        self._show_phone_picker(load_phones())

    def _dialog(self, title: str) -> tk.Toplevel:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        # Every dialog used to open pinned at (0, 0) regardless of where the
        # main window actually was, since Tk defaults a Toplevel's position
        # to the screen origin rather than the parent's. after_idle defers
        # this until the caller has finished packing its widgets (so
        # winfo_width/height below reflect the dialog's real, final size
        # instead of Tk's placeholder 1x1 before anything's laid out).
        dialog.after_idle(lambda: self._center_over_parent(dialog, self.root))
        return dialog

    def _center_over_parent(self, win: tk.Toplevel, parent: tk.Tk) -> None:
        win.update_idletasks()
        parent.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        x = max(px + (pw - w) // 2, 0)
        y = max(py + (ph - h) // 2, 0)
        win.geometry(f"+{x}+{y}")

    def _show_phone_picker(self, phones: list):
        dialog = self._dialog("Send to Phone")

        tk.Label(
            dialog, text="Send to which phone?", bg=BG, fg=TEXT, font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", padx=16, pady=(16, 8))

        if not phones:
            tk.Label(
                dialog, text="No phones added yet.", bg=BG, fg=MUTED, font=("Segoe UI", 9)
            ).pack(anchor="w", padx=16, pady=(0, 8))

        # id -> status dot Label, so the background ping below can color
        # each one in place once it knows the answer, the same online/
        # offline-dot idea the phone app already uses for its PC list.
        status_dots = {}

        def remove_phone(p):
            if not messagebox.askyesno(
                "PC Bridge",
                f"Remove \"{p['name']}\"? You'll need to add it again (address, "
                "PIN, and re-verify its certificate) to send to it later.",
            ):
                return
            remaining = [ph for ph in load_phones() if ph["id"] != p["id"]]
            save_phones(remaining)
            dialog.destroy()
            self._show_phone_picker(remaining)

        for phone in phones:
            row = tk.Frame(dialog, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            row.pack(fill="x", padx=16, pady=(0, 6))

            tk.Button(
                row, text=phone["name"], anchor="w",
                command=lambda p=phone: (dialog.destroy(), self._choose_send_kind(p)),
                bg=CARD, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
                relief="flat", font=("Segoe UI", 10), padx=10, pady=8,
            ).pack(side="left", fill="x", expand=True)

            tk.Button(
                row, text="✕", command=lambda p=phone: remove_phone(p),
                bg=CARD, fg=MUTED, activebackground=BORDER, activeforeground=RED,
                relief="flat", font=("Segoe UI", 10), padx=8, pady=8, bd=0,
            ).pack(side="right")

            dot = tk.Label(row, text="●", bg=CARD, fg=MUTED, font=("Segoe UI", 10))
            dot.pack(side="right", padx=(0, 4))
            status_dots[phone["id"]] = dot

        tk.Button(
            dialog, text="+ Add new phone", anchor="w",
            command=lambda: (dialog.destroy(), self._show_add_phone_dialog()),
            bg=BG, fg=BLUE, activebackground=BG, activeforeground=BLUE,
            relief="flat", font=("Segoe UI", 10, "underline"), padx=10, pady=8,
        ).pack(fill="x", padx=16, pady=(0, 6))

        tk.Button(
            dialog, text="Cancel", command=dialog.destroy,
            bg=BG, fg=MUTED, activebackground=BG, activeforeground=MUTED,
            relief="flat", font=("Segoe UI", 9),
        ).pack(anchor="e", padx=16, pady=(4, 16))

        # Ping every known phone on a background thread so the dialog opens
        # instantly -- an asleep or off-network phone would otherwise make
        # the whole list wait on its own connect timeout before anything
        # shows up. Each dot starts muted ("checking") and flips to
        # green/red as its own ping resolves independently.
        def ping_worker():
            for phone in phones:
                online = ping_phone(phone)

                def update(p=phone, ok=online):
                    dot = status_dots.get(p["id"])
                    if dot is not None and dot.winfo_exists():
                        dot.config(fg=GREEN if ok else RED)

                self.root.after(0, update)

        threading.Thread(target=ping_worker, daemon=True).start()

    def _show_add_phone_dialog(self):
        dialog = self._dialog("Add Phone")

        def labeled_entry(label_text):
            tk.Label(dialog, text=label_text, bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(
                anchor="w", padx=16, pady=(12, 2)
            )
            var = tk.StringVar()
            tk.Entry(
                dialog, textvariable=var, bg=CARD, fg=TEXT, insertbackground=TEXT,
                relief="flat", font=("Segoe UI", 10), width=32,
            ).pack(fill="x", padx=16, ipady=4)
            return var

        name_var = labeled_entry("Name (e.g. Florind's Pixel)")
        address_var = labeled_entry("Address:port (from the phone's sidebar)")
        pin_var = labeled_entry("PIN (from the phone's sidebar)")

        status_label = tk.Label(
            dialog, text="", bg=BG, fg=RED, font=("Segoe UI", 9), wraplength=280, justify="left"
        )
        status_label.pack(anchor="w", padx=16, pady=(8, 0))

        def do_connect():
            name = name_var.get().strip()
            address = address_var.get().strip()
            pin = pin_var.get().strip()
            if not name or not address or not pin:
                status_label.config(text="Fill in all three fields.")
                return
            connect_btn.config(state="disabled", text="Connecting...")
            status_label.config(text="")

            def worker():
                try:
                    fingerprint = connect_and_learn_fingerprint(address, pin)
                except Exception as e:
                    # str(e) can come back empty for some bare timeout/
                    # connection exceptions (e.g. a message-less
                    # socket.timeout from a hung TLS handshake) -- that
                    # made a real failure look like "nothing happened,
                    # button just reset," with no way to tell what went
                    # wrong. Always show at least the exception's type so
                    # there's never a silent blank failure here.
                    message = str(e) or f"{type(e).__name__} (no further details)"
                    self.root.after(0, lambda: (
                        connect_btn.config(state="normal", text="Connect"),
                        status_label.config(text=message),
                    ))
                    return

                def on_success():
                    phones = load_phones()
                    phone = {
                        "id": secrets.token_hex(8),
                        "name": name,
                        "address": address,
                        "pin": pin,
                        "cert_fingerprint": fingerprint,
                    }
                    phones.append(phone)
                    save_phones(phones)
                    dialog.destroy()
                    self._choose_send_kind(phone)

                self.root.after(0, on_success)

            threading.Thread(target=worker, daemon=True).start()

        connect_btn = tk.Button(
            dialog, text="Connect", command=do_connect,
            bg=BLUE, fg=ON_BLUE, activebackground=BLUE, activeforeground=ON_BLUE,
            relief="flat", font=("Segoe UI", 10, "bold"), padx=10, pady=6,
        )
        connect_btn.pack(anchor="w", padx=16, pady=(12, 16))

    def _choose_send_kind(self, phone: dict):
        dialog = self._dialog("Send to Phone")

        tk.Label(
            dialog, text=f"Send to {phone['name']}", bg=BG, fg=TEXT, font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", padx=16, pady=(16, 10))

        def pick_files():
            dialog.destroy()
            chosen = filedialog.askopenfilenames(title="Choose files to send")
            if chosen:
                self._send_paths_to_phone(phone, list(chosen))

        def pick_folder():
            dialog.destroy()
            chosen = filedialog.askdirectory(title="Choose a folder to send")
            if chosen:
                self._send_paths_to_phone(phone, [chosen])

        tk.Button(
            dialog, text="Choose files...", command=pick_files,
            bg=BLUE, fg=ON_BLUE, activebackground=BLUE, activeforeground=ON_BLUE,
            relief="flat", font=("Segoe UI", 10, "bold"), padx=10, pady=8,
        ).pack(fill="x", padx=16, pady=(0, 8))

        tk.Button(
            dialog, text="Choose a folder...", command=pick_folder,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 10), padx=10, pady=8,
        ).pack(fill="x", padx=16, pady=(0, 8))

        def browse():
            dialog.destroy()
            self._open_phone_explorer(phone)

        tk.Button(
            dialog, text="Browse phone...", command=browse,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 10), padx=10, pady=8,
        ).pack(fill="x", padx=16, pady=(0, 16))

    # ---- browse phone ----

    def _open_phone_explorer(self, phone: dict):
        """Opens this phone as a real mapped drive in Windows Explorer via
        the WebDAV bridge (see start_webdav_bridge above), instead of the
        custom browser window. Falls back to that custom window if mapping
        fails for any reason -- most likely Windows' "WebClient" service
        (its built-in WebDAV client) being disabled, which is common on a
        fresh install since it's Manual-start by default."""
        if sys.platform != "win32":
            self._show_phone_browser(phone)
            return

        try:
            port = start_webdav_bridge(phone)
        except Exception as e:
            messagebox.showerror("PC Bridge", f"Couldn't start the local bridge:\n{e}")
            return

        # The WebClient service is Manual-start on most non-Server Windows
        # editions, so it's often simply not running yet -- try to start
        # it before mapping. This can silently fail without admin rights;
        # that's fine, _map_free_drive below still reports a clear error
        # if the service genuinely isn't available at all.
        try:
            subprocess.run(
                ["sc", "start", "webclient"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
            )
        except Exception:
            pass

        drive = self._map_free_drive(port)
        if drive is None:
            if messagebox.askyesno(
                "PC Bridge",
                "Couldn't open this phone as a network drive in Explorer -- "
                "Windows' built-in WebDAV client (the \"WebClient\" service) "
                "may be disabled on this PC. Open the built-in browser "
                "window instead?",
            ):
                self._show_phone_browser(phone)
            return

        subprocess.Popen(["explorer.exe", f"{drive}:\\"])

    def _map_free_drive(self, port: int) -> str | None:
        """Maps the WebDAV bridge at 127.0.0.1:port onto the first unused
        drive letter (working down from Z:), via `net use` -- the same
        mechanism Windows' own "Map network drive" dialog uses under the
        hood. Returns the letter on success, or None if the very first
        attempt fails (treated as "WebClient itself isn't cooperating"
        rather than retrying every remaining letter for no reason)."""
        for letter in "ZYXWVUTSRQPONMLKJIHGFEDCBA":
            if os.path.exists(f"{letter}:\\"):
                continue
            try:
                result = subprocess.run(
                    ["net", "use", f"{letter}:", f"\\\\127.0.0.1@{port}\\", "/persistent:no"],
                    capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=15,
                )
            except Exception:
                return None
            return letter if result.returncode == 0 else None
        return None

    def _show_phone_browser(self, phone: dict):
        """A two-way file browser for one phone's full filesystem -- needs
        that phone to have granted itself "All files access" (see
        pcbridge-android's DeviceSidebar); if it hasn't, every action below
        just surfaces that server's own explanation instead of failing
        mysteriously."""
        dialog = self._dialog(f"Browse {phone['name']}")
        dialog.resizable(True, True)
        dialog.geometry("480x420")

        # state["path"]/["parent"]/["entries"] instead of plain locals --
        # every nested callback below (refresh/navigate/download/upload)
        # only ever mutates dict contents, never rebinds the name, so none
        # of them need `nonlocal` to see each other's updates.
        state = {"path": "", "parent": None, "entries": []}

        path_label = tk.Label(
            dialog, text="/", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w"
        )
        path_label.pack(fill="x", padx=16, pady=(12, 4))

        list_frame = tk.Frame(dialog, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        listbox = tk.Listbox(
            list_frame, bg=CARD, fg=TEXT, selectbackground=BLUE, selectforeground=ON_BLUE,
            font=("Segoe UI", 10), relief="flat", highlightthickness=0,
            yscrollcommand=scrollbar.set, selectmode="extended", activestyle="none",
        )
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=listbox.yview)

        status_label = tk.Label(
            dialog, text="Loading...", bg=BG, fg=MUTED, font=("Segoe UI", 9),
            wraplength=440, justify="left",
        )
        status_label.pack(anchor="w", padx=16, pady=(0, 4))

        def entry_at(index):
            """Maps a Listbox row index back to either "__parent__" (the
            ".." row, only present when not at the phone's storage root) or
            the matching entry dict from the last /api/browse/list result."""
            has_parent = state["parent"] is not None
            if has_parent:
                if index == 0:
                    return "__parent__"
                index -= 1
            entries = state["entries"]
            return entries[index] if 0 <= index < len(entries) else None

        def refresh():
            status_label.config(text="Loading...", fg=MUTED)

            def worker():
                try:
                    result = list_phone_dir(phone, state["path"])
                except Exception as e:
                    self.root.after(0, lambda: status_label.config(text=str(e), fg=RED))
                    return

                def apply():
                    state["path"] = result.get("path", "")
                    state["parent"] = result.get("parent")
                    state["entries"] = result.get("entries", [])
                    path_label.config(text="/" + state["path"])
                    listbox.delete(0, "end")
                    if state["parent"] is not None:
                        listbox.insert("end", "..")
                    for entry in state["entries"]:
                        if entry["is_dir"]:
                            listbox.insert("end", f"[DIR]  {entry['name']}")
                        else:
                            listbox.insert("end", f"{entry['name']}  ({_fmt_bytes(entry.get('size') or 0)})")
                    for i in range(listbox.size()):
                        target = entry_at(i)
                        if target == "__parent__" or (isinstance(target, dict) and target["is_dir"]):
                            listbox.itemconfig(i, {"fg": BLUE})
                    status_label.config(text=f"{len(state['entries'])} item(s).", fg=MUTED)

                self.root.after(0, apply)

            threading.Thread(target=worker, daemon=True).start()

        def on_double_click(_event=None):
            sel = listbox.curselection()
            if not sel:
                return
            target = entry_at(sel[0])
            if target == "__parent__":
                state["path"] = state["parent"] or ""
                refresh()
            elif isinstance(target, dict) and target["is_dir"]:
                state["path"] = f"{state['path']}/{target['name']}" if state["path"] else target["name"]
                refresh()

        listbox.bind("<Double-Button-1>", on_double_click)

        def selected_files():
            files = []
            for index in listbox.curselection():
                target = entry_at(index)
                if isinstance(target, dict) and not target["is_dir"]:
                    files.append(target)
            return files

        def do_download():
            files = selected_files()
            if not files:
                status_label.config(text="Select one or more files (not folders) to download.", fg=RED)
                return
            dest_dir = filedialog.askdirectory(title="Save downloaded file(s) to...")
            if not dest_dir:
                return
            status_label.config(text=f"Downloading {len(files)} file(s)...", fg=MUTED)

            def worker():
                failed = []
                for entry in files:
                    remote_path = f"{state['path']}/{entry['name']}" if state["path"] else entry["name"]
                    try:
                        download_file_from_phone(phone, remote_path, Path(dest_dir) / entry["name"])
                    except Exception as e:
                        failed.append((entry["name"], str(e)))

                def finish():
                    if failed:
                        lines = "\n".join(f"- {n}: {err}" for n, err in failed[:5])
                        status_label.config(text=f"Some downloads failed:\n{lines}", fg=RED)
                    else:
                        status_label.config(text=f"Downloaded {len(files)} file(s) to {dest_dir}.", fg=GREEN)

                self.root.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()

        def upload_paths(paths):
            targets = collect_send_targets(paths)
            if not targets:
                status_label.config(text="Nothing to upload.", fg=RED)
                return
            status_label.config(text=f"Uploading {len(targets)} file(s)...", fg=MUTED)

            def worker():
                failed = []
                sent = 0
                for local_path, rel_path in targets:
                    subdir = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
                    remote_dir = f"{state['path']}/{subdir}" if state["path"] and subdir else (subdir or state["path"])
                    try:
                        upload_file_to_phone(phone, local_path, remote_dir)
                        sent += 1
                    except Exception as e:
                        failed.append((rel_path, str(e)))

                def finish():
                    if failed:
                        lines = "\n".join(f"- {n}: {err}" for n, err in failed[:5])
                        status_label.config(text=f"Sent {sent}/{len(targets)}. Some failed:\n{lines}", fg=RED)
                    else:
                        status_label.config(text=f"Uploaded {sent} file(s) to /{state['path']}.", fg=GREEN)
                    refresh()

                self.root.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()

        def do_upload_files():
            chosen = filedialog.askopenfilenames(title="Choose files to upload")
            if chosen:
                upload_paths(list(chosen))

        def do_upload_folder():
            chosen = filedialog.askdirectory(title="Choose a folder to upload")
            if chosen:
                upload_paths([chosen])

        btn_row = tk.Frame(dialog, bg=BG)
        btn_row.pack(fill="x", padx=16, pady=(0, 16))

        tk.Button(
            btn_row, text="Download selected", command=do_download,
            bg=BLUE, fg=ON_BLUE, activebackground=BLUE, activeforeground=ON_BLUE,
            relief="flat", font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        ).pack(side="left")
        tk.Button(
            btn_row, text="Upload files...", command=do_upload_files,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 9), padx=8, pady=6,
        ).pack(side="left", padx=(6, 0))
        tk.Button(
            btn_row, text="Upload folder...", command=do_upload_folder,
            bg=BORDER, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=("Segoe UI", 9), padx=8, pady=6,
        ).pack(side="left", padx=(6, 0))
        tk.Button(
            btn_row, text="Close", command=dialog.destroy,
            bg=BG, fg=MUTED, activebackground=BG, activeforeground=MUTED,
            relief="flat", font=("Segoe UI", 9),
        ).pack(side="right")

        refresh()

    def _send_paths_to_phone(self, phone: dict, paths: list):
        targets = collect_send_targets(paths)
        if not targets:
            messagebox.showinfo("PC Bridge", "Nothing to send.")
            return

        progress = self._dialog("Sending...")
        progress.protocol("WM_DELETE_WINDOW", lambda: None)  # no closing mid-send

        label = tk.Label(
            progress, text="Starting...", bg=BG, fg=TEXT, font=("Segoe UI", 10),
            wraplength=280, justify="left",
        )
        label.pack(anchor="w", padx=16, pady=(16, 8))
        bar = ttk.Progressbar(progress, length=280, mode="determinate", maximum=len(targets))
        bar.pack(padx=16, pady=(0, 16))

        def worker():
            sent_count = 0
            failed = []
            current_phone = phone

            for index, (local_path, rel_path) in enumerate(targets):
                def on_progress(sent, total, _rel=rel_path, _i=index):
                    pct = int(sent * 100 / total) if total else 0
                    self.root.after(0, lambda: label.config(
                        text=f"Sending {_i + 1}/{len(targets)}: {_rel} ({pct}%)"
                    ))

                try:
                    send_file_to_phone(current_phone, local_path, rel_path, on_progress)
                    sent_count += 1
                except PhoneCertMismatch as e:
                    decision = {}
                    done = threading.Event()

                    def ask(_e=e):
                        decision["trust"] = messagebox.askyesno(
                            "PC Bridge",
                            f"\"{current_phone['name']}\" is presenting a different security "
                            "certificate than the one trusted before. This can happen if the "
                            "app was reinstalled -- but it can also mean someone is "
                            "intercepting the connection.\n\n"
                            f"New fingerprint: {_e.actual}\n\n"
                            "Only continue if you're sure this is expected.",
                        )
                        done.set()

                    self.root.after(0, ask)
                    done.wait()
                    if decision.get("trust"):
                        current_phone = dict(current_phone, cert_fingerprint=e.actual)
                        phones = load_phones()
                        for i, ph in enumerate(phones):
                            if ph["id"] == current_phone["id"]:
                                phones[i] = current_phone
                        save_phones(phones)
                        try:
                            send_file_to_phone(current_phone, local_path, rel_path, on_progress)
                            sent_count += 1
                        except Exception as e2:
                            failed.append((rel_path, str(e2)))
                    else:
                        failed.append((rel_path, "certificate not trusted"))
                        break
                except Exception as e:
                    failed.append((rel_path, str(e)))

                self.root.after(0, lambda c=index + 1: bar.config(value=c))

            def finish():
                progress.destroy()
                if failed:
                    lines = "\n".join(f"- {name}: {err}" for name, err in failed[:5])
                    more = f"\n...and {len(failed) - 5} more" if len(failed) > 5 else ""
                    messagebox.showwarning(
                        "PC Bridge",
                        f"Sent {sent_count}/{len(targets)} to {phone['name']}. "
                        f"Some failed:\n{lines}{more}",
                    )
                else:
                    messagebox.showinfo(
                        "PC Bridge", f"Sent {sent_count} file(s) to {phone['name']}."
                    )

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # ---- auto-update ----

    def check_for_updates(self, interactive: bool = False):
        """Runs the network check on a background thread so it can't
        freeze the window, then marshals every UI touch back onto the
        Tkinter main thread via root.after (Tkinter isn't thread-safe)."""

        def worker():
            repo = self.config.get("update_repo")
            token = self.config.get("update_token")
            if not repo or not token:
                if interactive:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "PC Bridge",
                        "Update checking isn't set up. Add \"update_repo\" "
                        "(e.g. \"yourname/pcbridge\") and \"update_token\" "
                        "to config.json -- see README-DESKTOP.md.",
                    ))
                return
            try:
                release = fetch_latest_release(repo, token)
            except Exception as e:
                if interactive:
                    self.root.after(0, lambda: messagebox.showerror(
                        "PC Bridge", f"Couldn't check for updates:\n{e}"
                    ))
                return
            latest = release.get("tag_name", "")
            if not is_newer(latest, APP_VERSION):
                if interactive:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "PC Bridge", f"You're up to date (v{APP_VERSION})."
                    ))
                return
            asset = find_asset(release)
            if not asset:
                if interactive:
                    self.root.after(0, lambda: messagebox.showwarning(
                        "PC Bridge",
                        f"Version {latest} is out, but no \"{UPDATE_ASSET_NAME}\" "
                        "file was attached to that release.",
                    ))
                return
            self.root.after(0, lambda: self._prompt_update(
                latest, repo, token, asset["id"], asset.get("size", 0)
            ))

        threading.Thread(target=worker, daemon=True).start()

    def _prompt_update(self, latest, repo, token, asset_id, expected_size=0):
        if not messagebox.askyesno(
            "PC Bridge",
            f"Version {latest} is available (you have v{APP_VERSION}). "
            "Download and install it now? The app will restart.",
        ):
            return
        self.hint_label.config(text=f"Downloading v{latest}...")

        def worker():
            dest = app_dir() / "PCBridge_update.exe"
            try:
                download_release_asset(repo, token, asset_id, dest)
                actual_size = dest.stat().st_size
                if expected_size and actual_size != expected_size:
                    dest.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"Downloaded {actual_size} bytes but GitHub said this "
                        f"release is {expected_size} bytes -- the download "
                        "looks incomplete or was altered (antivirus tools "
                        "sometimes strip freshly-downloaded exes). Try again "
                        "in a moment."
                    )
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "PC Bridge", f"Update download failed:\n{e}"
                ))
                return
            self.root.after(0, lambda: self._install_update(dest, expected_size))

        threading.Thread(target=worker, daemon=True).start()

    def _install_update(self, new_exe_path: Path, expected_size: int = 0):
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(
                "PC Bridge",
                "You're running from source (python pcbridge_app.py), not "
                "the packaged exe -- pull the latest code with git instead "
                "of installing this way.",
            )
            return

        target_exe = Path(sys.executable).resolve()
        size = expected_size or new_exe_path.stat().st_size
        ps1_path = _write_updater_script(target_exe, new_exe_path, os.getpid(), size)

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", str(ps1_path)],
            creationflags=creationflags, close_fds=True,
        )

        server.stop()
        self.icon.stop()
        self.root.destroy()
        os._exit(0)

    # ---- window show/hide, tray callbacks ----

    def hide_window(self):
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_tray_open(self, icon=None, item=None):
        self.root.after(0, self.show_window)

    def on_tray_toggle(self, icon=None, item=None):
        self.root.after(0, self.on_toggle_clicked)

    def on_tray_check_updates(self, icon=None, item=None):
        self.root.after(0, lambda: self.check_for_updates(interactive=True))

    def on_tray_send_to_phone(self, icon=None, item=None):
        self.root.after(0, lambda: (self.show_window(), self.on_send_to_phone()))

    def on_quit(self, icon=None, item=None):
        def _quit():
            server.stop()
            self.icon.stop()
            self.root.destroy()

        self.root.after(0, _quit)

    def run(self):
        self.root.mainloop()


def main():
    App().run()


if __name__ == "__main__":
    if "--server" in sys.argv:
        import server

        server.run()
    else:
        main()
