"""Small, deliberately read-only client for the Serbian SEF demo API.

The first endpoint is the version check.  It proves that a user-generated
demo API key works without transmitting invoices, XML documents, company
records, or any other accounting data.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SEF_DEMO_BASE_URL = "https://efakturadev.mfin.gov.rs"
SEF_PRODUCTION_BASE_URL = "https://efaktura.mfin.gov.rs"


class SefApiError(RuntimeError):
    """A safe, user-displayable error that never includes the API key."""


def get_sef_version(api_key: str, *, environment: str = "demo", timeout_seconds: int = 20) -> str:
    """Call the harmless SEF version endpoint with a user-supplied API key.

    `environment` is intentionally restricted to demo by the desktop UI for
    now.  Production sending will be introduced only after demo validation.
    """
    secret = str(api_key or "").strip()
    if not secret:
        raise SefApiError("Unesite SEF demo API ključ.")
    base_url = SEF_DEMO_BASE_URL if environment == "demo" else SEF_PRODUCTION_BASE_URL
    request = Request(
        f"{base_url}/api/publicApi/getEfakturaVersion",
        headers={"Accept": "application/json, text/plain", "ApiKey": secret, "User-Agent": "OpsNest-SEF-Demo-Check/1.0"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=max(5, min(60, int(timeout_seconds)))) as response:
            raw = response.read().decode("utf-8", errors="replace").strip()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise SefApiError("SEF demo nije prihvatio API ključ. Proverite ključ i ovlašćenje za demo firmu.") from exc
        raise SefApiError(f"SEF demo je vratio HTTP status {exc.code}. Pokušajte kasnije ili proverite demo nalog.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise SefApiError("Veza sa SEF demo okruženjem nije uspela. Proverite internet vezu i pokušajte ponovo.") from exc

    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        payload = raw
    if isinstance(payload, dict):
        version = payload.get("Version") or payload.get("version")
        if version is not None:
            return str(version)
    if isinstance(payload, str) and payload:
        return payload
    raise SefApiError("SEF demo je odgovorio bez prepoznatljive verzije sistema.")
