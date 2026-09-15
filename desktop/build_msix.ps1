param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9.-]{3,50}$")]
    [string]$IdentityName,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^CN=.+")]
    [string]$Publisher,
    [Parameter(Mandatory = $true)]
    [ValidateLength(1, 256)]
    [string]$PublisherDisplayName,
    [ValidateSet("x64", "x86", "arm64")]
    [string]$Architecture = "x64",
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"

function Find-WindowsSdkTool {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $kitsPath = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (Test-Path -LiteralPath $kitsPath) {
        $candidate = Get-ChildItem -LiteralPath $kitsPath -Filter $Name -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    throw "$Name was not found. Install the Windows 10/11 SDK App Certification Kit tools first."
}

function Escape-XmlAttribute {
    param([Parameter(Mandatory = $true)][string]$Value)
    return [System.Security.SecurityElement]::Escape($Value)
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$appSource = Join-Path $root "delta_fakture_app.py"
$versionMatch = Select-String -LiteralPath $appSource -Pattern '^OPSNEST_APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"' | Select-Object -First 1
if (-not $versionMatch) {
    throw "Could not determine OPSNEST_APP_VERSION from $appSource."
}
$desktopVersion = $versionMatch.Matches[0].Groups[1].Value
$msixVersion = "$desktopVersion.0"
$appFolder = Join-Path $root "release\$desktopVersion\OpsNest"
$appExe = Join-Path $appFolder "OpsNest.exe"
if (-not (Test-Path -LiteralPath $appExe)) {
    throw "Windows app payload was not found at $appExe. Run build_windows_exe.ps1 first."
}

$manifestTemplate = Join-Path $root "msix\AppxManifest.xml.template"
if (-not (Test-Path -LiteralPath $manifestTemplate)) {
    throw "MSIX manifest template was not found at $manifestTemplate."
}

$makeAppx = Find-WindowsSdkTool -Name "makeappx.exe"
$outputFolder = Join-Path $root "release\$desktopVersion"
$outputPath = Join-Path $outputFolder "OpsNest-$desktopVersion-$Architecture.msix"
$stageFolder = Join-Path $env:TEMP ("opsnest-msix-stage-" + [guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Path $stageFolder -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stageFolder "Assets") -Force | Out-Null
Get-ChildItem -LiteralPath $appFolder -Force | Copy-Item -Destination $stageFolder -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root "assets\opsnest-app-mark.png") -Destination (Join-Path $stageFolder "Assets\opsnest-app-mark.png") -Force

$manifest = Get-Content -LiteralPath $manifestTemplate -Raw
$replacements = @{
    "{{IDENTITY_NAME}}" = Escape-XmlAttribute $IdentityName
    "{{PUBLISHER}}" = Escape-XmlAttribute $Publisher
    "{{PUBLISHER_DISPLAY_NAME}}" = Escape-XmlAttribute $PublisherDisplayName
    "{{VERSION}}" = $msixVersion
    "{{ARCHITECTURE}}" = $Architecture
}
foreach ($key in $replacements.Keys) {
    $manifest = $manifest.Replace($key, $replacements[$key])
}
if ($manifest -match "{{[A-Z_]+}}") {
    throw "The MSIX manifest still contains unresolved release placeholders."
}
$manifestPath = Join-Path $stageFolder "AppxManifest.xml"
[System.IO.File]::WriteAllText($manifestPath, $manifest, [System.Text.UTF8Encoding]::new($false))

& $makeAppx pack /d $stageFolder /p $outputPath /o
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $outputPath)) {
    throw "MSIX packaging failed."
}

# Microsoft Store re-signs submitted MSIX packages. For direct MSIX delivery,
# pass -RequireSignature and configure the ordinary OpsNest signing environment.
$signScript = Join-Path $root "sign_windows_binary.ps1"
& $signScript -FilePath $outputPath -RequireSignature:$RequireSignature

Write-Host "MSIX package created: $outputPath"
Write-Host "Desktop version: $desktopVersion; package version: $msixVersion"
Write-Host "Keep the staging folder until Windows App Certification Kit validation is complete: $stageFolder"
