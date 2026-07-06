# PC Bridge — Android app

A native Android wrapper around the same PC Bridge web UI — same look,
same features, just with an app icon and no browser address bar. It's a
WebView pointed at your PC's server; the PIN screen, file browser, upload,
download, rename, and delete are the exact same HTML/JS you already use in
a mobile browser.

I can't hand you a finished `.apk` directly — building one requires
Android's toolchain (Google's SDK servers, Gradle, Kotlin), which isn't
reachable from the sandbox this assistant runs in. This folder is a
complete, ready-to-build Android Studio project; building the actual APK
is one step you do locally, and it's quick.

## What you need

- **Android Studio** (free): https://developer.android.com/studio
  If you don't have it, download and install it — first launch downloads
  its own Android SDK automatically.
- Your phone with **USB debugging** enabled (only needed if installing via
  cable instead of copying the APK file over).

## Build the APK

1. Open Android Studio → **Open** → select this `android` folder.
2. Let it sync (first time: it downloads the Android SDK/build tools it
   needs — takes a few minutes, needs internet).
3. Menu: **Build → Build Bundle(s) / APK(s) → Build APK(s)**.
4. When it finishes, click the **"locate"** link in the notification, or
   find the file at:
   `android/app/build/outputs/apk/debug/app-debug.apk`

That `.apk` is what you install on your phone.

## Install it on your phone

Easiest: copy `app-debug.apk` to your phone (e.g. via the PC Bridge app
itself, once it's running!) and tap it to install — you'll need to allow
"install unknown apps" for whichever app you opened it from, once.

Or, with the phone plugged in via USB and USB debugging on: in Android
Studio click the green **Run ▶** button instead of building manually — it
installs and launches it directly.

## Using the app

1. On your PC, run `python server.py` (see the main README) — note the
   printed address, e.g. `192.168.1.42:8000`.
2. Open the PC Bridge app on your phone. First launch asks for the PC's
   address — enter it exactly as printed (IP and port, no `http://`
   needed).
3. It loads the same file browser you're used to; enter the PIN as usual.
4. To reach it over Tailscale instead of Wi-Fi, enter the Tailscale IP
   instead — open the "⋯" menu (top-right, next to the checkbox icon) any
   time and tap "Change PC address" or "Reload page". These two options
   only appear inside the Android app; a plain browser/PWA just sees
   "Re-enter PIN" there, since there's no separate address to change.

## Notes

- The app allows plain `http://` traffic on purpose — your PC's LAN/
  Tailscale IP has no TLS certificate, and that's fine for a private
  network. See `android/app/src/main/AndroidManifest.xml`.
- Minimum supported Android version: 10 (API 29). This keeps file
  downloads simple (no extra storage permission prompts) and covers the
  overwhelming majority of phones in use.
- The app remembers the address you enter (`SharedPreferences`) so you
  only set it up once, unless you change PCs or networks.
- If you ever change the web UI (`pcbridge/static/*`), you don't need to
  touch the Android project at all — it just loads whatever the PC is
  serving, live.

## Splash screen

Every cold start shows a branded splash (icon + app name on a blue
background) for about 2 seconds while the app sets itself up, so it never
flashes a blank white screen. It only appears when the app process is
freshly launched — switching back to it from the recent-apps list doesn't
retrigger it, just the lock screen below.

## Lock screen (biometric / device PIN)

Once the splash clears, the app is gated behind a lock screen every time
it comes to the foreground (except the very first launch, before you've
entered a PC address — nothing to protect yet at that point).

## Device selector

Once unlocked, you land on a "pick a device" screen (the same panel as
the ☰ menu button) listing every saved PC with a live online/offline dot
and free-storage info, plus "This phone". Tap a PC to open it — this is
the normal PC-switch behavior, it just now runs first instead of being
tucked behind a button. Dismissing it with ✕ (or tapping outside it)
falls back to whichever PC was already active, so it never blocks you.

- It asks for whatever unlock method your phone already has set up:
  fingerprint, face, or your device PIN/pattern/password.
- **If your phone has no lock method configured at all**, the app can't
  ask for one — it fails open and lets you straight in, rather than
  locking you out. Set a lock method in your phone's Settings if you want
  this screen to actually protect anything.
- Backgrounding the app (switching apps, locking your phone) re-locks it;
  returning to it re-prompts. Using the app's own file picker doesn't
  count as backgrounding, so it won't re-lock mid-upload.
- This is a native, on-device lock only — it doesn't add any encryption
  or authentication to the network traffic between phone and PC (that's
  still plain PIN + `http://`, same as the web version).

## Notifications

On first launch (Android 13+ only), the app asks for notification
permission once. If you allow it, you'll get a small "Uploaded N files"
notification whenever an upload finishes from the phone — handy if you
switch away from the app mid-upload. Declining the prompt just means you
won't see that notification; nothing else is affected.

## Home-screen widget

After you've connected to a PC at least once, you can add a widget:

1. Long-press an empty spot on your home screen → **Widgets**.
2. Find **PC Bridge** in the list and drag it onto your home screen.

It shows the name of whichever PC is currently active in the app and
whether it's online, plus free storage space. Tapping the widget opens
the app. Android enforces a minimum background refresh interval of about
30 minutes for widgets, so the status can lag behind reality by that
much — opening the app itself always refreshes it immediately. The
widget only knows about the PC you most recently switched to inside the
app (via the device-drawer sidebar), not every saved PC.

## If you'd rather not use Android Studio

Two alternatives, both with tradeoffs:

- **Keep using the PWA** you already have — on Android Chrome, "Add to
  Home Screen" already gives you an app icon and a full-screen window
  with no build step at all. This APK mainly buys you a real installable
  file to share/sideload; day-to-day it behaves almost identically.
- **PWABuilder** (pwabuilder.com, free, made by Microsoft) can generate an
  Android package from a PWA's URL without installing Android Studio —
  but it needs the PWA reachable over a public HTTPS address, which your
  LAN-only server isn't. It would only work if you expose the server
  through something like a Tailscale Funnel or a reverse proxy with a
  real certificate — a bigger security tradeoff than this app, so it
  isn't set up by default.
