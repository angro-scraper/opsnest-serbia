"""Private, rule-based financial insights for every OpsNest business profile.

The module intentionally works only with dashboard aggregates. It produces
explainable operational prompts and never acts as tax, legal, lending, or
investment advice. A future opt-in generative AI provider can use the same
small, anonymised summary rather than raw invoices.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class FinancialInsight:
    priority: str
    title: str
    observation: str
    suggested_action: str


def _amount(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _percent(numerator: Decimal, denominator: Decimal) -> int:
    if denominator <= 0:
        return 0
    return int((numerator * Decimal("100") / denominator).quantize(Decimal("1")))


def financial_insights(stats: dict[str, Any]) -> list[FinancialInsight]:
    """Return transparent, country-neutral insights from dashboard aggregates."""
    issued = _amount(stats.get("month_issued"))
    paid = _amount(stats.get("month_paid"))
    balance = _amount(stats.get("month_balance"))
    overdue = _amount(stats.get("overdue_total"))
    invoice_count = int(stats.get("invoice_count") or 0)
    output_vat = _amount(stats.get("month_vat"))
    debtors = list(stats.get("debtors") or [])
    insights: list[FinancialInsight] = []

    if invoice_count == 0:
        return [FinancialInsight(
            "info",
            "Još nema dovoljno podataka",
            "U izabranom periodu nema izdatih faktura za analizu.",
            "Unesite ili izdajte fakture, pa ponovo otvorite savetnika.",
        )]

    if overdue > 0:
        overdue_share = _percent(overdue, balance)
        priority = "high" if overdue_share >= 50 else "medium"
        insights.append(FinancialInsight(
            priority,
            "Dospela potraživanja traže pažnju",
            f"{overdue_share}% trenutno otvorenog iznosa je dospelo.",
            "Pregledajte najstarije fakture, potvrdite uplate i pošaljite podsetnik samo kupcima sa stvarnim dugom.",
        ))

    if issued > 0:
        collection_rate = _percent(paid, issued)
        if collection_rate < 60:
            insights.append(FinancialInsight(
                "medium",
                "Naplata je sporija od fakturisanja",
                f"U izabranom periodu evidentirano je približno {collection_rate}% naplate u odnosu na fakturisano.",
                "Proverite rokove plaćanja za nove fakture i dogovorite prioritet naplate sa najvećim dužnicima.",
            ))

    if balance > 0 and debtors:
        largest = dict(debtors[0] or {})
        largest_balance = _amount(largest.get("balance"))
        largest_share = _percent(largest_balance, balance)
        if largest_share >= 40:
            name = str(largest.get("customer_name") or "Jedan kupac")
            insights.append(FinancialInsight(
                "medium",
                "Naplata zavisi od jednog kupca",
                f"{name} čini približno {largest_share}% ukupno otvorenog iznosa.",
                "Pratite dogovoreni datum naplate i izbegnite dodatno odlaganje obaveza dok se stanje ne razjasni.",
            ))

    if output_vat > 0:
        insights.append(FinancialInsight(
            "info",
            "Planirajte PDV likvidnost",
            "Dashboard prikazuje obračunati izlazni PDV za izabrani period.",
            "Uporedite ga sa ulaznim PDV-om i poreskom evidencijom zajedno sa knjigovođom pre prijave ili plaćanja.",
        ))

    if not insights:
        insights.append(FinancialInsight(
            "good",
            "Nema hitnog signala u izabranom periodu",
            "Nisu pronađeni dospeli iznosi niti izražena koncentracija otvorenih potraživanja.",
            "Nastavite redovno da potvrđujete uplate i proveravate dashboard pre novih većih obaveza.",
        ))
    order = {"high": 0, "medium": 1, "info": 2, "good": 3}
    return sorted(insights, key=lambda item: order.get(item.priority, 9))


def ai_financial_summary(
    stats: dict[str, Any],
    *,
    currency: str,
    business_profile: str,
    language: str,
    finance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the only accounting payload allowed to leave the desktop app.

    This intentionally excludes invoice rows, customer names, project names,
    notes, PDFs and attachments. The cloud receives a small numeric snapshot
    and can therefore ask the AI for operational guidance without exposing a
    customer's accounting archive.
    """
    finance = finance or {}
    issued = _amount(stats.get("month_issued"))
    paid = _amount(stats.get("month_paid"))
    outstanding = _amount(stats.get("month_balance"))
    overdue = _amount(stats.get("overdue_total"))
    debtors = list(stats.get("debtors") or [])
    largest_balance = _amount(dict(debtors[0] or {}).get("balance")) if debtors else Decimal("0")
    profile = str(business_profile or "general").strip().lower()
    return {
        "language": str(language or "en").strip().lower()[:10],
        "business_profile": profile if profile in {"construction", "general"} else "general",
        "currency": str(currency or "EUR").strip().upper()[:8],
        "invoice_count": max(0, int(stats.get("invoice_count") or 0)),
        "issued_total": float(max(Decimal("0"), issued)),
        "paid_total": float(max(Decimal("0"), paid)),
        "outstanding_total": float(max(Decimal("0"), outstanding)),
        "overdue_total": float(max(Decimal("0"), overdue)),
        "output_vat_total": float(max(Decimal("0"), _amount(stats.get("month_vat")))),
        "collection_rate_percent": _percent(max(Decimal("0"), paid), issued),
        "overdue_share_percent": _percent(max(Decimal("0"), overdue), outstanding),
        "top_debtor_share_percent": _percent(max(Decimal("0"), largest_balance), outstanding),
        # Only company-level aggregate amounts leave the device.  They make
        # liquidity advice useful without exposing vendor, project or bank-row data.
        "expense_total": float(max(Decimal("0"), _amount(finance.get("expense_total")))),
        "open_payables_total": float(max(Decimal("0"), _amount(finance.get("open_payables_total")))),
        "cash_opening_total": float(max(Decimal("0"), _amount(finance.get("cash_opening_total")))),
        "cash_forecast_closing_total": float(_amount(finance.get("cash_forecast_closing_total"))),
        "cash_flow_horizon_days": max(1, min(365, int(finance.get("cash_flow_horizon_days") or 30))),
    }
