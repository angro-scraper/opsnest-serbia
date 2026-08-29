param(
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\Users\49162\AppData\Local\Programs\Python\Python313\python.exe"
$version = "2.13.13"
$release = Join-Path $root "release"

if (-not (Test-Path $python)) {
    throw "Python not found at $python"
}

$appFolder = Join-Path $root "release\$version\OpsNest"
$appExe = Join-Path $appFolder "OpsNest.exe"
if (-not (Test-Path $appExe)) {
    throw "OpsNest app build was not found at $appExe. Build the app first with build_windows_exe.ps1."
}

$signScript = Join-Path $root "sign_windows_binary.ps1"
if (-not (Test-Path $signScript)) {
    throw "Code signing script not found at $signScript"
}

# Re-check the payload before it is embedded in the one-file installer.
& $signScript -FilePath $appExe -RequireSignature:$RequireSignature

$installerIcon = Join-Path $root "assets\opsnest.ico"
if (-not (Test-Path $installerIcon)) {
    throw "Installer icon not found at $installerIcon"
}

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--onefile",
    "--windowed",
    "--name", "OpsNest-Setup-$version",
    "--distpath", $release,
    "--icon", $installerIcon,
    "--add-data", "$installerIcon;assets",
    "--add-data", "$appFolder;payload\OpsNest",
    (Join-Path $root "opsnest_setup.py")
)

& $python @pyinstallerArgs

$installerExe = Join-Path $release "OpsNest-Setup-$version.exe"
& $signScript -FilePath $installerExe -RequireSignature:$RequireSignature
