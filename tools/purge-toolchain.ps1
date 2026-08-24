<#
.SYNOPSIS
    Removes every build tool installed for the PC Bridge rebuild.

.DESCRIPTION
    Run this when the project is finished to return the machine to
    roughly its pre-project state.

    Baseline recorded 2026-08-09, before anything was installed:
      rustc/cargo   NOT present  (a stale ~/.rustup/settings.toml existed)
      node/npm      NOT present
      Visual Studio NOT present  (no vswhere.exe, no BuildTools)
      java/Android  NOT present  (and never installed - see TOOLCHAIN.md)
      git           present      <- PRE-EXISTING, never touched by this script
      WebView2      present      <- PRE-EXISTING, never touched by this script

    Nothing in this script touches git, WebView2, OneDrive, or any
    Visual C++ redistributable. See the "deliberately not removed"
    notes at the bottom.

.PARAMETER Yes
    Actually perform the removal. Without it, the script only reports
    what it would do.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\purge-toolchain.ps1
    powershell -ExecutionPolicy Bypass -File .\purge-toolchain.ps1 -Yes
#>

[CmdletBinding()]
param(
    [switch]$Yes
)

$ErrorActionPreference = 'Continue'
$script:Freed = 0

function Write-Head($text) {
    Write-Host ""
    Write-Host $text -ForegroundColor Cyan
    Write-Host ("-" * $text.Length) -ForegroundColor DarkGray
}

function Get-SizeGB($path) {
    if (-not (Test-Path $path)) { return 0 }
    try {
        $bytes = (Get-ChildItem $path -Recurse -Force -ErrorAction SilentlyContinue |
                  Measure-Object -Property Length -Sum).Sum
        if ($null -eq $bytes) { return 0 }
        return [math]::Round($bytes / 1GB, 2)
    } catch { return 0 }
}

function Remove-Target($path, $label) {
    if (-not (Test-Path $path)) {
        Write-Host "  [skip]   $label - not present"
        return
    }
    $size = Get-SizeGB $path
    if (-not $Yes) {
        Write-Host "  [would]  $label ($size GB) -> $path" -ForegroundColor Yellow
        $script:Freed += $size
        return
    }
    Write-Host "  [remove] $label ($size GB) ..." -NoNewline
    try {
        Remove-Item $path -Recurse -Force -ErrorAction Stop
        Write-Host " done" -ForegroundColor Green
        $script:Freed += $size
    } catch {
        Write-Host " FAILED: $_" -ForegroundColor Red
    }
}

if (-not $Yes) {
    Write-Host ""
    Write-Host "DRY RUN - nothing will be removed. Re-run with -Yes to apply." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- Rust
Write-Head "1. Rust toolchain (user-scope, no admin needed)"

$rustup = Join-Path $env:USERPROFILE ".cargo\bin\rustup.exe"
if (Test-Path $rustup) {
    $cargoSize  = Get-SizeGB (Join-Path $env:USERPROFILE ".cargo")
    $rustupSize = Get-SizeGB (Join-Path $env:USERPROFILE ".rustup")
    $total = [math]::Round($cargoSize + $rustupSize, 2)
    if ($Yes) {
        Write-Host "  [remove] rustup self uninstall ($total GB) ..." -NoNewline
        & $rustup self uninstall -y 2>&1 | Out-Null
        Write-Host " done" -ForegroundColor Green
        $script:Freed += $total
        # rustup normally clears both, but sweep any remnants (incl. the
        # pre-existing stale settings.toml noted in the baseline above).
        Remove-Target (Join-Path $env:USERPROFILE ".cargo")  "leftover .cargo"
        Remove-Target (Join-Path $env:USERPROFILE ".rustup") "leftover .rustup"
    } else {
        Write-Host "  [would]  rustup self uninstall ($total GB)" -ForegroundColor Yellow
        Write-Host "  [would]  remove ~\.cargo and ~\.rustup, and the user PATH entry" -ForegroundColor Yellow
        $script:Freed += $total
    }
} else {
    Write-Host "  [skip]   rustup - not present"
}

# ---------------------------------------------------------------- Node
Write-Head "2. Node.js (portable - just a folder, nothing registered)"

Remove-Target "C:\dev\toolchain\node"            "portable Node"
Remove-Target (Join-Path $env:APPDATA "npm-cache") "npm cache"

# ------------------------------------------------------- Build Tools
Write-Head "3. Visual Studio Build Tools (REQUIRES ADMIN)"

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
    $ids = & $vswhere -products "Microsoft.VisualStudio.Product.BuildTools" -format value -property productId 2>$null
    $path = & $vswhere -products "Microsoft.VisualStudio.Product.BuildTools" -format value -property installationPath 2>$null
    if ($path) {
        $size = Get-SizeGB $path
        Write-Host "  Found Build Tools ($size GB) at:"
        Write-Host "    $path"
        Write-Host ""
        Write-Host "  Remove it with the Visual Studio Installer (needs an admin prompt):" -ForegroundColor Yellow
        Write-Host '    & "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vs_installer.exe" uninstall --installPath "' -NoNewline
        Write-Host "$path" -NoNewline
        Write-Host '" --quiet --norestart'
        Write-Host ""
        Write-Host "  Or: Settings > Apps > 'Visual Studio Build Tools 2022' > Uninstall"
        $script:Freed += $size
    } else {
        Write-Host "  [skip]   Build Tools - installer present but no BuildTools product"
    }
} else {
    Write-Host "  [skip]   Visual Studio Installer - not present"
}

# ------------------------------------------------- Project artifacts
Write-Head "4. Project build artifacts (safe to delete, regenerable)"

Remove-Target "C:\dev\pcbridge\src-tauri\target" "Rust build output (target/)"
Remove-Target "C:\dev\pcbridge\node_modules"     "node_modules"

# ------------------------------------------------------------ Summary
Write-Head "Summary"
Write-Host ("  Disk reclaimed: ~{0} GB" -f [math]::Round($script:Freed, 2))
Write-Host ""
Write-Host "  Deliberately NOT removed:" -ForegroundColor DarkGray
Write-Host "    - git and WebView2       : pre-existing, unrelated to this project" -ForegroundColor DarkGray
Write-Host "    - VC++ redistributables  : shared by other installed software;" -ForegroundColor DarkGray
Write-Host "                               removing them can break unrelated apps" -ForegroundColor DarkGray
Write-Host "    - Windows SDK            : may be shared; removed only if you" -ForegroundColor DarkGray
Write-Host "                               uninstall it from Settings > Apps yourself" -ForegroundColor DarkGray
Write-Host "    - C:\dev\pcbridge source : your actual project - delete by hand" -ForegroundColor DarkGray
Write-Host ""
if (-not $Yes) {
    Write-Host "  Re-run with -Yes to actually remove." -ForegroundColor Yellow
    Write-Host ""
}
