"""Country, language, currency and company-form regressions (isolated data)."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delta_fakture_app as app
import delta_fakture_core as core
from opsnest_company_i18n import COMPANY_COPY, FINANCE_CONTROL_COPY


class CompanyChoicesTests(unittest.TestCase):
    def test_every_country_has_a_safe_export_language_in_every_ui_language(self):
        for lang in app.UI_LANGUAGE_LABELS:
            values = app.country_option_values(lang)
            self.assertEqual(len(values), len(core.COUNTRY_VAT_DEFAULTS))
            self.assertEqual({app.country_code_from_option(v) for v in values}, set(core.COUNTRY_VAT_DEFAULTS))
            self.assertTrue(values[1].startswith("BG - "))
            for country in core.COUNTRY_VAT_DEFAULTS:
                with self.subTest(country=country, language=lang):
                    summary = app.company_automation_summary(country, "6201", lang)
                    self.assertIn(core.default_currency_for_country(country), summary)
                    self.assertIn(app.default_document_language_for_country(country), app.INVOICE_DOCUMENT_LANGUAGE_LABELS)

    def test_bulgaria_uses_euro_with_lev_available_for_historical_documents(self):
        self.assertEqual(core.default_currency_for_country("BG"), "EUR")
        self.assertIn("BGN", core.SUPPORTED_CURRENCIES)
        self.assertEqual(app.currency_option_label("EUR", "bg"), "EUR — Евро (€)")
        for code in core.SUPPORTED_CURRENCIES:
            for lang in app.UI_LANGUAGE_LABELS:
                self.assertEqual(app.currency_code_from_option(app.currency_option_label(code, lang)), code)

    def test_all_translated_business_choices_decode_to_the_original_code(self):
        mappings = (
            (app.BUSINESS_PROFILE_LABELS, app.business_profile_code_from_label),
            (app.VAT_REGIME_LABELS, app.vat_regime_code_from_label),
            (app.EINVOICE_ROUTE_LABELS, app.einvoice_route_code_from_label),
            (app.SERBIA_LEGAL_FORM_LABELS, app.serbia_legal_form_code_from_label),
            (app.SERBIA_TAX_MODE_LABELS, app.serbia_tax_mode_code_from_label),
        )
        for labels, decode in mappings:
            for code, source in labels.items():
                for lang in app.UI_LANGUAGE_LABELS:
                    with self.subTest(code=code, language=lang):
                        self.assertEqual(decode(app.tr(source, lang)), code)

    def test_company_copy_is_complete_and_preserves_format_placeholders(self):
        from string import Formatter
        labels = (*COMPANY_COPY, *FINANCE_CONTROL_COPY)
        self.assertEqual(len(labels), len({row[0] for row in labels}))
        for row in labels:
            self.assertEqual(len(row), 5)
            fields = lambda value: {name for _, name, _, _ in Formatter().parse(value) if name}
            for caption in row:
                self.assertTrue(caption)
                self.assertEqual(fields(row[0]), fields(caption))

    def test_zero_vat_and_same_day_terms_survive_save_reload(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"DELTA_FAKTURE_ROOT": folder}):
            previous_root = core._root_dir_cache
            core._root_dir_cache = None
            db = core.Database(Path(folder) / "company.db")
            try:
                for regime in core.VAT_REGIME_CODES:
                    db.save_company({"country_code": "RS", "default_currency": "RSD", "vat_regime": regime,
                                     "default_vat_rate": 0, "payment_term_days": 0})
                    saved = db.get_company()
                    self.assertEqual(saved["default_vat_rate"], 0)
                    self.assertEqual(saved["payment_term_days"], 0)
                db.save_company({"country_code": "BG", "default_currency": "EUR", "vat_regime": "out_of_scope", "default_vat_rate": .2})
                self.assertEqual(db.get_company()["default_vat_rate"], 0)
                for country, currency, language in (("RS", "RSD", "sr"), ("BG", "EUR", "bg"), ("DE", "EUR", "en")):
                    db.save_company({"country_code": country, "default_currency": currency, "vat_regime": "out_of_scope", "default_vat_rate": 0})
                    snapshot = db._prepare_invoice_snapshot({})
                    self.assertEqual(snapshot["currency"], currency)
                    self.assertEqual(snapshot["document_language"], language)
                    self.assertEqual(snapshot["vat_rate"], 0)
                db.save_company({"vat_regime": "standard", "default_vat_rate": .2})
                # A manually entered zero on a particular invoice is also valid.
                self.assertEqual(db._prepare_invoice_snapshot({"vat_rate": 0})["vat_rate"], 0)
            finally:
                db.close()
                core._root_dir_cache = previous_root


class CompanyFormTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.previous_language = app.active_ui_language()
        app.set_active_ui_language("sr")
        self.company = core.default_company_settings()
        self.company.update(country_code="BG", default_currency="EUR", ui_language="bg", name="QA COMPANY")
        self.host = SimpleNamespace(db=SimpleNamespace(get_company=lambda: dict(self.company)), open_company_registration=lambda: None)
        self.form = app.CompanyTab(self.root, self.host)
        self.form.pack(fill="both", expand=True)
        self.form.refresh()

    def tearDown(self):
        app.set_active_ui_language(self.previous_language)
        self.root.destroy()

    def choose_country(self, country):
        self.form.vars["country_code"].set(app.country_option_label(country, self.form._company_language()))
        self.form._apply_company_automation()

    def choose_language(self, lang):
        self.form.vars["ui_language"].set(app.language_label(lang))
        self.form._sync_company_language()

    def test_full_country_language_matrix_keeps_codes_and_unsaved_data(self):
        self.form.vars["name"].set("Unsaved Serbian name / България")
        for country in core.COUNTRY_VAT_DEFAULTS:
            self.choose_country(country)
            for lang in app.UI_LANGUAGE_LABELS:
                self.choose_language(lang)
                with self.subTest(country=country, language=lang):
                    self.assertEqual(app.country_code_from_option(self.form.vars["country_code"].get()), country)
                    self.assertEqual(app.currency_code_from_option(self.form.vars["default_currency"].get()), core.default_currency_for_country(country))
                    self.assertEqual(self.form.vars["name"].get(), "Unsaved Serbian name / България")
                    self.assertEqual(self.form.save_button.cget("text"), app.tr("Sačuvaj", lang))
                    self.assertEqual(self.form.vars["payment_method"].get(), app.tr("Bankovni prenos", lang))
                    self.assertEqual(tuple(self.form.country_combo.cget("values")), tuple(app.country_option_values(lang)))

    def test_translated_options_are_not_replaced_by_generic_localization(self):
        self.choose_country("RS")
        self.choose_language("bg")
        self.form.vars["vat_regime"].set(app.tr("Van sistema PDV-a", "bg"))
        self.form._apply_vat_regime()
        for lang in ("sr", "en", "de", "ru", "bg"):
            self.choose_language(lang)
            app.localize_widget_tree(self.form, lang)
            self.assertEqual(app.vat_regime_code_from_label(self.form.vars["vat_regime"].get()), "out_of_scope")
            self.assertEqual(self.form.vars["default_vat_rate"].get(), "0.00")

    def test_vat_change_and_activity_typing_do_not_reset_currency_or_delivery_route(self):
        self.choose_country("RS")
        self.form.vars["default_currency"].set(app.currency_option_label("EUR", "bg"))
        self.form.vars["einvoice_route"].set(app.tr(app.EINVOICE_ROUTE_LABELS["external_portal"], "bg"))
        self.form.vars["activity_code"].set("6201")
        self.assertEqual(app.business_profile_code_from_label(self.form.vars["business_profile"].get()), "digital_creative")
        self.form._apply_vat_regime()
        self.assertEqual(app.currency_code_from_option(self.form.vars["default_currency"].get()), "EUR")
        self.assertEqual(app.einvoice_route_code_from_label(self.form.vars["einvoice_route"].get()), "external_portal")

    def test_country_specific_fields_and_identifier_follow_country(self):
        self.choose_country("RS")
        self.assertEqual(self.form._identifier_label._opsnest_source_text, "PIB")
        self.assertTrue(all(widget.winfo_manager() == "grid" for row in self.form._serbia_form_widgets.values() for widget in row))
        self.choose_country("BG")
        self.assertEqual(self.form._identifier_label.cget("text"), "ЕИК / БУЛСТАТ")
        self.assertTrue(all(not widget.winfo_manager() for row in self.form._serbia_form_widgets.values() for widget in row))

    def test_all_static_company_form_texts_have_bulgarian_translations(self):
        from opsnest_company_i18n import COMPANY_TRANSLATIONS
        allowed = {"IBAN", "BIC / SWIFT"}
        pending = [self.form]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            source = getattr(widget, "_opsnest_source_text", "")
            if source and not ("textvariable" in widget.keys() and widget.cget("textvariable")):
                self.assertTrue(source in COMPANY_TRANSLATIONS["bg"] or source in allowed, source)

    def test_save_is_outside_scrolling_region_and_form_stacks(self):
        self.form._reflow_company_form(SimpleNamespace(width=940))
        self.assertTrue(self.form._form_stacked)
        self.assertEqual(self.form._form_panels[1].grid_info()["row"], 1)
        self.assertNotIn(str(self.form.form_scroll), str(self.form.save_button))
        self.form._reflow_company_form(SimpleNamespace(width=1600))
        self.assertFalse(self.form._form_stacked)
        self.assertEqual(self.form._form_panels[1].grid_info()["column"], 1)

    def test_registration_uses_the_same_choices_and_keeps_zero_vat(self):
        self.company.update(vat_regime="out_of_scope", default_vat_rate=0)
        self.host.db.get_subscription = lambda: {"status": "active"}
        with patch.object(app, "center_window"), patch.object(app, "fit_dialog_to_content"), patch.object(tk.Toplevel, "grab_set"):
            dialog = app.CompanyRegistrationDialog(self.root, self.host)
            dialog.withdraw()
            try:
                self.assertEqual(dialog.vars["default_vat_rate"].get(), "0")
                for country in core.COUNTRY_VAT_DEFAULTS:
                    dialog.vars["country_code"].set(app.country_option_label(country, "bg"))
                    dialog._apply_company_automation()
                    self.assertEqual(app.currency_code_from_option(dialog.vars["default_currency"].get()), core.default_currency_for_country(country))
                    self.assertEqual(dialog.vars["default_vat_rate"].get(), "0.00")
                dialog.vars["ui_language"].set("Srpski")
                dialog._sync_company_language()
                self.assertEqual(dialog.vars["vat_regime"].get(), "Van sistema PDV-a")
            finally:
                dialog.destroy()

    def test_saving_translated_form_persists_business_codes_not_captions(self):
        from unittest.mock import Mock
        self.host.db.save_company = Mock()
        self.host.apply_language = Mock()
        self.host.after = Mock()
        self.host.send_due_payment_reminders_silently = Mock()
        self.choose_country("BG")
        self.choose_language("bg")
        self.form.vars["vat_regime"].set(app.tr("Van sistema PDV-a", "bg"))
        self.form._apply_vat_regime()
        self.form.vars["payment_term_days"].set("0")
        with patch.object(app.messagebox, "showinfo"):
            self.form.save()
        saved = self.host.db.save_company.call_args.args[0]
        self.assertEqual(saved["country_code"], "BG")
        self.assertEqual(saved["default_currency"], "EUR")
        self.assertEqual(saved["vat_regime"], "out_of_scope")
        self.assertEqual(saved["default_vat_rate"], 0)
        self.assertEqual(saved["payment_term_days"], 0)
        self.assertEqual(saved["payment_method"], "Banka")
        self.assertEqual(saved["ui_language"], "bg")


if __name__ == "__main__":
    unittest.main()
