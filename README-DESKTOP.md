# PC Bridge — desktop control app

A small system tray app for running the PC Bridge server without ever
opening a terminal. Click the tray icon to open a tiny window that shows
whether the server is running, its address, and the PIN your phone
needs — with buttons to start/stop it and change the PIN or shared
folder.

This replaces having to run `python server.py` yourself every time.
`server.py` still works exactly as before if you ever want to run it
directly (e.g. on a different OS, or in a script).

## Installing PC Bridge

Go to your repo's **Releases** page and download `PCBridge-Setup.exe`
from the latest release, then run it:

- No admin prompt -- it installs just for your Windows account, under
  `%LOCALAPPDATA%\Programs\PCBridge`.
- Adds a **Start Menu shortcut** (with the PC Bridge icon) and offers an
  optional Desktop shortcut checkbox during install.
- Offers to launch PC Bridge as soon as setup finishes.
- Shows up in **Settings -> Apps** like a normal program, with a proper
  uninstaller -- no manually hunting down leftover files.

This is built automatically by the same CI pipeline that builds the
plain exe (see "Auto-update" below) -- `PCBridge-Setup.exe` and the
plain `PCBridge.exe` both get attached to every release. Auto-update
doesn't care which one you used to install: it always finds and swaps
in a fresh plain `PCBridge.exe` inside wherever the app is currently
running from, whether that's the installer's folder or a portable copy
you placed yourself.

**Prefer the old portable style** (just a single `.exe` you put
wherever you like, no Start Menu entry)? Download `PCBridge.exe`
directly from the same Release instead of the Setup file -- see
"Packaging into a single portable PCBridge.exe" below.

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
run it on. It's off until `update_repo`/`update_token` are set somehow --
nothing happens, no errors, if they aren't. There are two ways to set
them, and you can use either or both:

### Option A -- baked in automatically by the CI build (recommended)

If you build via `.github/workflows/build-release.yml` (see "Publishing
an update" below), the resulting `PCBridge.exe` already knows its own
repo and token -- nobody running that exe has to touch `config.json` at
all. This needs one one-time setup step:

1. Create a GitHub **fine-grained personal access token**, scoped to
   *only* the `pcbridge` repo, with **Contents: Read-only** permission
   (that's the minimum needed to read releases/download assets from a
   private repo). Create one at github.com -> Settings -> Developer
   settings -> Personal access tokens -> Fine-grained tokens.
2. Add it as a **repository secret** (not in any file you commit):
   your repo -> **Settings** -> **Secrets and variables** -> **Actions**
   -> **New repository secret** -> name it `UPDATE_TOKEN`, paste the
   token as the value.

That's it -- every build the workflow produces from then on has this
baked in. `update_repo` doesn't need a secret at all; the workflow fills
it in automatically from the repo it's running in.

### Option B -- set manually in config.json

For anything you build yourself locally (the manual PyInstaller command
below), or to point a specific install at a different repo/token than
what's baked in (a value here always overrides the baked-in one). Add
to `config.json`, next to `pin` and `root_dir`:

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
- `update_token` -- the same kind of fine-grained token described above.
- `auto_check_updates` -- `true` checks once on every launch (in
  addition to the "Check for updates" button/tray item, which always
  works on demand); set `false` to only ever check manually.

**Security note (applies either way):** the token ends up readable
inside the exe itself -- baked into the binary if built via CI, or
readable in plaintext in `config.json` if set manually -- and travels
with every copy you hand out. Keep it scoped to just this repo with
read-only access, and rotate/revoke it (same GitHub settings page) if a
machine or copy it's on is ever compromised or shared somewhere you
didn't intend. If the repo doesn't actually need to be private, making
it public removes the need for a token entirely -- `update_repo` alone
is enough.

### Publishing an update (automatic -- no manual rebuild)

`.github/workflows/build-release.yml` builds `PCBridge.exe` on a real
Windows machine in GitHub's cloud and publishes it as a Release for you
-- you never have to run PyInstaller by hand again. Publishing a new
version is just:

```
git tag v1.2.0
git push origin v1.2.0
```

(the tag must match `v` + digits, e.g. `v1.2.0` -- that's what the
workflow watches for)

That's it -- there's no separate "bump the version" step to remember.
`APP_VERSION` used to be a literal near the top of `pcbridge_app.py` you
had to manually edit before every release; that was silently skipped for
one real release, which caused the exe to keep reporting its old
version forever and auto-update to loop endlessly redownloading "the
latest" build. The workflow now generates `app_version.py` from the tag
itself right before PyInstaller runs (the same way it already baked in
`update_secrets.py`), so the version the exe reports always matches the
tag it was actually built from.

GitHub Actions checks out the repo, installs dependencies, builds
`PCBridge.exe` with the exact same command shown above, then builds
`PCBridge-Setup.exe` from it (Inno Setup, `installer/PCBridge.iss`
-- see "Installing PC Bridge" above), and creates a Release named
`v1.2.0` with *both* attached. Every installed copy with
`update_repo`/`update_token` set will notice it next launch (or
whenever someone clicks "Check for updates"), confirm, download, and
swap itself for the new version -- restarting automatically. This
happens the same way regardless of whether you originally installed via
the Setup exe or a portable copy. You can watch the build run under
your repo's **Actions** tab.

No new Python dependency was needed for either the client updater or
this workflow -- the updater only uses the standard library (`urllib`),
so `requirements.txt` didn't change.

**First-time setup:** push the repo including
`.github/workflows/build-release.yml` and `installer/PCBridge.iss`, and
add the `UPDATE_TOKEN` repository secret described in "Auto-update" ->
"Option A" above (that's the only manual step -- without it, the build
still succeeds but the resulting exe just won't have update-checking
baked in). Since the repo is private, Actions is included in GitHub's
free tier up to a monthly minutes allowance (this build takes a few
minutes and you publish rarely, so it comfortably fits).

**Prefer to build locally instead?** The manual PyInstaller command
above under "Packaging into a single portable PCBridge.exe" still works
exactly the same way, if you'd rather not use tags/Actions for a given
build.

### How the swap works

Windows won't let a running `.exe` overwrite itself, so on confirm the
app downloads the new exe as `PCBridge_update.exe` next to the current
one, then hands off to a throwaway PowerShell script
(`pcbridge_update.ps1`) that waits for the current process to exit,
verifies the downloaded file's size matches what GitHub reported (twice
-- once right after downloading, once again right before launching it),
retries the move a few times if the file's briefly locked, and only
then relaunches it. Every step gets logged to `pcbridge_update.log`
next to `config.json`. Both throwaway files delete themselves once
done, so nothing lingers on success.

