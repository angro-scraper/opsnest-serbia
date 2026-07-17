"""Single source of truth for OpsNest subscription packages.

This module deliberately contains only public product rules. It is safe to use
from the Windows app, the cloud API and the public pricing site generator.
"""

from __future__ import annotations

from typing import Any


TRIAL_DAYS = 7
TRIAL_FEATURE_PLAN = "pro"


PLAN_CATALOG: dict[str, dict[str, Any]] = {
    "starter": {
        "code": "starter",
        "name": "Starter",
        "price_eur": "9.90",
        "projects": 3,
        "issued_invoices_per_month": 30,
        "pdf_imports_per_month": 30,
        "seats": 1,
        "features": {"core_invoicing", "pdf_excel", "project_dashboard", "email_support"},
        "highlights": [
            "1 local owner seat",
            "3 active projects",
            "30 issued invoices per month",
            "30 PDF imports per month",
            "Invoices, PDF/Excel, payments and project dashboard",
            "E-mail support",
        ],
    },
    "business": {
        "code": "business",
        "name": "Business",
        "price_eur": "19.90",
        "projects": 15,
        "issued_invoices_per_month": 250,
        "pdf_imports_per_month": 250,
        "seats": 5,
        "features": {
            "core_invoicing",
            "pdf_excel",
            "project_dashboard",
            "email_support",
            "project_budget",
            "bank_matching",
            "vat_evidence",
            "accountant_export",
            "team_users",
            "invoice_approval",
            "custom_invoice_templates",
        },
        "highlights": [
            "Up to 5 team seats",
            "15 active projects",
            "250 issued invoices per month",
            "250 PDF imports per month",
            "Project budgets, bank statement matching and VAT evidence",
            "Accountant export and priority e-mail support",
            "Owner approval before issuing and custom invoice templates",
        ],
    },
    "pro": {
        "code": "pro",
        "name": "Pro",
        "price_eur": "29.90",
        "projects": None,
        "issued_invoices_per_month": None,
        "pdf_imports_per_month": None,
        "seats": 20,
        "features": {
            "core_invoicing",
            "pdf_excel",
            "project_dashboard",
            "email_support",
            "project_budget",
            "bank_matching",
            "vat_evidence",
            "accountant_export",
            "advanced_pdf_import",
            "priority_support",
            "team_users",
            "invoice_approval",
            "custom_invoice_templates",
        },
        "highlights": [
            "Up to 20 team seats",
            "Unlimited active projects",
            "Unlimited issued invoices and PDF imports",
            "All Business tools plus advanced PDF processing",
            "Owner approval and custom invoice templates",
            "Priority support and complete project reporting",
        ],
    },
}


def normalize_plan_code(value: object) -> str:
    code = str(value or "starter").strip().lower()
    return code if code in PLAN_CATALOG else "starter"


def effective_plan_code(status: object, plan_code: object) -> str:
    """A verified seven-day trial exposes every paid feature before purchase."""
    if str(status or "").strip().lower() == "trial":
        return TRIAL_FEATURE_PLAN
    return normalize_plan_code(plan_code)


def plan_details(plan_code: object) -> dict[str, Any]:
    return dict(PLAN_CATALOG[normalize_plan_code(plan_code)])


def plan_limit(plan_code: object, key: str) -> int | None:
    value = plan_details(plan_code).get(key)
    return int(value) if isinstance(value, int) else None


def plan_includes(plan_code: object, feature: str) -> bool:
    return feature in set(plan_details(plan_code).get("features") or set())


def public_plan_catalog() -> list[dict[str, Any]]:
    """Return JSON-safe package data for clients and the public price page."""
    return [
        {
            "code": code,
            "name": data["name"],
            "price_eur": data["price_eur"],
            "projects": data["projects"],
            "issued_invoices_per_month": data["issued_invoices_per_month"],
            "pdf_imports_per_month": data["pdf_imports_per_month"],
            "seats": data["seats"],
            "highlights": list(data["highlights"]),
        }
        for code, data in PLAN_CATALOG.items()
    ]
