# OpsNest Desktop 2.13.3 — candidate release

## Operational controls

- Cloud session tokens are protected on each Windows profile with DPAPI.
- Month closing rechecks the financial audit chain and a verified backup no
  older than 24 hours.
- Recurring supplier expenses are idempotent, so a restart cannot create a
  duplicate payable for the same schedule date.
- Cloud change detection now has a stable checksum: device-only synchronization
  timestamps cannot make an unchanged company appear unsynchronized.

## Controlled source and documents

- The complete Desktop source, checks and release process are now maintained
  with the cloud service in the same Git repository.
- The built-in invoice workbook is a neutral OpsNest template with no prior
  company data. It is tested to accept all automated export updates.

## Release condition

This is not an official production release until its installer is Authenticode
signed, uploaded to the public download URL, and the published checksum has
been verified by public HTTP download.
