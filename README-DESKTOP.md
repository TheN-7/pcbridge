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
Windows and PyInstaller, which isn't reachable from the sandbox this
assistant runs in. **You don't have to do this by hand either, though**
— see "Publishing an update" under Auto-update below: pushing a version
tag makes GitHub build it for you. The manual steps here are for your
very first build, or if you'd rather not use that.

This is the exact command to run yourself, once, on your PC:

```
pip install pyinstaller
python -c "from PIL import Image; Image.open('static/icons/icon-192.png').save('icon.ico')"
python -m PyInstaller --onefile --windowed --name "PCBridge" --icon icon.ico --add-data "static;static" pcbridge_app.py
```

(Using `python -m PyInstaller` instead of the bare `pyinstaller` command
avoids "command not found" if pip installed it somewhere not on your
PATH, which happens often with the Microsoft Store version of Python.)

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

## Auto-update

The app can check your GitHub repo for a newer release and update itself
-- no need to manually rebuild/redistribute the exe to every machine you
run it on. It's off by default until you configure it (nothing happens,
no errors) because it needs a couple of settings:

Add these to `config.json`, next to `pin` and `root_dir`:

```json
{
  "root_dir": "C:\\Users\\you",
  "port": 8000,
  "pin": "483920",
  "update_repo": "yourusername/pcbridge",
  "update_token": "github_pat_...",
  "auto_check_updates": true
}
```

- `update_repo` -- your GitHub repo as `owner/name`.
- `update_token` -- a GitHub **fine-grained personal access token**,
  scoped to *only* this one repo, with **Contents: Read-only**
  permission (that's the minimum needed to read releases and download
  assets from a private repo). Create one at
  github.com -> Settings -> Developer settings -> Fine-grained tokens.
- `auto_check_updates` -- `true` checks once on every launch (in
  addition to the "Check for updates" button/tray item, which always
  works on demand); set `false` to only ever check manually.

**Security note:** this token has to live in plaintext in `config.json`
so the app can read it, and it travels with every copy of the app you
hand out. Keep it scoped to just this repo with read-only access, and
rotate/revoke it (same GitHub settings page) if a machine it's on is
ever compromised or the copy is shared somewhere you didn't intend. If
the repo doesn't actually need to be private, making it public removes
the need for a token entirely -- `update_repo` alone is enough.

### Publishing an update (automatic -- no manual rebuild)

`.github/workflows/build-release.yml` builds `PCBridge.exe` on a real
Windows machine in GitHub's cloud and publishes it as a Release for you
-- you never have to run PyInstaller by hand again. Publishing a new
version is just:

1. Bump `APP_VERSION` near the top of `pcbridge_app.py`.
2. Commit and push that change.
3. Tag it and push the tag:
   ```
   git tag v1.2.0
   git push origin v1.2.0
   ```
   (the tag must match `v` + digits, e.g. `v1.2.0` -- that's what the
   workflow watches for)

That's it. GitHub Actions checks out the repo, installs dependencies,
builds `PCBridge.exe` with the exact same command shown above, and
creates a Release named `v1.2.0` with that exe attached (already
correctly named `PCBridge.exe`). Every installed copy with
`update_repo`/`update_token` set will notice it next launch (or
whenever someone clicks "Check for updates"), confirm, download, and
swap itself for the new version -- restarting automatically. You can
watch it run under your repo's **Actions** tab.

No new Python dependency was needed for either the client updater or
this workflow -- the updater only uses the standard library (`urllib`),
so `requirements.txt` didn't change.

**First-time setup:** just push the repo, including the
`.github/workflows/build-release.yml` file -- nothing else to enable.
Since the repo is private, Actions is included in GitHub's free tier up
to a monthly minutes allowance (this build takes a few minutes and you
publish rarely, so it comfortably fits).

**Prefer to build locally instead?** The manual PyInstaller command
above under "Packaging into a single portable PCBridge.exe" still works
exactly the same way, if you'd rather not use tags/Actions for a given
build.

### How the swap works

Windows won't let a running `.exe` overwrite itself, so on confirm the
app downloads the new exe as `PCBridge_update.exe` next to the current
one, writes a tiny throwaway `pcbridge_update.bat` that waits for the
current process to exit, renames the new file over the old one, and
relaunches it -- then the app quits immediately to let that happen.
`pcbridge_update.bat` deletes itself once done, so nothing lingers.

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
