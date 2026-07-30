"""High-value accounting workflow checks for a clean OpsNest workspace.

These tests deliberately exercise the rules that must never regress: an
invoice cannot skip the draft/approval process, a returned document becomes
editable with an audit comment, contractual advances are generated from the
project, and month-end close cannot bypass the control list.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch
from datetime import date
from pathlib import Path


# Keep the release gate runnable from either repository root or `desktop/`.
# A release check that depends on the operator's current directory is too easy
# to silently skip in CI or during a handover.
_DESKTOP_ROOT = Path(__file__).resolve().parents[1]
if str(_DESKTOP_ROOT) not in sys.path:
    sys.path.insert(0, str(_DESKTOP_ROOT))


class CriticalWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="opsnest-qa-")
        os.environ["DELTA_FAKTURE_ROOT"] = self.temp_dir.name

        # The core caches its data-root choice; reset it before each isolated
        # workspace so no test can read or write a real customer database.
        import delta_fakture_core as core

        core._root_dir_cache = None
        self.core = core
        self.db = core.Database(Path(self.temp_dir.name) / "Data" / "qa.db")
        self.db.apply_subscription_update(status="active", plan_code="pro")

        self.customer_id = self.db.save_customer({"name": "QA Customer", "email": "qa@example.invalid"})
        self.project_id = self.db.save_project(
            {
                "customer_id": self.customer_id,
                "name": "QA Project",
                "contract_net_amount": "100000",
                "advance_percent": "20",
            }
        )
        self.supplier_attachment = Path(self.temp_dir.name) / "supplier-invoice.pdf"
        self.supplier_attachment.write_bytes(b"QA supplier evidence")

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()
        os.environ.pop("DELTA_FAKTURE_ROOT", None)
        self.core._root_dir_cache = None

    def invoice_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "project_id": self.project_id,
            "customer_id": self.customer_id,
            "issue_date": date.today().isoformat(),
            "tax_event_date": date.today().isoformat(),
            "due_date": date.today().isoformat(),
            "currency": "EUR",
            "vat_rate": "0.20",
            "prepared_by_role": "accountant",
            "prepared_by_name": "QA Accountant",
            "status_code": "draft",
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def standard_items() -> list[dict[str, object]]:
        return [
            {
                "category": "Rad",
                "description": "QA service",
                "unit": "kom.",
                "quantity": "1",
                "unit_price": "1000",
                "discount_percent": "0",
            }
        ]

    def test_draft_revision_approval_issue_and_safe_edit(self) -> None:
        invoice_id = self.db.save_invoice(self.invoice_payload(), self.standard_items())
        draft = self.db.get_invoice(invoice_id)
        self.assertEqual(draft["status_code"], "draft")
        first_number = draft["invoice_number"]

        self.db.save_invoice(
            self.invoice_payload(id=invoice_id, status_code="pending_approval"),
            self.standard_items(),
        )
        self.db.return_invoice_for_revision(invoice_id, "QA Owner", "Dodajte broj ugovora.")
        self.assertEqual(self.db.get_invoice(invoice_id)["status_code"], "draft")
        audit = self.db.list_invoice_audit(invoice_id)
        self.assertTrue(any(row["action_code"] == "returned_for_revision" for row in audit))

        self.db.save_invoice(
            self.invoice_payload(id=invoice_id, status_code="pending_approval"),
            self.standard_items(),
        )
        self.db.approve_invoice(invoice_id, "QA Owner")
        self.db.save_invoice(
            self.invoice_payload(
                id=invoice_id,
                status_code="issued",
                prepared_by_role="owner",
                prepared_by_name="QA Owner",
            ),
            self.standard_items(),
        )
        issued = self.db.get_invoice(invoice_id)
        self.assertEqual(issued["status_code"], "issued")
        self.assertEqual(issued["invoice_number"], first_number)

        changed_items = self.standard_items()
        changed_items[0]["unit_price"] = "1100"
        self.db.save_invoice(
            self.invoice_payload(
                id=invoice_id,
                status_code="issued",
                prepared_by_role="owner",
                prepared_by_name="QA Owner",
            ),
            changed_items,
        )
        self.assertEqual(self.db.get_invoice(invoice_id)["invoice_number"], first_number)
        self.assertEqual(len(self.db.list_invoices(project_id=self.project_id)), 1)

    def test_contract_advance_and_month_close_gate(self) -> None:
        advance_id = self.db.save_invoice(
            self.invoice_payload(invoice_kind="advance"),
            [],
        )
        advance = self.db.get_invoice(advance_id)
        self.assertEqual(advance["status_code"], "draft")
        self.assertEqual(advance["invoice_kind"], "advance")
        lines = self.db.list_invoice_items(advance_id)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["category"], "Ugovorni avans")
        self.assertAlmostEqual(float(lines[0]["net_amount"]), 20000.0, places=2)

        period = date.today().replace(day=1)
        period_key = period.strftime("%Y-%m")
        period_end = period.replace(day=28)
        while True:
            try:
                period_end = period_end.replace(day=period_end.day + 1)
            except ValueError:
                break
        with self.assertRaises(ValueError):
            self.db.save_accounting_period(
                {"period_from": period.isoformat(), "period_to": period_end.isoformat(), "status": "closed"}
            )

        for task in self.db.monthly_control_tasks():
            self.db.set_monthly_control_task(period_key, task["code"], "done", completed_by="QA Owner")
        latest_backup = self.db.list_backups()[0]
        Path(latest_backup["path"]).write_bytes(b"corrupted monthly-close backup")
        with self.assertRaisesRegex(ValueError, "backup"):
            self.db.save_accounting_period(
                {"period_from": period.isoformat(), "period_to": period_end.isoformat(), "status": "closed"}
            )
        self.db.create_and_verify_backup()
        period_id = self.db.save_accounting_period(
            {"period_from": period.isoformat(), "period_to": period_end.isoformat(), "status": "closed", "closed_by": "QA Owner"}
        )
        self.assertGreater(period_id, 0)

    def test_closed_period_can_only_reopen_with_reason_and_audit(self) -> None:
        period = date.today().replace(day=1)
        period_key = period.strftime("%Y-%m")
        period_end = period.replace(day=28)
        while True:
            try:
                period_end = period_end.replace(day=period_end.day + 1)
            except ValueError:
                break
        for task in self.db.monthly_control_tasks():
            self.db.set_monthly_control_task(period_key, task["code"], "done", completed_by="QA Owner")
        self.db.create_and_verify_backup()
        period_id = self.db.save_accounting_period(
            {"period_from": period.isoformat(), "period_to": period_end.isoformat(), "status": "closed", "closed_by": "QA Owner"}
        )
        with self.assertRaises(ValueError):
            self.db.save_accounting_period(
                {"id": period_id, "period_from": period.isoformat(), "period_to": period_end.isoformat(), "status": "open"}
            )
        with self.assertRaises(ValueError):
            self.db.reopen_accounting_period(period_id, reopened_by="QA Owner", reason="")
        self.db.reopen_accounting_period(period_id, reopened_by="QA Owner", reason="Ispravka potvrđenog izvoda banke.")
        self.assertFalse(self.db.is_financial_date_locked(period.isoformat()))
        audit = self.db.list_financial_audit("accounting_period", period_id)
        self.assertTrue(any(row["action_code"] == "period_reopened" for row in audit))

    def test_month_close_rechecks_audit_integrity_even_when_checklist_is_done(self) -> None:
        period = date.today().replace(day=1)
        period_key = period.strftime("%Y-%m")
        period_end = period.replace(day=28)
        while True:
            try:
                period_end = period_end.replace(day=period_end.day + 1)
            except ValueError:
                break
        for task in self.db.monthly_control_tasks():
            self.db.set_monthly_control_task(period_key, task["code"], "done", completed_by="QA Owner")
        self.db.create_and_verify_backup()
        event = self.db.list_financial_audit("monthly_control", int(period_key.replace("-", "")))[0]
        self.db.conn.execute(
            "UPDATE financial_audit_log SET details=? WHERE id=?",
            ("neovlašćeno izmenjen zapis", int(event["id"])),
        )
        self.db.conn.commit()
        with self.assertRaisesRegex(ValueError, "audit"):
            self.db.save_accounting_period(
                {"period_from": period.isoformat(), "period_to": period_end.isoformat(), "status": "closed"}
            )

    def test_journal_requires_review_and_keeps_a_correction_trail(self) -> None:
        bank_id = self.db.save_ledger_account({"code": "1000", "name": "Banka", "account_type": "asset"})
        expense_id = self.db.save_ledger_account({"code": "6000", "name": "Trošak", "account_type": "expense"})
        with self.assertRaises(ValueError):
            self.db.save_journal_entry(
                {
                    "entry_date": date.today().isoformat(),
                    "status": "posted",
                    "lines": [
                        {"account_id": expense_id, "debit": "120", "currency": "EUR"},
                        {"account_id": bank_id, "credit": "120", "currency": "EUR"},
                    ],
                }
            )
        entry_id = self.db.save_journal_entry(
            {
                "entry_date": date.today().isoformat(),
                "reference": "QA-J-1",
                "description": "QA ručno knjiženje",
                "status": "draft",
                "lines": [
                    {"account_id": expense_id, "debit": "120", "currency": "EUR"},
                    {"account_id": bank_id, "credit": "120", "currency": "EUR"},
                ],
            }
        )
        self.assertEqual(self.db.get_journal_entry(entry_id)["status"], "draft")
        self.assertEqual(self.db.ledger_trial_balance()["rows"], [])

        self.db.post_journal_entry(entry_id, posted_by="QA Accountant", comment="Kontrola iznosa i konta.")
        self.assertEqual(self.db.get_journal_entry(entry_id)["status"], "posted")
        self.assertEqual(len(self.db.ledger_trial_balance()["rows"]), 2)
        with self.assertRaises(ValueError):
            self.db.save_journal_entry(
                {
                    "id": entry_id,
                    "entry_date": date.today().isoformat(),
                    "status": "draft",
                    "lines": [
                        {"account_id": expense_id, "debit": "120", "currency": "EUR"},
                        {"account_id": bank_id, "credit": "120", "currency": "EUR"},
                    ],
                }
            )
        correction_id = self.db.create_reversing_journal_entry(
            entry_id,
            created_by="QA Accountant",
            reason="Pogrešan dokument u knjiženju.",
        )
        correction = self.db.get_journal_entry(correction_id)
        self.assertEqual(correction["status"], "draft")
        correction_lines = self.db.list_journal_lines(correction_id)
        self.assertAlmostEqual(float(correction_lines[0]["credit_amount"]), 120.0, places=2)
        audit = self.db.list_financial_audit("journal_entry", entry_id)
        self.assertTrue(any(row["action_code"] == "posted_after_review" for row in audit))
        self.assertTrue(any(row["action_code"] == "correction_draft_created" for row in audit))

    def test_payable_needs_approval_again_after_a_material_edit(self) -> None:
        vendor_id = self.db.save_vendor({"name": "QA Supplier", "email": "supplier@example.invalid"})
        bill_id = self.db.save_vendor_bill(
            {
                "vendor_id": vendor_id,
                "project_id": self.project_id,
                "bill_number": "SUP-001",
                "bill_date": date.today().isoformat(),
                "due_date": date.today().isoformat(),
                "net_amount": "100",
                "vat_rate": "20",
                "currency": "EUR",
                "attachment_path": str(self.supplier_attachment),
                "approval_status": "pending",
                "prepared_by_name": "QA Accountant",
            }
        )
        with self.assertRaises(ValueError):
            self.db.record_vendor_bill_payment(bill_id, "120", date.today().isoformat())
        self.db.approve_vendor_bill(bill_id, "QA Owner")
        self.assertEqual(self.db.get_vendor_bill(bill_id)["approval_status"], "approved")
        self.db.save_vendor_bill(
            {
                "id": bill_id,
                "vendor_id": vendor_id,
                "project_id": self.project_id,
                "bill_number": "SUP-001",
                "bill_date": date.today().isoformat(),
                "due_date": date.today().isoformat(),
                "net_amount": "125",
                "vat_rate": "20",
                "currency": "EUR",
                "prepared_by_name": "QA Accountant",
            }
        )
        edited = self.db.get_vendor_bill(bill_id)
        self.assertEqual(edited["approval_status"], "pending")
        audit = self.db.list_financial_audit("vendor_bill", bill_id)
        self.assertTrue(any(row["action_code"] == "returned_for_review_after_edit" for row in audit))

    def test_payable_without_evidence_cannot_be_approved_or_paid(self) -> None:
        vendor_id = self.db.save_vendor({"name": "Evidence Required Supplier"})
        bill_id = self.db.save_vendor_bill(
            {
                "vendor_id": vendor_id,
                "project_id": self.project_id,
                "bill_number": "NO-EVIDENCE-1",
                "bill_date": date.today().isoformat(),
                "due_date": date.today().isoformat(),
                "net_amount": "100",
                "vat_rate": "20",
                "currency": "EUR",
                "approval_status": "approved",
            }
        )
        bill = self.db.get_vendor_bill(bill_id)
        self.assertEqual(bill["approval_status"], "pending")
        waiting = self.db.vendor_payment_plan()["waiting_for_approval"]
        self.assertEqual(waiting[0]["review_blocker"], "missing_evidence")
        work_center = self.db.daily_work_center()
        self.assertTrue(any(int(row["id"]) == bill_id for row in work_center["vendor_evidence_missing"]))
        with self.assertRaises(ValueError):
            self.db.approve_vendor_bill(bill_id, "QA Owner")
        with self.assertRaises(ValueError):
            self.db.record_vendor_bill_payment(bill_id, "120", date.today().isoformat())

    def test_preparer_cannot_approve_own_invoice_or_payable(self) -> None:
        invoice_id = self.db.save_invoice(
            self.invoice_payload(prepared_by_name="QA Accountant"),
            self.standard_items(),
        )
        self.db.save_invoice(
            self.invoice_payload(id=invoice_id, status_code="pending_approval", prepared_by_name="QA Accountant"),
            self.standard_items(),
        )
        with self.assertRaises(ValueError):
            self.db.approve_invoice(invoice_id, "QA Accountant")
        self.db.approve_invoice(invoice_id, "QA Owner")

        vendor_id = self.db.save_vendor({"name": "Segregation QA Supplier"})
        bill_id = self.db.save_vendor_bill(
            {
                "vendor_id": vendor_id,
                "bill_number": "SOD-1",
                "bill_date": date.today().isoformat(),
                "due_date": date.today().isoformat(),
                "net_amount": "120",
                "vat_rate": "20",
                "currency": "EUR",
                "attachment_path": str(self.supplier_attachment),
                "approval_status": "pending",
                "prepared_by_name": "QA Accountant",
            }
        )
        with self.assertRaises(ValueError):
            self.db.approve_vendor_bill(bill_id, "QA Accountant")
        self.db.approve_vendor_bill(bill_id, "QA Owner")
        self.db.record_vendor_bill_payment(bill_id, "144", date.today().isoformat(), recorded_by_name="QA Treasury")
        bill_audit = self.db.list_financial_audit("vendor_bill", bill_id)
        self.assertTrue(any("QA Treasury" in str(row["details"]) for row in bill_audit))

    def test_owner_approval_ceiling_escalates_large_and_foreign_payables(self) -> None:
        company = self.db.get_company()
        self.db.save_company({**company, "default_currency": "EUR", "vendor_bill_owner_approval_threshold": "1000"})
        vendor_id = self.db.save_vendor({"name": "Approval ceiling supplier"})

        bill_id = self.db.save_vendor_bill(
            {
                "vendor_id": vendor_id,
                "bill_number": "CEILING-EUR",
                "bill_date": date.today().isoformat(),
                "due_date": date.today().isoformat(),
                "net_amount": "1000",
                "vat_rate": "0",
                "currency": "EUR",
                "attachment_path": str(self.supplier_attachment),
                "approval_status": "pending",
                "prepared_by_name": "QA Accountant",
            }
        )
        with self.assertRaisesRegex(ValueError, "vlasnika"):
            self.db.approve_vendor_bill(bill_id, "QA Administrator", approver_role="administrator")
        self.assertEqual(self.db.get_vendor_bill(bill_id)["approval_status"], "pending")
        self.db.approve_vendor_bill(bill_id, "QA Owner", approver_role="owner")
        self.assertEqual(self.db.get_vendor_bill(bill_id)["approval_status"], "approved")

        foreign_bill_id = self.db.save_vendor_bill(
            {
                "vendor_id": vendor_id,
                "bill_number": "CEILING-BGN",
                "bill_date": date.today().isoformat(),
                "due_date": date.today().isoformat(),
                "net_amount": "10",
                "vat_rate": "0",
                "currency": "BGN",
                "attachment_path": str(self.supplier_attachment),
                "approval_status": "pending",
                "prepared_by_name": "QA Accountant",
            }
        )
        with self.assertRaisesRegex(ValueError, "valuti BGN"):
            self.db.approve_vendor_bill(foreign_bill_id, "QA Administrator", approver_role="administrator")
        self.db.approve_vendor_bill(foreign_bill_id, "QA Owner", approver_role="owner")
        policy_audit = self.db.list_financial_audit("vendor_bill", foreign_bill_id)
        self.assertTrue(any(row["action_code"] == "owner_approval_required" for row in policy_audit))

    def test_financial_audit_export_is_itself_audited(self) -> None:
        self.db.record_financial_audit_export("QA Accountant", 17)
        events = self.db.list_financial_audit("financial_audit", 0)
        self.assertTrue(any(row["action_code"] == "exported" and "17" in str(row["details"]) for row in events))

    def test_financial_audit_hash_chain_detects_a_modified_record(self) -> None:
        self.db.record_financial_audit_export("QA Accountant", 3)
        verified = self.db.verify_financial_audit_chain()
        self.assertTrue(verified["ok"])
        self.assertGreaterEqual(verified["count"], 1)
        self.assertTrue(verified["last_hash"])

        event = self.db.list_financial_audit("financial_audit", 0)[0]
        self.db.conn.execute(
            "UPDATE financial_audit_log SET details=? WHERE id=?",
            ("naknadno izmenjen zapis", int(event["id"])),
        )
        self.db.conn.commit()
        tampered = self.db.verify_financial_audit_chain()
        self.assertFalse(tampered["ok"])
        self.assertEqual(tampered["invalid_id"], int(event["id"]))

    def test_rejected_payable_has_one_work_center_queue_entry(self) -> None:
        vendor_id = self.db.save_vendor({"name": "Rejected queue supplier"})
        bill_id = self.db.save_vendor_bill(
            {
                "vendor_id": vendor_id,
                "bill_number": "REJECT-1",
                "bill_date": date.today().isoformat(),
                "due_date": date.today().isoformat(),
                "net_amount": "100",
                "vat_rate": "20",
                "currency": "EUR",
                "approval_status": "pending",
                "prepared_by_name": "QA Accountant",
            }
        )
        self.db.reject_vendor_bill(bill_id, "QA Owner", "Nedostaje originalni dokument.")
        center = self.db.daily_work_center()
        self.assertTrue(any(int(row["id"]) == bill_id for row in center["rejected_vendor_bills"]))
        self.assertFalse(any(int(row["id"]) == bill_id for row in center["vendor_evidence_missing"]))

    def test_backup_is_a_consistent_sqlite_snapshot_and_restore_rejects_invalid_file(self) -> None:
        report = self.db.create_and_verify_backup()
        self.assertTrue(report["ok"])
        self.assertTrue(Path(report["path"]).is_file())
        self.assertIn("integritet", report["detail"].lower())

        health = self.db.backup_health_report()
        self.assertTrue(health["ok"])
        self.assertTrue(health["current"]["ok"])
        self.assertTrue(health["backup"]["ok"])

        invalid_backup = self.core.backup_dir() / "not-an-opsnest-backup.db"
        invalid_backup.write_bytes(b"not a SQLite database")
        with self.assertRaises(ValueError):
            self.db.restore_backup(invalid_backup)

    def test_team_sync_detects_unsent_local_changes_without_tracking_device_metadata(self) -> None:
        # A brand-new company can legitimately have no financial audit events.
        # Once a financial control occurs, its verified chain head must travel
        # as metadata with the protected team revision.
        self.db.record_financial_audit_export("QA Accountant", 1)
        snapshot = self.db.build_cloud_sync_snapshot()
        self.assertEqual(len(snapshot["financial_audit_hash"]), 64)
        self.assertGreater(int(snapshot["financial_audit_count"]), 0)
        self.db.mark_cloud_sync(4, snapshot["sha256"])
        clean = self.db.cloud_sync_change_status()
        self.assertTrue(clean["tracked"])
        self.assertFalse(clean["has_unsynced_changes"])

        # Updating only Desktop sync bookkeeping must not make a clean company
        # look like it has accounting work waiting to be uploaded.
        self.db.mark_cloud_sync(4, snapshot["sha256"])
        self.assertFalse(self.db.cloud_sync_change_status()["has_unsynced_changes"])

        self.db.save_customer({"name": "Unsynced customer"})
        changed = self.db.cloud_sync_change_status()
        self.assertTrue(changed["tracked"])
        self.assertTrue(changed["has_unsynced_changes"])

    def test_neutral_invoice_template_accepts_export_updates(self) -> None:
        """The distributed first-party template must contain no former customer data."""
        from delta_fakture_core import TEMPLATE_XLSX
        from delta_fakture_export import _copy_zip_with_updates, build_invoice_xlsx_updates

        self.assertEqual(TEMPLATE_XLSX.name, "opsnest_invoice_template.xlsx")
        self.assertTrue(TEMPLATE_XLSX.is_file())
        output = Path(self.temp_dir.name) / "neutral-invoice.xlsx"
        invoice = {
            "company": {"name": "Example Contractor", "email": "office@example.invalid"},
            "invoice_number": "QA-001",
            "currency": "EUR",
            "issue_date": date.today().isoformat(),
            "tax_event_date": date.today().isoformat(),
            "due_date": date.today().isoformat(),
            "payment_method": "Bank transfer",
            "project_name": "QA Project",
            "customer_name": "Example Customer",
            "items": [],
        }
        _copy_zip_with_updates(TEMPLATE_XLSX, output, build_invoice_xlsx_updates(invoice))
        with zipfile.ZipFile(output) as workbook:
            sheet_xml = workbook.read("xl/worksheets/sheet1.xml")
            all_xml = b"\n".join(
                workbook.read(name)
                for name in workbook.namelist()
                if name.endswith(".xml")
            )
        self.assertIn(b"Example Contractor", sheet_xml)
        self.assertNotIn(b"Delta Hochbau", all_xml)

    def test_bulgarian_document_language_translates_system_advance_terms(self) -> None:
        """A Serbian-entered contractual advance must issue with Bulgarian system terms."""
        from delta_fakture_export import build_invoice_xlsx_updates

        updates = build_invoice_xlsx_updates(
            {
                "company": {"country_code": "BG"},
                "document_language": "bg",
                "invoice_kind": "advance",
                "payment_method": "Banka",
                "items": [
                    {
                        "category": "Ugovorni avans",
                        "description": "Avans 20% po ugovoru QA-1",
                        "unit": "kom.",
                        "quantity": 1,
                        "unit_price": 100,
                        "discount_percent": 0,
                        "net_amount": 100,
                        "vat_amount": 20,
                        "gross_amount": 120,
                    }
                ],
            }
        )["xl/worksheets/sheet1.xml"]
        self.assertEqual(updates["I8"], "Банков превод")
        self.assertEqual(updates["B25"], "Договорен аванс")
        self.assertEqual(updates["C25"], "Аванс 20% по договор QA-1")
        self.assertEqual(updates["D25"], "бр.")

    def test_bulgarian_invoice_editor_commands_have_stable_display_labels(self) -> None:
        from delta_fakture_app import canonical_ui_text, tr

        self.assertEqual(tr("Avansni račun", "bg"), "Авансова фактура")
        self.assertEqual(tr("Forma fakture", "bg"), "Формуляр на фактура")
        self.assertEqual(tr("Šabloni fakture", "bg"), "Шаблони за фактури")
        self.assertEqual(tr("Backup now", "bg"), "Архивирай сега")
        self.assertEqual(tr("Vrati veličinu", "bg"), "Възстанови размера")
        self.assertEqual(canonical_ui_text("Авансова фактура", "bg"), "Avansni račun")

    def test_powershell_paths_keep_single_windows_separators(self) -> None:
        from delta_fakture_export import _powershell_literal

        self.assertEqual(_powershell_literal(r"C:\OpsNest\Preview\invoice.xlsx"), r"'C:\OpsNest\Preview\invoice.xlsx'")

    def test_credit_note_uses_source_invoice_document_language(self) -> None:
        """Follow-up tax documents remain in the language of the original invoice."""
        from openpyxl import load_workbook
        from delta_fakture_export import export_credit_note_xlsx

        output = Path(self.temp_dir.name) / "credit-note-bg.xlsx"
        export_credit_note_xlsx(
            {
                "credit_note_number": "CN-1",
                "issue_date": date.today().isoformat(),
                "currency": "EUR",
                "net_amount": 100,
                "vat_amount": 20,
                "gross_amount": 120,
                "company": {"country_code": "BG", "name": "QA Supplier"},
                "source_invoice": {"document_language": "bg", "invoice_number": "1000000001"},
            },
            output,
        )
        sheet = load_workbook(output).active
        self.assertEqual(sheet["A1"].value, "КРЕДИТНО ИЗВЕСТИЕ")
        self.assertEqual(sheet["A4"].value, "НОМЕР НА КРЕДИТНОТО ИЗВЕСТИЕ")
        self.assertEqual(sheet["A18"].value, "Данъчна основа")

    def test_cloud_client_waits_for_safe_cold_start(self) -> None:
        """A free-tier wake-up must not become a false service-unavailable error."""
        from opsnest_cloud_client import CLOUD_REQUEST_TIMEOUT_SECONDS, OpsNestCloudClient

        response = MagicMock()
        response.read.return_value = b'{"latest_version":"2.13.0"}'
        context = MagicMock()
        context.__enter__.return_value = response
        with patch("opsnest_cloud_client.urlopen", return_value=context) as mocked_urlopen:
            self.assertEqual(OpsNestCloudClient("https://api.example.invalid").desktop_update()["latest_version"], "2.13.0")
        self.assertEqual(mocked_urlopen.call_args.kwargs["timeout"], CLOUD_REQUEST_TIMEOUT_SECONDS)
        self.assertGreaterEqual(CLOUD_REQUEST_TIMEOUT_SECONDS, 60)

    def test_startup_defers_remote_license_and_secondary_tab_work(self) -> None:
        """A cold cloud service must never freeze the first usable desktop screen."""
        source = (_DESKTOP_ROOT / "delta_fakture_app.py").read_text(encoding="utf-8")
        self.assertIn("def refresh_online_license_silently_in_background", source)
        self.assertIn("self.after(900, self.refresh_online_license_silently_in_background)", source)
        self.assertNotIn("self.after(900, lambda: self.refresh_online_license(silent=True))", source)
        self.assertIn("def _refresh_secondary_tab_step", source)
        self.assertIn("self.after(320, lambda: self._refresh_secondary_tab_step(index + 1))", source)

    def test_cloud_credentials_are_protected_and_legacy_values_migrate(self) -> None:
        self.db.save_cloud_connection(
            api_url="https://api.example.invalid",
            workspace_token="qa-workspace-token",
            owner_email="owner@example.invalid",
        )
        self.db.save_cloud_member_session(
            member_id="member-qa",
            member_token="qa-member-token",
            member_role="accountant",
            member_name="QA Accountant",
        )
        stored = self.db.conn.execute(
            "SELECT cloud_workspace_token, cloud_member_token FROM workspace_subscription WHERE id = 1"
        ).fetchone()
        self.assertNotEqual(stored[0], "qa-workspace-token")
        self.assertNotEqual(stored[1], "qa-member-token")
        expected_prefix = "dpapi:v1:" if os.name == "nt" else "plain:v1:"
        self.assertTrue(stored[0].startswith(expected_prefix))
        self.assertTrue(stored[1].startswith(expected_prefix))
        connection = self.db.cloud_connection()
        self.assertEqual(connection["workspace_token"], "qa-workspace-token")
        self.assertEqual(connection["member_token"], "qa-member-token")

        # Existing 2.13.0 databases had revocable tokens in legacy plain text.
        # The first safe read upgrades them without breaking the linked workspace.
        self.db.conn.execute(
            "UPDATE workspace_subscription SET cloud_workspace_token = ?, cloud_member_token = ? WHERE id = 1",
            ("legacy-workspace-token", "legacy-member-token"),
        )
        self.db.conn.commit()
        migrated = self.db.cloud_connection()
        self.assertEqual(migrated["workspace_token"], "legacy-workspace-token")
        self.assertEqual(migrated["member_token"], "legacy-member-token")
        upgraded = self.db.conn.execute(
            "SELECT cloud_workspace_token, cloud_member_token FROM workspace_subscription WHERE id = 1"
        ).fetchone()
        self.assertTrue(upgraded[0].startswith(expected_prefix))
        self.assertTrue(upgraded[1].startswith(expected_prefix))

    def test_saved_team_session_is_available_only_on_the_current_device_profile(self) -> None:
        self.db.save_cloud_connection(
            api_url="https://api.example.invalid",
            workspace_token="qa-workspace-token",
            owner_email="owner@example.invalid",
        )
        self.db.save_cloud_member_session(
            member_id="owner-qa",
            member_token="qa-owner-token",
            member_role="owner",
            member_name="QA Owner",
        )
        self.assertTrue(self.db.has_persisted_team_session())
        self.db.clear_cloud_member_session()
        self.assertFalse(self.db.has_persisted_team_session())

    def test_recurring_expense_is_idempotent_after_schedule_update_interruption(self) -> None:
        vendor_id = self.db.save_vendor({"name": "Recurring QA supplier"})
        run_day = date.today().replace(day=1)
        recurring_id = self.db.save_recurring_expense(
            {
                "vendor_id": vendor_id,
                "name": "QA monthly rent",
                "category": "Ostali troškovi",
                "interval_months": 1,
                "next_run_date": run_day.isoformat(),
                "net_amount": "500",
                "vat_rate": "20",
                "currency": "EUR",
                "payment_term_days": 14,
            }
        )
        self.assertEqual(self.db.run_due_recurring_expenses(through=run_day), 1)
        first_count = self.db.conn.execute(
            "SELECT COUNT(*) FROM vendor_bills WHERE bill_number=?",
            (f"AUTO-{recurring_id}-{run_day:%Y%m%d}",),
        ).fetchone()[0]
        self.assertEqual(first_count, 1)

        # Simulate a stop after the bill was committed but before its template
        # moved forward. The durable run record must recover it, not create a
        # second payable for the same month.
        self.db.conn.execute(
            "UPDATE recurring_expenses SET next_run_date=? WHERE id=?",
            (run_day.isoformat(), recurring_id),
        )
        self.db.conn.commit()
        self.assertEqual(self.db.run_due_recurring_expenses(through=run_day), 0)
        recovered_count = self.db.conn.execute(
            "SELECT COUNT(*) FROM vendor_bills WHERE bill_number=?",
            (f"AUTO-{recurring_id}-{run_day:%Y%m%d}",),
        ).fetchone()[0]
        self.assertEqual(recovered_count, 1)
        next_run_date = self.db.conn.execute(
            "SELECT next_run_date FROM recurring_expenses WHERE id=?", (recurring_id,)
        ).fetchone()[0]
        self.assertGreater(next_run_date, run_day.isoformat())


if __name__ == "__main__":
    unittest.main()
