"""Local Serbian SEF readiness checks.

This module deliberately does not send documents to the Serbian eInvoice
system and does not claim SEF compliance.  It gives the invoice workflow one
strict, testable place to decide whether the business data is complete enough
to start a later UBL/API implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ISSUED_STATUSES = {"issued", "partial", "paid", "due"}
UNIT_CODES = {
    "kom": "C62",
    "piece": "C62",
    "pcs": "C62",
    "kg": "KGM",
    "g": "GRM",
    "h": "HUR",
    "sat": "HUR",
    "dan": "DAY",
    "m": "MTR",
    "m2": "MTK",
    "m²": "MTK",
    "m3": "MTQ",
    "m³": "MTQ",
    "l": "LTR",
}
UBL_INVOICE_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
UBL_COMMON_BASIC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
UBL_COMMON_AGGREGATE_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"

ET.register_namespace("", UBL_INVOICE_NS)
ET.register_namespace("cbc", UBL_COMMON_BASIC_NS)
ET.register_namespace("cac", UBL_COMMON_AGGREGATE_NS)


@dataclass(frozen=True)
class SefReadinessReport:
    """A local pre-flight result; it is not a SEF response or validation."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def is_ready_for_technical_mapping(self) -> bool:
        return not self.errors

    def format_for_user(self, *, context: str = "SEF") -> str:
        lines = [f"{context} priprema — lokalna provera podataka"]
        if self.errors:
            lines += ["", "Potrebno je dopuniti:"]
            lines += [f"• {item}" for item in self.errors]
        else:
            lines += ["", "Osnovni podaci su spremni za sledeći tehnički korak."]
        if self.warnings:
            lines += ["", "Napomene:"]
            lines += [f"• {item}" for item in self.warnings]
        lines += [
            "",
            (
                "Ovo nije slanje na SEF niti potvrda usklađenosti. "
                "UBL profil i API će se uvoditi tek kroz SEF demo okruženje."
                if context == "SEF"
                else "Ovo nije slanje e-fakture niti potvrda usklađenosti za bilo koju državu."
            ),
        ]
        return "\n".join(lines)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive(value: Any) -> bool:
    try:
        return Decimal(str(value or 0)) > Decimal("0")
    except (InvalidOperation, ValueError):
        return False


