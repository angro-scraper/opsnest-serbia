param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [string]$CertificateThumbprint = $env:OPSNEST_CODESIGN_CERT_THUMBPRINT,
    [string]$TimestampUrl = $(if ($env:OPSNEST_TIMESTAMP_URL) { $env:OPSNEST_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }),
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"

function Find-SignTool {
    if ($env:OPSNEST_SIGNTOOL_PATH -and (Test-Path $env:OPSNEST_SIGNTOOL_PATH)) {
        return (Resolve-Path $env:OPSNEST_SIGNTOOL_PATH).Path
    }

    $command = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $kitsPath = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (Test-Path $kitsPath) {
        $candidate = Get-ChildItem -Path $kitsPath -Filter "signtool.exe" -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) {
            return $candidate.FullName
        }
    }

    return $null
}

$resolvedFile = (Resolve-Path $FilePath -ErrorAction SilentlyContinue)
if (-not $resolvedFile) {
    throw "Binary was not found: $FilePath"
}

$thumbprint = ($CertificateThumbprint -replace "\\s", "").ToUpperInvariant()
if (-not $thumbprint) {
    $message = "Code signing was skipped for $($resolvedFile.Path). Set OPSNEST_CODESIGN_CERT_THUMBPRINT before a production release."
    if ($RequireSignature) {
        throw $message
    }
    Write-Warning $message
    return
}

$certificate = Get-Item -Path "Cert:\CurrentUser\My\$thumbprint" -ErrorAction SilentlyContinue
if (-not $certificate -or -not $certificate.HasPrivateKey) {
    throw "No usable code-signing certificate with thumbprint $thumbprint was found in Cert:\CurrentUser\My."
}

$signTool = Find-SignTool
if (-not $signTool) {
    throw "signtool.exe was not found. Install the Windows SDK or set OPSNEST_SIGNTOOL_PATH."
}

& $signTool sign /fd SHA256 /sha1 $thumbprint /tr $TimestampUrl /td SHA256 /v $resolvedFile.Path
if ($LASTEXITCODE -ne 0) {
    throw "signtool failed while signing $($resolvedFile.Path)."
}

& $signTool verify /pa /v $resolvedFile.Path
if ($LASTEXITCODE -ne 0) {
    throw "signtool verification failed for $($resolvedFile.Path)."
}

$signature = Get-AuthenticodeSignature -FilePath $resolvedFile.Path
if ($signature.Status -ne "Valid") {
    throw "Authenticode status is $($signature.Status) for $($resolvedFile.Path)."
}

Write-Host "Signed and verified: $($resolvedFile.Path)"
Write-Host "Publisher certificate: $($certificate.Subject)"
