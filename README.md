# PC Bridge

Browse, upload, and download files between your PC and phone over your local
network (or Tailscale). Your PC runs a small server; your phone connects to
it with a browser and installs it as an app icon (PWA) — no app store needed.

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
Open on your phone:  http://192.168.1.42:8000
```

Leave this window open — the server runs as long as this is running.

**Prefer not to use a terminal?** See `README-DESKTOP.md` for a small
system tray app (`pcbridge_app.py`) with a Start/Stop button and a
window for changing the PIN and shared folder — no console window, and
packages into a single portable `.exe`.

## 3. Connect from your phone

1. Make sure your phone is on the **same Wi-Fi network** as the PC (or both
   are on the same Tailscale network — see below).
2. Open the printed `http://<ip>:8000` address in your phone's browser.
3. Enter the PIN shown in the PC console.
4. To install it as an app icon:
   - **Android (Chrome):** tap the ⋮ menu → "Add to Home screen" / "Install app".
   - **iPhone (Safari):** tap the Share icon → "Add to Home Screen".

From then on it opens like a normal app, full-screen, with its own icon.

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

Restart the server after editing `config.json`.

## Security notes

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
3. On your phone, open `http://<tailscale-ip>:8000` instead of the LAN IP —
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
