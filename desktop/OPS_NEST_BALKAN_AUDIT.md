# OpsNest Balkan product audit — 24 July 2026

This is an implementation audit, not marketing copy. “Ready” means the
function exists locally and was reviewed in the source. “Preparation” means a
safe local workflow exists but no state authority is contacted. “Blocked” means
that an external credential, regulated integration, or a local accountant must
be involved before the feature can be truthfully released as compliant.

## Product promise: project, money, and documents in one place

### Ready now

- Projects have a customer, contract value excluding VAT, advance percentage,
  one controlled advance invoice, final invoices, a contract-realisation view,
  budget, project P&L, project documents, archive, VAT working evidence,
  accountant export, and project finance view. Advances are deliberately kept
  out of project income until the underlying work is billed.
- Customers, invoices, collections, credit notes, payment history, reminders,
  purchase documents, suppliers, supplier liabilities, recurring expenses,
  cash accounts, and a company-wide P&L/cash-flow screen exist.
- A project input document can now become exactly one linked supplier liability.
  The document remains the expense evidence; the liability is the payable and
  bank-reconciliation record. Company P&L excludes the linked duplicate.
- Local PDF text extraction / optional Tesseract OCR proposes supplier, date,
  number, totals, VAT, and category. Every extracted field still needs human
  review before saving.
- CSV/XLSX bank-statement import handles inflows and outflows, suggests a match,
  requires confirmation, and reverses its financial effect safely if the bank
  line is deleted.
- Cash flow is shown for 7, 30, and 90 days, split by currency. It includes
  receivables, supplier liabilities, recurring expenses, opening cash accounts,
  and confirmed future bank lines.
- Owner/accountant roles, invoice approval, local financial audit log, period
  closing, a manual chart of accounts, balanced journal entries, and trial
  balance are present.
- Financial controls additionally require evidence before a supplier payable
  can be approved or paid, block self-approval by the preparer, record the
  person who confirms a bank outflow, and provide a CSV audit export with a
  separate SHA-256 control file. The daily work centre separately flags
  missing supplier evidence and rejected payables.
- Every new local financial-audit record extends a SHA-256 predecessor chain.
  The monthly control can verify that chain and stops the normal audit export
  when an altered, deleted or reordered record is detected. This is a
  tamper-evident local control, not a substitute for legally immutable archive
  retention or a country-specific audit certificate.
- The owner can configure a supplier-payable approval ceiling in the company
  currency. When it is active, an administrator cannot approve a bill at or
  above that ceiling, and every foreign-currency bill goes to the owner until
  the company adopts a documented FX and delegated-authority policy. The
  approval escalation itself is retained in the financial audit trail.
- Teams can sign in and explicitly upload/download a checksum-protected local
  database snapshot. The platform has team roles and an operational platform
  audit trail.

### Present but not yet the promised full flow

- Supplier liabilities can be submitted for owner approval when a non-owner
  creates them in a Business/Pro team workspace. Pending liabilities cannot be
  confirmed against a bank outflow. The owner can approve, reject with a reason,
  return for correction, and discuss each liability in a local comment trail.
- OCR is local extraction, not an accounting AI that reliably chooses a chart
  account, VAT treatment, supplier, and payment approval by itself.
- The manual ledger validates double entry and locks posted entries, but source
  documents do not yet create country-specific automatic postings.
- Project P&L is a single reporting-currency view. Company cash flow and P&L
  correctly keep currencies separate. A full multi-currency project view needs
  a documented FX-rate and reporting-currency model before BGN/EUR or other
  currencies can be mixed in one project total.
- Team sync is whole-database snapshot sync, not live concurrent editing or an
  accountant web portal. Project archive files are local and are not part of
  the snapshot payload.

### Explicitly not implemented / must not be advertised as ready

- Direct bank connection / PSD2 or Open Banking payment initiation.
- Real Serbian SEF send/receive/status workflow. The app currently has local
  readiness checks, UBL draft export, and a demo key connectivity check only.
- A Bulgarian state e-invoice connector, CAIS EPP submission, or certified
  EN 16931 validation.
- Croatian Fiskalizacija 2.0/eRačun exchange, fiscalisation messaging,
  information-intermediary connection, KPD catalogue mapping, or production
  XML validation.
- Official VAT returns, statutory country chart of accounts, statutory closing,
  payroll, annual accounts, and a legal claim of tax or accounting compliance.

## Country-pack status

| Country | Current OpsNest state | Next product work | External gate |
| --- | --- | --- | --- |
| Serbia | SEF readiness, local UBL draft, per-company key entry flow, advance/final invoices, VAT working evidence | Versioned SEF client, outbound/inbound/outbox statuses, XML validation, EEO/previous-tax workflows | Each customer company must have an SEF account and API key; implementation must be tested against current SEF demo and production documentation |
| Bulgaria | EUR default for new firms; BGN kept for historic records; local VAT, generic EN 16931/B2G preparation | EUR changeover reporting, VAT export agreed with a Bulgarian accountant, SAF-T scope/eligibility and exporter only if applicable | Local accountant validation and any required NAP/KЕP workflow; there is no generic claimable national e-invoice connector in the app |
| Croatia | Generic UBL preparation only | KPD catalogue per item, fiscalisation/eRačun validator, receipt and delivery statuses, certified intermediary integration | Croatia’s current eRačun workflow requires the applicable technical rules and an authorised information-intermediary / credentials |
| BiH, Montenegro, North Macedonia | Country VAT/currency defaults and generic structured-document preparation | Country research and partner-led pack | Local accountant partner and official interface/rule validation |

## Delivery order

1. Add rejection, comments, limits, and accounting-AI suggestions to the
   supplier approval flow. Human approval remains compulsory. This is the
   highest-value local work and does not require a government contract.
2. Replace snapshot-only collaboration with cloud records, document storage,
   conflict handling, and a browser accountant workspace for multiple firms.
3. Introduce an explicit project reporting-currency and FX model; then show
   project P&L in EUR/BGN/RSD/etc. without guessing conversion.
4. Build Serbia SEF as the first live country connector only after obtaining a
   customer company demo key and passing the official demo workflow.
5. Build Croatia as a partner-integrated pack, not as a generic UBL export.
6. Add automatic country chart-of-accounts posting, statutory reports, and
   closing only with a local accountant’s controlled mapping.

## Non-negotiable release wording

Until a country pack passes its official demo/production validation and a local
accountant has signed off its mapping, OpsNest may say “prepared for review” or
“working evidence”. It must not say “SEF connected”, “fiscalised”, “filed”,
“certified”, “statutory accounting”, or “tax compliant”.