**If an update ever seems to install a broken copy** (a crash dialog
mentioning something like `pyi_rth_inspect` or a missing
`base_library.zip` right after an update relaunches): this is almost
always antivirus interference, not a code bug -- unsigned PyInstaller
exes that get silently downloaded and launched by another program (as
opposed to a person double-clicking a download) are exactly the pattern
heuristic antivirus flags hardest, and some will quarantine or strip
files out of the freshly-extracted exe right as it starts. Check
`pcbridge_update.log` first (it'll say if the size check itself caught
a bad download) and Windows Security -> Protection history for anything
quarantined around that time; adding an exclusion for the PC Bridge
install folder avoids this going forward.

## Send to Phone

Besides browsing/downloading from your phone, the PC can push files or a
whole folder straight onto a phone's storage -- useful when it's easier
to pick something on the PC than to go find it in the phone's file
browser. Needs the phone's "Receive from PC" toggle turned on first (see
its sidebar), which shows the address/PIN/fingerprint to add here.

**Adding a phone automatically (recommended):**

Since a recent version, the pairing happens in one direction only. On the
phone, add this PC the normal way (see the Android app's Add PC flow) with
**Allow receiving files** already turned on -- the phone then registers
itself with this PC automatically, and it just shows up next time you
open **Send to Phone...**, no separate "Add Phone" step needed here. If
**Allow receiving files** was turned on *after* the PC was already added,
turning the toggle on retroactively registers with every PC already known
to that phone.

**Adding a phone manually (older phone app versions, or if the automatic
step above didn't happen):**

1. On the phone, open the sidebar and turn on **Allow receiving files**.
   It'll show an address (like `192.168.1.42:8000`), a PIN, and a
   certificate fingerprint.
2. On the PC, click **Send to Phone...** (in the window or the tray
   menu) -> **+ Add new phone** -> type a name, the address, and the PIN
   -> **Connect**.
3. Once connected, pick **Choose files...** or **Choose a folder...**.

Either way, the PC remembers the phone (in `phones.json`, gitignored like
`config.json`) so it only needs to happen once. A whole folder keeps its
structure -- everything lands on the phone under **Downloads/Received
from PC/**.

**Works across networks automatically, if you have Tailscale.** A phone
reports both its LAN address and a Tailscale address (if Tailscale is
running on it) when it registers, and the PC stores both and tries each
one in turn on every connect -- LAN first (fastest when you're on the
same Wi-Fi), Tailscale as an automatic fallback otherwise. You don't need
to re-add a phone just because you're on a different network than last
time; if either address still works, it connects. This needs Tailscale
running on **both** the PC and the phone to actually help -- with only
one side on Tailscale, or neither, a phone is only reachable while both
devices share the same LAN, same as before. A phone added manually before
this existed, or with an older phone app, only has its single typed
address remembered until it re-registers (turn "Allow receiving files"
off and back on to force that).

**Online status:** opening **Send to Phone...** pings every remembered
phone in the background and colors a dot next to its name green
(reachable right now) or red (not reachable -- asleep, off Wi-Fi, or the
receiving toggle is off). The list itself never waits on this: it appears
instantly and each dot fills in as its own ping resolves.

**Security:** the same trust-on-first-use model as the phone's own
Add PC flow, just in reverse -- the PC pins the phone's certificate
fingerprint the first time it connects, and refuses to send (with a
warning you have to explicitly dismiss) if that fingerprint ever changes
without you expecting it.

### Browse phone

Beyond one-shot "push these files" sends, **Browse phone...** (the third
button after **Choose files...**/**Choose a folder...**) opens a two-way
file browser over the phone's *entire* filesystem -- navigate into any
folder (double-click to open, ".." to go up), download one or more
selected files to a folder you pick, or upload files/a whole folder
straight into whatever folder you're currently looking at.

This needs the phone to have granted itself **All files access** first
(the sidebar's "Browse this phone" section, under "Receive from PC") --
without it, every action in this dialog shows the phone's own explanation
instead of a generic connection error. This is a heavier, more
sensitive permission than anything else in the app (`MANAGE_EXTERNAL_STORAGE`,
not a browser-picker toggle), which is why it needs a deliberate opt-in
from a dedicated Android Settings screen rather than a plain switch --
same PIN-checked, certificate-pinned connection as the rest of "Send to
Phone" underneath it, just reaching further than the phone's Downloads
folder.

**Opens as a real Explorer window, not a custom dialog.** Clicking
**Browse phone...** first tries to map the phone as an actual network
drive and open it in Windows Explorer, using a small WebDAV bridge this
app runs on `127.0.0.1` (loopback only -- never reachable from your
network) that translates Explorer's requests into calls against the
phone's own browse API. If that mapping fails for any reason -- most
commonly because Windows' built-in WebDAV client (the "WebClient"
service) is disabled, which is common on a fresh Windows install since
it's Manual-start by default -- you'll be offered the original built-in
browser dialog (the two-way file list described above) as a fallback
instead, so "Browse phone..." never becomes a dead end. If Explorer
opens but drag/drop or renaming inside it acts oddly, that's Windows'
own WebDAV client being picky (it has a real-world reputation for this,
independent of anything this app does) -- the fallback dialog is always
available as a reliable alternative via the same button if you ever want
it. This drive-mapping path hasn't been exercised against a real copy of
Windows Explorer during development; if it doesn't work well on your
machine, it's worth reporting back what happened.

## Auto-start at login

Not set up by default (you asked for manual start). If you change your
mind later: press `Win+R`, run `shell:startup`, and drop a shortcut to
`PCBridge.exe` in the folder that opens — Windows will launch it
automatically every time you log in. If you used the installer, the
easiest way to get that shortcut is to copy the one it already made in
your Start Menu (right-click it there → Copy, then paste into the
`shell:startup` folder) rather than hunting down the exe manually.

## Notes

- **Installed via the Setup exe → lives under**
  `%LOCALAPPDATA%\Programs\PCBridge`; **portable copy → lives wherever
  you put it.** Either way, `config.json`/`pcbridge.log` live right next
  to that real `PCBridge.exe`, not in any temporary folder Windows
  extracts a one-file build into — this was specifically handled so
  your PIN and folder choice survive between runs (see `app_dir()` in
  both `server.py` and `pcbridge_app.py`). If you ever have two copies
  running from two different locations, remember each has its own
  separate `config.json` — see the `dist\config.json` mix-up earlier in
  this project's history if that ever seems to happen again.
- The installer deliberately installs **per-user, not system-wide**
  (`PrivilegesRequired=lowest` in `installer/PCBridge.iss`) — no admin
  prompt to install, and auto-update keeps working exactly as it does
  now (swapping the exe in place needs write access to its own folder,
  which `Program Files` would block without running the app as admin
  forever).
- The tray app starts/stops the server as a separate child process
  (rather than running it in the same process), so Stop is always a
  clean kill — no risk of it lingering half-shut-down.
- This is a desktop control surface only — it doesn't change anything
  about how the phone side works (Android app or browser/PWA), and
  doesn't add authentication beyond the existing PIN.
