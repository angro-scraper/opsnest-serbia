# OpsNest Desktop

This directory is the version-controlled source of the Windows Desktop client.
It is intentionally separate from the cloud API in the repository root, but
the two are released together only after their compatible versions have passed
the workflow checks.

## Safe development rules

- Never commit `Data/`, customer invoices, attachments, backups, local SQLite
  databases, API keys, passwords, certificate material or an `.env` file.
- Run the workflow tests before a release:

  ```powershell
  python -m unittest tests.test_critical_workflows -v
  ```

- Build from this directory with the supported Windows Python runtime:

  ```powershell
  .\build_windows_exe.ps1 -RequireSignature
  .\build_setup_exe.ps1 -RequireSignature
  ```

  `-RequireSignature` is mandatory for an official production release. See
  `CODE_SIGNING.md` for the certificate procedure.

## Microsoft Store / MSIX

The direct Windows installer remains the normal route for current customers.
For Store discovery and Store-managed installation, build a separate MSIX only
after the legal publisher has registered in Partner Center and supplied the
exact package identity and publisher string:

```powershell
.\build_msix.ps1 -IdentityName "PARTNER_CENTER_IDENTITY_NAME" `
  -Publisher "CN=EXACT_PARTNER_CENTER_PUBLISHER" `
  -PublisherDisplayName "LEGAL_PUBLISHER_NAME" -RequireSignature
```

The full commercial checklist and clean-install validation procedure are in
`MS_STORE_RELEASE.md`. Neither the Store account nor identity values belong in
source control.

## Release order

1. Merge reviewed Desktop and cloud changes.
2. Run the Desktop and cloud control tests.
3. Build and verify the Authenticode signature and SHA-256 of the installer.
4. Upload the installer to the public download location.
5. Only then publish the matching cloud update manifest and verify the public
   update API.

The desktop app is a controlled financial workflow tool. Country-specific tax
filings, e-invoice submission and bank connections remain disabled until their
own country pack, credentials and professional validation are complete.
