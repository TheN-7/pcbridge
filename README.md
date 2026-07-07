# PC Bridge

Browse, upload, and download files between your PC and phone over your local
network (or Tailscale). Your PC runs a small server; your phone connects to
it with a browser and installs it as an app icon (PWA) — no app store needed
— or use the dedicated Android app (see below).

## 1. Install (on the PC, one time)

Requires Python 3.9+.

```
cd pcbridge
pip install -r requirements.txt
```

## 2. Run

```
python server.py
```

The console prints something like:

```
PC Bridge is starting
Serving folder: C:\Users\you
PIN: 483920
Open on your phone:  https://192.168.1.42:8000
Certificate fingerprint: AA:BB:CC:...
```

Leave this window open — the server runs as long as this is running.

**Prefer not to use a terminal?** See `README-DESKTOP.md` for a small
system tray app (`pcbridge_app.py`) with a Start/Stop button, a window
for changing the PIN and shared folder, auto-update, and a proper
installer with a Start Menu icon — no console window needed.

## 3. Connect from your phone

1. Make sure your phone is on the **same Wi-Fi network** as the PC (or both
   are on the same Tailscale network — see below).
2. Open the printed `https://<ip>:8000` address in your phone's browser.
   The certificate is self-signed, so your browser will show a warning the
   first time — that's expected (see "Security notes" below); proceed past it.
3. Enter the PIN shown in the PC console.
4. To install it as an app icon:
   - **Android (Chrome):** tap the ⋮ menu → "Add to Home screen" / "Install app".
   - **iPhone (Safari):** tap the Share icon → "Add to Home Screen".

From then on it opens like a normal app, full-screen, with its own icon.

**Prefer a native Android app instead of the browser/PWA?** See the
separate `pcbridge-android` repo — a fully native Kotlin/Compose app
talking directly to this server's API (no browser warning to click past,
since it pins the certificate itself instead), with biometric lock,
notifications, a home-screen widget, transfer history, and its own
signed-release CI pipeline. It's kept as its own repo (not a folder in
this one) so it can have its own build/release cycle.

## Using it

- Tap a folder to open it, tap a file to download it to your phone.
- Tap **⋮** next to any item for Download / Rename / Delete.
- The **+** button uploads files from your phone (photos, downloads, etc.)
  into the folder you're currently viewing.
- Long-press the **+** button to upload an entire folder at once (Android
  Chrome; not all browsers support folder picking).
- The small folder-with-`+` button creates a new folder.
- On a desktop browser you can also drag and drop files onto the page.

## Connecting to more than one PC

The ☰ icon (top-left, next to "Home") opens a device drawer where you can
see all the PCs you've connected to, add a new one, and switch between
them. It also opens automatically every time you log in, so you land on
"pick a device" before the file browser:

- **+ Add PC** — enter another PC's address and PIN (same format as the
  console prints: `192.168.1.50:8000`). Each PC must be running its own
  `server.py`.
- Tap a saved PC to switch to it. A green dot means it responded just now;
  gray/red means it didn't (server not running, wrong network, etc.).
- Long-press isn't needed to remove one — there's a small ✕ on each row
  (you always need at least one saved).
- Closing the drawer (✕, or tapping outside it) without picking a PC just
  keeps whichever one was already active.

Two things worth knowing about how this works, since there's no central
server tying your PCs together:

1. **Your saved PC list lives with whichever PC's page you're using as your
   entry point** — it's stored in that page's browser storage. In practice
   this is a non-issue: you'll normally always open the app the same way
   (the installed PWA/APK icon, or the same bookmark), so it just works.
2. **Switching PCs fetches the other PC directly from your phone** — your
   phone's browser talks to both PCs' servers at once; nothing is relayed
   through the PC whose page happens to be open.

## Configuration

First run creates a `config.json` next to `server.py`:

```json
{
  "root_dir": "C:\\Users\\you",
  "port": 8000,
  "pin": "483920"
}
```

- `root_dir` — the folder exposed to your phone. Everything inside is
  reachable; nothing outside it is. Change this to a specific folder (e.g. a
  `Shared` folder) if you don't want your whole home directory exposed.
- `port` — change if 8000 is taken.
- `pin` — set your own, or delete the field to have a new random one
  generated next run.

Restart the server after editing `config.json`. (If you're using the
desktop tray app instead, see `README-DESKTOP.md` — it also has auto-
update settings that live in this same file.)

## Security notes

- The server serves HTTPS, not plain HTTP. On first run it generates its own
  self-signed certificate (`cert.pem`/`key.pem`, next to `config.json` —
  gitignored, never shared) since a real certificate authority won't issue
  one for a private LAN IP. Apps trust that certificate the first time they
  connect to a PC (the console prints its fingerprint if you ever want to
  double-check by eye) and remember it after that — the same trust model SSH
  uses for host keys. If a PC's certificate ever changes, apps that already
  trust it will refuse to connect and show a warning instead of silently
  reconnecting, since that can also mean something is intercepting the
  connection.
- Anyone who has the PIN and can reach the server's IP/port can read, upload
  to, rename, and delete files under `root_dir`. Keep the PIN private and
  pick a restrictive `root_dir` if this matters to you.
- This is designed for trusted networks (your home Wi-Fi, or your own
  Tailscale tailnet) — it is **not** hardened for exposing directly to the
  open internet.
- Windows may prompt for Firewall access the first time you run it — allow
  it on **Private networks**.

## Using it over Tailscale (instead of LAN)

If your PC and phone both have [Tailscale](https://tailscale.com) installed
and signed into the same account:

1. Run `python server.py` as usual.
2. Find your PC's Tailscale IP (or MagicDNS name) with `tailscale ip` on the
   PC, or check the Tailscale admin console.
3. On your phone, open `https://<tailscale-ip>:8000` instead of the LAN IP —
   this works even when the phone is off your home Wi-Fi (e.g. on mobile
   data), since Tailscale creates a private network between your devices.

No code changes needed — the server already listens on all interfaces
(`0.0.0.0`), so it's reachable via whichever network path (LAN or Tailscale)
your phone happens to use.

## Running it automatically on PC startup (optional)

Two ways to do this:

- **Tray app** (see `README-DESKTOP.md`) — put a shortcut to
  `PCBridge.exe` in your Startup folder (`Win+R` → `shell:startup`). You
  still start the server yourself from the tray icon each time unless
  you set that up too; the app itself just launches automatically.
- **Bare server.py** — create a shortcut to `pythonw.exe server.py`
  (note the `w`, so no console window appears) and place it in the same
  Startup folder. This starts the server immediately with no tray
  icon/window at all, and no way to stop it short of Task Manager.

## Project layout

- `server.py` / `static/` — the FastAPI backend and PWA frontend (this
  is the whole app when run with just `python server.py`).
- `pcbridge_app.py` — optional desktop tray control app (see
  `README-DESKTOP.md`): Start/Stop, PIN/folder editing, auto-update, and
  the source for the packaged `PCBridge.exe`/installer.
- `installer/PCBridge.iss` — Inno Setup script that builds the
  installer version of the desktop app.
- `.github/workflows/build-release.yml` — CI: tag a version to get a
  built `PCBridge.exe` and `PCBridge-Setup.exe` published automatically.
- `android/` — present here for reference/history, but meant to be
  developed as its own separate repo (`pcbridge-android`) going forward
  — see that repo's own README for the Android app.
