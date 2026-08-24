# Toolchain

Everything installed to build this project, and how to remove it again.

This machine had **no development toolchain at all** before this project —
only `git` and the WebView2 runtime. That baseline is recorded in
`tools/purge-toolchain.ps1` so the cleanup is precise rather than
guesswork.

## What's installed

| Tool | Version | Location | Needs admin? | Purge |
|---|---|---|---|---|
| Rust (rustup) | 1.97.1 | `%USERPROFILE%\.cargo`, `%USERPROFILE%\.rustup` | No | `rustup self uninstall` — fully clean |
| Node.js | 24.19.0 LTS | `C:\dev\toolchain\node` | No | Delete the folder — nothing registered |
| VS Build Tools 2022 | 17.14.37 | `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools` | **Yes** | Via VS Installer — see below |

Node is deliberately a **portable extract**, not an installer: it writes
no registry keys, no PATH entry, no Program Files directory. Deleting
`C:\dev\toolchain\node` removes it completely.

Only two Build Tools components were installed, rather than the full
"Desktop development with C++" workload:

- `Microsoft.VisualStudio.Component.VC.Tools.x86.x64` — the MSVC linker Rust needs
- `Microsoft.VisualStudio.Component.Windows11SDK.22621` — Windows SDK headers

That keeps it near the minimum Tauri can build with. MSVC is not
optional on Windows: Tauri does not support Rust's `windows-gnu` target.

## Why nothing runs on PATH globally

`tools/env.ps1` puts the portable Node and cargo on `PATH` **for the
current shell only**. Nothing is added to your user or system
environment permanently except rustup's own `~/.cargo/bin` entry, which
`rustup self uninstall` removes.

```powershell
. .\tools\env.ps1
```

## Removing it all

Dry run first — this prints what would go and how much disk it frees,
and changes nothing:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\purge-toolchain.ps1
```

Then apply:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\purge-toolchain.ps1 -Yes
```

Build Tools is the one piece the script can't remove for you — it needs
an elevated uninstall. The script prints the exact command, or use
**Settings → Apps → Visual Studio Build Tools 2022 → Uninstall**.

### Deliberately left alone

- **git** and **WebView2** — both pre-existing. WebView2 is also what the
  shipped app renders in, so removing it would break the installed app.
- **Visual C++ redistributables** — commonly shared by other installed
  software. Removing them can break unrelated programs, so the script
  never touches them.
- **Windows SDK** — may be shared with other tooling; removed only if you
  choose to uninstall it yourself from Settings → Apps.

## What was *not* installed

No Java, no Android SDK, no Android Studio — roughly 10 GB avoided.
The Android app stays a thin WebView wrapper for v1, so it picks up the
new interface automatically without a native rebuild. If we later go
native, that toolchain gets installed then, and this document gets a new
row.