def _amount(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _decimal_text(value: Any, *, places: str = "0.01") -> str:
    return format(_amount(value).quantize(Decimal(places)), "f")


def _quantity_text(value: Any) -> str:
    try:
        return format(Decimal(str(value or 0)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return "0"


def _unit_code(value: Any) -> str:
    return UNIT_CODES.get(_text(value).lower(), "C62")


def _tag(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _basic(parent: ET.Element, name: str, value: Any, **attributes: str) -> ET.Element:
    element = ET.SubElement(parent, _tag(UBL_COMMON_BASIC_NS, name), attributes)
    element.text = _text(value)
    return element


def _aggregate(parent: ET.Element, name: str) -> ET.Element:
    return ET.SubElement(parent, _tag(UBL_COMMON_AGGREGATE_NS, name))


def _party(
    parent: ET.Element,
    *,
    name: Any,
    identifier: Any,
    identifier_scheme: str,
    address: Any,
    country_code: str = "",
) -> None:
    party = _aggregate(parent, "Party")
    identity = _aggregate(party, "PartyIdentification")
    _basic(identity, "ID", identifier, schemeID=identifier_scheme)
    party_name = _aggregate(party, "PartyName")
    _basic(party_name, "Name", name)
    postal = _aggregate(party, "PostalAddress")
    _basic(postal, "StreetName", address)
    if country_code:
        country = _aggregate(postal, "Country")
        _basic(country, "IdentificationCode", country_code)
    tax_scheme = _aggregate(party, "PartyTaxScheme")
    _basic(tax_scheme, "CompanyID", identifier, schemeID=identifier_scheme)
    scheme = _aggregate(tax_scheme, "TaxScheme")
    _basic(scheme, "ID", "VAT")


def build_ubl_21_draft(invoice: dict[str, Any]) -> bytes:
    """Build a review-only generic UBL 2.1 invoice.

    This is deliberately *not* a national e-invoice customization. It provides a
    structurally useful, deterministic starting point for the later SEF demo
    mapping, without encouraging a user to upload an unvalidated document.
    """
    report = einvoice_readiness(invoice)
    if report.errors:
        raise ValueError("UBL nacrt se ne može napraviti dok provera e-fakture ima greške.")
    if _positive(invoice.get("retention_percent")):
        raise ValueError("UBL nacrt još ne podržava garancijsko zadržavanje. Sačuvajte fakturu za regionalno mapiranje.")

    company = dict(invoice.get("company") or {})
    currency = _text(invoice.get("currency")).upper() or "EUR"
    tax_rate = _amount(invoice.get("vat_rate")) * Decimal("100")
    tax_base = _amount(invoice.get("tax_base"))
    vat_total = _amount(invoice.get("vat_total"))
    gross_total = _amount(invoice.get("gross_total"))
    discount_total = _amount(invoice.get("discount_total"))
    advance_amount = _amount(invoice.get("advance_amount"))
    payable_amount = _amount(invoice.get("balance_total"))
    company_uses_vat = bool(_text(company.get("vat_number")))
    customer_uses_vat = bool(_text(invoice.get("customer_vat")))
    company_id = _text(company.get("vat_number")) or _text(company.get("eik"))
    customer_id = _text(invoice.get("customer_vat")) or _text(invoice.get("customer_eik"))

    root = ET.Element(_tag(UBL_INVOICE_NS, "Invoice"))
    _basic(root, "CustomizationID", "urn:opsnest:ubl-2.1-draft:1.0")
    _basic(root, "ProfileID", "OpsNest review-only UBL draft")
    _basic(root, "ID", invoice.get("invoice_number"))
    _basic(root, "IssueDate", invoice.get("issue_date"))
    _basic(root, "DueDate", invoice.get("due_date"))
    _basic(root, "InvoiceTypeCode", "380")
    _basic(root, "DocumentCurrencyCode", currency)
    _basic(root, "Note", "Review-only UBL 2.1 draft. Not validated for a national e-invoice system.")
    if _text(invoice.get("tax_event_date")):
        _basic(root, "TaxPointDate", invoice.get("tax_event_date"))

    supplier = _aggregate(root, "AccountingSupplierParty")
    _party(
        supplier,
        name=company.get("name"),
        identifier=company_id,
        identifier_scheme="VAT" if company_uses_vat else "ORG",
        address=company.get("address"),
        country_code=_text(company.get("country_code")).upper(),
    )
    customer = _aggregate(root, "AccountingCustomerParty")
    _party(
        customer,
        name=invoice.get("customer_name"),
        identifier=customer_id,
        identifier_scheme="VAT" if customer_uses_vat else "ORG",
        address=invoice.get("customer_address"),
    )

    payment_means = _aggregate(root, "PaymentMeans")
    _basic(payment_means, "PaymentMeansCode", "30")
    _basic(payment_means, "PaymentID", invoice.get("invoice_number"))
    if _text(company.get("iban")):
        account = _aggregate(payment_means, "PayeeFinancialAccount")
        _basic(account, "ID", company.get("iban"))

    if discount_total > 0:
        allowance = _aggregate(root, "AllowanceCharge")
        _basic(allowance, "ChargeIndicator", "false")
        _basic(allowance, "AllowanceChargeReason", "Invoice discount")
        _basic(allowance, "Amount", _decimal_text(discount_total), currencyID=currency)
        allowance_tax = _aggregate(allowance, "TaxCategory")
        _basic(allowance_tax, "ID", "S")
        _basic(allowance_tax, "Percent", _decimal_text(tax_rate))
        allowance_scheme = _aggregate(allowance_tax, "TaxScheme")
        _basic(allowance_scheme, "ID", "VAT")

    tax_total = _aggregate(root, "TaxTotal")
    _basic(tax_total, "TaxAmount", _decimal_text(vat_total), currencyID=currency)
    tax_subtotal = _aggregate(tax_total, "TaxSubtotal")
    _basic(tax_subtotal, "TaxableAmount", _decimal_text(tax_base), currencyID=currency)
    _basic(tax_subtotal, "TaxAmount", _decimal_text(vat_total), currencyID=currency)
    tax_category = _aggregate(tax_subtotal, "TaxCategory")
    _basic(tax_category, "ID", "S")
    _basic(tax_category, "Percent", _decimal_text(tax_rate))
    tax_scheme = _aggregate(tax_category, "TaxScheme")
    _basic(tax_scheme, "ID", "VAT")

    totals = _aggregate(root, "LegalMonetaryTotal")
    _basic(totals, "LineExtensionAmount", _decimal_text(invoice.get("subtotal")), currencyID=currency)
    _basic(totals, "TaxExclusiveAmount", _decimal_text(tax_base), currencyID=currency)
    _basic(totals, "TaxInclusiveAmount", _decimal_text(gross_total), currencyID=currency)
    if advance_amount > 0:
        _basic(totals, "PrepaidAmount", _decimal_text(advance_amount), currencyID=currency)
    _basic(totals, "PayableAmount", _decimal_text(payable_amount), currencyID=currency)

    for line_number, item in enumerate(list(invoice.get("items") or []), start=1):
        line = _aggregate(root, "InvoiceLine")
        quantity = _amount(item.get("quantity"))
        line_net = _amount(item.get("net_amount"))
        _basic(line, "ID", line_number)
        _basic(line, "InvoicedQuantity", _quantity_text(quantity), unitCode=_unit_code(item.get("unit")))
        _basic(line, "LineExtensionAmount", _decimal_text(line_net), currencyID=currency)
        item_node = _aggregate(line, "Item")
        _basic(item_node, "Name", item.get("description"))
        item_tax = _aggregate(item_node, "ClassifiedTaxCategory")
        _basic(item_tax, "ID", "S")
        _basic(item_tax, "Percent", _decimal_text(tax_rate))
        item_scheme = _aggregate(item_tax, "TaxScheme")
        _basic(item_scheme, "ID", "VAT")
        price = _aggregate(line, "Price")
        unit_price = line_net / quantity if quantity else Decimal("0")
        _basic(price, "PriceAmount", _decimal_text(unit_price), currencyID=currency)

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def export_ubl_21_draft(invoice: dict[str, Any], output_path: Path) -> Path:
    """Write the review-only UBL draft to a user-selected local archive path."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(build_ubl_21_draft(invoice))
    return destination


def einvoice_readiness(invoice: dict[str, Any]) -> SefReadinessReport:
    """Check country-neutral business data for a structured e-invoice draft.

    This is intentionally not a legal or technical compliance validator for
    any country. Country-specific adapters extend these checks separately.
    """
    errors: list[str] = []
    warnings: list[str] = []
    company = dict(invoice.get("company") or {})

    if not _text(company.get("name")):
        errors.append("Unesite naziv izdavaoca u podacima firme.")
    if not _text(company.get("address")):
        errors.append("Unesite adresu izdavaoca u podacima firme.")
    if not (_text(company.get("eik")) or _text(company.get("vat_number"))):
        errors.append("Unesite PIB ili PDV broj izdavaoca u podacima firme.")

    status = _text(invoice.get("status_code")).lower()
    if status not in ISSUED_STATUSES:
        errors.append("Faktura mora biti izdata nakon odobrenja pre pripreme e-fakture.")
    if not _text(invoice.get("invoice_number")):
        errors.append("Faktura nema broj.")
    if not _text(invoice.get("issue_date")):
        errors.append("Unesite datum izdavanja.")
    if not _text(invoice.get("tax_event_date")):
        errors.append("Unesite datum poreskog događaja.")
    if not _text(invoice.get("due_date")):
        errors.append("Unesite rok plaćanja.")

    if not _text(invoice.get("customer_name")):
        errors.append("Unesite naziv kupca.")
    if not _text(invoice.get("customer_address")):
        errors.append("Unesite adresu kupca.")
    if not (_text(invoice.get("customer_eik")) or _text(invoice.get("customer_vat"))):
        errors.append("Unesite PIB ili PDV broj kupca.")

    items = list(invoice.get("items") or [])
    if not items:
        errors.append("Dodajte najmanje jednu stavku fakture.")
    for index, item in enumerate(items, start=1):
        if not _text(item.get("description")):
            errors.append(f"Stavka {index} nema opis.")
        if not _text(item.get("unit")):
            errors.append(f"Stavka {index} nema jedinicu mere.")
        if not _positive(item.get("quantity")):
            errors.append(f"Stavka {index} mora imati količinu veću od nule.")
        if not _positive(item.get("net_amount")):
            errors.append(f"Stavka {index} mora imati neto iznos veći od nule.")

    return SefReadinessReport(tuple(errors), tuple(warnings))


def sef_readiness(invoice: dict[str, Any]) -> SefReadinessReport:
    """Add Serbian SEF-specific checks on top of generic e-invoice data."""
    base = einvoice_readiness(invoice)
    errors = list(base.errors)
    warnings = list(base.warnings)
    company = dict(invoice.get("company") or {})
    if _text(company.get("country_code")).upper() != "RS":
        errors.append("U podacima firme izaberite državu registracije: RS — Srbija.")
    currency = _text(invoice.get("currency")).upper()
    if currency and currency != "RSD":
        warnings.append(
            "Faktura je u stranoj valuti. Srbija SEF zahteva i poreske međuzbirove u RSD; "
            "to će biti deo narednog UBL mapiranja."
        )
    if _positive(invoice.get("advance_amount")):
        warnings.append("Avans zahteva posebno SEF mapiranje i referencu na avansni dokument.")
    if _positive(invoice.get("retention_percent")):
        warnings.append("Garancijsko zadržavanje zahteva proveru poslovnog i SEF mapiranja pre slanja.")
    if _positive(invoice.get("discount_total")):
        warnings.append("Popust će biti posebno mapiran po stavkama u UBL dokumentu.")

    return SefReadinessReport(tuple(errors), tuple(warnings))


def bulgaria_en16931_readiness(invoice: dict[str, Any]) -> SefReadinessReport:
    """Prepare a Bulgarian invoice for a possible EN 16931 / B2G exchange.

    Bulgaria has no general B2B e-invoicing clearance API in this workflow.
    This therefore remains a local completeness check for a structured UBL
    document, never a CAIS EPP upload, legal validation, or delivery receipt.
    """
    base = einvoice_readiness(invoice)
    errors = list(base.errors)
    warnings = list(base.warnings)
    company = dict(invoice.get("company") or {})
    if _text(company.get("country_code")).upper() != "BG":
        errors.append("U podacima firme izaberite državu registracije: BG — Bugarska.")
    if not _text(company.get("vat_number")):
        warnings.append(
            "Izdavalac nema unet PDV broj. Ako je firma PDV obveznik ili ga javni naručilac traži, "
            "dopunite PDV broj pre razmene dokumenta."
        )
    if not _text(company.get("iban")):
        warnings.append("IBAN nije unet; kupac ili javni naručilac može zahtevati podatke za plaćanje.")
    warnings.extend(
        [
            "Za Bugarsku nema opšteg B2B/B2C mandata za e-fakture; strukturirani dokument se šalje samo kada ga kupac ili javni naručilac zahteva.",
            "Ovo je lokalna priprema UBL 2.1 dokumenta za EN 16931 tehnički pregled. Nije CAIS EPP predaja niti sertifikovana EN 16931 validacija.",
        ]
    )
    return SefReadinessReport(tuple(errors), tuple(warnings))
