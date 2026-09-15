"""Invoice-kind transitions using the real Tk controls, without customer data."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delta_fakture_app as app
import delta_fakture_core as core


class InvoiceLineModeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.addCleanup(self.root.destroy)
        previous_language = app.active_ui_language()
        app.set_active_ui_language("sr")
        self.addCleanup(app.set_active_ui_language, previous_language)

        # Build only the real line-entry panel. No MainApp, database, network,
        # invoice numbering, or user workspace is initialized by this harness.
        editor = app.InvoiceEditor.__new__(app.InvoiceEditor)
        tk.Toplevel.__init__(editor, self.root)
        editor.withdraw()
        self.editor = editor
        company = core.default_company_settings()
        company.update(default_currency="EUR", default_vat_rate=.2, vat_regime="standard")
        editor.app = SimpleNamespace(company=company)
        editor.db = SimpleNamespace(
            project_advance_terms=Mock(return_value={
                "contract_net_amount": 10000, "advance_percent": 20,
                "advance_net_amount": 2000,
            }),
            project_advance_invoice_item=Mock(return_value={
                "category": "Ugovorni avans", "description": "Avans 20%",
                "unit": "kom.", "quantity": 1, "unit_price": 2000,
                "discount_percent": 0, "code_stage": "AVANS",
            }),
        )
        editor.invoice_id = None
        editor.project_map = {"QA Project [1]": 1}
        editor.advance_source_combo = None
        editor.item_data = []
        editor.payment_rows = []
        editor.attachment_rows = []
        values = {
            "invoice_kind": app.INVOICE_KIND_LABELS["standard"],
            "project_id": "QA Project [1]", "currency": "EUR",
            "invoice_number": "", "status_code": "draft",
            **{key: "0" for key in (
                "discount_total", "retention_percent", "advance_amount",
                "subtotal", "tax_base", "vat_total", "vat_caption",
                "gross_total", "retention_amount", "due_before_paid",
                "paid_total", "balance_total",
            )},
        }
        editor.vars = {key: tk.StringVar(editor, value=value) for key, value in values.items()}
        editor.quick_vars = {key: tk.StringVar(editor) for key in (
            "category", "description", "unit", "quantity", "unit_price",
            "discount_percent", "code_stage",
        )}
        editor.advance_lines_notice_var = tk.StringVar(editor)
        editor.advance_amount_entry = ttk.Entry(editor, textvariable=editor.vars["advance_amount"])
        editor.lines_tab = ttk.Frame(editor)
        editor.payments_tree = ttk.Treeview(editor)
        editor.attachments_tree = ttk.Treeview(editor)
        editor._build_lines_tab()

    def choose_kind(self, kind, language="sr"):
        app.set_active_ui_language(language)
        self.editor.vars["invoice_kind"].set(app.tr(app.INVOICE_KIND_LABELS[kind], language))
        self.editor._on_invoice_kind_changed()
        self.assertEqual(self.editor._selected_invoice_kind(), kind)

    def assert_regular_controls(self):
        editor = self.editor
        self.assertEqual(editor.lines_quick_frame.winfo_manager(), "pack")
        self.assertEqual(editor.lines_toolbar.winfo_manager(), "pack")
        self.assertEqual(editor.advance_lines_notice.winfo_manager(), "")
        siblings = editor.lines_tree.master.pack_slaves()
        self.assertEqual(siblings[-3:], [editor.lines_quick_frame, editor.lines_toolbar, editor.lines_tree])

    def add_work_line(self):
        values = {"category": "Rad", "description": "QA work", "unit": "kom.",
                  "quantity": "2", "unit_price": "125,50", "discount_percent": "0"}
        for key, value in values.items():
            self.editor.quick_vars[key].set(value)
        self.editor.quick_add_button.invoke()

    def test_initial_standard_mode_keeps_controls_in_order(self):
        self.choose_kind("standard")
        self.assert_regular_controls()

    def test_advance_to_standard_refreshes_rows_totals_and_accepts_new_items(self):
        editor = self.editor
        self.choose_kind("advance")
        self.assertEqual(editor.lines_quick_frame.winfo_manager(), "")
        self.assertEqual(editor.lines_toolbar.winfo_manager(), "")
        self.assertEqual(editor.lines_tree.item("1", "values")[2], "Avans 20%")
        self.assertEqual(editor.vars["gross_total"].get(), app.fmt_money(2400, "EUR"))

        self.choose_kind("standard")
        self.assert_regular_controls()
        self.assertEqual(editor.lines_tree.get_children(), ())
        self.assertEqual(editor.vars["gross_total"].get(), app.fmt_money(0, "EUR"))
        self.add_work_line()
        self.assertEqual(len(editor.item_data), 1)
        self.assertEqual(editor.lines_tree.item("1", "values")[2], "QA work")
        self.assertEqual(editor.vars["gross_total"].get(), app.fmt_money(301.2, "EUR"))
        self.assertEqual(editor.vars["status_code"].get(), "draft")

    def test_repeated_transitions_preserve_existing_items_and_unsaved_input(self):
        editor = self.editor
        self.choose_kind("standard")
        self.add_work_line()
        existing = deepcopy(editor.item_data)
        editor.quick_vars["description"].set("Unsaved material")
        for language in app.UI_LANGUAGE_LABELS:
            for target in ("standard", "final", "standard"):
                with self.subTest(language=language, target=target):
                    self.choose_kind("advance", language)
                    with patch.object(app.messagebox, "showinfo") as notice:
                        editor.add_quick_line()
                        notice.assert_called_once()
                    self.choose_kind(target, language)
                    self.assert_regular_controls()
                    self.assertEqual(editor.item_data, existing)
                    self.assertEqual(editor.quick_vars["description"].get(), "Unsaved material")
                    self.assertEqual(editor.lines_tree.item("1", "values")[2], "QA work")
                    self.assertEqual(editor.vars["gross_total"].get(), app.fmt_money(301.2, "EUR"))
                    self.assertEqual(str(editor.advance_amount_entry.cget("state")),
                                     "readonly" if target == "final" else "normal")

    def test_missing_advance_project_does_not_prevent_return_to_standard(self):
        self.editor.vars["project_id"].set("")
        self.choose_kind("advance")
        self.assertEqual(self.editor.lines_tree.get_children(), ())
        self.choose_kind("standard")
        self.assert_regular_controls()


if __name__ == "__main__":
    unittest.main()
