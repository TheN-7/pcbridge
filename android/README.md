# PC Bridge — Android app

A thin WebView wrapper around PC Bridge's web app (from the main
[pcbridge](../pcbridge) repo), with a few native additions on top:
biometric/PIN lock screen, upload-complete notifications, a home-screen
widget showing your active PC's status, and (scaffolding for) in-app
update checking.

This folder is meant to live as its **own separate GitHub repo** —
that's why it has its own `.gitignore`, `README.md`, and CI workflow,
independent of the main PC Bridge desktop/server repo.

## Building locally

Open this folder in Android Studio (or run from the command line):

```
./gradlew assembleDebug
```

The debug APK needs no signing setup — install it straight from
`app/build/outputs/apk/debug/app-debug.apk` for testing.

`assembleRelease` (a properly signed build, same as what CI produces)
needs a keystore first — see "Signing & CI" below.

## What's native vs. web

MainActivity just loads your PC's address into a WebView — the actual
file-browser UI is entirely the web app from the main repo's `static/`
folder, so changes there show up here automatically without an APK
rebuild. The Kotlin code only handles things a plain WebView can't do:

- Remembering the PC's address (prompted on first run).
- A biometric/device-credential lock screen shown on cold start.
- Wiring `<input type=file>` to Android's file picker, and downloads to
  `DownloadManager`.
- Upload-complete notifications and a home-screen widget, both driven
  by a small `window.AndroidBridge` JS interface the web app calls into.

## Signing & CI

`app/build.gradle` reads signing info from `keystore.properties`
(gitignored) if present; release builds are left unsigned if it's
missing, so `assembleDebug` always works with zero setup.

**One-time: generate your own release keystore.** This is the identity
that makes every future release "the same app" to Android, so updates
install cleanly instead of needing an uninstall first. Run this
yourself (it prompts for passwords interactively — don't pass them as
command-line arguments, which would leave them sitting in your shell
history):

```
keytool -genkeypair -v -keystore keystore/pcbridge-release.jks -alias pcbridge -keyalg RSA -keysize 2048 -validity 10000
```

Then copy `keystore.properties.example` to `keystore.properties` and
fill in the `storePassword`/`keyPassword` you just chose (`storeFile`
and `keyAlias` are already correct if you used the command above
as-is).

**Back up `keystore/pcbridge-release.jks` somewhere safe** (a password
manager or encrypted storage) — if it's ever lost, there's no way to
recover it, and every existing install would need a manual
uninstall+reinstall to move to a new signing key.

**For CI to build signed releases**, add these four repository secrets
(your repo → **Settings** → **Secrets and variables** → **Actions** →
**New repository secret**):

- `ANDROID_KEYSTORE_BASE64` — base64 of the `.jks` file itself. On
  Windows (PowerShell):
  ```powershell
  [Convert]::ToBase64String([IO.File]::ReadAllBytes("keystore\pcbridge-release.jks")) | Set-Clipboard
  ```
  then paste the clipboard contents as the secret's value.
- `ANDROID_KEYSTORE_PASSWORD` — the password you chose above.
- `ANDROID_KEY_ALIAS` — `pcbridge`, unless you used a different `-alias`.
- `ANDROID_KEY_PASSWORD` — same as the keystore password, if you didn't
  set a separate one.

### Publishing a build

```
git tag v1.0.0
git push origin v1.0.0
```

`.github/workflows/build-apk.yml` picks up any tag matching `v*.*.*`,
builds a signed release APK on GitHub's infrastructure, and attaches it
to a Release as `PCBridge.apk`. Download it from your repo's Releases
page and install it on your phone (you'll need "install unknown apps"
enabled for whichever app you use to open it, same as any sideloaded
app — this repo isn't on the Play Store).

`versionName` comes straight from the tag; `versionCode` (the integer
Android uses internally to decide "is this actually newer") comes from
the workflow run number, which only ever increases, so you don't have
to track it by hand.

## In-app update checking — not built yet

`AndroidManifest.xml` already has the `REQUEST_INSTALL_PACKAGES`
permission and a `FileProvider` set up, and `app/build.gradle` already
exposes `BuildConfig.UPDATE_REPO`/`BuildConfig.UPDATE_TOKEN` (baked in
by CI from `github.repository` + the `UPDATE_TOKEN` secret, mirroring
the desktop app's `update_secrets.py` approach) — but the actual Kotlin
code that checks this repo's latest release, downloads the APK, and
launches the system installer hasn't been written yet. Right now,
getting a new version means manually downloading `PCBridge.apk` from
Releases and installing it over the old one yourself. If you want the
same "Check for updates" experience as the desktop tray app, that's the
next piece to build.

## Setting this up as its own repo

1. Create a new (private, if you want) GitHub repo, e.g. `pcbridge-android`.
2. From this folder:
   ```
   git init
   git add -A
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<you>/pcbridge-android.git
   git push -u origin main
   ```
3. Add the four `ANDROID_*` secrets described above (and optionally
   `UPDATE_TOKEN`, for whenever in-app update checking gets built).
4. Tag a release (see "Publishing a build" above) to get your first
   signed APK.
