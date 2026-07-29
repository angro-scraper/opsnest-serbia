param(
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\Users\49162\AppData\Local\Programs\Python\Python313\python.exe"
$version = "2.13.10"
# Each desktop version has an isolated payload folder.  It avoids modifying a
# previous release that may still be held by OneDrive or a running installer.
$release = Join-Path $root "release\$version"
# OneDrive can mark PyInstaller's bytecode cache as a reparse/read-only path.
# Keep the disposable work tree outside the synced source folder.
$workPath = Join-Path $env:TEMP "opsnest-pyinstaller-work-$version"

if (-not (Test-Path $python)) {
    throw "Python not found at $python"
}

# OpsNest prints the supplied invoice layout through installed Microsoft Excel.
# Keeping the renderer native makes the Windows package much smaller and faster.

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--windowed",
    "--name", "OpsNest",
    "--distpath", $release,
    "--workpath", $workPath,
    "--icon", "$root\assets\opsnest.ico",
    "--add-data", "$root\assets;assets",
    "--add-data", "$root\OPS_NEST_OPERATIONS_RUNBOOK.md;.",
    "$root\delta_fakture_app.py"
)

& $python @pyinstallerArgs

$appExe = Join-Path $release "OpsNest\OpsNest.exe"
$signScript = Join-Path $root "sign_windows_binary.ps1"
if (-not (Test-Path $signScript)) {
    throw "Code signing script not found at $signScript"
}

# Sign the executable before the installer packages it as payload.
& $signScript -FilePath $appExe -RequireSignature:$RequireSignature
