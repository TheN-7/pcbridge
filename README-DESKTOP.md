# PC Bridge — desktop control app

A small system tray app for running the PC Bridge server without ever
opening a terminal. Click the tray icon to open a tiny window that shows
whether the server is running, its address, and the PIN your phone
needs — with buttons to start/stop it and change the PIN or shared
folder.

This replaces having to run `python server.py` yourself every time.
`server.py` still works exactly as before if you ever want to run it
directly (e.g. on a different OS, or in a script).

## Running it (no packaging needed)

```
pip install -r requirements.txt
python pcbridge_app.py
```

A tray icon appears. Click it (or the "Open PC Bridge" menu item) to
open the window, then press **Start server**.

## What the window shows

- **Status** — a red/green dot and Start/Stop button.
- **Address** — appears once running; click it to copy it to your
  clipboard (this is what you type into the Android app or a browser on
  your phone).
- **PIN** — the PIN your phone enters. **Randomize** picks a new random
  one; **Save** stores whatever's typed in the box. If the server is
  currently running, you'll be asked whether to restart it so the new
  PIN takes effect (config changes only apply the next time the server
  process starts).
- **Shared folder** — the folder your phone can browse. **Change
  folder...** opens a normal folder picker.

Closing the window (the X button) just hides it — the app keeps running
in the tray. Use **Quit** from the tray icon's right-click menu to
actually exit (this also stops the server if it's running).

If Start doesn't stick (the status flips back to Stopped, or you get an
error dialog), check `pcbridge.log`, written next to `config.json` — the
most common cause is the port already being used by something else.

## Packaging into a single portable PCBridge.exe

I can't hand you a finished `.exe` directly — building one requires
Windows and PyInstaller running on your machine, which isn't reachable
from the sandbox this assistant runs in. This is the exact command to
run yourself, once, on your PC:

```
pip install pyinstaller
pyinstaller --onefile --windowed --name "PCBridge" --icon static/icons/icon-192.png --add-data "static;static" pcbridge_app.py
```

The finished file appears at `dist/PCBridge.exe`. Copy that one file
wherever you like (Desktop, a folder, a USB stick) — it's fully
portable and doesn't need Python installed on the machine you copy it
to. `config.json` and `pcbridge.log` will be created next to wherever
you put the `.exe`, the first time you run it.

**Windows SmartScreen / antivirus:** since this isn't a signed,
publisher-verified executable, Windows may show an "Unrecognized app"
warning the first time you run it (click "More info" → "Run anyway"),
and some antivirus tools flag unsigned PyInstaller binaries as
suspicious purely because that packaging pattern is popular with
malware too. That's a false positive here — but it's worth knowing
before it happens, rather than assuming something's wrong.

## Auto-start at login

Not set up by default (you asked for manual start). If you change your
mind later: press `Win+R`, run `shell:startup`, and drop a shortcut to
`PCBridge.exe` in the folder that opens — Windows will launch it
automatically every time you log in.

## Notes

- `config.json`/`pcbridge.log` live next to the real `PCBridge.exe`
  file, not in whatever temporary folder Windows extracts a one-file
  build into — this was specifically handled so your PIN and folder
  choice survive between runs (see `app_dir()` in both `server.py` and
  `pcbridge_app.py`).
- The tray app starts/stops the server as a separate child process
  (rather than running it in the same process), so Stop is always a
  clean kill — no risk of it lingering half-shut-down.
- This is a desktop control surface only — it doesn't change anything
  about how the phone side works (Android app or browser/PWA), and
  doesn't add authentication beyond the existing PIN.
