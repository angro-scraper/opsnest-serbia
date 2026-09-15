# OpsNest Windows commercial release

OpsNest has two official Windows delivery channels. They use the same reviewed
desktop source, but not the same signing or update model.

| Channel | Use | Required before public sale |
| --- | --- | --- |
| Direct download | Existing customers, website checkout and the in-app update flow | Authenticode-sign the app and installer, publish a versioned installer, verify its public SHA-256, then update the cloud manifest. |
| Microsoft Store (MSIX) | Discovery, trusted installation and Store-managed updates | Reserve the OpsNest name in Partner Center, use the exact identity and publisher values it provides, build MSIX, run Windows App Certification Kit, submit required listing/legal material. |

## What is already prepared

- `build_windows_exe.ps1` and `build_setup_exe.ps1` sign the desktop payload
  and final direct installer when `-RequireSignature` is supplied.
- `build_msix.ps1` packages the reviewed PyInstaller payload as a full-trust
  Win32 MSIX package. It intentionally requires Store identity values as
  parameters; those values must not be invented or committed to the repository.
- The public website already has Windows download, pricing, support, privacy,
  terms and cancellation pages. A stable support address is used everywhere.
- Desktop updates verify both HTTPS source and a published SHA-256 before the
  installer is launched.

## One-time commercial prerequisites

1. Register the legal publisher in Microsoft Partner Center and reserve the
   product name `OpsNest`.
2. Obtain the exact **Package/Identity name** and **Publisher** string from
   Partner Center. The publisher is normally an exact `CN=...` string; do not
   substitute a company display name.
3. For direct downloads, obtain an organization code-signing identity and
   configure the release machine as described in `CODE_SIGNING.md`. Use one
   stable publisher identity for every release.
4. Review the privacy notice, terms, cancellation policy, pricing, support
   SLA/contact and country-specific tax wording with the business's legal and
   accounting advisers before they are advertised as final terms.
5. Prepare Store listing assets: short/long description, support URL, privacy
   URL, application logo in Store-required sizes, at least one real screenshot,
   category, age rating and market/pricing choices.

## MSIX build after Partner Center registration

Install the Windows SDK tools that provide `makeappx.exe` and `signtool.exe`,
then run from `desktop` after the ordinary desktop payload has been built:

```powershell
.\build_windows_exe.ps1 -RequireSignature
.\build_msix.ps1 `
  -IdentityName "PARTNER_CENTER_IDENTITY_NAME" `
  -Publisher "CN=EXACT_PARTNER_CENTER_PUBLISHER" `
  -PublisherDisplayName "LEGAL_PUBLISHER_NAME" `
  -RequireSignature
```

For an MSIX submitted only to Microsoft Store, Store certification re-signs the
accepted package. A directly downloaded MSIX must have a valid trusted
signature; use `-RequireSignature` for that path.

Run the Windows App Certification Kit on the created package and perform a
clean install, first-run, local-data preservation, update, uninstall and
reinstall test on a non-production Windows account before submission.

## Release decision

Do not replace the direct installer with the Store package. Keep both:

- Direct installer for existing Balkan customers and the current secure
  in-app update route.
- Microsoft Store MSIX for trust, discovery and low-friction installation.

Never publish a new number, installer URL or Store package before its exact
binary has passed tests, signature verification where required, and a clean
installation test. The Store submission, legal acceptance and payment choices
must be completed by the authorized company owner in Partner Center.
