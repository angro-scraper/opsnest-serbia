# OpsNest Windows code signing

Windows and Chrome can warn about a new unsigned installer. The production
release must be Authenticode-signed with a code-signing certificate issued to
the OpsNest business. The certificate proves the publisher. It does not bypass
Windows security controls or guarantee immediate browser reputation.

## One-time setup

1. Buy an Organization Validated (OV) or Extended Validation (EV) code-signing
   certificate from a trusted issuer. EV normally gives the strongest Windows
   publisher assurance, but the issuer decides the delivery method.
2. Complete the issuer's company identity verification.
3. Install the certificate or the issuer's hardware/cloud-signing provider so
   it appears in `Cert:\CurrentUser\My` with a private key.
4. Install the Windows SDK Signing Tools if `signtool.exe` is not available.
5. Set the certificate thumbprint only in the release machine environment:

```powershell
$env:OPSNEST_CODESIGN_CERT_THUMBPRINT = "YOUR_CERTIFICATE_THUMBPRINT"
```

Never put a certificate password, private key, PFX file, or thumbprint into
GitHub, Render, the desktop application, or a public document.

## Official release build

```powershell
.\build_windows_exe.ps1 -RequireSignature
.\build_setup_exe.ps1 -RequireSignature
```

`-RequireSignature` stops the build if a valid certificate or SignTool is not
available. The scripts sign and verify both `release\OpsNest\OpsNest.exe` and
the final `release\OpsNest-Setup-<version>.exe`.

## Verify before publishing

```powershell
Get-AuthenticodeSignature .\release\OpsNest-Setup-<version>.exe |
  Format-List Status,StatusMessage,SignerCertificate
```

The expected status is `Valid`. Upload only that verified installer to the
GitHub release and update the public desktop-release manifest afterwards.
