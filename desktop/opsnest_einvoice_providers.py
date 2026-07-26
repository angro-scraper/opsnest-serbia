"""Registry of optional country-specific e-invoice connectors.

The invoice workflow and generic UBL draft live outside this registry. Each
provider is a small adapter for one public system and its own lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EInvoiceProvider:
    code: str
    country_code: str
    display_name: str
    integration_stage: str
    supports_demo_connection: bool = False
    supports_submission: bool = False


SERBIA_SEF = EInvoiceProvider(
    code="serbia-sef",
    country_code="RS",
    display_name="Serbia SEF",
    integration_stage="demo-connection",
    supports_demo_connection=True,
    supports_submission=False,
)

BULGARIA_EN16931 = EInvoiceProvider(
    code="bulgaria-en16931",
    country_code="BG",
    display_name="Bulgaria EN 16931 / B2G preparation",
    integration_stage="b2g-document-preparation",
    supports_demo_connection=False,
    supports_submission=False,
)

# All country choices in the current company profile have a safe e-invoice
# route.  These profiles intentionally prepare a structured EN 16931 / UBL
# document only; they do not pretend to be a state API connector.
COUNTRY_EN16931_NAMES = {
    "AL": "Albania", "AT": "Austria", "BA": "Bosnia and Herzegovina", "BE": "Belgium", "CZ": "Czechia", "DE": "Germany",
    "ES": "Spain", "FR": "France", "GB": "United Kingdom", "GR": "Greece",
    "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "ME": "Montenegro", "MK": "North Macedonia", "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SI": "Slovenia", "SK": "Slovakia", "XK": "Kosovo", "OTHER": "Other country",
}


def _structured_ubl_provider(country_code: str, name: str) -> EInvoiceProvider:
    return EInvoiceProvider(
        code=f"{country_code.lower()}-en16931-ubl",
        country_code=country_code,
        display_name=f"{name} EN 16931 / UBL preparation",
        integration_stage="structured-ubl-preparation",
        supports_demo_connection=False,
        supports_submission=False,
    )


_PROVIDERS = (
    SERBIA_SEF,
    BULGARIA_EN16931,
    *(_structured_ubl_provider(code, name) for code, name in COUNTRY_EN16931_NAMES.items()),
)


def provider_for_country(country_code: object) -> Optional[EInvoiceProvider]:
    code = str(country_code or "").strip().upper()
    return next((provider for provider in _PROVIDERS if provider.country_code == code), None)
