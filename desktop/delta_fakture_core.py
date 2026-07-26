from __future__ import annotations

import base64
import ctypes
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
import hashlib
import hmac
import re
import secrets
import unicodedata
import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from ctypes import wintypes
except ImportError:  # pragma: no cover - ctypes on supported Python builds includes wintypes
    wintypes = None

import xml.etree.ElementTree as ET

from opsnest_plans import effective_plan_code, plan_details, plan_includes, plan_limit


APP_NAME = "OpsNest"
ROOT_ENV_VAR = "DELTA_FAKTURE_ROOT"
DEFAULT_ROOT = Path(r"C:\OpsNest")
LEGACY_ROOT = Path(r"C:\OpsNestLegacy")
USER_DEFAULT_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / APP_NAME

APP_DIR = Path(__file__).resolve().parent
ASSETS_DIR = APP_DIR / "assets"
# Every new installation uses the neutral first-party template.  A legacy
# workbook is only retained as a runtime fallback for already-installed older
# clients; it is never distributed in the controlled source package.
TEMPLATE_XLSX = ASSETS_DIR / "opsnest_invoice_template.xlsx"
if not TEMPLATE_XLSX.exists():
    TEMPLATE_XLSX = ASSETS_DIR / "invoice_template.xlsx"
LOGO_FILE = ASSETS_DIR / "logo.jpg"

DATA_DIR_NAME = "Data"
INVOICE_DIR_NAME = "Fakture"
ATTACHMENTS_DIR_NAME = "Prilozi"
BACKUP_DIR_NAME = "Backup"
PROJECTS_DIR_NAME = "Projekti"
PROJECT_DOCUMENTS_DIR_NAME = "Dokumenti"
PROJECT_OUTPUT_INVOICES_DIR_NAME = "Izlazne_fakture"
PROJECT_INPUT_INVOICES_DIR_NAME = "Ulazni_racuni"
CREDIT_NOTES_DIR_NAME = "Odobrenja"
PROJECT_REPORTS_DIR_NAME = "Izvestaji"
PROJECT_VAT_REPORTS_DIR_NAME = "PDV_evidencija"
PROJECT_ACCOUNTANT_REPORTS_DIR_NAME = "Knjigovodja"
UNASSIGNED_PROJECT_DIR_NAME = "Bez_projekta"

DEFAULT_CURRENCY = "EUR"
DEFAULT_VAT_RATE = Decimal("0.20")
PROJECT_INVOICE_SUFFIX_DIGITS = 9
DEFAULT_EXCHANGE_RATE = Decimal("1.95583")
DEFAULT_PAYMENT_TERM_DAYS = 14
DEFAULT_ISSUE_PLACE = "Sofija"
DEFAULT_PAYMENT_METHOD = "Banka"
DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_SECURITY = "tls"
LOCAL_LOGIN_ITERATIONS = 210_000
TRIAL_DAYS = 7
SUBSCRIPTION_WRITE_STATUSES = {"trial", "active", "legacy"}
SMTP_SECURITY_OPTIONS = ["none", "tls", "ssl"]
LOCAL_CLOUD_SECRET_PREFIX = "dpapi:v1:"
LOCAL_CLOUD_SECRET_DEV_PREFIX = "plain:v1:"


class LocalCloudSecretUnavailable(RuntimeError):
    """A cloud session belongs to a different Windows profile or is corrupted."""


class _WindowsDataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _windows_data_blob(value: bytes) -> tuple[_WindowsDataBlob, Any]:
    """Return a DATA_BLOB and retain its backing memory for the native API call."""
    backing = ctypes.create_string_buffer(value, len(value))
    return (
        _WindowsDataBlob(
            len(value),
            ctypes.cast(backing, ctypes.POINTER(ctypes.c_byte)),
        ),
        backing,
    )


def _dpapi_crypt(value: bytes, *, decrypt: bool) -> bytes:
    """Protect or unprotect a local session with the current Windows user profile."""
    if os.name != "nt" or wintypes is None:
        raise LocalCloudSecretUnavailable("Windows zaštita lokalne sesije nije dostupna.")
    if not value:
        return b""

    crypt32 = ctypes.WinDLL("Crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32", use_last_error=True)
    protect = crypt32.CryptProtectData
    unprotect = crypt32.CryptUnprotectData
    for function in (protect, unprotect):
        function.restype = wintypes.BOOL
    protect.argtypes = [
        ctypes.POINTER(_WindowsDataBlob), ctypes.c_wchar_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_WindowsDataBlob),
    ]
    unprotect.argtypes = [
        ctypes.POINTER(_WindowsDataBlob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_WindowsDataBlob),
    ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    source, source_backing = _windows_data_blob(value)
    _ = source_backing  # keep the input buffer alive for the native call
    protected = _WindowsDataBlob()
    if decrypt:
        success = unprotect(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(protected))
        action = "otključavanje"
    else:
        success = protect(
            ctypes.byref(source), "OpsNest local cloud session", None, None, None, 0, ctypes.byref(protected)
        )
        action = "zaštita"
    if not success:
        error_code = ctypes.get_last_error()
        raise LocalCloudSecretUnavailable(f"Windows {action} lokalne sesije nije uspela ({error_code}).")
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        if protected.pbData:
            kernel32.LocalFree(ctypes.cast(protected.pbData, ctypes.c_void_p))


def _protect_local_cloud_secret(value: str) -> str:
    """Store revocable cloud credentials encrypted at rest on Windows."""
    plain = str(value or "").strip()
    if not plain:
        return ""
    if os.name == "nt":
        encrypted = _dpapi_crypt(plain.encode("utf-8"), decrypt=False)
        return LOCAL_CLOUD_SECRET_PREFIX + base64.b64encode(encrypted).decode("ascii")
    # Desktop production targets Windows.  The explicit development marker avoids
    # pretending that a non-Windows test database is encrypted.
    return LOCAL_CLOUD_SECRET_DEV_PREFIX + base64.b64encode(plain.encode("utf-8")).decode("ascii")


def _read_local_cloud_secret(value: str) -> tuple[str, bool, bool]:
    """Return plaintext, whether legacy migration is required, and whether re-login is required."""
    stored = str(value or "").strip()
    if not stored:
        return "", False, False
    if stored.startswith(LOCAL_CLOUD_SECRET_PREFIX):
        try:
            raw = base64.b64decode(stored[len(LOCAL_CLOUD_SECRET_PREFIX) :], validate=True)
            return _dpapi_crypt(raw, decrypt=True).decode("utf-8"), False, False
        except (ValueError, UnicodeDecodeError, LocalCloudSecretUnavailable):
            return "", False, True
    if stored.startswith(LOCAL_CLOUD_SECRET_DEV_PREFIX):
        if os.name == "nt":
            # A copied development database must not grant a Windows cloud session.
            return "", False, True
        try:
            raw = base64.b64decode(stored[len(LOCAL_CLOUD_SECRET_DEV_PREFIX) :], validate=True)
            return raw.decode("utf-8"), False, False
        except (ValueError, UnicodeDecodeError):
            return "", False, True
    # Releases before 2.13.1 stored the revocable token as plain text.  It is
    # upgraded transparently the first time this Windows profile opens it.
    return stored, True, False

# Company setup must be useful outside the original construction use case.
# These are sensible invoice defaults, not tax advice: every invoice keeps an
# editable VAT rate and the owner remains responsible for local compliance.
SUPPORTED_CURRENCIES = ("EUR", "RSD", "BGN", "BAM", "MKD", "ALL", "GBP", "CHF", "USD", "PLN", "CZK", "HUF", "RON")
BUSINESS_PROFILE_CODES = {
    "general",
    "construction",
    "professional_services",
    "retail_trade",
    "hospitality",
    "manufacturing",
    "digital_creative",
    "nonprofit",
}
VAT_REGIME_CODES = {"standard", "exempt", "reverse_charge", "out_of_scope"}
EINVOICE_ROUTE_CODES = {"automatic", "structured_ubl", "external_portal"}

# The selected country supplies a sensible starting point for a new company.
# It is intentionally a default only: invoices retain an editable VAT rate so
# owners can apply reverse charge, exemptions, or a reduced rate when required.
COUNTRY_VAT_DEFAULTS: dict[str, Decimal] = {
    "AL": Decimal("0.20"),
    "BA": Decimal("0.17"),
    "BG": Decimal("0.20"),
    "DE": Decimal("0.19"),
    "AT": Decimal("0.20"),
    "BE": Decimal("0.21"),
    "HR": Decimal("0.25"),
    "CZ": Decimal("0.21"),
    "FR": Decimal("0.20"),
    "GR": Decimal("0.24"),
    "HU": Decimal("0.27"),
    "IE": Decimal("0.23"),
    "IT": Decimal("0.22"),
    "ME": Decimal("0.21"),
    "MK": Decimal("0.18"),
    "NL": Decimal("0.21"),
    "PL": Decimal("0.23"),
    "PT": Decimal("0.23"),
    "RO": Decimal("0.19"),
    "RS": Decimal("0.20"),
    "SK": Decimal("0.23"),
    "SI": Decimal("0.22"),
    "ES": Decimal("0.21"),
    "GB": Decimal("0.20"),
    "XK": Decimal("0.18"),
    "OTHER": Decimal("0.20"),
}

COUNTRY_CURRENCY_DEFAULTS: dict[str, str] = {
    "AL": "ALL",
    "BA": "BAM",
    # Bulgaria adopted the euro on 1 January 2026. BGN remains available for
    # historic documents and imported statements, but new Bulgarian companies
    # must start with EUR as their operational currency.
    "BG": "EUR",
    "CZ": "CZK",
    "GB": "GBP",
    "HU": "HUF",
    "PL": "PLN",
    "RO": "RON",
    "RS": "RSD",
    "MK": "MKD",
    "ME": "EUR",
    "XK": "EUR",
    "OTHER": "EUR",
}


def normalize_country_code(value: Any) -> str:
    code = str(value or "BG").strip().upper()
    return code if code in COUNTRY_VAT_DEFAULTS else "OTHER"


def default_vat_rate_for_country(value: Any) -> Decimal:
    return COUNTRY_VAT_DEFAULTS[normalize_country_code(value)]


def default_currency_for_country(value: Any) -> str:
    """Return a practical starting invoice currency for a new company."""
    return COUNTRY_CURRENCY_DEFAULTS.get(normalize_country_code(value), "EUR")


def normalize_currency(value: Any, *, fallback: str = DEFAULT_CURRENCY) -> str:
    code = str(value or fallback).strip().upper()
    return code if code in SUPPORTED_CURRENCIES else fallback

CATEGORY_OPTIONS = [
    "Труд",
    "Материали",
    "Механизация",
    "Транспорт",
    "Други",
]

PROJECT_DOCUMENT_TYPES = ["input", "output"]
PROJECT_COST_GROUPS = ["Rad", "Materijal", "Plate", "Ostali troškovi"]
PROJECT_INCOME_GROUPS = ["Radovi", "Materijal", "Mehanizacija", "Transport", "Ostali prihodi"]

UNIT_OPTIONS = ["бр.", "м", "m²", "m³", "кг", "т", "час", "компл."]

PAYMENT_METHOD_OPTIONS = [
    "Банков превод",
    "В брой",
    "Карта",
    "Комбинирано",
]

STATUS_CODES = ["draft", "pending_approval", "approved", "issued", "partial", "paid", "due", "cancelled"]
STATUS_LABELS = {
    "draft": "Nacrt",
    "pending_approval": "Na proveri",
    "approved": "Odobrena",
    "issued": "Izdata",
    "partial": "Delimično plaćena",
    "paid": "Plaćena",
    "due": "Dospela",
    "cancelled": "Stornirana",
}

INVOICE_KINDS = ("standard", "advance", "final")
INVOICE_KIND_LABELS = {
    "standard": "Standardni račun",
    "advance": "Avansni račun",
    "final": "Završni račun",
}


def normalize_invoice_kind(value: Any) -> str:
    code = str(value or "standard").strip().lower()
    return code if code in INVOICE_KINDS else "standard"

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

ET.register_namespace("", NS_MAIN)
ET.register_namespace("r", NS_REL)
ET.register_namespace("xdr", NS_DRAWING)


_root_dir_cache: Optional[Path] = None


def _is_writable_root(path: Path) -> bool:
    """Check whether the selected data folder can be used before opening SQLite."""
    probe = path / ".opsnest-write-probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def get_root_dir() -> Path:
    global _root_dir_cache
    if _root_dir_cache is not None:
        return _root_dir_cache

    configured_root = os.environ.get(ROOT_ENV_VAR)
    if configured_root:
        candidate = Path(configured_root)
        if not _is_writable_root(candidate):
            raise OSError(f"OpsNest nema pristup folderu podataka: {candidate}")
        _root_dir_cache = candidate
        return candidate

    # Prefer prior database locations, then fall back to a user-writable folder.
    candidates = [DEFAULT_ROOT, LEGACY_ROOT, USER_DEFAULT_ROOT]
    for candidate in candidates:
        database = candidate / DATA_DIR_NAME / "delta_fakture.db"
        if database.exists() and _is_writable_root(candidate):
            _root_dir_cache = candidate
            return candidate
    for candidate in candidates:
        if _is_writable_root(candidate):
            _root_dir_cache = candidate
            return candidate

    raise OSError("OpsNest ne može da napravi folder za lokalne podatke.")


def data_dir() -> Path:
    return get_root_dir() / DATA_DIR_NAME


def invoice_dir() -> Path:
    return get_root_dir() / INVOICE_DIR_NAME


def attachments_dir() -> Path:
    return get_root_dir() / ATTACHMENTS_DIR_NAME


def backup_dir() -> Path:
    return get_root_dir() / BACKUP_DIR_NAME


def ensure_app_folders() -> None:
    for path in [get_root_dir(), data_dir(), invoice_dir(), attachments_dir(), backup_dir()]:
        path.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today_iso() -> str:
    return date.today().isoformat()


def parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def format_date(value: Any) -> str:
    parsed = parse_date(value)
    return parsed.strftime("%d.%m.%Y") if parsed else ""


def iso_from_date(value: Any) -> Optional[str]:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


def decimal_from(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money_round(value: Any) -> Decimal:
    return decimal_from(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_float(value: Any) -> float:
    return float(money_round(value))


def format_currency(value: Any, currency: str = DEFAULT_CURRENCY) -> str:
    amount = money_round(value)
    text = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {currency}"


def format_number_short(value: Any) -> str:
    amount = money_round(value)
    if amount == amount.to_integral():
        return f"{int(amount):,}".replace(",", ".")
    return f"{amount:,.2f}".replace(",", ".")


def safe_filename(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in (" ", "-", "_", ".") else "_" for ch in text)
    cleaned = cleaned.strip().replace(" ", "_")
    return cleaned or "file"


def project_archive_folder_name(project_id: int, project_name: str) -> str:
    """Return the permanent, readable on-disk name for one project."""
    label = safe_filename(project_name).strip("._") or "Projekat"
    return f"P-{int(project_id):06d}_{label[:72]}"


def status_label(code: str) -> str:
    return STATUS_LABELS.get(code, code)


def normalize_status(value: str) -> str:
    if not value:
        return "draft"
    lowered = value.strip().lower()
    for code, label in STATUS_LABELS.items():
        if lowered in {code, label.lower()}:
            return code
    return "draft"


def bank_match_key(value: Any) -> str:
    """Normalize account names and references for conservative bank matching."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def payment_method_default() -> str:
    return DEFAULT_PAYMENT_METHOD


def default_company_settings() -> dict[str, Any]:
    return {
        "id": 1,
        "name": "",
        "eik": "",
        "vat_number": "",
        "address": "",
        "phone": "",
        "email": "",
        "bank_name": "",
        "iban": "",
        "bic": "",
        "director_name": "",
        "logo_path": "",
        "business_profile": "general",
        "country_code": "OTHER",
        "default_vat_rate": float(DEFAULT_VAT_RATE),
        "default_currency": DEFAULT_CURRENCY,
        "vat_regime": "standard",
        "einvoice_route": "automatic",
        "payment_term_days": DEFAULT_PAYMENT_TERM_DAYS,
        "exchange_rate": float(DEFAULT_EXCHANGE_RATE),
        "issue_place": DEFAULT_ISSUE_PLACE,
        "payment_method": DEFAULT_PAYMENT_METHOD,
        "smtp_host": "",
        "smtp_port": DEFAULT_SMTP_PORT,
        "smtp_security": DEFAULT_SMTP_SECURITY,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_from_name": "",
        "smtp_from_email": "",
        "smtp_reply_to": "",
        "auto_payment_reminders": 0,
        "payment_reminder_interval_days": 7,
        "ui_language": "sr",
        "onboarding_completed": 0,
        "team_invoice_approval_required": 1,
        # A zero amount deliberately means that an owner-only approval ceiling
        # is disabled.  The amount is always interpreted in the company's
        # primary currency; foreign-currency bills are escalated instead of
        # being converted using an unverified FX rate.
        "vendor_bill_owner_approval_threshold": 0.0,
        "login_email": "",
        "login_pin_salt": "",
        "login_pin_hash": "",
        "next_credit_note_seq": 1,
        "updated_at": now_iso(),
    }


def default_subscription_state() -> dict[str, Any]:
    """Return the local mirror of the workspace subscription state.

    The billing server will become the source of truth when Stripe is connected.
    Until then, this record gives a new workspace a clear, auditable trial window
    without placing any card or payment information in the desktop database.
    """
    return {
        "id": 1,
        "workspace_id": str(uuid.uuid4()),
        "status": "not_started",
        "plan_code": "starter",
        "trial_started_at": "",
        "trial_ends_at": "",
        "billing_provider": "",
        "external_subscription_id": "",
        "last_verified_at": "",
        "cloud_api_url": "",
        "cloud_workspace_token": "",
        "cloud_owner_email": "",
        "cloud_member_id": "",
        "cloud_member_token": "",
        "cloud_member_role": "",
        "cloud_member_name": "",
        "cloud_sync_revision": 0,
        "cloud_sync_sha256": "",
        "cloud_last_sync_at": "",
        "cloud_last_error": "",
        "updated_at": now_iso(),
    }


class SubscriptionAccessError(ValueError):
    """Raised when a subscription in read-only mode attempts a business change."""


class PlanLimitError(ValueError):
    """Raised when a paid package does not include a requested capability."""


def default_project_snapshot() -> dict[str, Any]:
    return {
        "project_name": "",
        "site_address": "",
        "contract_no": "",
        "protocol_no": "",
        "period_from": None,
        "period_to": None,
        "order_reference": "",
    }


def default_customer_snapshot() -> dict[str, Any]:
    return {
        "customer_name": "",
        "customer_eik": "",
        "customer_vat": "",
        "customer_address": "",
        "customer_contact": "",
        "customer_phone": "",
        "customer_email": "",
        "customer_payment_terms": DEFAULT_PAYMENT_TERM_DAYS,
    }


def invoice_number_from_seq(seq: int) -> str:
    return f"{seq:010d}"


def credit_note_number_from_seq(seq: int) -> str:
    """Return a separate, sequential number for a formal credit note."""
    return f"ODOB-{seq:010d}"


def project_invoice_number(prefix: Any, local_sequence: Any) -> str:
    """Create a project-owned number, e.g. prefix 2 + local 1 = 2000000001."""
    project_prefix = str(prefix or "").strip()
    if not project_prefix.isdigit() or int(project_prefix) <= 0:
        raise ValueError("Oznaka bloka faktura mora biti pozitivan broj.")
    sequence = int(local_sequence or 0)
    maximum = 10**PROJECT_INVOICE_SUFFIX_DIGITS - 1
    if sequence < 1 or sequence > maximum:
        raise ValueError("Lokalni redni broj fakture je van dozvoljenog opsega.")
    return f"{project_prefix}{sequence:0{PROJECT_INVOICE_SUFFIX_DIGITS}d}"


def ensure_logo_source() -> Optional[Path]:
    if LOGO_FILE.exists():
        return LOGO_FILE
    if TEMPLATE_XLSX.exists():
        try:
            with zipfile.ZipFile(TEMPLATE_XLSX) as zf:
                media = [name for name in zf.namelist() if name.startswith("xl/media/")]
                if not media:
                    return None
                first = media[0]
                data = zf.read(first)
                LOGO_FILE.parent.mkdir(parents=True, exist_ok=True)
                LOGO_FILE.write_bytes(data)
                return LOGO_FILE
        except Exception:
            return None
    return None


def template_defaults() -> dict[str, Any]:
    """Return neutral defaults without embedding a previous customer's identity."""
    return {
        "company": {
            "name": "",
            "default_vat_rate": float(DEFAULT_VAT_RATE),
            "default_currency": DEFAULT_CURRENCY,
            "payment_term_days": DEFAULT_PAYMENT_TERM_DAYS,
            "exchange_rate": float(DEFAULT_EXCHANGE_RATE),
            "issue_place": DEFAULT_ISSUE_PLACE,
            "payment_method": DEFAULT_PAYMENT_METHOD,
        }
    }


def number_to_words_bg(value: Any, currency: str = "EUR") -> str:
    amount = money_round(value)
    whole = int(amount)
    cents = int((amount - Decimal(whole)) * 100)

    def under_20(n: int) -> str:
        words = {
            0: "нула",
            1: "едно",
            2: "две",
            3: "три",
            4: "четири",
            5: "пет",
            6: "шест",
            7: "седем",
            8: "осем",
            9: "девет",
            10: "десет",
            11: "единадесет",
            12: "дванадесет",
            13: "тринадесет",
            14: "четиринадесет",
            15: "петнадесет",
            16: "шестнадесет",
            17: "седемнадесет",
            18: "осемнадесет",
            19: "деветнадесет",
        }
        return words[n]

    def tens(n: int) -> str:
        words = {
            2: "двадесет",
            3: "тридесет",
            4: "четиридесет",
            5: "петдесет",
            6: "шестдесет",
            7: "седемдесет",
            8: "осемдесет",
            9: "деветдесет",
        }
        return words[n]

    def hundreds(n: int) -> str:
        words = {
            1: "сто",
            2: "двеста",
            3: "триста",
            4: "четиристотин",
            5: "петстотин",
            6: "шестстотин",
            7: "седемстотин",
            8: "осемстотин",
            9: "деветстотин",
        }
        return words[n]

    def chunk_to_words(n: int) -> str:
        parts: list[str] = []
        h = n // 100
        t = (n // 10) % 10
        u = n % 10
        if h:
            parts.append(hundreds(h))
        if t == 1:
            if h and u == 0:
                parts.append("и")
            parts.append(under_20(10 + u))
        else:
            if t:
                if h:
                    parts.append("и")
                parts.append(tens(t))
            if u:
                if h or t:
                    parts.append("и")
                parts.append(under_20(u))
        return " ".join(parts) if parts else under_20(0)

    segments: list[str] = []
    millions = whole // 1_000_000
    thousands = (whole // 1000) % 1000
    rest = whole % 1000

    if millions:
        if millions == 1:
            segments.append("един милион")
        else:
            segments.append(f"{chunk_to_words(millions)} милиона")

    if thousands:
        if thousands == 1:
            segments.append("хиляда")
        else:
            seg = chunk_to_words(thousands)
            if seg.endswith("едно"):
                seg = seg[:-4] + "една"
            elif seg.endswith("едно "):
                seg = seg[:-5] + "една"
            segments.append(f"{seg} хиляди")

    if rest or not segments:
        segments.append(chunk_to_words(rest))

    currency_word = "евро" if currency == "EUR" else "лева"
    cent_word = "цента" if cents != 1 else "цент"
    if cents:
        return f"{' '.join(segments)} {currency_word} и {cents:02d} {cent_word}"
    return f"{' '.join(segments)} {currency_word}"


def calculate_line_item(
    quantity: Any,
    unit_price: Any,
    discount_percent: Any = 0,
    vat_rate: Any = DEFAULT_VAT_RATE,
) -> dict[str, Decimal]:
    qty = decimal_from(quantity)
    price = decimal_from(unit_price)
    discount = decimal_from(discount_percent) / Decimal("100")
    vat = decimal_from(vat_rate)
    net = money_round(qty * price * (Decimal("1") - discount))
    vat_amount = money_round(net * vat)
    gross = money_round(net + vat_amount)
    return {
        "quantity": money_round(qty),
        "unit_price": money_round(price),
        "discount_percent": money_round(discount_percent),
        "net_amount": net,
        "vat_amount": vat_amount,
        "gross_amount": gross,
    }


def calculate_invoice_totals(
    items: Iterable[dict[str, Any]],
    *,
    vat_rate: Any = DEFAULT_VAT_RATE,
    discount_total: Any = 0,
    retention_percent: Any = 0,
    advance_amount: Any = 0,
    paid_total: Any = 0,
    currency: str = DEFAULT_CURRENCY,
) -> dict[str, Decimal | str]:
    normalized_items = list(items)
    subtotal = Decimal("0")
    vat_sum = Decimal("0")
    gross_sum = Decimal("0")
    line_count = 0
    for item in normalized_items:
        line = calculate_line_item(
            item.get("quantity"),
            item.get("unit_price"),
            item.get("discount_percent", 0),
            vat_rate,
        )
        subtotal += line["net_amount"]
        vat_sum += line["vat_amount"]
        gross_sum += line["gross_amount"]
        line_count += 1

    discount_total_dec = money_round(discount_total)
    retention_percent_dec = decimal_from(retention_percent)
    advance_amount_dec = money_round(advance_amount)
    paid_total_dec = money_round(paid_total)
    vat_rate_dec = decimal_from(vat_rate)

    tax_base = money_round(max(Decimal("0"), subtotal - discount_total_dec))
    vat_total = money_round(tax_base * vat_rate_dec)
    gross_total = money_round(tax_base + vat_total)
    retention_amount = money_round(gross_total * retention_percent_dec)
    due_before_paid = money_round(max(Decimal("0"), gross_total - retention_amount - advance_amount_dec))
    balance_total = money_round(max(Decimal("0"), due_before_paid - paid_total_dec))

    return {
        "line_count": line_count,
        "subtotal": money_round(subtotal),
        "vat_sum": money_round(vat_sum),
        "gross_sum": money_round(gross_sum),
        "discount_total": discount_total_dec,
        "tax_base": tax_base,
        "vat_total": vat_total,
        "gross_total": gross_total,
        "retention_percent": retention_percent_dec,
        "retention_amount": retention_amount,
        "advance_amount": advance_amount_dec,
        "paid_total": paid_total_dec,
        "due_before_paid": due_before_paid,
        "balance_total": balance_total,
        "words": number_to_words_bg(balance_total if balance_total else gross_total, currency),
    }


def row_to_dict(row: Optional[sqlite3.Row]) -> dict[str, Any]:
    return dict(row) if row is not None else {}


class Database:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        ensure_app_folders()
        self.db_path = Path(db_path) if db_path else data_dir() / "delta_fakture.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.initialize_schema()
        self.bootstrap_defaults()

    def close(self) -> None:
        self.conn.close()

    def initialize_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS company_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL DEFAULT '',
                eik TEXT NOT NULL DEFAULT '',
                vat_number TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                bank_name TEXT NOT NULL DEFAULT '',
                iban TEXT NOT NULL DEFAULT '',
                bic TEXT NOT NULL DEFAULT '',
                director_name TEXT NOT NULL DEFAULT '',
                logo_path TEXT NOT NULL DEFAULT '',
                business_profile TEXT NOT NULL DEFAULT 'general',
                country_code TEXT NOT NULL DEFAULT 'BG',
                default_vat_rate REAL NOT NULL DEFAULT 0.20,
                default_currency TEXT NOT NULL DEFAULT 'EUR',
                vat_regime TEXT NOT NULL DEFAULT 'standard',
                einvoice_route TEXT NOT NULL DEFAULT 'automatic',
                payment_term_days INTEGER NOT NULL DEFAULT 14,
                exchange_rate REAL NOT NULL DEFAULT 1.95583,
                issue_place TEXT NOT NULL DEFAULT 'Sofija',
                payment_method TEXT NOT NULL DEFAULT 'Банков превод',
                smtp_host TEXT NOT NULL DEFAULT '',
                smtp_port INTEGER NOT NULL DEFAULT 587,
                smtp_security TEXT NOT NULL DEFAULT 'tls',
                smtp_username TEXT NOT NULL DEFAULT '',
                smtp_password TEXT NOT NULL DEFAULT '',
                smtp_from_name TEXT NOT NULL DEFAULT '',
                smtp_from_email TEXT NOT NULL DEFAULT '',
                smtp_reply_to TEXT NOT NULL DEFAULT '',
                auto_payment_reminders INTEGER NOT NULL DEFAULT 0,
                payment_reminder_interval_days INTEGER NOT NULL DEFAULT 7,
                ui_language TEXT NOT NULL DEFAULT 'sr',
                onboarding_completed INTEGER NOT NULL DEFAULT 0,
                team_invoice_approval_required INTEGER NOT NULL DEFAULT 1,
                vendor_bill_owner_approval_threshold REAL NOT NULL DEFAULT 0,
                login_email TEXT NOT NULL DEFAULT '',
                login_pin_salt TEXT NOT NULL DEFAULT '',
                login_pin_hash TEXT NOT NULL DEFAULT '',
                next_invoice_seq INTEGER NOT NULL DEFAULT 1,
                next_credit_note_seq INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS workspace_subscription (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                workspace_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'not_started',
                plan_code TEXT NOT NULL DEFAULT 'starter',
                trial_started_at TEXT NOT NULL DEFAULT '',
                trial_ends_at TEXT NOT NULL DEFAULT '',
                billing_provider TEXT NOT NULL DEFAULT '',
                external_subscription_id TEXT NOT NULL DEFAULT '',
                last_verified_at TEXT NOT NULL DEFAULT '',
                cloud_api_url TEXT NOT NULL DEFAULT '',
                cloud_workspace_token TEXT NOT NULL DEFAULT '',
                cloud_owner_email TEXT NOT NULL DEFAULT '',
                cloud_member_id TEXT NOT NULL DEFAULT '',
                cloud_member_token TEXT NOT NULL DEFAULT '',
                cloud_member_role TEXT NOT NULL DEFAULT '',
                cloud_member_name TEXT NOT NULL DEFAULT '',
                cloud_sync_revision INTEGER NOT NULL DEFAULT 0,
                cloud_sync_sha256 TEXT NOT NULL DEFAULT '',
                cloud_last_sync_at TEXT NOT NULL DEFAULT '',
                cloud_last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                eik TEXT NOT NULL DEFAULT '',
                vat_number TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                contact_person TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                payment_term_days INTEGER NOT NULL DEFAULT 14,
                note TEXT NOT NULL DEFAULT '',
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER,
                name TEXT NOT NULL,
                archive_folder TEXT NOT NULL DEFAULT '',
                invoice_prefix TEXT NOT NULL DEFAULT '',
                next_invoice_number INTEGER NOT NULL DEFAULT 1,
                site_address TEXT NOT NULL DEFAULT '',
                contract_no TEXT NOT NULL DEFAULT '',
                contract_net_amount REAL NOT NULL DEFAULT 0,
                advance_percent REAL NOT NULL DEFAULT 0,
                protocol_no TEXT NOT NULL DEFAULT '',
                period_from TEXT NOT NULL DEFAULT '',
                period_to TEXT NOT NULL DEFAULT '',
                order_reference TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS project_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                document_type TEXT NOT NULL DEFAULT 'input',
                cost_group TEXT NOT NULL DEFAULT 'Ostali troškovi',
                document_date TEXT NOT NULL DEFAULT '',
                document_no TEXT NOT NULL DEFAULT '',
                partner_name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                net_amount REAL NOT NULL DEFAULT 0,
                vat_rate REAL NOT NULL DEFAULT 0.20,
                vat_amount REAL NOT NULL DEFAULT 0,
                gross_amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'EUR',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_project_documents_project_date
            ON project_documents(project_id, document_date DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_project_documents_project_type
            ON project_documents(project_id, document_type, cost_group);

            CREATE TABLE IF NOT EXISTS project_budgets (
                project_id INTEGER PRIMARY KEY,
                planned_income_net REAL NOT NULL DEFAULT 0,
                planned_rad_net REAL NOT NULL DEFAULT 0,
                planned_material_net REAL NOT NULL DEFAULT 0,
                planned_plates_net REAL NOT NULL DEFAULT 0,
                planned_other_costs_net REAL NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_seq INTEGER NOT NULL UNIQUE,
                invoice_number TEXT NOT NULL UNIQUE,
                status_code TEXT NOT NULL DEFAULT 'draft',
                invoice_kind TEXT NOT NULL DEFAULT 'standard',
                advance_source_invoice_id INTEGER,
                issue_date TEXT NOT NULL DEFAULT '',
                tax_event_date TEXT NOT NULL DEFAULT '',
                due_date TEXT NOT NULL DEFAULT '',
                customer_id INTEGER,
                project_id INTEGER,
                customer_name TEXT NOT NULL DEFAULT '',
                customer_eik TEXT NOT NULL DEFAULT '',
                customer_vat TEXT NOT NULL DEFAULT '',
                customer_address TEXT NOT NULL DEFAULT '',
                customer_contact TEXT NOT NULL DEFAULT '',
                customer_phone TEXT NOT NULL DEFAULT '',
                customer_email TEXT NOT NULL DEFAULT '',
                customer_payment_term_days INTEGER NOT NULL DEFAULT 14,
                project_name TEXT NOT NULL DEFAULT '',
                site_address TEXT NOT NULL DEFAULT '',
                contract_no TEXT NOT NULL DEFAULT '',
                protocol_no TEXT NOT NULL DEFAULT '',
                period_from TEXT NOT NULL DEFAULT '',
                period_to TEXT NOT NULL DEFAULT '',
                order_reference TEXT NOT NULL DEFAULT '',
                issue_place TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'EUR',
                payment_method TEXT NOT NULL DEFAULT 'Банков превод',
                note TEXT NOT NULL DEFAULT '',
                vat_rate REAL NOT NULL DEFAULT 0.20,
                discount_total REAL NOT NULL DEFAULT 0,
                retention_percent REAL NOT NULL DEFAULT 0,
                advance_amount REAL NOT NULL DEFAULT 0,
                subtotal REAL NOT NULL DEFAULT 0,
                tax_base REAL NOT NULL DEFAULT 0,
                vat_total REAL NOT NULL DEFAULT 0,
                gross_total REAL NOT NULL DEFAULT 0,
                retention_amount REAL NOT NULL DEFAULT 0,
                due_before_paid REAL NOT NULL DEFAULT 0,
                paid_total REAL NOT NULL DEFAULT 0,
                balance_total REAL NOT NULL DEFAULT 0,
                exchange_rate REAL NOT NULL DEFAULT 1.95583,
                invoice_template_id INTEGER NOT NULL DEFAULT 0,
                document_language TEXT NOT NULL DEFAULT '',
                prepared_by_role TEXT NOT NULL DEFAULT '',
                prepared_by_name TEXT NOT NULL DEFAULT '',
                approved_by_name TEXT NOT NULL DEFAULT '',
                approved_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                issued_at TEXT NOT NULL DEFAULT '',
                cancelled_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_invoices_project_status
            ON invoices(project_id, status_code);

            CREATE TABLE IF NOT EXISTS invoice_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                line_no INTEGER NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL DEFAULT '',
                quantity REAL NOT NULL DEFAULT 0,
                unit_price REAL NOT NULL DEFAULT 0,
                discount_percent REAL NOT NULL DEFAULT 0,
                net_amount REAL NOT NULL DEFAULT 0,
                vat_amount REAL NOT NULL DEFAULT 0,
                gross_amount REAL NOT NULL DEFAULT 0,
                code_stage TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                payment_date TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL DEFAULT 0,
                method TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS invoice_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                action_code TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_invoice_audit_log_invoice
            ON invoice_audit_log(invoice_id, id DESC);

            CREATE TABLE IF NOT EXISTS credit_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credit_note_seq INTEGER NOT NULL UNIQUE,
                credit_note_number TEXT NOT NULL UNIQUE,
                source_invoice_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                source_invoice_number TEXT NOT NULL DEFAULT '',
                customer_name TEXT NOT NULL DEFAULT '',
                project_name TEXT NOT NULL DEFAULT '',
                issue_date TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'EUR',
                reason TEXT NOT NULL DEFAULT '',
                net_amount REAL NOT NULL DEFAULT 0,
                vat_rate REAL NOT NULL DEFAULT 0.20,
                vat_amount REAL NOT NULL DEFAULT 0,
                gross_amount REAL NOT NULL DEFAULT 0,
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(source_invoice_id) REFERENCES invoices(id) ON DELETE RESTRICT,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE RESTRICT
            );

            CREATE INDEX IF NOT EXISTS idx_credit_notes_source_invoice
            ON credit_notes(source_invoice_id, id DESC);

            CREATE INDEX IF NOT EXISTS idx_credit_notes_project_date
            ON credit_notes(project_id, issue_date DESC, id DESC);

            CREATE TABLE IF NOT EXISTS bank_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_fingerprint TEXT NOT NULL UNIQUE,
                source_file TEXT NOT NULL DEFAULT '',
                source_row INTEGER NOT NULL DEFAULT 0,
                transaction_date TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT '',
                payer_name TEXT NOT NULL DEFAULT '',
                payer_iban TEXT NOT NULL DEFAULT '',
                reference TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                suggested_invoice_id INTEGER,
                matched_invoice_id INTEGER,
                payment_id INTEGER,
                match_score INTEGER NOT NULL DEFAULT 0,
                match_reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new',
                imported_at TEXT NOT NULL DEFAULT '',
                matched_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(suggested_invoice_id) REFERENCES invoices(id) ON DELETE SET NULL,
                FOREIGN KEY(matched_invoice_id) REFERENCES invoices(id) ON DELETE SET NULL,
                FOREIGN KEY(payment_id) REFERENCES payments(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_bank_transactions_status
            ON bank_transactions(status, transaction_date DESC);

            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                attachment_type TEXT NOT NULL DEFAULT '',
                original_name TEXT NOT NULL DEFAULT '',
                stored_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS company_team_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL COLLATE NOCASE,
                display_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'member',
                status TEXT NOT NULL DEFAULT 'invited',
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(email)
            );

            CREATE INDEX IF NOT EXISTS idx_company_team_members_active
            ON company_team_members(archived, email);
            """
        )
        self.conn.commit()
        self._migrate_company_settings_schema()
        self._migrate_project_schema()
        self._migrate_bank_transactions_schema()
        self._migrate_project_budget_schema()
        self._migrate_credit_note_schema()
        self._migrate_subscription_schema()
        self._migrate_team_schema()
        self._migrate_workflow_schema()
        self._migrate_invoice_template_and_approval_schema()
        self._migrate_invoice_kind_schema()
        self._migrate_einvoice_schema()
        self._migrate_financial_control_schema()

    def _migrate_financial_control_schema(self) -> None:
        """Add the country-neutral owner-finance layer without pretending to file taxes.

        These records are operational source documents: supplier liabilities,
        cash accounts and period locks.  A local chart of accounts or a tax
        filing connector can consume them later without changing user data.
        """
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE,
                tax_id TEXT NOT NULL DEFAULT '',
                vat_number TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                iban TEXT NOT NULL DEFAULT '',
                payment_term_days INTEGER NOT NULL DEFAULT 14,
                note TEXT NOT NULL DEFAULT '',
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(name)
            );
            CREATE INDEX IF NOT EXISTS idx_vendors_active ON vendors(archived, name);

            CREATE TABLE IF NOT EXISTS vendor_bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id INTEGER,
                project_id INTEGER,
                bill_number TEXT NOT NULL DEFAULT '',
                bill_date TEXT NOT NULL DEFAULT '',
                due_date TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'Ostali troškovi',
                description TEXT NOT NULL DEFAULT '',
                net_amount REAL NOT NULL DEFAULT 0,
                vat_rate REAL NOT NULL DEFAULT 0,
                vat_amount REAL NOT NULL DEFAULT 0,
                gross_amount REAL NOT NULL DEFAULT 0,
                paid_amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'EUR',
                status TEXT NOT NULL DEFAULT 'open',
                recurring_template_id INTEGER,
                source_project_document_id INTEGER,
                approval_status TEXT NOT NULL DEFAULT 'approved',
                prepared_by_name TEXT NOT NULL DEFAULT '',
                approved_by_name TEXT NOT NULL DEFAULT '',
                approved_at TEXT NOT NULL DEFAULT '',
                rejection_reason TEXT NOT NULL DEFAULT '',
                rejected_by_name TEXT NOT NULL DEFAULT '',
                rejected_at TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                attachment_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(vendor_id) REFERENCES vendors(id) ON DELETE SET NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL,
                FOREIGN KEY(source_project_document_id) REFERENCES project_documents(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_vendor_bills_due ON vendor_bills(status, due_date, currency);
            CREATE INDEX IF NOT EXISTS idx_vendor_bills_vendor ON vendor_bills(vendor_id, bill_date DESC);
            CREATE TABLE IF NOT EXISTS vendor_bill_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_bill_id INTEGER NOT NULL,
                author_name TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT 'comment',
                comment_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(vendor_bill_id) REFERENCES vendor_bills(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_vendor_bill_comments_bill ON vendor_bill_comments(vendor_bill_id, id DESC);
            CREATE TABLE IF NOT EXISTS recurring_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id INTEGER,
                project_id INTEGER,
                name TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'Ostali troškovi',
                interval_months INTEGER NOT NULL DEFAULT 1,
                next_run_date TEXT NOT NULL DEFAULT '',
                net_amount REAL NOT NULL DEFAULT 0,
                vat_rate REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'EUR',
                payment_term_days INTEGER NOT NULL DEFAULT 14,
                active INTEGER NOT NULL DEFAULT 1,
                note TEXT NOT NULL DEFAULT '',
                last_bill_id INTEGER,
                last_run_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(vendor_id) REFERENCES vendors(id) ON DELETE SET NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL,
                FOREIGN KEY(last_bill_id) REFERENCES vendor_bills(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_recurring_expenses_due ON recurring_expenses(active, next_run_date);
            CREATE TABLE IF NOT EXISTS recurring_expense_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recurring_expense_id INTEGER NOT NULL,
                run_date TEXT NOT NULL,
                vendor_bill_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(recurring_expense_id, run_date),
                FOREIGN KEY(recurring_expense_id) REFERENCES recurring_expenses(id) ON DELETE CASCADE,
                FOREIGN KEY(vendor_bill_id) REFERENCES vendor_bills(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_recurring_expense_runs_template
            ON recurring_expense_runs(recurring_expense_id, run_date);

            CREATE TABLE IF NOT EXISTS cash_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                account_type TEXT NOT NULL DEFAULT 'bank',
                currency TEXT NOT NULL DEFAULT 'EUR',
                opening_balance REAL NOT NULL DEFAULT 0,
                opening_date TEXT NOT NULL DEFAULT '',
                iban_last4 TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_cash_accounts_active ON cash_accounts(active, currency, name);

            CREATE TABLE IF NOT EXISTS accounting_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_from TEXT NOT NULL,
                period_to TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                note TEXT NOT NULL DEFAULT '',
                closed_by TEXT NOT NULL DEFAULT '',
                closed_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(period_from, period_to)
            );
            CREATE INDEX IF NOT EXISTS idx_accounting_periods_status ON accounting_periods(status, period_from, period_to);

            CREATE TABLE IF NOT EXISTS ledger_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                account_type TEXT NOT NULL DEFAULT 'expense',
                active INTEGER NOT NULL DEFAULT 1,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(code)
            );
            CREATE INDEX IF NOT EXISTS idx_ledger_accounts_type ON ledger_accounts(active, account_type, code);

            CREATE TABLE IF NOT EXISTS journal_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_date TEXT NOT NULL DEFAULT '',
                reference TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_journal_entries_date ON journal_entries(status, entry_date, id DESC);

            CREATE TABLE IF NOT EXISTS journal_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                debit_amount REAL NOT NULL DEFAULT 0,
                credit_amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'EUR',
                source_type TEXT NOT NULL DEFAULT 'manual',
                source_id INTEGER,
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(entry_id) REFERENCES journal_entries(id) ON DELETE CASCADE,
                FOREIGN KEY(account_id) REFERENCES ledger_accounts(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_journal_lines_account ON journal_lines(account_id, currency, entry_id);

            CREATE TABLE IF NOT EXISTS financial_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_type TEXT NOT NULL DEFAULT '',
                record_id INTEGER NOT NULL DEFAULT 0,
                action_code TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                previous_hash TEXT NOT NULL DEFAULT '',
                entry_hash TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_financial_audit_record ON financial_audit_log(record_type, record_id, id DESC);

            CREATE TABLE IF NOT EXISTS monthly_control_checklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_key TEXT NOT NULL,
                task_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                note TEXT NOT NULL DEFAULT '',
                completed_by TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(period_key, task_code)
            );
            CREATE INDEX IF NOT EXISTS idx_monthly_control_checklist_period
            ON monthly_control_checklist(period_key, status, task_code);
            """
        )
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(bank_transactions)").fetchall()}
        additions = {
            "direction": "TEXT NOT NULL DEFAULT 'inflow'",
            "cash_account_id": "INTEGER",
            "suggested_vendor_bill_id": "INTEGER",
            "matched_vendor_bill_id": "INTEGER",
        }
        for column, ddl in additions.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE bank_transactions ADD COLUMN {column} {ddl}")
        vendor_bill_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(vendor_bills)").fetchall()}
        vendor_bill_additions = {
            "source_project_document_id": "INTEGER",
            "approval_status": "TEXT NOT NULL DEFAULT 'approved'",
            "prepared_by_name": "TEXT NOT NULL DEFAULT ''",
            "approved_by_name": "TEXT NOT NULL DEFAULT ''",
            "approved_at": "TEXT NOT NULL DEFAULT ''",
            "rejection_reason": "TEXT NOT NULL DEFAULT ''",
            "rejected_by_name": "TEXT NOT NULL DEFAULT ''",
            "rejected_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column, ddl in vendor_bill_additions.items():
            if column not in vendor_bill_columns:
                self.conn.execute(f"ALTER TABLE vendor_bills ADD COLUMN {column} {ddl}")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_vendor_bills_source_project_document "
            "ON vendor_bills(source_project_document_id) WHERE source_project_document_id IS NOT NULL"
        )
        self.conn.execute("UPDATE bank_transactions SET direction = 'inflow' WHERE TRIM(direction) = ''")
        self.conn.commit()
        self._migrate_financial_audit_schema()

    def _migrate_financial_audit_schema(self) -> None:
        """Backfill a tamper-evident chain for existing local audit records.

        Older rows form a verified local baseline at upgrade time. New rows
        append their predecessor's digest, so later direct changes, deletions
        or reordering are detected by the verification command. This is local
        integrity evidence, not a substitute for immutable statutory retention.
        """
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(financial_audit_log)").fetchall()}
        for column, ddl in {
            "previous_hash": "TEXT NOT NULL DEFAULT ''",
            "entry_hash": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in columns:
                self.conn.execute(f"ALTER TABLE financial_audit_log ADD COLUMN {column} {ddl}")
        rows = self.conn.execute(
            "SELECT id, record_type, record_id, action_code, details, created_at FROM financial_audit_log ORDER BY id"
        ).fetchall()
        prior = ""
        for row in rows:
            digest = self._financial_audit_entry_hash(
                previous_hash=prior,
                record_id=int(row["id"]),
                record_type=str(row["record_type"] or ""),
                linked_record_id=int(row["record_id"] or 0),
                action_code=str(row["action_code"] or ""),
                details=str(row["details"] or ""),
                created_at=str(row["created_at"] or ""),
            )
            self.conn.execute(
                "UPDATE financial_audit_log SET previous_hash=?, entry_hash=? WHERE id=?",
                (prior, digest, int(row["id"])),
            )
            prior = digest
        self.conn.commit()

    def _migrate_einvoice_schema(self) -> None:
        """Create a country-neutral local outbox for structured documents."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS einvoice_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                provider_code TEXT NOT NULL DEFAULT 'generic-ubl',
                country_code TEXT NOT NULL DEFAULT '',
                format_code TEXT NOT NULL DEFAULT 'ubl-2.1-draft',
                document_path TEXT NOT NULL DEFAULT '',
                document_hash TEXT NOT NULL DEFAULT '',
                status_code TEXT NOT NULL DEFAULT 'review_only',
                remote_document_id TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                submitted_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
                UNIQUE(invoice_id, provider_code, format_code)
            );
            CREATE INDEX IF NOT EXISTS idx_einvoice_documents_status
            ON einvoice_documents(status_code, updated_at DESC);
            """
        )
        self.conn.commit()

    def _migrate_company_settings_schema(self) -> None:
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(company_settings)").fetchall()
        }
        required = {
            "smtp_host": "TEXT NOT NULL DEFAULT ''",
            "smtp_port": "INTEGER NOT NULL DEFAULT 587",
            "smtp_security": "TEXT NOT NULL DEFAULT 'tls'",
            "smtp_username": "TEXT NOT NULL DEFAULT ''",
            "smtp_password": "TEXT NOT NULL DEFAULT ''",
            "smtp_from_name": "TEXT NOT NULL DEFAULT ''",
            "smtp_from_email": "TEXT NOT NULL DEFAULT ''",
            "smtp_reply_to": "TEXT NOT NULL DEFAULT ''",
            "auto_payment_reminders": "INTEGER NOT NULL DEFAULT 0",
            "payment_reminder_interval_days": "INTEGER NOT NULL DEFAULT 7",
            "business_profile": "TEXT NOT NULL DEFAULT 'general'",
            "country_code": "TEXT NOT NULL DEFAULT 'BG'",
            "vat_regime": "TEXT NOT NULL DEFAULT 'standard'",
            "einvoice_route": "TEXT NOT NULL DEFAULT 'automatic'",
            "ui_language": "TEXT NOT NULL DEFAULT 'sr'",
            "onboarding_completed": "INTEGER NOT NULL DEFAULT 0",
            "team_invoice_approval_required": "INTEGER NOT NULL DEFAULT 1",
            "vendor_bill_owner_approval_threshold": "REAL NOT NULL DEFAULT 0",
            "login_email": "TEXT NOT NULL DEFAULT ''",
            "login_pin_salt": "TEXT NOT NULL DEFAULT ''",
            "login_pin_hash": "TEXT NOT NULL DEFAULT ''",
            "next_credit_note_seq": "INTEGER NOT NULL DEFAULT 1",
        }
        changed = False
        for column, ddl in required.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE company_settings ADD COLUMN {column} {ddl}")
                changed = True
        if changed or self.conn.in_transaction:
            self.conn.commit()

    def _migrate_team_schema(self) -> None:
        """Add the local team roster without changing the single-device login."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS company_team_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL COLLATE NOCASE,
                display_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'member',
                status TEXT NOT NULL DEFAULT 'invited',
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(email)
            );
            CREATE INDEX IF NOT EXISTS idx_company_team_members_active
            ON company_team_members(archived, email);
            """
        )
        self.conn.commit()

    def _migrate_invoice_template_and_approval_schema(self) -> None:
        """Add safe invoice-form references and the owner approval audit trail."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS invoice_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                stored_path TEXT NOT NULL DEFAULT '',
                is_default INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_invoice_templates_active
            ON invoice_templates(archived, is_default, name);

            CREATE TABLE IF NOT EXISTS owner_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_code TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                invoice_id INTEGER,
                project_id INTEGER,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                read_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_owner_notifications_unread
            ON owner_notifications(is_read, created_at DESC);
            """
        )
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(invoices)").fetchall()
        }
        required = {
            "invoice_template_id": "INTEGER NOT NULL DEFAULT 0",
            "document_language": "TEXT NOT NULL DEFAULT ''",
            "prepared_by_role": "TEXT NOT NULL DEFAULT ''",
            "prepared_by_name": "TEXT NOT NULL DEFAULT ''",
            "approved_by_name": "TEXT NOT NULL DEFAULT ''",
            "approved_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column, ddl in required.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE invoices ADD COLUMN {column} {ddl}")
        self.conn.commit()

    def _migrate_invoice_kind_schema(self) -> None:
        """Keep advance and final invoice links available to upgraded local files."""
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(invoices)").fetchall()
        }
        required = {
            "invoice_kind": "TEXT NOT NULL DEFAULT 'standard'",
            "advance_source_invoice_id": "INTEGER",
        }
        for column, ddl in required.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE invoices ADD COLUMN {column} {ddl}")
        self.conn.execute(
            "UPDATE invoices SET invoice_kind = 'standard' WHERE invoice_kind IS NULL OR invoice_kind NOT IN ('standard','advance','final')"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_advance_source ON invoices(advance_source_invoice_id)"
        )
        self.conn.commit()

    def _migrate_workflow_schema(self) -> None:
        """Store workflow helpers separately so historic invoices stay untouched."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS invoice_reminder_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL,
                recipient_email TEXT NOT NULL DEFAULT '',
                reminder_type TEXT NOT NULL DEFAULT 'payment',
                subject TEXT NOT NULL DEFAULT '',
                sent_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_invoice_reminder_log_invoice
            ON invoice_reminder_log(invoice_id, sent_at DESC);

            CREATE TABLE IF NOT EXISTS pdf_partner_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lookup_key TEXT NOT NULL UNIQUE,
                extracted_name TEXT NOT NULL DEFAULT '',
                partner_name TEXT NOT NULL DEFAULT '',
                document_type TEXT NOT NULL DEFAULT 'input',
                cost_group TEXT NOT NULL DEFAULT '',
                vat_rate REAL NOT NULL DEFAULT 0.20,
                usage_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS recurring_invoice_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                customer_id INTEGER,
                name TEXT NOT NULL DEFAULT '',
                interval_months INTEGER NOT NULL DEFAULT 1,
                next_run_date TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL DEFAULT '{}',
                items_json TEXT NOT NULL DEFAULT '[]',
                last_invoice_id INTEGER,
                last_run_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL,
                FOREIGN KEY(last_invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_recurring_invoice_templates_due
            ON recurring_invoice_templates(active, next_run_date);
            """
        )
        self.conn.commit()

    def _migrate_project_schema(self) -> None:
        """Add project archive and numbering fields without touching existing invoices."""
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(projects)").fetchall()
        }
        required = {
            "archive_folder": "TEXT NOT NULL DEFAULT ''",
            "invoice_prefix": "TEXT NOT NULL DEFAULT ''",
            "next_invoice_number": "INTEGER NOT NULL DEFAULT 1",
            "contract_net_amount": "REAL NOT NULL DEFAULT 0",
            "advance_percent": "REAL NOT NULL DEFAULT 0",
        }
        changed = False
        for column, ddl in required.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {ddl}")
                changed = True
        # Existing projects keep their already issued invoices. Their first new invoice
        # starts in a project-specific block based on the newly assigned prefix.
        self.conn.execute(
            "UPDATE projects SET invoice_prefix = CAST(id AS TEXT) WHERE TRIM(invoice_prefix) = ''"
        )
        self.conn.execute(
            "UPDATE projects SET next_invoice_number = 1 WHERE next_invoice_number IS NULL OR next_invoice_number < 1"
        )
        if changed:
            self.conn.commit()

    def _migrate_bank_transactions_schema(self) -> None:
        """Keep bank import records upgrade-safe for already created local databases."""
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bank_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_fingerprint TEXT NOT NULL UNIQUE,
                source_file TEXT NOT NULL DEFAULT '',
                source_row INTEGER NOT NULL DEFAULT 0,
                transaction_date TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT '',
                payer_name TEXT NOT NULL DEFAULT '',
                payer_iban TEXT NOT NULL DEFAULT '',
                reference TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                suggested_invoice_id INTEGER,
                matched_invoice_id INTEGER,
                payment_id INTEGER,
                match_score INTEGER NOT NULL DEFAULT 0,
                match_reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new',
                imported_at TEXT NOT NULL DEFAULT '',
                matched_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(suggested_invoice_id) REFERENCES invoices(id) ON DELETE SET NULL,
                FOREIGN KEY(matched_invoice_id) REFERENCES invoices(id) ON DELETE SET NULL,
                FOREIGN KEY(payment_id) REFERENCES payments(id) ON DELETE SET NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bank_transactions_status ON bank_transactions(status, transaction_date DESC)"
        )
        self.conn.commit()

    def _migrate_project_budget_schema(self) -> None:
        """Create project budget storage without changing historic accounting data."""
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_budgets (
                project_id INTEGER PRIMARY KEY,
                planned_income_net REAL NOT NULL DEFAULT 0,
                planned_rad_net REAL NOT NULL DEFAULT 0,
                planned_material_net REAL NOT NULL DEFAULT 0,
                planned_plates_net REAL NOT NULL DEFAULT 0,
                planned_other_costs_net REAL NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        self.conn.commit()

    def _migrate_credit_note_schema(self) -> None:
        """Create the formal credit-note ledger for databases made by older releases."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS credit_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credit_note_seq INTEGER NOT NULL UNIQUE,
                credit_note_number TEXT NOT NULL UNIQUE,
                source_invoice_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                source_invoice_number TEXT NOT NULL DEFAULT '',
                customer_name TEXT NOT NULL DEFAULT '',
                project_name TEXT NOT NULL DEFAULT '',
                issue_date TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'EUR',
                reason TEXT NOT NULL DEFAULT '',
                net_amount REAL NOT NULL DEFAULT 0,
                vat_rate REAL NOT NULL DEFAULT 0.20,
                vat_amount REAL NOT NULL DEFAULT 0,
                gross_amount REAL NOT NULL DEFAULT 0,
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(source_invoice_id) REFERENCES invoices(id) ON DELETE RESTRICT,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_credit_notes_source_invoice
            ON credit_notes(source_invoice_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_credit_notes_project_date
            ON credit_notes(project_id, issue_date DESC, id DESC);
            """
        )
        self.conn.commit()

    def _migrate_subscription_schema(self) -> None:
        """Keep subscription storage upgrade-safe for data made before billing existed."""
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(workspace_subscription)").fetchall()
        }
        required = {
            "workspace_id": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'not_started'",
            "plan_code": "TEXT NOT NULL DEFAULT 'starter'",
            "trial_started_at": "TEXT NOT NULL DEFAULT ''",
            "trial_ends_at": "TEXT NOT NULL DEFAULT ''",
            "billing_provider": "TEXT NOT NULL DEFAULT ''",
            "external_subscription_id": "TEXT NOT NULL DEFAULT ''",
            "last_verified_at": "TEXT NOT NULL DEFAULT ''",
            "cloud_api_url": "TEXT NOT NULL DEFAULT ''",
            "cloud_workspace_token": "TEXT NOT NULL DEFAULT ''",
            "cloud_owner_email": "TEXT NOT NULL DEFAULT ''",
            "cloud_member_id": "TEXT NOT NULL DEFAULT ''",
            "cloud_member_token": "TEXT NOT NULL DEFAULT ''",
            "cloud_member_role": "TEXT NOT NULL DEFAULT ''",
            "cloud_member_name": "TEXT NOT NULL DEFAULT ''",
            "cloud_sync_revision": "INTEGER NOT NULL DEFAULT 0",
            "cloud_sync_sha256": "TEXT NOT NULL DEFAULT ''",
            "cloud_last_sync_at": "TEXT NOT NULL DEFAULT ''",
            "cloud_last_error": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        }
        changed = False
        for column, ddl in required.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE workspace_subscription ADD COLUMN {column} {ddl}")
                changed = True
        if changed:
            self.conn.commit()

    def bootstrap_defaults(self) -> None:
        existing = self.conn.execute("SELECT id FROM company_settings WHERE id = 1").fetchone()
        if existing is None:
            defaults = default_company_settings()
            self.conn.execute(
                """
                INSERT INTO company_settings (
                    id, name, eik, vat_number, address, phone, email, bank_name, iban, bic,
                    director_name, logo_path, country_code, default_vat_rate, default_currency, payment_term_days,
                    exchange_rate, issue_place, payment_method, next_invoice_seq, updated_at
                ) VALUES (
                    1, :name, :eik, :vat_number, :address, :phone, :email, :bank_name, :iban, :bic,
                    :director_name, :logo_path, :country_code, :default_vat_rate, :default_currency, :payment_term_days,
                    :exchange_rate, :issue_place, :payment_method, 1, :updated_at
                )
                """,
                defaults,
            )
            self.conn.commit()
        self._ensure_subscription_state()

    def _ensure_subscription_state(self) -> None:
        """Create one state record while preserving access for existing installations."""
        existing = self.conn.execute("SELECT id FROM workspace_subscription WHERE id = 1").fetchone()
        if existing:
            return
        company = self.get_company()
        is_existing_workspace = bool(
            int(company.get("onboarding_completed") or 0)
            or str(company.get("name") or "").strip()
            or str(company.get("eik") or "").strip()
        )
        payload = default_subscription_state()
        # A release must never lock a company that already trusted OpsNest with
        # operational documents before subscriptions were introduced.
        payload["status"] = "legacy" if is_existing_workspace else "not_started"
        self.conn.execute(
            """
            INSERT INTO workspace_subscription (
                id, workspace_id, status, plan_code, trial_started_at, trial_ends_at,
                billing_provider, external_subscription_id, last_verified_at,
                cloud_api_url, cloud_workspace_token, cloud_owner_email,
                cloud_member_id, cloud_member_token, cloud_member_role, cloud_member_name,
                cloud_sync_revision,
                cloud_last_sync_at, cloud_last_error,
                updated_at
            ) VALUES (
                :id, :workspace_id, :status, :plan_code, :trial_started_at, :trial_ends_at,
                :billing_provider, :external_subscription_id, :last_verified_at,
                :cloud_api_url, :cloud_workspace_token, :cloud_owner_email,
                :cloud_member_id, :cloud_member_token, :cloud_member_role, :cloud_member_name,
                :cloud_sync_revision,
                :cloud_last_sync_at, :cloud_last_error,
                :updated_at
            )
            """,
            payload,
        )
        self.conn.commit()

    def get_company(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM company_settings WHERE id = 1").fetchone()
        payload = default_company_settings()
        payload.update(row_to_dict(row) or {})
        payload["default_currency"] = normalize_currency(
            payload.get("default_currency"),
            fallback=default_currency_for_country(payload.get("country_code")),
        )
        return payload

    def company_needs_registration(self) -> bool:
        company = self.get_company()
        if int(company.get("onboarding_completed") or 0):
            return False
        return not (str(company.get("name") or "").strip() and str(company.get("eik") or "").strip())

    def company_has_local_login(self) -> bool:
        company = self.get_company()
        return bool(
            str(company.get("login_email") or "").strip()
            and str(company.get("login_pin_salt") or "").strip()
            and str(company.get("login_pin_hash") or "").strip()
        )

    def set_company_login(self, login_email: str, pin: str) -> None:
        email = str(login_email or "").strip().lower()
        pin_value = str(pin or "")
        if "@" not in email:
            raise ValueError("Unesite ispravan e-mail za prijavu.")
        if len(pin_value) < 4 or not pin_value.isdigit():
            raise ValueError("PIN mora imati najmanje 4 cifre.")
        salt = secrets.token_hex(16)
        pin_hash = hashlib.pbkdf2_hmac("sha256", pin_value.encode("utf-8"), salt.encode("ascii"), LOCAL_LOGIN_ITERATIONS).hex()
        payload = self.get_company()
        payload.update({"login_email": email, "login_pin_salt": salt, "login_pin_hash": pin_hash})
        self.save_company(payload)

    def verify_company_login(self, login_email: str, pin: str) -> bool:
        company = self.get_company()
        email = str(login_email or "").strip().lower()
        stored_email = str(company.get("login_email") or "").strip().lower()
        salt = str(company.get("login_pin_salt") or "")
        stored_hash = str(company.get("login_pin_hash") or "")
        if not email or not stored_email or not salt or not stored_hash:
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", str(pin or "").encode("utf-8"), salt.encode("ascii"), LOCAL_LOGIN_ITERATIONS).hex()
        return hmac.compare_digest(email, stored_email) and hmac.compare_digest(candidate, stored_hash)

    def get_subscription(self, *, as_of: datetime | None = None) -> dict[str, Any]:
        """Read the effective subscription state without modifying business data."""
        self._ensure_subscription_state()
        row = self.conn.execute("SELECT * FROM workspace_subscription WHERE id = 1").fetchone()
        payload = default_subscription_state()
        payload.update(row_to_dict(row) or {})
        if not str(payload.get("workspace_id") or "").strip():
            payload["workspace_id"] = str(uuid.uuid4())
            payload["updated_at"] = now_iso()
            self.conn.execute(
                "UPDATE workspace_subscription SET workspace_id = ?, updated_at = ? WHERE id = 1",
                (payload["workspace_id"], payload["updated_at"]),
            )
            self.conn.commit()

        effective_status = str(payload.get("status") or "not_started").strip().lower()
        reference = as_of or datetime.now()
        trial_end: datetime | None = None
        try:
            raw_end = str(payload.get("trial_ends_at") or "").strip()
            trial_end = datetime.fromisoformat(raw_end) if raw_end else None
        except ValueError:
            trial_end = None

        days_remaining = 0
        if effective_status == "trial" and trial_end:
            remaining_seconds = (trial_end - reference).total_seconds()
            if remaining_seconds <= 0:
                effective_status = "expired"
            else:
                days_remaining = max(1, int((remaining_seconds + 86_399) // 86_400))
        elif effective_status == "trial":
            # A malformed local trial record must fail closed for writes, while
            # still allowing the owner to read and export their existing data.
            effective_status = "expired"

        payload["stored_status"] = str(payload.get("status") or "not_started").strip().lower()
        payload["status"] = effective_status
        payload["days_remaining"] = days_remaining
        payload["can_write"] = effective_status in SUBSCRIPTION_WRITE_STATUSES
        payload["read_only"] = not payload["can_write"]
        return payload

    def start_trial_if_needed(self) -> dict[str, Any]:
        """Start one seven-day, card-free trial after a new company registers."""
        subscription = self.get_subscription()
        if subscription.get("stored_status") != "not_started":
            return subscription
        started_at = datetime.now()
        ends_at = started_at + timedelta(days=TRIAL_DAYS)
        self.conn.execute(
            """
            UPDATE workspace_subscription
            SET status = 'trial', plan_code = 'starter', trial_started_at = ?,
                trial_ends_at = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                started_at.isoformat(timespec="seconds"),
                ends_at.isoformat(timespec="seconds"),
                now_iso(),
            ),
        )
        self.conn.commit()
        return self.get_subscription()

    def cloud_connection(self) -> dict[str, str]:
        subscription = self.get_subscription()
        workspace_token, workspace_legacy, workspace_unavailable = _read_local_cloud_secret(
            subscription.get("cloud_workspace_token") or ""
        )
        member_token, member_legacy, member_unavailable = _read_local_cloud_secret(
            subscription.get("cloud_member_token") or ""
        )
        if workspace_legacy or member_legacy or workspace_unavailable or member_unavailable:
            updates: dict[str, Any] = {"updated_at": now_iso()}
            if workspace_unavailable:
                updates["cloud_workspace_token"] = ""
            elif workspace_legacy:
                updates["cloud_workspace_token"] = _protect_local_cloud_secret(workspace_token)
            if member_unavailable:
                updates.update(
                    {
                        "cloud_member_id": "",
                        "cloud_member_token": "",
                        "cloud_member_role": "",
                        "cloud_member_name": "",
                    }
                )
            elif member_legacy:
                updates["cloud_member_token"] = _protect_local_cloud_secret(member_token)
            if workspace_unavailable or member_unavailable:
                updates["cloud_last_error"] = (
                    "Lokalna cloud sesija više nije dostupna ovom Windows nalogu. Prijavite se ponovo."
                )
            assignments = ", ".join(f"{column} = ?" for column in updates)
            self.conn.execute(
                f"UPDATE workspace_subscription SET {assignments} WHERE id = 1",
                tuple(updates.values()),
            )
            self.conn.commit()
            subscription.update(updates)
        return {
            "api_url": str(subscription.get("cloud_api_url") or "").strip(),
            "workspace_token": workspace_token,
            "owner_email": str(subscription.get("cloud_owner_email") or "").strip(),
            "member_id": str(subscription.get("cloud_member_id") or "").strip(),
            "member_token": member_token,
            "member_role": str(subscription.get("cloud_member_role") or "").strip(),
            "member_name": str(subscription.get("cloud_member_name") or "").strip(),
            "sync_revision": str(subscription.get("cloud_sync_revision") or "0").strip(),
        }

    def save_cloud_connection(self, *, api_url: str, workspace_token: str, owner_email: str) -> None:
        self.conn.execute(
            """
            UPDATE workspace_subscription
            SET cloud_api_url = ?, cloud_workspace_token = ?, cloud_owner_email = ?,
                cloud_last_sync_at = ?, cloud_last_error = '', updated_at = ?
            WHERE id = 1
            """,
            (
                str(api_url).strip().rstrip("/"),
                _protect_local_cloud_secret(workspace_token),
                str(owner_email).strip().lower(),
                now_iso(),
                now_iso(),
            ),
        )
        self.conn.commit()

    def save_cloud_member_session(
        self,
        *,
        member_id: str,
        member_token: str,
        member_role: str,
        member_name: str,
    ) -> None:
        """Persist only the revocable cloud session returned after a successful team sign-in."""
        self.conn.execute(
            """
            UPDATE workspace_subscription
            SET cloud_member_id = ?, cloud_member_token = ?, cloud_member_role = ?, cloud_member_name = ?,
                cloud_last_error = '', updated_at = ?
            WHERE id = 1
            """,
            (
                str(member_id or "").strip(),
                _protect_local_cloud_secret(member_token),
                str(member_role or "").strip(),
                str(member_name or "").strip(),
                now_iso(),
            ),
        )
        self.conn.commit()

    def clear_cloud_member_session(self) -> None:
        self.conn.execute(
            """
            UPDATE workspace_subscription
            SET cloud_member_id = '', cloud_member_token = '', cloud_member_role = '', cloud_member_name = '',
                updated_at = ?
            WHERE id = 1
            """,
            (now_iso(),),
        )
        self.conn.commit()

    def link_team_workspace(
        self,
        *,
        workspace_id: str,
        api_url: str,
        member_id: str,
        member_token: str,
        member_role: str,
        member_name: str,
    ) -> None:
        """Link this computer to a centrally verified team workspace without storing any password."""
        self.conn.execute(
            """
            UPDATE workspace_subscription
            SET workspace_id = ?, cloud_api_url = ?, cloud_workspace_token = '', cloud_owner_email = '',
                cloud_member_id = ?, cloud_member_token = ?,
                cloud_member_role = ?, cloud_member_name = ?, cloud_sync_revision = 0,
                cloud_sync_sha256 = '',
                cloud_last_error = '', updated_at = ?
            WHERE id = 1
            """,
            (
                str(workspace_id).strip(),
                str(api_url).strip().rstrip("/"),
                str(member_id).strip(),
                _protect_local_cloud_secret(member_token),
                str(member_role).strip(),
                str(member_name).strip(),
                now_iso(),
            ),
        )
        self.conn.commit()

    def cloud_sync_state(self) -> dict[str, Any]:
        subscription = self.get_subscription()
        return {
            "revision": int(subscription.get("cloud_sync_revision") or 0),
            "sha256": str(subscription.get("cloud_sync_sha256") or "").strip().lower(),
            "last_sync_at": str(subscription.get("cloud_last_sync_at") or ""),
            "last_error": str(subscription.get("cloud_last_error") or ""),
        }

    def mark_cloud_sync(self, revision: int, sha256: str = "") -> None:
        """Record the exact sanitized business revision last synchronized.

        The digest is deliberately calculated from a snapshot with device-only
        session and sync metadata removed.  That lets the Desktop distinguish a
        real local accounting change from its own harmless sync timestamp.
        """
        normalized_sha = str(sha256 or "").strip().lower()
        if normalized_sha and (len(normalized_sha) != 64 or any(char not in "0123456789abcdef" for char in normalized_sha)):
            raise ValueError("Kontrolni zbir sinhronizacije nije ispravan.")
        self.conn.execute(
            """
            UPDATE workspace_subscription
            SET cloud_sync_revision = ?, cloud_sync_sha256 = ?, cloud_last_sync_at = ?, cloud_last_error = '', updated_at = ?
            WHERE id = 1
            """,
            (int(revision), normalized_sha, now_iso(), now_iso()),
        )
        self.conn.commit()

    def cloud_sync_change_status(self) -> dict[str, Any]:
        """Report whether this device has business changes not in its shared revision.

        Old Desktop installations did not retain a content digest.  They are
        reported as ``baseline_unknown`` rather than being silently treated as
        clean, so a user consciously chooses the authoritative copy once.
        """
        state = self.cloud_sync_state()
        revision = int(state["revision"])
        baseline = str(state["sha256"])
        if revision <= 0:
            return {"revision": revision, "tracked": False, "baseline_unknown": False, "has_unsynced_changes": False}
        if not baseline:
            return {"revision": revision, "tracked": False, "baseline_unknown": True, "has_unsynced_changes": False}
        current = self.build_cloud_sync_snapshot()["sha256"]
        return {
            "revision": revision,
            "tracked": True,
            "baseline_unknown": False,
            "has_unsynced_changes": current != baseline,
            "baseline_sha256": baseline,
            "current_sha256": current,
        }

    def build_cloud_sync_snapshot(self) -> dict[str, str]:
        """Create a sanitized compressed SQLite copy for the central team revision store.

        SMTP passwords, local PIN hashes, and all cloud credentials are purposefully
        removed. Project attachment files are not included; they remain local until
        dedicated attachment synchronization is enabled.
        """
        self.conn.commit()
        temp_handle = tempfile.NamedTemporaryFile(prefix="opsnest_sync_", suffix=".db", delete=False)
        temp_path = Path(temp_handle.name)
        temp_handle.close()
        try:
            target = sqlite3.connect(temp_path)
            try:
                self.conn.backup(target)
            finally:
                target.close()
            sanitized = sqlite3.connect(temp_path)
            try:
                sanitized.execute(
                    """
                    UPDATE company_settings
                    SET smtp_password = '', login_pin_salt = '', login_pin_hash = ''
                    WHERE id = 1
                    """
                )
                sanitized.execute(
                    """
                    UPDATE workspace_subscription
                    SET cloud_workspace_token = '', cloud_member_id = '', cloud_member_token = '',
                        cloud_member_role = '', cloud_member_name = '', cloud_sync_revision = 0,
                        cloud_sync_sha256 = '', cloud_last_sync_at = '', cloud_last_error = '', updated_at = ''
                    WHERE id = 1
                    """
                )
                sanitized.commit()
                # SQLite updates its file-level bookkeeping even for device-only
                # sync metadata.  Rebuild the temporary sanitized copy so its
                # checksum represents business content, not free pages or the
                # SQLite change counter of this particular computer.
                sanitized.execute("VACUUM")
            finally:
                sanitized.close()
            archive = io.BytesIO()
            # The sync checksum must be a digest of sanitized business data, not
            # a digest of the moment this archive was created.  ``writestr``
            # otherwise inserts the current timestamp into each ZIP member and
            # makes a clean workspace look unsynchronized on every check.
            def stable_zip_info(name: str) -> zipfile.ZipInfo:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0
                return info

            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                manifest = json.dumps(
                    {"format": "opsnest-team-snapshot", "version": 1},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                bundle.writestr(stable_zip_info("manifest.json"), manifest)
                bundle.writestr(stable_zip_info("opsnest.db"), temp_path.read_bytes())
            raw = archive.getvalue()
            return {
                "snapshot_b64": base64.b64encode(raw).decode("ascii"),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def apply_cloud_sync_snapshot(self, snapshot_b64: str, sha256: str) -> None:
        """Replace local business data after checksum and SQLite schema validation.

        The current SQLite file is backed up first. Device-local SMTP, local PIN,
        and cloud session details are preserved so a downloaded team revision cannot
        take over authentication on this computer.
        """
        try:
            raw = base64.b64decode(str(snapshot_b64 or "").encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("Sinhronizovani podaci nisu ispravni.") from exc
        if hashlib.sha256(raw).hexdigest() != str(sha256 or "").strip().lower():
            raise ValueError("Kontrolni zbir sinhronizovanih podataka se ne poklapa.")
        if len(raw) > 25 * 1024 * 1024:
            raise ValueError("Sinhronizovana baza je veća od dozvoljenih 25 MB.")
        preserved_company = self.conn.execute(
            "SELECT smtp_host, smtp_port, smtp_security, smtp_username, smtp_password, smtp_from_name, smtp_from_email, smtp_reply_to, login_email, login_pin_salt, login_pin_hash FROM company_settings WHERE id = 1"
        ).fetchone()
        preserved_subscription = self.conn.execute(
            "SELECT workspace_id, cloud_api_url, cloud_workspace_token, cloud_owner_email, cloud_member_id, cloud_member_token, cloud_member_role, cloud_member_name FROM workspace_subscription WHERE id = 1"
        ).fetchone()
        self._maybe_backup("pre_team_sync")
        temp_handle = tempfile.NamedTemporaryFile(prefix="opsnest_received_sync_", suffix=".db", delete=False)
        temp_path = Path(temp_handle.name)
        temp_handle.close()
        try:
            with zipfile.ZipFile(io.BytesIO(raw), "r") as bundle:
                names = bundle.namelist()
                if names != ["manifest.json", "opsnest.db"]:
                    raise ValueError("Sinhronizovani paket nema očekivanu OpsNest strukturu.")
                database_entry = bundle.getinfo("opsnest.db")
                if database_entry.file_size <= 0 or database_entry.file_size > 150 * 1024 * 1024:
                    raise ValueError("Sinhronizovana baza ima nedozvoljenu veličinu.")
                if "opsnest.db" not in names:
                    raise ValueError("Sinhronizovani paket ne sadrži OpsNest bazu.")
                temp_path.write_bytes(bundle.read("opsnest.db"))
            source = sqlite3.connect(temp_path)
            try:
                integrity = source.execute("PRAGMA integrity_check").fetchone()
                if not integrity or str(integrity[0]).lower() != "ok":
                    raise ValueError("Sinhronizovana baza nije prošla SQLite proveru integriteta.")
                tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                if not {"company_settings", "projects", "invoices"}.issubset(tables):
                    raise ValueError("Sinhronizovana baza nema očekivanu OpsNest strukturu.")
                source.backup(self.conn)
            finally:
                source.close()
            self.initialize_schema()
            self.bootstrap_defaults()
            if preserved_company:
                self.conn.execute(
                    """
                    UPDATE company_settings
                    SET smtp_host = ?, smtp_port = ?, smtp_security = ?, smtp_username = ?, smtp_password = ?,
                        smtp_from_name = ?, smtp_from_email = ?, smtp_reply_to = ?,
                        login_email = ?, login_pin_salt = ?, login_pin_hash = ?
                    WHERE id = 1
                    """,
                    tuple(preserved_company),
                )
            if preserved_subscription:
                self.conn.execute(
                    """
                    UPDATE workspace_subscription
                    SET workspace_id = ?, cloud_api_url = ?, cloud_workspace_token = ?, cloud_owner_email = ?,
                        cloud_member_id = ?, cloud_member_token = ?, cloud_member_role = ?, cloud_member_name = ?
                    WHERE id = 1
                    """,
                    tuple(preserved_subscription),
                )
            self.conn.commit()
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def record_cloud_sync_error(self, message: str) -> None:
        self.conn.execute(
            "UPDATE workspace_subscription SET cloud_last_error = ?, updated_at = ? WHERE id = 1",
            (str(message or "")[:500], now_iso()),
        )
        self.conn.commit()

    def apply_subscription_update(
        self,
        *,
        status: str,
        plan_code: str,
        billing_provider: str = "",
        external_subscription_id: str = "",
        verified_at: str | None = None,
        trial_started_at: str | None = None,
        trial_ends_at: str | None = None,
    ) -> dict[str, Any]:
        """Apply a verified server-side billing decision to this local workspace.

        This method deliberately accepts no payment data. The future billing
        service will validate Stripe webhooks and then send only the resulting
        plan/status information to the desktop client.
        """
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"verification_pending", "trial", "active", "past_due", "suspended", "cancelled", "expired", "legacy"}:
            raise ValueError("Nepoznat status pretplate.")
        normalized_plan = str(plan_code or "starter").strip().lower() or "starter"
        self.conn.execute(
            """
            UPDATE workspace_subscription
            SET status = ?, plan_code = ?, billing_provider = ?,
                external_subscription_id = ?, last_verified_at = ?,
                trial_started_at = COALESCE(NULLIF(?, ''), trial_started_at),
                trial_ends_at = COALESCE(NULLIF(?, ''), trial_ends_at),
                updated_at = ?
            WHERE id = 1
            """,
            (
                normalized_status,
                normalized_plan,
                str(billing_provider or "").strip(),
                str(external_subscription_id or "").strip(),
                str(verified_at or now_iso()).strip(),
                str(trial_started_at or "").strip(),
                str(trial_ends_at or "").strip(),
                now_iso(),
            ),
        )
        self.conn.commit()
        return self.get_subscription()

    def assert_business_write_access(self) -> None:
        """Permit new business documents only for active or trial workspaces."""
        subscription = self.get_subscription()
        if subscription.get("can_write"):
            return
        status = str(subscription.get("status") or "").lower()
        if status == "not_started":
            raise SubscriptionAccessError("Prvo registrujte firmu da biste unosili poslovne podatke.")
        if status == "expired":
            raise SubscriptionAccessError(
                "Probni period je istekao. Podaci su bezbedni i možete ih pregledati, štampati, izvesti ili napraviti backup, ali su izmene zaključane do aktivacije paketa."
            )
        raise SubscriptionAccessError(
            "Pretplata je u režimu samo za pregled. Možete otvoriti, štampati, izvesti i napraviti backup podataka, ali ne i menjati poslovne evidencije."
        )

    def subscription_plan(self) -> dict[str, Any]:
        """Return the purchased package and the effective package for this session.

        A seven-day trial intentionally exposes the Pro feature set. The stored
        plan remains Starter until the customer actually purchases a package.
        """
        subscription = self.get_subscription()
        purchased_code = str(subscription.get("plan_code") or "starter")
        effective_code = effective_plan_code(subscription.get("status"), purchased_code)
        return {
            "subscription": subscription,
            "purchased_code": purchased_code,
            "effective_code": effective_code,
            "details": plan_details(effective_code),
        }

    def plan_usage(self) -> dict[str, Any]:
        """Return only aggregate usage needed for the package screen and limits."""
        plan = self.subscription_plan()
        month_start = date.today().replace(day=1).isoformat()
        active_projects = int(
            self.conn.execute("SELECT COUNT(*) FROM projects WHERE archived = 0").fetchone()[0]
        )
        issued_invoices = int(
            self.conn.execute(
                """
                SELECT COUNT(*) FROM invoices
                WHERE status_code IN ('issued', 'partial', 'paid', 'due') AND issue_date >= ?
                """,
                (month_start,),
            ).fetchone()[0]
        )
        pdf_imports = int(
            self.conn.execute(
                """
                SELECT COUNT(*) FROM project_documents
                WHERE created_at >= ? AND note LIKE '%PDF:%'
                """,
                (month_start,),
            ).fetchone()[0]
        )
        team_members = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM company_team_members WHERE archived = 0"
            ).fetchone()[0]
        )
        # The local profile owner always uses the first seat. Team records are
        # additional seats, even before cloud sign-in is introduced.
        team_seats_used = 1 + team_members
        return {
            **plan,
            "month_start": month_start,
            "active_projects": active_projects,
            "issued_invoices": issued_invoices,
            "pdf_imports": pdf_imports,
            "team_members": team_members,
            "team_seats_used": team_seats_used,
            "limits": {
                "projects": plan_limit(plan["effective_code"], "projects"),
                "issued_invoices_per_month": plan_limit(plan["effective_code"], "issued_invoices_per_month"),
                "pdf_imports_per_month": plan_limit(plan["effective_code"], "pdf_imports_per_month"),
                "seats": plan_limit(plan["effective_code"], "seats"),
            },
        }

    def team_members(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM company_team_members"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY CASE role WHEN 'owner' THEN 0 WHEN 'accountant' THEN 1 ELSE 2 END, display_name COLLATE NOCASE, email COLLATE NOCASE"
        return [row_to_dict(row) for row in self.conn.execute(sql).fetchall()]

    def assert_team_member_allowed(self, member_id: int | None = None) -> None:
        """Guard paid team seats while keeping the owner profile available."""
        self.assert_plan_feature("team_users")
        if member_id:
            existing = self.conn.execute(
                "SELECT id FROM company_team_members WHERE id = ? AND archived = 0",
                (int(member_id),),
            ).fetchone()
            if existing:
                return
        usage = self.plan_usage()
        limit = usage["limits"]["seats"]
        if limit is not None and usage["team_seats_used"] >= limit:
            raise PlanLimitError(
                f"Paket {usage['details']['name']} uključuje najviše {limit} korisničkih mesta, uključujući vlasnika. "
                "Otvorite Paketi i plaćanje da pređete na veći paket."
            )

    def save_team_member(self, data: dict[str, Any]) -> dict[str, Any]:
        """Save an invited local team member; cloud sign-in remains a later step."""
        member_id = int(data.get("id") or 0) or None
        self.assert_team_member_allowed(member_id)
        email = str(data.get("email") or "").strip().lower()
        display_name = str(data.get("display_name") or "").strip()
        role = str(data.get("role") or "member").strip().lower()
        status = str(data.get("status") or "invited").strip().lower()
        if "@" not in email:
            raise ValueError("Unesite ispravan e-mail korisnika.")
        if not display_name:
            raise ValueError("Unesite ime korisnika.")
        if role not in {"member", "accountant"}:
            raise ValueError("Izaberite dozvoljenu ulogu korisnika.")
        if status not in {"invited", "active"}:
            status = "invited"
        owner_email = str(self.get_company().get("login_email") or self.get_company().get("email") or "").strip().lower()
        if owner_email and email == owner_email:
            raise ValueError("Vlasnik firme već koristi prvo korisničko mesto.")
        now = now_iso()
        self._backup_before_change("team_member")
        try:
            if member_id:
                self.conn.execute(
                    """
                    UPDATE company_team_members
                    SET email = ?, display_name = ?, role = ?, status = ?, archived = 0, updated_at = ?
                    WHERE id = ?
                    """,
                    (email, display_name, role, status, now, member_id),
                )
            else:
                self.conn.execute(
                    """
                    INSERT INTO company_team_members (email, display_name, role, status, archived, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, ?, ?)
                    """,
                    (email, display_name, role, status, now, now),
                )
                member_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Korisnik sa ovim e-mailom već postoji u timu.") from exc
        row = self.conn.execute("SELECT * FROM company_team_members WHERE id = ?", (member_id,)).fetchone()
        return row_to_dict(row)

    def archive_team_member(self, member_id: int) -> None:
        # Removing a team record must remain possible after a downgrade.
        self._backup_before_change("team_member_archive")
        self.conn.execute(
            "UPDATE company_team_members SET archived = 1, updated_at = ? WHERE id = ?",
            (now_iso(), int(member_id)),
        )
        self.conn.commit()

    def assert_plan_feature(self, feature: str) -> None:
        plan = self.subscription_plan()
        if plan_includes(plan["effective_code"], feature):
            return
        required_plan = {
            "project_budget": "Business",
            "bank_matching": "Business",
            "vat_evidence": "Business",
            "accountant_export": "Business",
            "advanced_pdf_import": "Pro",
            "team_users": "Business",
            "invoice_approval": "Business",
            "custom_invoice_templates": "Business",
        }.get(feature, "odgovarajući")
        raise PlanLimitError(
            f"Ova funkcija je dostupna od paketa {required_plan}. Otvorite Paketi i plaćanje da izaberete paket."
        )

    def assert_new_project_allowed(self) -> None:
        usage = self.plan_usage()
        limit = usage["limits"]["projects"]
        if limit is not None and usage["active_projects"] >= limit:
            raise PlanLimitError(
                f"Paket {usage['details']['name']} dozvoljava najviše {limit} aktivna projekta. "
                "Arhivirajte postojeći projekat ili izaberite veći paket."
            )

    def assert_issued_invoice_allowed(self) -> None:
        usage = self.plan_usage()
        limit = usage["limits"]["issued_invoices_per_month"]
        if limit is not None and usage["issued_invoices"] >= limit:
            raise PlanLimitError(
                f"Paket {usage['details']['name']} dozvoljava najviše {limit} izdatih faktura mesečno. "
                "Otvorite Paketi i plaćanje da nastavite."
            )

    def assert_pdf_import_allowed(self) -> None:
        usage = self.plan_usage()
        limit = usage["limits"]["pdf_imports_per_month"]
        if limit is not None and usage["pdf_imports"] >= limit:
            raise PlanLimitError(
                f"Paket {usage['details']['name']} dozvoljava najviše {limit} PDF uvoza mesečno. "
                "Otvorite Paketi i plaćanje da nastavite."
            )

    def invoice_template_dir(self) -> Path:
        directory = data_dir() / "InvoiceTemplates"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def default_invoice_template_id(self) -> int:
        row = self.conn.execute(
            """
            SELECT id FROM invoice_templates
            WHERE archived = 0 AND is_default = 1
            ORDER BY id ASC LIMIT 1
            """
        ).fetchone()
        return int(row["id"]) if row else 0

    def list_invoice_templates(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        clauses = "" if include_archived else "WHERE archived = 0"
        rows = self.conn.execute(
            f"SELECT * FROM invoice_templates {clauses} ORDER BY is_default DESC, name COLLATE NOCASE, id"
        ).fetchall()
        original = {
            "id": 0,
            "name": "Originalni OpsNest šablon",
            "stored_path": str(TEMPLATE_XLSX),
            "is_default": 1 if not any(int(row["is_default"] or 0) for row in rows) else 0,
            "archived": 0,
            "is_original": 1,
        }
        return [original, *[row_to_dict(row) for row in rows]]

    def get_invoice_template(self, template_id: int | None) -> dict[str, Any]:
        selected_id = int(template_id or 0)
        if selected_id <= 0:
            return {
                "id": 0,
                "name": "Originalni OpsNest šablon",
                "stored_path": str(TEMPLATE_XLSX),
                "is_default": 1,
                "archived": 0,
                "is_original": 1,
            }
        row = self.conn.execute("SELECT * FROM invoice_templates WHERE id = ?", (selected_id,)).fetchone()
        return row_to_dict(row)

    def invoice_template_path(self, template_id: int | None, *, allow_archived: bool = True) -> Path:
        template = self.get_invoice_template(template_id)
        if not template or (int(template.get("archived") or 0) and not allow_archived):
            raise ValueError("Izabrani šablon fakture više nije dostupan.")
        path = Path(str(template.get("stored_path") or ""))
        if not path.is_file():
            raise ValueError("Fajl izabranog šablona fakture nije pronađen.")
        if path.suffix.lower() != ".xlsx":
            raise ValueError("Šablon fakture mora biti Excel .xlsx fajl.")
        return path

    def save_invoice_template(
        self,
        source_path: str | Path,
        *,
        name: str = "",
        set_default: bool = False,
    ) -> int:
        self.assert_plan_feature("custom_invoice_templates")
        source = Path(source_path)
        if not source.is_file() or source.suffix.lower() != ".xlsx":
            raise ValueError("Izaberite postojeći Excel .xlsx šablon fakture.")
        template_name = str(name or source.stem).strip() or source.stem
        destination = self.invoice_template_dir() / (
            f"{uuid.uuid4().hex}_{safe_filename(template_name)[:80]}.xlsx"
        )
        self._backup_before_change("invoice_template")
        shutil.copy2(source, destination)
        now = now_iso()
        if set_default:
            self.conn.execute("UPDATE invoice_templates SET is_default = 0 WHERE archived = 0")
        cur = self.conn.execute(
            """
            INSERT INTO invoice_templates (name, stored_path, is_default, archived, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (template_name, str(destination), int(bool(set_default)), now, now),
        )
        self.conn.commit()
        self._maybe_backup("invoice_template")
        return int(cur.lastrowid)

    def set_default_invoice_template(self, template_id: int | None) -> None:
        self.assert_plan_feature("custom_invoice_templates")
        selected_id = int(template_id or 0)
        if selected_id:
            self.invoice_template_path(selected_id, allow_archived=False)
        self._backup_before_change("invoice_template_default")
        self.conn.execute("UPDATE invoice_templates SET is_default = 0 WHERE archived = 0")
        if selected_id:
            self.conn.execute(
                "UPDATE invoice_templates SET is_default = 1, updated_at = ? WHERE id = ?",
                (now_iso(), selected_id),
            )
        self.conn.commit()
        self._maybe_backup("invoice_template_default")

    def archive_invoice_template(self, template_id: int) -> None:
        self.assert_plan_feature("custom_invoice_templates")
        selected_id = int(template_id or 0)
        if selected_id <= 0:
            raise ValueError("Originalni OpsNest šablon je zaštićen i ne može se arhivirati.")
        template = self.get_invoice_template(selected_id)
        if not template:
            raise ValueError("Šablon fakture nije pronađen.")
        if int(template.get("is_default") or 0):
            raise ValueError("Prvo postavite drugi podrazumevani šablon.")
        self._backup_before_change("invoice_template_archive")
        self.conn.execute(
            "UPDATE invoice_templates SET archived = 1, updated_at = ? WHERE id = ?",
            (now_iso(), selected_id),
        )
        self.conn.commit()
        self._maybe_backup("invoice_template_archive")

    def _create_owner_notification(
        self,
        *,
        event_code: str,
        title: str,
        message: str,
        invoice_id: int | None = None,
        project_id: int | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO owner_notifications (
                event_code, title, message, invoice_id, project_id, is_read, created_at, read_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, '')
            """,
            (event_code, title, message, invoice_id, project_id, now_iso()),
        )

    def list_owner_notifications(self, *, unread_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        where = "WHERE n.is_read = 0" if unread_only else ""
        rows = self.conn.execute(
            f"""
            SELECT n.*, i.invoice_number, i.customer_name, i.project_name, i.gross_total
            FROM owner_notifications n
            LEFT JOIN invoices i ON i.id = n.invoice_id
            {where}
            ORDER BY n.is_read ASC, n.id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def mark_owner_notifications_read(self, *, invoice_id: int | None = None) -> None:
        now = now_iso()
        if invoice_id is None:
            self.conn.execute(
                "UPDATE owner_notifications SET is_read = 1, read_at = ? WHERE is_read = 0",
                (now,),
            )
        else:
            self.conn.execute(
                """
                UPDATE owner_notifications
                SET is_read = 1, read_at = ?
                WHERE invoice_id = ? AND is_read = 0
                """,
                (now, int(invoice_id)),
            )
        self.conn.commit()

    def pending_invoice_approvals(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM invoices
            WHERE status_code = 'pending_approval'
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def pending_invoice_approval_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total FROM invoices WHERE status_code = 'pending_approval'"
        ).fetchone()
        return int(row["total"] if row else 0)

    def approve_invoice(self, invoice_id: int, approved_by_name: str, *, issue_after_approval: bool = False) -> None:
        self.assert_plan_feature("invoice_approval")
        invoice = self.get_invoice(int(invoice_id))
        if not invoice:
            raise ValueError("Faktura nije pronađena.")
        if str(invoice.get("status_code") or "") != "pending_approval":
            raise ValueError("Samo faktura poslata na proveru može biti odobrena.")
        approver = str(approved_by_name or "").strip() or "Vlasnik"
        preparer = str(invoice.get("prepared_by_name") or "").strip()
        if preparer and preparer.casefold() == approver.casefold():
            raise ValueError(
                "Razdvajanje dužnosti: fakturu mora odobriti druga osoba od one koja ju je pripremila. "
                "Pošaljite je vlasniku ili administratoru na proveru."
            )
        now = now_iso()
        self._backup_before_change(f"invoice_approval_{invoice.get('invoice_number') or invoice_id}")
        resulting_status = "issued" if issue_after_approval else "approved"
        self.conn.execute(
            """
            UPDATE invoices
            SET status_code = ?, approved_by_name = ?, approved_at = ?, updated_at = ?,
                issued_at = CASE WHEN ? = 'issued' AND issued_at = '' THEN ? ELSE issued_at END
            WHERE id = ?
            """,
            (resulting_status, approver, now, now, resulting_status, now, int(invoice_id)),
        )
        if issue_after_approval:
            self._record_invoice_audit(
                int(invoice_id),
                "approved_and_issued",
                f"Fakturu je odobrio/la i izdao/la: {approver}.",
            )
        else:
            self._record_invoice_audit(int(invoice_id), "approved", f"Fakturu je odobrio/la: {approver}.")
        self.conn.execute(
            """
            UPDATE owner_notifications
            SET is_read = 1, read_at = ?
            WHERE invoice_id = ? AND is_read = 0
            """,
            (now, int(invoice_id)),
        )
        self.conn.commit()
        self._maybe_backup(f"invoice_approval_{invoice.get('invoice_number') or invoice_id}")

    def return_invoice_for_revision(self, invoice_id: int, returned_by_name: str, comment: str) -> None:
        """Return a pending invoice to its preparer as an editable draft.

        A comment is deliberately mandatory: it is the reviewer's instruction and
        remains in the invoice audit trail instead of being lost in a dialog.
        """
        self.assert_plan_feature("invoice_approval")
        invoice = self.get_invoice(int(invoice_id))
        if not invoice:
            raise ValueError("Faktura nije pronađena.")
        if str(invoice.get("status_code") or "") != "pending_approval":
            raise ValueError("Samo faktura poslata na proveru može biti vraćena na doradu.")
        reason = str(comment or "").strip()
        if not reason:
            raise ValueError("Komentar za doradu je obavezan.")

        reviewer = str(returned_by_name or "").strip() or "Vlasnik"
        now = now_iso()
        backup_label = f"invoice_revision_{invoice.get('invoice_number') or invoice_id}"
        self._backup_before_change(backup_label)
        self.conn.execute(
            """
            UPDATE invoices
            SET status_code = 'draft', approved_by_name = '', approved_at = '', updated_at = ?
            WHERE id = ?
            """,
            (now, int(invoice_id)),
        )
        self._record_invoice_audit(
            int(invoice_id),
            "returned_for_revision",
            f"Vratio/la na doradu {reviewer}. Komentar: {reason}",
        )
        self.conn.execute(
            """
            UPDATE owner_notifications
            SET is_read = 1, read_at = ?
            WHERE invoice_id = ? AND is_read = 0
            """,
            (now, int(invoice_id)),
        )
        self.conn.commit()
        self._maybe_backup(backup_label)

    def save_company(self, data: dict[str, Any]) -> None:
        payload = default_company_settings()
        existing = self.conn.execute("SELECT * FROM company_settings WHERE id = 1").fetchone()
        payload.update(row_to_dict(existing) or {})
        payload.update({k: v for k, v in data.items() if k in payload or k == "id"})
        payload["country_code"] = normalize_country_code(payload.get("country_code"))
        payload["business_profile"] = str(payload.get("business_profile") or "general").strip().lower()
        if payload["business_profile"] not in BUSINESS_PROFILE_CODES:
            payload["business_profile"] = "general"
        payload["default_currency"] = normalize_currency(
            payload.get("default_currency"),
            fallback=default_currency_for_country(payload["country_code"]),
        )
        payload["vat_regime"] = str(payload.get("vat_regime") or "standard").strip().lower()
        if payload["vat_regime"] not in VAT_REGIME_CODES:
            payload["vat_regime"] = "standard"
        payload["einvoice_route"] = str(payload.get("einvoice_route") or "automatic").strip().lower()
        if payload["einvoice_route"] not in EINVOICE_ROUTE_CODES:
            payload["einvoice_route"] = "automatic"
        payload["default_vat_rate"] = float(payload.get("default_vat_rate") or DEFAULT_VAT_RATE)
        payload["payment_term_days"] = int(payload.get("payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS)
        payload["exchange_rate"] = float(payload.get("exchange_rate") or DEFAULT_EXCHANGE_RATE)
        payload["smtp_port"] = int(payload.get("smtp_port") or DEFAULT_SMTP_PORT)
        payload["smtp_security"] = str(payload.get("smtp_security") or DEFAULT_SMTP_SECURITY).strip().lower() or DEFAULT_SMTP_SECURITY
        payload["smtp_from_name"] = str(payload.get("smtp_from_name") or payload.get("name") or "").strip()
        payload["auto_payment_reminders"] = int(bool(payload.get("auto_payment_reminders")))
        payload["payment_reminder_interval_days"] = max(
            1,
            min(90, int(payload.get("payment_reminder_interval_days") or 7)),
        )
        payload["ui_language"] = str(payload.get("ui_language") or "sr").strip().lower()
        if payload["ui_language"] not in {"sr", "en", "de", "bg", "ru"}:
            payload["ui_language"] = "sr"
        payload["onboarding_completed"] = int(bool(payload.get("onboarding_completed")))
        payload["team_invoice_approval_required"] = int(
            bool(payload.get("team_invoice_approval_required", 1))
        )
        owner_threshold = money_round(payload.get("vendor_bill_owner_approval_threshold") or 0)
        if owner_threshold < 0:
            raise ValueError("Limit za odobrenje vlasnika ne može biti negativan.")
        payload["vendor_bill_owner_approval_threshold"] = float(owner_threshold)
        payload["login_email"] = str(payload.get("login_email") or "").strip().lower()
        payload["login_pin_salt"] = str(payload.get("login_pin_salt") or "").strip()
        payload["login_pin_hash"] = str(payload.get("login_pin_hash") or "").strip()
        payload["updated_at"] = now_iso()
        self._backup_before_change("company")
        self.conn.execute(
            """
            INSERT INTO company_settings (
                id, name, eik, vat_number, address, phone, email, bank_name, iban, bic,
                director_name, logo_path, business_profile, country_code, default_vat_rate, default_currency, vat_regime, einvoice_route, payment_term_days,
                exchange_rate, issue_place, payment_method, smtp_host, smtp_port, smtp_security,
                smtp_username, smtp_password, smtp_from_name, smtp_from_email, smtp_reply_to,
                auto_payment_reminders, payment_reminder_interval_days,
                ui_language, onboarding_completed, team_invoice_approval_required, vendor_bill_owner_approval_threshold,
                login_email, login_pin_salt, login_pin_hash,
                next_invoice_seq, next_credit_note_seq, updated_at
            ) VALUES (
                1, :name, :eik, :vat_number, :address, :phone, :email, :bank_name, :iban, :bic,
                :director_name, :logo_path, :business_profile, :country_code, :default_vat_rate, :default_currency, :vat_regime, :einvoice_route, :payment_term_days,
                :exchange_rate, :issue_place, :payment_method, :smtp_host, :smtp_port, :smtp_security,
                :smtp_username, :smtp_password, :smtp_from_name, :smtp_from_email, :smtp_reply_to,
                :auto_payment_reminders, :payment_reminder_interval_days,
                :ui_language, :onboarding_completed, :team_invoice_approval_required, :vendor_bill_owner_approval_threshold,
                :login_email, :login_pin_salt, :login_pin_hash,
                COALESCE((SELECT next_invoice_seq FROM company_settings WHERE id = 1), 1),
                COALESCE((SELECT next_credit_note_seq FROM company_settings WHERE id = 1), 1), :updated_at
            )
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                eik=excluded.eik,
                vat_number=excluded.vat_number,
                address=excluded.address,
                phone=excluded.phone,
                email=excluded.email,
                bank_name=excluded.bank_name,
                iban=excluded.iban,
                bic=excluded.bic,
                director_name=excluded.director_name,
                logo_path=excluded.logo_path,
                business_profile=excluded.business_profile,
                country_code=excluded.country_code,
                default_vat_rate=excluded.default_vat_rate,
                default_currency=excluded.default_currency,
                vat_regime=excluded.vat_regime,
                einvoice_route=excluded.einvoice_route,
                payment_term_days=excluded.payment_term_days,
                exchange_rate=excluded.exchange_rate,
                issue_place=excluded.issue_place,
                payment_method=excluded.payment_method,
                smtp_host=excluded.smtp_host,
                smtp_port=excluded.smtp_port,
                smtp_security=excluded.smtp_security,
                smtp_username=excluded.smtp_username,
                smtp_password=excluded.smtp_password,
                smtp_from_name=excluded.smtp_from_name,
                smtp_from_email=excluded.smtp_from_email,
                smtp_reply_to=excluded.smtp_reply_to,
                auto_payment_reminders=excluded.auto_payment_reminders,
                payment_reminder_interval_days=excluded.payment_reminder_interval_days,
                ui_language=excluded.ui_language,
                onboarding_completed=excluded.onboarding_completed,
                team_invoice_approval_required=excluded.team_invoice_approval_required,
                vendor_bill_owner_approval_threshold=excluded.vendor_bill_owner_approval_threshold,
                login_email=excluded.login_email,
                login_pin_salt=excluded.login_pin_salt,
                login_pin_hash=excluded.login_pin_hash,
                updated_at=excluded.updated_at
            """,
            payload,
        )
        self.conn.commit()

    def bump_invoice_sequence(self) -> int:
        row = self.conn.execute("SELECT next_invoice_seq FROM company_settings WHERE id = 1").fetchone()
        seq = int(row["next_invoice_seq"]) if row else 1
        self.conn.execute(
            "UPDATE company_settings SET next_invoice_seq = ? WHERE id = 1",
            (seq + 1,),
        )
        self.conn.commit()
        return seq

    def _reserve_credit_note_sequence(self) -> int:
        row = self.conn.execute("SELECT next_credit_note_seq FROM company_settings WHERE id = 1").fetchone()
        seq = int(row["next_credit_note_seq"]) if row else 1
        self.conn.execute(
            "UPDATE company_settings SET next_credit_note_seq = ? WHERE id = 1",
            (seq + 1,),
        )
        return seq

    @staticmethod
    def _normalize_project_invoice_prefix(value: Any) -> str:
        prefix = str(value or "").strip()
        if not prefix:
            return ""
        if not prefix.isdigit() or int(prefix) <= 0:
            raise ValueError("Oznaka bloka faktura mora biti pozitivan broj, npr. 1 ili 2.")
        return str(int(prefix))

    def _next_available_project_invoice_prefix(self, exclude_project_id: int | None = None) -> str:
        params: list[Any] = []
        sql = "SELECT invoice_prefix FROM projects WHERE TRIM(invoice_prefix) <> ''"
        if exclude_project_id:
            sql += " AND id <> ?"
            params.append(exclude_project_id)
        used = {str(row["invoice_prefix"]).strip() for row in self.conn.execute(sql, params).fetchall()}
        candidate = 1
        while str(candidate) in used:
            candidate += 1
        return str(candidate)

    def _ensure_project_invoice_prefix_available(self, prefix: str, exclude_project_id: int | None = None) -> None:
        params: list[Any] = [prefix]
        sql = "SELECT id FROM projects WHERE invoice_prefix = ?"
        if exclude_project_id:
            sql += " AND id <> ?"
            params.append(exclude_project_id)
        if self.conn.execute(sql, params).fetchone():
            raise ValueError(f"Blok faktura {prefix} je već dodeljen drugom projektu.")

    def preview_project_invoice_number(self, project_id: int) -> str:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Projekat ne postoji.")
        prefix = self._normalize_project_invoice_prefix(project.get("invoice_prefix")) or str(project_id)
        local_sequence = int(project.get("next_invoice_number") or 1)
        while self.conn.execute("SELECT 1 FROM invoices WHERE invoice_number = ?", (project_invoice_number(prefix, local_sequence),)).fetchone():
            local_sequence += 1
        return project_invoice_number(prefix, local_sequence)

    def reserve_project_invoice_number(self, project_id: int) -> str:
        """Reserve the next visible number only when a new project invoice is saved."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Projekat ne postoji.")
        prefix = self._normalize_project_invoice_prefix(project.get("invoice_prefix")) or str(project_id)
        local_sequence = int(project.get("next_invoice_number") or 1)
        invoice_number = project_invoice_number(prefix, local_sequence)
        while self.conn.execute("SELECT 1 FROM invoices WHERE invoice_number = ?", (invoice_number,)).fetchone():
            local_sequence += 1
            invoice_number = project_invoice_number(prefix, local_sequence)
        self.conn.execute(
            "UPDATE projects SET invoice_prefix = ?, next_invoice_number = ?, updated_at = ? WHERE id = ?",
            (prefix, local_sequence + 1, now_iso(), project_id),
        )
        return invoice_number

    def list_customers(self, search: str = "", include_archived: bool = False) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if search:
            where.append("(name LIKE ? OR eik LIKE ? OR vat_number LIKE ? OR address LIKE ?)")
            q = f"%{search}%"
            params.extend([q, q, q, q])
        if not include_archived:
            where.append("archived = 0")
        sql = "SELECT * FROM customers"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY name COLLATE NOCASE"
        return [row_to_dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_customer(self, customer_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        return row_to_dict(row)

    def save_customer(self, data: dict[str, Any]) -> int:
        now = now_iso()
        payload = {
            "name": data.get("name", "").strip(),
            "eik": data.get("eik", "").strip(),
            "vat_number": data.get("vat_number", "").strip(),
            "address": data.get("address", "").strip(),
            "contact_person": data.get("contact_person", "").strip(),
            "phone": data.get("phone", "").strip(),
            "email": data.get("email", "").strip(),
            "payment_term_days": int(data.get("payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS),
            "note": data.get("note", "").strip(),
            "archived": int(bool(data.get("archived", 0))),
            "updated_at": now,
        }
        self._backup_before_change(f"customer_{data.get('id') or 'new'}")
        if data.get("id"):
            payload["id"] = int(data["id"])
            self.conn.execute(
                """
                UPDATE customers
                SET name=:name, eik=:eik, vat_number=:vat_number, address=:address,
                    contact_person=:contact_person, phone=:phone, email=:email,
                    payment_term_days=:payment_term_days, note=:note, archived=:archived,
                    updated_at=:updated_at
                WHERE id=:id
                """,
                payload,
            )
            customer_id = int(data["id"])
        else:
            payload["created_at"] = now
            cur = self.conn.execute(
                """
                INSERT INTO customers (
                    name, eik, vat_number, address, contact_person, phone, email,
                    payment_term_days, note, archived, created_at, updated_at
                ) VALUES (
                    :name, :eik, :vat_number, :address, :contact_person, :phone, :email,
                    :payment_term_days, :note, :archived, :created_at, :updated_at
                )
                """,
                payload,
            )
            customer_id = int(cur.lastrowid)
        self.conn.commit()
        return customer_id

    def archive_customer(self, customer_id: int, archived: bool = True) -> None:
        self._backup_before_change(f"customer_archive_{customer_id}")
        self.conn.execute(
            "UPDATE customers SET archived = ?, updated_at = ? WHERE id = ?",
            (1 if archived else 0, now_iso(), customer_id),
        )
        self.conn.commit()

    def list_projects(self, customer_id: Optional[int] = None, include_archived: bool = False) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if customer_id:
            where.append("customer_id = ?")
            params.append(customer_id)
        if not include_archived:
            where.append("archived = 0")
        sql = "SELECT * FROM projects"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY name COLLATE NOCASE"
        return [row_to_dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_project(self, project_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return row_to_dict(row)

    def _ensure_project_archive_folder(self, project_id: int) -> str:
        """Backfill one stable folder name for projects created before this feature."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Projekat ne postoji.")
        folder_name = str(project.get("archive_folder") or "").strip()
        if folder_name:
            return folder_name
        folder_name = project_archive_folder_name(project_id, str(project.get("name") or ""))
        self.conn.execute(
            "UPDATE projects SET archive_folder = ?, updated_at = ? WHERE id = ?",
            (folder_name, now_iso(), project_id),
        )
        self.conn.commit()
        return folder_name

    def project_archive_dir(self, project_id: int) -> Path:
        """Create and return the project-owned document archive shown by the application."""
        folder_name = self._ensure_project_archive_folder(project_id)
        root = invoice_dir() / PROJECTS_DIR_NAME / folder_name
        for folder in (
            root / PROJECT_DOCUMENTS_DIR_NAME,
            root / PROJECT_INPUT_INVOICES_DIR_NAME,
            root / PROJECT_OUTPUT_INVOICES_DIR_NAME,
            root / CREDIT_NOTES_DIR_NAME,
            root / PROJECT_REPORTS_DIR_NAME / PROJECT_VAT_REPORTS_DIR_NAME,
            root / PROJECT_REPORTS_DIR_NAME / PROJECT_ACCOUNTANT_REPORTS_DIR_NAME,
        ):
            folder.mkdir(parents=True, exist_ok=True)
        return root

    def project_documents_dir(self, project_id: int) -> Path:
        return self.project_archive_dir(project_id) / PROJECT_DOCUMENTS_DIR_NAME

    def project_input_invoices_dir(self, project_id: int) -> Path:
        """Folder for original incoming invoices imported into one project."""
        return self.project_archive_dir(project_id) / PROJECT_INPUT_INVOICES_DIR_NAME

    def project_output_invoices_dir(self, project_id: int) -> Path:
        """Folder for original outgoing invoices imported into one project."""
        return self.project_archive_dir(project_id) / PROJECT_OUTPUT_INVOICES_DIR_NAME

    def project_credit_notes_dir(self, project_id: int) -> Path:
        """Folder that owns all formal credit notes for one project."""
        return self.project_archive_dir(project_id) / CREDIT_NOTES_DIR_NAME

    def project_vat_reports_dir(self, project_id: int, period_from: Any = None) -> Path:
        """Store accountant-ready VAT working reports inside the owning project."""
        report_date = parse_date(period_from) or date.today()
        folder = self.project_archive_dir(project_id) / PROJECT_REPORTS_DIR_NAME / PROJECT_VAT_REPORTS_DIR_NAME / str(report_date.year)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def project_accountant_reports_dir(self, project_id: int, period_from: Any = None) -> Path:
        """Keep one complete accountant export package in the owning project archive."""
        report_date = parse_date(period_from) or date.today()
        folder = self.project_archive_dir(project_id) / PROJECT_REPORTS_DIR_NAME / PROJECT_ACCOUNTANT_REPORTS_DIR_NAME / str(report_date.year)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def vendor_bill_attachments_dir(self, bill_id: int) -> Path:
        """Return the company archive folder for one incoming supplier bill.

        Unlike project documents, supplier bills may be company-wide.  Keeping
        their originals under the invoice archive means a file selected from
        Downloads or e-mail remains available after that source is removed.
        """
        bill = self.get_vendor_bill(bill_id)
        if not bill:
            raise ValueError("Obaveza dobavljača ne postoji.")
        bill_date = parse_date(bill.get("bill_date")) or date.today()
        vendor = safe_filename(str(bill.get("vendor_name") or "Dobavljac"))[:64]
        folder = invoice_dir() / PROJECT_INPUT_INVOICES_DIR_NAME / str(bill_date.year) / f"OB-{int(bill_id):06d}_{vendor}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def archive_vendor_bill_attachment(self, bill_id: int, source_path: Any) -> str:
        """Copy one selected supplier document into the permanent local archive."""
        source = Path(str(source_path or "")).expanduser()
        if not source.is_file():
            raise ValueError("Izabrani dokument dobavljača ne postoji ili nije datoteka.")
        destination_root = self.vendor_bill_attachments_dir(bill_id)
        destination = destination_root / safe_filename(source.name)
        index = 1
        while destination.exists() and destination.resolve() != source.resolve():
            destination = destination_root / f"{safe_filename(source.stem)}_{index}{source.suffix.lower()}"
            index += 1
        if destination.resolve() != source.resolve():
            shutil.copy2(source, destination)
        return str(destination)

    def invoice_archive_dir(self, invoice_id: int) -> Path:
        """Return the one folder that owns an invoice, its PDF, Excel copy, and attachments."""
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            raise ValueError("Faktura ne postoji.")
        project_id = int(invoice.get("project_id") or 0)
        if project_id and self.get_project(project_id):
            project_root = self.project_archive_dir(project_id)
            output_root = project_root / PROJECT_OUTPUT_INVOICES_DIR_NAME
        else:
            output_root = invoice_dir() / UNASSIGNED_PROJECT_DIR_NAME / PROJECT_OUTPUT_INVOICES_DIR_NAME
        issue_date = parse_date(invoice.get("issue_date")) or date.today()
        invoice_number = safe_filename(str(invoice.get("invoice_number") or f"invoice_{invoice_id}"))
        folder = output_root / str(issue_date.year) / invoice_number
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def invoice_attachments_dir(self, invoice_id: int) -> Path:
        folder = self.invoice_archive_dir(invoice_id) / ATTACHMENTS_DIR_NAME
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def credit_note_archive_dir(self, credit_note_id: int) -> Path:
        note = self.get_credit_note(credit_note_id)
        if not note:
            raise ValueError("Odobrenje ne postoji.")
        issue_date = parse_date(note.get("issue_date")) or date.today()
        folder = self.project_credit_notes_dir(int(note["project_id"])) / str(issue_date.year) / safe_filename(
            str(note.get("credit_note_number") or f"odobrenje_{credit_note_id}")
        )
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save_project(self, data: dict[str, Any]) -> int:
        self.assert_business_write_access()
        now = now_iso()
        existing_project = self.get_project(int(data["id"])) if data.get("id") else {}
        if not existing_project and not bool(data.get("archived", 0)):
            self.assert_new_project_allowed()
        requested_prefix = self._normalize_project_invoice_prefix(data.get("invoice_prefix"))
        if not requested_prefix:
            requested_prefix = str(existing_project.get("invoice_prefix") or "") or self._next_available_project_invoice_prefix(
                int(data["id"]) if data.get("id") else None
            )
        self._ensure_project_invoice_prefix_available(
            requested_prefix,
            int(data["id"]) if data.get("id") else None,
        )
        self._backup_before_change(f"project_{data.get('id') or 'new'}")
        if existing_project and requested_prefix != str(existing_project.get("invoice_prefix") or ""):
            has_invoices = self.conn.execute(
                "SELECT 1 FROM invoices WHERE project_id = ? LIMIT 1",
                (int(data["id"]),),
            ).fetchone()
            if has_invoices:
                raise ValueError("Blok faktura ne može da se promeni nakon prve fakture projekta.")
        contract_net_amount = money_round(data.get("contract_net_amount") or 0)
        advance_percent = decimal_from(data.get("advance_percent") or 0)
        if contract_net_amount < 0:
            raise ValueError("Vrednost ugovora bez PDV-a ne može biti negativna.")
        if advance_percent < 0 or advance_percent > 100:
            raise ValueError("Procenat avansa mora biti između 0 i 100.")
        if advance_percent > 0 and contract_net_amount <= 0:
            raise ValueError("Za procenat avansa unesite vrednost ugovora bez PDV-a.")
        payload = {
            "customer_id": int(data["customer_id"]) if data.get("customer_id") else None,
            "name": data.get("name", "").strip(),
            "site_address": data.get("site_address", "").strip(),
            "contract_no": data.get("contract_no", "").strip(),
            "contract_net_amount": float(contract_net_amount),
            "advance_percent": float(advance_percent),
            "protocol_no": data.get("protocol_no", "").strip(),
            "period_from": iso_from_date(data.get("period_from")) or "",
            "period_to": iso_from_date(data.get("period_to")) or "",
            "order_reference": data.get("order_reference", "").strip(),
            "note": data.get("note", "").strip(),
            "invoice_prefix": requested_prefix,
            "next_invoice_number": int(existing_project.get("next_invoice_number") or 1),
            "archived": int(bool(data.get("archived", 0))),
            "updated_at": now,
        }
        if data.get("id"):
            payload["id"] = int(data["id"])
            self.conn.execute(
                """
                UPDATE projects
                SET customer_id=:customer_id, name=:name, site_address=:site_address,
                    contract_no=:contract_no, contract_net_amount=:contract_net_amount, advance_percent=:advance_percent, protocol_no=:protocol_no, period_from=:period_from,
                    period_to=:period_to, order_reference=:order_reference, note=:note,
                    invoice_prefix=:invoice_prefix, next_invoice_number=:next_invoice_number,
                    archived=:archived, updated_at=:updated_at
                WHERE id=:id
                """,
                payload,
            )
            project_id = int(data["id"])
        else:
            payload["created_at"] = now
            cur = self.conn.execute(
                """
                INSERT INTO projects (
                    customer_id, name, invoice_prefix, next_invoice_number, site_address, contract_no, contract_net_amount, advance_percent, protocol_no,
                    period_from, period_to, order_reference, note, archived, created_at, updated_at
                ) VALUES (
                    :customer_id, :name, :invoice_prefix, :next_invoice_number, :site_address, :contract_no, :contract_net_amount, :advance_percent, :protocol_no,
                    :period_from, :period_to, :order_reference, :note, :archived, :created_at, :updated_at
                )
                """,
                payload,
            )
            project_id = int(cur.lastrowid)
            archive_folder = project_archive_folder_name(project_id, payload["name"])
            self.conn.execute(
                "UPDATE projects SET archive_folder = ? WHERE id = ?",
                (archive_folder, project_id),
            )
        self.conn.commit()
        self.project_archive_dir(project_id)
        return project_id

    def project_advance_terms(self, project_id: int) -> dict[str, Decimal | str]:
        """Return the contract-controlled advance amount for a project.

        The amount belongs to the project agreement, never to a material or
        labour line.  The resulting invoice still receives one protected
        technical line so its tax totals, PDF and e-invoice export stay valid.
        """
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Projekat ne postoji.")
        contract_net = money_round(project.get("contract_net_amount") or 0)
        percent = decimal_from(project.get("advance_percent") or 0)
        if contract_net <= 0:
            raise ValueError("U projektu unesite vrednost ugovora bez PDV-a pre izdavanja avansa.")
        if percent <= 0 or percent > 100:
            raise ValueError("U projektu unesite procenat avansa između 0 i 100.")
        return {
            "contract_net_amount": contract_net,
            "advance_percent": percent,
            "advance_net_amount": money_round(contract_net * percent / Decimal("100")),
            "contract_no": str(project.get("contract_no") or "").strip(),
        }

    def project_advance_invoice_item(self, project_id: int) -> dict[str, Any]:
        terms = self.project_advance_terms(project_id)
        contract_label = f" po ugovoru {terms['contract_no']}" if terms["contract_no"] else ""
        return {
            "category": "Ugovorni avans",
            "description": f"Avans {terms['advance_percent']:g}%{contract_label}",
            "unit": "kom.",
            "quantity": 1,
            "unit_price": float(terms["advance_net_amount"]),
            "discount_percent": 0,
            "code_stage": "AVANS",
        }

    def archive_project(self, project_id: int, archived: bool = True) -> None:
        self._backup_before_change(f"project_archive_{project_id}")
        self.conn.execute(
            "UPDATE projects SET archived = ?, updated_at = ? WHERE id = ?",
            (1 if archived else 0, now_iso(), project_id),
        )
        self.conn.commit()

    def get_project_document(self, document_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM project_documents WHERE id = ?", (document_id,)).fetchone()
        return row_to_dict(row)

    def list_project_documents(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM project_documents WHERE project_id = ? ORDER BY document_date DESC, id DESC",
            (project_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    @staticmethod
    def _pdf_partner_key(value: Any) -> str:
        normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        # Keep non-Latin letters too, so supplier mappings also work in SR/RU/BG invoices.
        return "".join(char for char in normalized if char.isalnum())[:180]

    def get_pdf_partner_mapping(self, extracted_name: Any) -> dict[str, Any]:
        """Return a remembered supplier/customer choice for a recognized PDF name."""
        lookup_key = self._pdf_partner_key(extracted_name)
        if not lookup_key:
            return {}
        row = self.conn.execute(
            "SELECT * FROM pdf_partner_mappings WHERE lookup_key = ?",
            (lookup_key,),
        ).fetchone()
        return row_to_dict(row)

    def remember_pdf_partner_mapping(
        self,
        extracted_name: Any,
        partner_name: Any,
        document_type: str,
        cost_group: str,
        vat_rate: Any,
        *,
        commit: bool = True,
    ) -> None:
        """Remember a reviewed PDF import so the next import needs less typing."""
        lookup_key = self._pdf_partner_key(extracted_name)
        clean_partner = str(partner_name or "").strip()
        if not lookup_key or not clean_partner:
            return
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO pdf_partner_mappings (
                lookup_key, extracted_name, partner_name, document_type, cost_group, vat_rate,
                usage_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(lookup_key) DO UPDATE SET
                extracted_name=excluded.extracted_name,
                partner_name=excluded.partner_name,
                document_type=excluded.document_type,
                cost_group=excluded.cost_group,
                vat_rate=excluded.vat_rate,
                usage_count=pdf_partner_mappings.usage_count + 1,
                updated_at=excluded.updated_at
            """,
            (
                lookup_key,
                str(extracted_name or "").strip(),
                clean_partner,
                str(document_type or "input").strip().lower(),
                str(cost_group or "").strip(),
                float(decimal_from(vat_rate or DEFAULT_VAT_RATE)),
                now,
                now,
            ),
        )
        if commit:
            self.conn.commit()

    def project_vat_evidence(self, project_id: int, period_from: Any, period_to: Any) -> dict[str, Any]:
        """Build a EUR-only working VAT ledger for an accountant, grouped by project and period.

        This is deliberately a review/export aid, not a generated NRA submission file.
        Draft and cancelled invoices are excluded; formal credit notes reduce output VAT.
        """
        project = self.get_project(int(project_id))
        if not project:
            raise ValueError("Projekat ne postoji.")
        start = parse_date(period_from)
        end = parse_date(period_to)
        if not start or not end:
            raise ValueError("Unesite početak i kraj perioda u formatu dd.mm.gggg.")
        if start > end:
            raise ValueError("Početak perioda ne može biti posle kraja perioda.")

        invoice_rows = self.conn.execute(
            """
            SELECT id, issue_date AS document_date, invoice_number AS document_no,
                   customer_name AS partner_name, customer_vat AS partner_vat,
                   project_name AS description, tax_base AS net_amount,
                   vat_total AS vat_amount, gross_total AS gross_amount, currency
            FROM invoices
            WHERE project_id = ? AND status_code NOT IN ('draft', 'pending_approval', 'approved', 'cancelled')
            ORDER BY issue_date, id
            """,
            (int(project_id),),
        ).fetchall()
        credit_rows = self.conn.execute(
            """
            SELECT c.id, c.issue_date AS document_date, c.credit_note_number AS document_no,
                   c.customer_name AS partner_name, i.customer_vat AS partner_vat,
                   ('Uz fakturu ' || COALESCE(c.source_invoice_number, '') || ': ' || COALESCE(c.reason, '')) AS description,
                   c.net_amount, c.vat_amount, c.gross_amount, c.currency
            FROM credit_notes c
            LEFT JOIN invoices i ON i.id = c.source_invoice_id
            WHERE c.project_id = ?
            ORDER BY c.issue_date, c.id
            """,
            (int(project_id),),
        ).fetchall()
        project_document_rows = self.conn.execute(
            """
            SELECT id, document_type, document_date, document_no,
                   partner_name, '' AS partner_vat, description,
                   net_amount, vat_amount, gross_amount, currency, cost_group
            FROM project_documents
            WHERE project_id = ?
            ORDER BY document_date, id
            """,
            (int(project_id),),
        ).fetchall()

        output_rows: list[dict[str, Any]] = []
        input_rows: list[dict[str, Any]] = []
        foreign_currency_rows: list[dict[str, Any]] = []
        missing_date_rows: list[dict[str, Any]] = []

        def include_row(
            row: sqlite3.Row,
            *,
            section: str,
            document_type: str,
            multiplier: Decimal = Decimal("1"),
        ) -> None:
            source = row_to_dict(row)
            document_date = parse_date(source.get("document_date"))
            prepared = {
                "id": int(source.get("id") or 0),
                "section": section,
                "document_type": document_type,
                "document_date": document_date.isoformat() if document_date else "",
                "document_no": str(source.get("document_no") or ""),
                "partner_name": str(source.get("partner_name") or ""),
                "partner_vat": str(source.get("partner_vat") or ""),
                "description": str(source.get("description") or ""),
                "net_amount": money_round(multiplier * decimal_from(source.get("net_amount") or 0)),
                "vat_amount": money_round(multiplier * decimal_from(source.get("vat_amount") or 0)),
                "gross_amount": money_round(multiplier * decimal_from(source.get("gross_amount") or 0)),
                "currency": str(source.get("currency") or DEFAULT_CURRENCY).upper(),
            }
            if not document_date:
                missing_date_rows.append(prepared)
                return
            if document_date < start or document_date > end:
                return
            if prepared["currency"] != DEFAULT_CURRENCY:
                foreign_currency_rows.append(prepared)
                return
            (output_rows if section == "output" else input_rows).append(prepared)

        for row in invoice_rows:
            include_row(row, section="output", document_type="Izdana faktura")
        for row in credit_rows:
            include_row(row, section="output", document_type="Kreditno odobrenje", multiplier=Decimal("-1"))
        for row in project_document_rows:
            section = "input" if row["document_type"] == "input" else "output"
            label = "Ulazni račun" if section == "input" else "Izlazni račun"
            include_row(row, section=section, document_type=label)

        output_rows.sort(key=lambda item: (item["document_date"], item["document_no"], item["id"]))
        input_rows.sort(key=lambda item: (item["document_date"], item["document_no"], item["id"]))

        def total(rows: list[dict[str, Any]], field: str) -> Decimal:
            return money_round(sum((decimal_from(row.get(field) or 0) for row in rows), Decimal("0")))

        output_net = total(output_rows, "net_amount")
        output_vat = total(output_rows, "vat_amount")
        output_gross = total(output_rows, "gross_amount")
        input_net = total(input_rows, "net_amount")
        input_vat = total(input_rows, "vat_amount")
        input_gross = total(input_rows, "gross_amount")
        return {
            "project": project,
            "company": self.get_company(),
            "period_from": start.isoformat(),
            "period_to": end.isoformat(),
            "generated_at": now_iso(),
            "currency": DEFAULT_CURRENCY,
            "output_rows": output_rows,
            "input_rows": input_rows,
            "foreign_currency_rows": foreign_currency_rows,
            "missing_date_rows": missing_date_rows,
            "totals": {
                "output_net": output_net,
                "output_vat": output_vat,
                "output_gross": output_gross,
                "input_net": input_net,
                "input_vat": input_vat,
                "input_gross": input_gross,
                "vat_payable": money_round(output_vat - input_vat),
                "output_document_count": len(output_rows),
                "input_document_count": len(input_rows),
            },
        }

    def project_accountant_report(self, project_id: int, period_from: Any, period_to: Any) -> dict[str, Any]:
        """Build one clear, EUR-only accountant package for a selected project period.

        The detailed VAT report remains the source for issued and incoming documents.
        This adds cash movements plus cancelled invoices so a beginner does not need to
        assemble separate lists before sending the period to an accountant.
        """
        report = self.project_vat_evidence(project_id, period_from, period_to)
        start = parse_date(report["period_from"])
        end = parse_date(report["period_to"])
        if not start or not end:
            raise ValueError("Period za izvoz nije ispravan.")

        payment_rows = self.conn.execute(
            """
            SELECT p.id, p.payment_date, p.amount, p.method, p.note,
                   i.id AS invoice_id, i.invoice_number, i.customer_name, i.currency
            FROM payments p
            JOIN invoices i ON i.id = p.invoice_id
            WHERE i.project_id = ?
              AND p.payment_date >= ? AND p.payment_date <= ?
            ORDER BY p.payment_date, p.id
            """,
            (int(project_id), start.isoformat(), end.isoformat()),
        ).fetchall()
        payments: list[dict[str, Any]] = []
        foreign_currency_payments: list[dict[str, Any]] = []
        for row in payment_rows:
            item = row_to_dict(row)
            currency = str(item.get("currency") or DEFAULT_CURRENCY).upper()
            amount = money_round(item.get("amount") or 0)
            prepared = {
                "id": int(item.get("id") or 0),
                "payment_date": str(item.get("payment_date") or ""),
                "type": "Uplata" if amount >= 0 else "Povraćaj uplate",
                "invoice_number": str(item.get("invoice_number") or ""),
                "partner_name": str(item.get("customer_name") or ""),
                "method": str(item.get("method") or ""),
                "note": str(item.get("note") or ""),
                "amount": amount,
                "currency": currency,
            }
            if currency != DEFAULT_CURRENCY:
                foreign_currency_payments.append(prepared)
            else:
                payments.append(prepared)

        cancelled_rows = self.conn.execute(
            """
            SELECT id, invoice_number, customer_name, issue_date, cancelled_at,
                   tax_base, vat_total, gross_total, currency, note
            FROM invoices
            WHERE project_id = ? AND status_code = 'cancelled'
              AND substr(NULLIF(cancelled_at, ''), 1, 10) >= ?
              AND substr(NULLIF(cancelled_at, ''), 1, 10) <= ?
            ORDER BY cancelled_at, id
            """,
            (int(project_id), start.isoformat(), end.isoformat()),
        ).fetchall()
        cancelled: list[dict[str, Any]] = []
        for row in cancelled_rows:
            item = row_to_dict(row)
            cancelled.append(
                {
                    "id": int(item.get("id") or 0),
                    "document_date": str(item.get("cancelled_at") or "")[:10],
                    "document_no": str(item.get("invoice_number") or ""),
                    "partner_name": str(item.get("customer_name") or ""),
                    "description": str(item.get("note") or ""),
                    "net_amount": money_round(item.get("tax_base") or 0),
                    "vat_amount": money_round(item.get("vat_total") or 0),
                    "gross_amount": money_round(item.get("gross_total") or 0),
                    "currency": str(item.get("currency") or DEFAULT_CURRENCY).upper(),
                }
            )

        corrections = [
            row for row in report["output_rows"] if row.get("document_type") == "Kreditno odobrenje"
        ]
        paid_total = money_round(sum((decimal_from(row["amount"]) for row in payments if row["amount"] > 0), Decimal("0")))
        refund_total = money_round(sum((-decimal_from(row["amount"]) for row in payments if row["amount"] < 0), Decimal("0")))
        net_collected = money_round(sum((decimal_from(row["amount"]) for row in payments), Decimal("0")))
        result = dict(report)
        result.update(
            {
                "payment_rows": payments,
                "foreign_currency_payments": foreign_currency_payments,
                "credit_note_rows": corrections,
                "cancelled_rows": cancelled,
            }
        )
        result["totals"] = {
            **report["totals"],
            "payment_total": paid_total,
            "refund_total": refund_total,
            "net_collected": net_collected,
            "payment_count": len(payments),
            "cancelled_invoice_count": len(cancelled),
            "credit_note_count": len(corrections),
        }
        return result

    def project_period_summary(self, project_id: int, period_from: Any, period_to: Any) -> dict[str, Any]:
        """Return the beginner-friendly income, cost, collection and VAT view for one period."""
        report = self.project_accountant_report(project_id, period_from, period_to)
        start = str(report["period_from"])
        end = str(report["period_to"])
        receivables = self.conn.execute(
            """
            SELECT COALESCE(SUM(
                CASE WHEN i.balance_total > COALESCE(c.gross_total, 0)
                THEN i.balance_total - COALESCE(c.gross_total, 0) ELSE 0 END
            ), 0) AS balance_total
            FROM invoices i
            LEFT JOIN (
                SELECT source_invoice_id, COALESCE(SUM(gross_amount), 0) AS gross_total
                FROM credit_notes
                GROUP BY source_invoice_id
            ) c ON c.source_invoice_id = i.id
            WHERE i.project_id = ?
              AND i.status_code NOT IN ('draft', 'pending_approval', 'approved', 'cancelled')
              AND i.issue_date >= ? AND i.issue_date <= ?
              AND i.currency = ?
            """,
            (int(project_id), start, end, DEFAULT_CURRENCY),
        ).fetchone()
        totals = report["totals"]
        income_net = money_round(totals["output_net"])
        expense_net = money_round(totals["input_net"])
        return {
            "project": report["project"],
            "period_from": start,
            "period_to": end,
            "income_net": income_net,
            "expense_net": expense_net,
            "paid_total": money_round(totals["net_collected"]),
            "open_invoice_total": money_round(receivables["balance_total"] if receivables else 0),
            "output_vat": money_round(totals["output_vat"]),
            "input_vat": money_round(totals["input_vat"]),
            "vat_payable": money_round(totals["vat_payable"]),
            "profit_net": money_round(income_net - expense_net),
            "invoice_count": int(totals["output_document_count"]),
            "input_document_count": int(totals["input_document_count"]),
        }

    def project_reminders(self, project_id: int, days: int = 7) -> dict[str, Any]:
        """Collect only the practical follow-ups a project owner needs to see."""
        project = self.get_project(int(project_id))
        if not project:
            raise ValueError("Projekat ne postoji.")
        today = date.today()
        limit = today + timedelta(days=max(0, int(days)))
        due_soon_rows = self.conn.execute(
            """
            SELECT id, invoice_number, customer_name, due_date, balance_total, currency
            FROM invoices
            WHERE project_id = ? AND status_code NOT IN ('draft', 'pending_approval', 'approved', 'cancelled', 'paid')
              AND balance_total > 0 AND due_date >= ? AND due_date <= ?
            ORDER BY due_date, id
            """,
            (int(project_id), today.isoformat(), limit.isoformat()),
        ).fetchall()
        overdue_rows = self.conn.execute(
            """
            SELECT id, invoice_number, customer_name, due_date, balance_total, currency
            FROM invoices
            WHERE project_id = ? AND status_code NOT IN ('draft', 'pending_approval', 'approved', 'cancelled', 'paid')
              AND balance_total > 0 AND due_date <> '' AND due_date < ?
            ORDER BY due_date, id
            """,
            (int(project_id), today.isoformat()),
        ).fetchall()
        missing_pdf_rows = self.conn.execute(
            """
            SELECT id, document_date, document_no, partner_name, description, gross_amount
            FROM project_documents
            WHERE project_id = ? AND document_type = 'input'
              AND note NOT LIKE '%PDF:%'
            ORDER BY document_date, id
            """,
            (int(project_id),),
        ).fetchall()
        budget = self.project_financial_summary(project_id).get("cost_groups", {})
        over_budget = [
            {"group": group, **values}
            for group, values in budget.items()
            if values.get("over_budget")
        ]
        due_soon = [row_to_dict(row) for row in due_soon_rows]
        overdue = [row_to_dict(row) for row in overdue_rows]
        missing_pdf = [row_to_dict(row) for row in missing_pdf_rows]
        return {
            "project": project,
            "days": max(1, int(days)),
            "due_soon": due_soon,
            "overdue": overdue,
            "missing_pdf": missing_pdf,
            "over_budget": over_budget,
            "total_count": len(due_soon) + len(overdue) + len(missing_pdf) + len(over_budget),
        }

    def save_project_document(self, data: dict[str, Any]) -> int:
        """Save a manual input or output invoice that belongs to one project."""
        self.assert_business_write_access()
        project_id = int(data.get("project_id") or 0)
        if not project_id or not self.get_project(project_id):
            raise ValueError("Izaberite postojeći projekat.")
        existing_document = self.get_project_document(int(data["id"])) if data.get("id") else {}
        document_type = str(data.get("document_type") or "input").strip().lower()
        if document_type not in PROJECT_DOCUMENT_TYPES:
            raise ValueError("Tip dokumenta mora biti ulazni ili izlazni račun.")
        allowed_groups = PROJECT_COST_GROUPS if document_type == "input" else PROJECT_INCOME_GROUPS
        default_group = "Ostali troškovi" if document_type == "input" else "Ostali prihodi"
        cost_group = str(data.get("cost_group") or default_group).strip()
        if cost_group not in allowed_groups:
            cost_group = default_group
        document_date = iso_from_date(data.get("document_date")) or today_iso()
        self.assert_financial_date_open(document_date)
        vat_rate = decimal_from(data.get("vat_rate") or 0)
        if vat_rate > 1:
            vat_rate /= Decimal("100")
        vat_rate = max(Decimal("0"), vat_rate)
        net_amount = money_round(data.get("net_amount") or 0)
        vat_amount = money_round(net_amount * vat_rate)
        gross_amount = money_round(net_amount + vat_amount)
        requested_currency = normalize_currency(data.get("currency"), fallback=self.get_company().get("default_currency") or DEFAULT_CURRENCY)
        historic_currency = str(existing_document.get("currency") or "").strip().upper()
        if historic_currency:
            currency = historic_currency
        else:
            currency = requested_currency
        now = now_iso()
        payload = {
            "project_id": project_id,
            "document_type": document_type,
            "cost_group": cost_group,
            "document_date": document_date,
            "document_no": str(data.get("document_no") or "").strip(),
            "partner_name": str(data.get("partner_name") or "").strip(),
            "description": str(data.get("description") or "").strip(),
            "net_amount": float(net_amount),
            "vat_rate": float(vat_rate),
            "vat_amount": float(vat_amount),
            "gross_amount": float(gross_amount),
            "currency": currency,
            "note": str(data.get("note") or "").strip(),
            "updated_at": now,
        }
        if not data.get("id") and "PDF:" in payload["note"]:
            self.assert_pdf_import_allowed()
        self._backup_before_change(f"project_document_{project_id}", replaces_post_backup=True)
        if data.get("id"):
            payload["id"] = int(data["id"])
            self.conn.execute(
                """
                UPDATE project_documents SET
                    project_id=:project_id, document_type=:document_type, cost_group=:cost_group,
                    document_date=:document_date, document_no=:document_no, partner_name=:partner_name,
                    description=:description, net_amount=:net_amount, vat_rate=:vat_rate,
                    vat_amount=:vat_amount, gross_amount=:gross_amount, currency=:currency,
                    note=:note, updated_at=:updated_at
                WHERE id=:id
                """,
                payload,
            )
            document_id = int(data["id"])
        else:
            payload["created_at"] = now
            cur = self.conn.execute(
                """
                INSERT INTO project_documents (
                    project_id, document_type, cost_group, document_date, document_no, partner_name,
                    description, net_amount, vat_rate, vat_amount, gross_amount, currency, note,
                    created_at, updated_at
                ) VALUES (
                    :project_id, :document_type, :cost_group, :document_date, :document_no, :partner_name,
                    :description, :net_amount, :vat_rate, :vat_amount, :gross_amount, :currency, :note,
                    :created_at, :updated_at
                )
                """,
                payload,
            )
            document_id = int(cur.lastrowid)
        if "PDF:" in payload["note"]:
            self.remember_pdf_partner_mapping(
                data.get("ocr_partner_name") or payload["partner_name"],
                payload["partner_name"],
                document_type,
                cost_group,
                vat_rate,
                commit=False,
            )
        self.conn.commit()
        self._maybe_backup(f"project_document_{project_id}")
        return document_id

    def delete_project_document(self, document_id: int) -> None:
        row = self.get_project_document(document_id)
        if row:
            self._backup_before_change(f"project_document_{row.get('project_id', 'deleted')}", replaces_post_backup=True)
        self.conn.execute("DELETE FROM project_documents WHERE id = ?", (document_id,))
        self.conn.commit()
        if row:
            self._maybe_backup(f"project_document_{row.get('project_id', 'deleted')}")

    def get_project_budget(self, project_id: int) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "project_id": project_id,
            "planned_income_net": Decimal("0"),
            "planned_rad_net": Decimal("0"),
            "planned_material_net": Decimal("0"),
            "planned_plates_net": Decimal("0"),
            "planned_other_costs_net": Decimal("0"),
            "note": "",
            "updated_at": "",
            "is_configured": False,
        }
        row = self.conn.execute("SELECT * FROM project_budgets WHERE project_id = ?", (project_id,)).fetchone()
        if not row:
            return defaults
        budget = row_to_dict(row)
        for key in (
            "planned_income_net",
            "planned_rad_net",
            "planned_material_net",
            "planned_plates_net",
            "planned_other_costs_net",
        ):
            budget[key] = money_round(budget.get(key) or 0)
        budget["is_configured"] = True
        return budget

    def save_project_budget(self, project_id: int, data: dict[str, Any]) -> None:
        if not self.get_project(project_id):
            raise ValueError("Projekat ne postoji.")
        fields = (
            "planned_income_net",
            "planned_rad_net",
            "planned_material_net",
            "planned_plates_net",
            "planned_other_costs_net",
        )
        payload: dict[str, Any] = {"project_id": project_id, "note": str(data.get("note") or "").strip(), "updated_at": now_iso()}
        for field in fields:
            value = money_round(data.get(field) or 0)
            if value < 0:
                raise ValueError("Budžetski iznosi ne mogu biti negativni.")
            payload[field] = float(value)
        self._backup_before_change(f"project_budget_{project_id}", replaces_post_backup=True)
        self.conn.execute(
            """
            INSERT INTO project_budgets (
                project_id, planned_income_net, planned_rad_net, planned_material_net,
                planned_plates_net, planned_other_costs_net, note, updated_at
            ) VALUES (
                :project_id, :planned_income_net, :planned_rad_net, :planned_material_net,
                :planned_plates_net, :planned_other_costs_net, :note, :updated_at
            )
            ON CONFLICT(project_id) DO UPDATE SET
                planned_income_net=excluded.planned_income_net,
                planned_rad_net=excluded.planned_rad_net,
                planned_material_net=excluded.planned_material_net,
                planned_plates_net=excluded.planned_plates_net,
                planned_other_costs_net=excluded.planned_other_costs_net,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            payload,
        )
        self.conn.commit()
        self._maybe_backup(f"project_budget_{project_id}")

    def project_financial_summary(self, project_id: int) -> dict[str, Any]:
        """Return revenue, expense groups and result excluding VAT from profit."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Projekat ne postoji.")
        invoices = self.conn.execute(
            """
            SELECT COALESCE(SUM(i.tax_base), 0) AS net_total,
                   COALESCE(SUM(i.vat_total), 0) AS vat_total,
                   COALESCE(SUM(i.gross_total), 0) AS gross_total,
                   COALESCE(SUM(i.paid_total), 0) AS paid_total,
                   COALESCE(SUM(
                       CASE WHEN i.balance_total > COALESCE(c.gross_total, 0)
                       THEN i.balance_total - COALESCE(c.gross_total, 0) ELSE 0 END
                   ), 0) AS balance_total,
                   COALESCE(SUM(
                       CASE
                           WHEN i.balance_total > COALESCE(c.gross_total, 0) AND i.due_date <> '' AND i.due_date < date('now')
                           THEN i.balance_total - COALESCE(c.gross_total, 0)
                           ELSE 0
                       END
                   ), 0) AS overdue_total,
                   COUNT(*) AS count
            FROM invoices i
            LEFT JOIN (
                SELECT source_invoice_id, COALESCE(SUM(gross_amount), 0) AS gross_total
                FROM credit_notes
                GROUP BY source_invoice_id
            ) c ON c.source_invoice_id = i.id
            WHERE i.project_id = ?
              AND i.status_code NOT IN ('draft', 'pending_approval', 'approved', 'cancelled')
              AND COALESCE(i.invoice_kind, 'standard') <> 'advance'
            """,
            (project_id,),
        ).fetchone()
        # An advance is a receivable under the agreement, not project income.
        # Keep it in a separate ledger so a project cannot appear profitable
        # merely because the customer paid an advance before work is billed.
        advances = self.conn.execute(
            """
            SELECT COALESCE(SUM(tax_base), 0) AS net_total,
                   COALESCE(SUM(vat_total), 0) AS vat_total,
                   COALESCE(SUM(gross_total), 0) AS gross_total,
                   COALESCE(SUM(paid_total), 0) AS paid_total,
                   COALESCE(SUM(balance_total), 0) AS balance_total,
                   COUNT(*) AS count
            FROM invoices
            WHERE project_id = ?
              AND invoice_kind = 'advance'
              AND status_code NOT IN ('draft', 'pending_approval', 'approved', 'cancelled')
            """,
            (project_id,),
        ).fetchone()
        credit_notes = self.conn.execute(
            """
            SELECT COALESCE(SUM(c.net_amount), 0) AS net_total,
                   COALESCE(SUM(c.vat_amount), 0) AS vat_total,
                   COALESCE(SUM(c.gross_amount), 0) AS gross_total,
                   COUNT(*) AS count
            FROM credit_notes c
            JOIN invoices i ON i.id = c.source_invoice_id
            WHERE c.project_id = ? AND i.status_code NOT IN ('draft', 'pending_approval', 'approved', 'cancelled')
            """,
            (project_id,),
        ).fetchone()
        manual_output = self.conn.execute(
            """
            SELECT COALESCE(SUM(net_amount), 0) AS net_total,
                   COALESCE(SUM(vat_amount), 0) AS vat_total,
                   COALESCE(SUM(gross_amount), 0) AS gross_total,
                   COUNT(*) AS count
            FROM project_documents
            WHERE project_id = ? AND document_type = 'output'
            """,
            (project_id,),
        ).fetchone()
        input_rows = self.conn.execute(
            """
            SELECT cost_group,
                   COALESCE(SUM(net_amount), 0) AS net_total,
                   COALESCE(SUM(vat_amount), 0) AS vat_total,
                   COALESCE(SUM(gross_amount), 0) AS gross_total,
                   COUNT(*) AS count
            FROM project_documents
            WHERE project_id = ? AND document_type = 'input'
            GROUP BY cost_group
            """,
            (project_id,),
        ).fetchall()

        def amount(row: Optional[sqlite3.Row], key: str) -> Decimal:
            return money_round(row[key] if row else 0)

        groups: dict[str, dict[str, Any]] = {
            group: {"net": Decimal("0"), "vat": Decimal("0"), "gross": Decimal("0"), "count": 0}
            for group in PROJECT_COST_GROUPS
        }
        for row in input_rows:
            group = row["cost_group"] if row["cost_group"] in groups else "Ostali troškovi"
            groups[group] = {
                "net": amount(row, "net_total"),
                "vat": amount(row, "vat_total"),
                "gross": amount(row, "gross_total"),
                "count": int(row["count"] or 0),
            }

        invoice_net = amount(invoices, "net_total")
        invoice_vat = amount(invoices, "vat_total")
        invoice_gross = amount(invoices, "gross_total")
        credit_net = amount(credit_notes, "net_total")
        credit_vat = amount(credit_notes, "vat_total")
        credit_gross = amount(credit_notes, "gross_total")
        manual_net = amount(manual_output, "net_total")
        manual_vat = amount(manual_output, "vat_total")
        manual_gross = amount(manual_output, "gross_total")
        income_net = invoice_net - credit_net + manual_net
        income_vat = invoice_vat - credit_vat + manual_vat
        income_gross = invoice_gross - credit_gross + manual_gross
        expense_net = sum((values["net"] for values in groups.values()), Decimal("0"))
        expense_vat = sum((values["vat"] for values in groups.values()), Decimal("0"))
        expense_gross = sum((values["gross"] for values in groups.values()), Decimal("0"))
        contract_net = money_round(project.get("contract_net_amount") or 0)
        advance_percent = decimal_from(project.get("advance_percent") or 0)
        advance_planned_net = money_round(contract_net * advance_percent / Decimal("100"))
        advance_net = amount(advances, "net_total")
        advance_vat = amount(advances, "vat_total")
        advance_gross = amount(advances, "gross_total")
        advance_paid = amount(advances, "paid_total")
        advance_open = amount(advances, "balance_total")
        contract_billed_net = invoice_net - credit_net
        contract_remaining_net = money_round(contract_net - contract_billed_net)
        contract_progress_percent = (
            money_round(contract_billed_net * Decimal("100") / contract_net)
            if contract_net > 0
            else Decimal("0")
        )
        advance_progress_percent = (
            money_round(advance_net * Decimal("100") / advance_planned_net)
            if advance_planned_net > 0
            else Decimal("0")
        )
        budget = self.get_project_budget(project_id)
        planned_by_group = {
            "Rad": budget["planned_rad_net"],
            "Materijal": budget["planned_material_net"],
            "Plate": budget["planned_plates_net"],
            "Ostali troškovi": budget["planned_other_costs_net"],
        }
        for group in PROJECT_COST_GROUPS:
            planned = planned_by_group[group]
            actual = groups[group]["net"]
            groups[group]["planned_net"] = planned
            groups[group]["variance_net"] = actual - planned
            groups[group]["remaining_net"] = planned - actual
            groups[group]["over_budget"] = bool(budget["is_configured"] and actual > planned)
        planned_expense_net = sum(planned_by_group.values(), Decimal("0"))
        planned_income_net = budget["planned_income_net"]
        planned_profit_net = planned_income_net - planned_expense_net
        actual_profit_net = income_net - expense_net
        budget_summary = {
            **budget,
            "planned_expense_net": planned_expense_net,
            "planned_profit_net": planned_profit_net,
            "income_variance_net": income_net - planned_income_net,
            "expense_variance_net": expense_net - planned_expense_net,
            "profit_variance_net": actual_profit_net - planned_profit_net,
        }
        return {
            "income_net": income_net,
            "income_vat": income_vat,
            "income_gross": income_gross,
            "invoice_income_net": invoice_net - credit_net,
            "credit_note_net": credit_net,
            "credit_note_vat": credit_vat,
            "credit_note_gross": credit_gross,
            "manual_output_net": manual_net,
            "paid_total": amount(invoices, "paid_total"),
            "open_invoice_total": amount(invoices, "balance_total"),
            "overdue_invoice_total": amount(invoices, "overdue_total"),
            "expense_net": expense_net,
            "expense_vat": expense_vat,
            "expense_gross": expense_gross,
            "profit_net": actual_profit_net,
            "vat_difference": income_vat - expense_vat,
            "contract": {
                "contract_no": str(project.get("contract_no") or ""),
                "net_amount": contract_net,
                "billed_net": contract_billed_net,
                "remaining_net": contract_remaining_net,
                "overrun_net": money_round(max(Decimal("0"), -contract_remaining_net)),
                "progress_percent": contract_progress_percent,
                "advance_percent": advance_percent,
                "advance_planned_net": advance_planned_net,
                "advance_issued_net": advance_net,
                "advance_vat": advance_vat,
                "advance_issued_gross": advance_gross,
                "advance_paid_gross": advance_paid,
                "advance_open_gross": advance_open,
                "advance_progress_percent": advance_progress_percent,
                "advance_invoice_count": int(advances["count"] if advances else 0),
            },
            "cost_groups": groups,
            "budget": budget_summary,
            "issued_invoice_count": int(invoices["count"] if invoices else 0),
            "credit_note_count": int(credit_notes["count"] if credit_notes else 0),
            "manual_output_count": int(manual_output["count"] if manual_output else 0),
            "input_document_count": sum(values["count"] for values in groups.values()),
        }

    def list_project_financial_overview(self) -> list[dict[str, Any]]:
        """Load the project list and all summary totals in a few grouped queries.

        The previous implementation recalculated financials once per project.
        That becomes noticeably slow once the database contains many projects,
        invoices, and project documents.
        """
        rows = self.conn.execute(
            """
            WITH invoice_totals AS (
                SELECT
                    project_id,
                    COALESCE(SUM(tax_base), 0) AS net_total,
                    COALESCE(SUM(vat_total), 0) AS vat_total,
                    COALESCE(SUM(gross_total), 0) AS gross_total
                FROM invoices
                WHERE status_code NOT IN ('draft', 'pending_approval', 'approved', 'cancelled')
                  AND COALESCE(invoice_kind, 'standard') <> 'advance'
                  AND project_id IS NOT NULL
                GROUP BY project_id
            ),
            credit_note_totals AS (
                SELECT
                    c.project_id,
                    COALESCE(SUM(c.net_amount), 0) AS net_total,
                    COALESCE(SUM(c.vat_amount), 0) AS vat_total,
                    COALESCE(SUM(c.gross_amount), 0) AS gross_total
                FROM credit_notes c
                JOIN invoices i ON i.id = c.source_invoice_id
                WHERE i.status_code NOT IN ('draft', 'pending_approval', 'approved', 'cancelled')
                GROUP BY c.project_id
            ),
            manual_output_totals AS (
                SELECT
                    project_id,
                    COALESCE(SUM(net_amount), 0) AS net_total,
                    COALESCE(SUM(vat_amount), 0) AS vat_total,
                    COALESCE(SUM(gross_amount), 0) AS gross_total
                FROM project_documents
                WHERE document_type = 'output'
                GROUP BY project_id
            ),
            input_totals AS (
                SELECT
                    project_id,
                    COALESCE(SUM(net_amount), 0) AS net_total,
                    COALESCE(SUM(vat_amount), 0) AS vat_total,
                    COALESCE(SUM(gross_amount), 0) AS gross_total
                FROM project_documents
                WHERE document_type = 'input'
                GROUP BY project_id
            )
            SELECT
                p.*,
                COALESCE(c.name, '') AS customer_name,
                COALESCE(i.net_total, 0) - COALESCE(n.net_total, 0) + COALESCE(o.net_total, 0) AS income_net,
                COALESCE(i.vat_total, 0) - COALESCE(n.vat_total, 0) + COALESCE(o.vat_total, 0) AS income_vat,
                COALESCE(i.gross_total, 0) - COALESCE(n.gross_total, 0) + COALESCE(o.gross_total, 0) AS income_gross,
                COALESCE(e.net_total, 0) AS expense_net,
                COALESCE(e.vat_total, 0) AS expense_vat,
                COALESCE(e.gross_total, 0) AS expense_gross
            FROM projects p
            LEFT JOIN customers c ON c.id = p.customer_id
            LEFT JOIN invoice_totals i ON i.project_id = p.id
            LEFT JOIN credit_note_totals n ON n.project_id = p.id
            LEFT JOIN manual_output_totals o ON o.project_id = p.id
            LEFT JOIN input_totals e ON e.project_id = p.id
            WHERE p.archived = 0
            ORDER BY p.name COLLATE NOCASE
            """
        ).fetchall()
        overview: list[dict[str, Any]] = []
        for source in rows:
            row = row_to_dict(source)
            income_net = money_round(row.pop("income_net", 0))
            income_vat = money_round(row.pop("income_vat", 0))
            income_gross = money_round(row.pop("income_gross", 0))
            expense_net = money_round(row.pop("expense_net", 0))
            expense_vat = money_round(row.pop("expense_vat", 0))
            expense_gross = money_round(row.pop("expense_gross", 0))
            row["financials"] = {
                "income_net": income_net,
                "income_vat": income_vat,
                "income_gross": income_gross,
                "expense_net": expense_net,
                "expense_vat": expense_vat,
                "expense_gross": expense_gross,
                "profit_net": income_net - expense_net,
                "vat_difference": income_vat - expense_vat,
            }
            overview.append(row)
        return overview

    def list_invoice_items(self, invoice_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY line_no, id",
            (invoice_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def list_payments(self, invoice_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM payments WHERE invoice_id = ? ORDER BY payment_date, id",
            (invoice_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def list_attachments(self, invoice_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM attachments WHERE invoice_id = ? ORDER BY id",
            (invoice_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def get_invoice(self, invoice_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        invoice = row_to_dict(row)
        if invoice:
            invoice["items"] = self.list_invoice_items(invoice_id)
            invoice["payments"] = self.list_payments(invoice_id)
            invoice["attachments"] = self.list_attachments(invoice_id)
        return invoice

    def get_invoice_by_number(self, invoice_number: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM invoices WHERE invoice_number = ?",
            (invoice_number,),
        ).fetchone()
        return self.get_invoice(int(row["id"])) if row else {}

    def _record_invoice_audit(self, invoice_id: int, action_code: str, details: str) -> None:
        self.conn.execute(
            """
            INSERT INTO invoice_audit_log (invoice_id, action_code, details, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (int(invoice_id), str(action_code or "").strip(), str(details or "").strip(), now_iso()),
        )

    def list_invoice_audit(self, invoice_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM invoice_audit_log WHERE invoice_id = ? ORDER BY id DESC",
            (int(invoice_id),),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def register_einvoice_draft(
        self,
        invoice_id: int,
        *,
        provider_code: str,
        country_code: str,
        document_path: str,
        document_hash: str,
    ) -> int:
        """Record a local review-only structured document in the e-invoice outbox."""
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            raise ValueError("Faktura nije pronađena.")
        if str(invoice.get("status_code") or "") not in {"issued", "partial", "paid", "due"}:
            raise ValueError("Samo izdata faktura može u e-faktura outbox.")
        provider = str(provider_code or "generic-ubl").strip().lower() or "generic-ubl"
        country = normalize_country_code(country_code)
        path = str(document_path or "").strip()
        digest = str(document_hash or "").strip().lower()
        if not path or not digest:
            raise ValueError("E-faktura nacrt nema putanju ili kontrolni zbir.")
        now = now_iso()
        self._backup_before_change(f"einvoice_{invoice.get('invoice_number') or invoice_id}")
        self.conn.execute(
            """
            INSERT INTO einvoice_documents (
                invoice_id, provider_code, country_code, format_code, document_path,
                document_hash, status_code, created_at, updated_at
            ) VALUES (?, ?, ?, 'ubl-2.1-draft', ?, ?, 'review_only', ?, ?)
            ON CONFLICT(invoice_id, provider_code, format_code) DO UPDATE SET
                country_code=excluded.country_code,
                document_path=excluded.document_path,
                document_hash=excluded.document_hash,
                status_code='review_only',
                remote_document_id='',
                last_error='',
                updated_at=excluded.updated_at,
                submitted_at=''
            """,
            (int(invoice_id), provider, country, path, digest, now, now),
        )
        row = self.conn.execute(
            """
            SELECT id FROM einvoice_documents
            WHERE invoice_id = ? AND provider_code = ? AND format_code = 'ubl-2.1-draft'
            """,
            (int(invoice_id), provider),
        ).fetchone()
        self._record_invoice_audit(
            int(invoice_id),
            "einvoice_draft_saved",
            f"UBL 2.1 nacrt je evidentiran u e-faktura outbox-u ({provider}, samo za pregled).",
        )
        self.conn.commit()
        return int(row["id"]) if row else 0

    def list_einvoice_documents(self, invoice_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM einvoice_documents
            WHERE invoice_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (int(invoice_id),),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def payment_reminder_candidates(
        self,
        *,
        project_id: int | None = None,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return open issued invoices that are due soon or already overdue."""
        today = date.today()
        limit = today + timedelta(days=max(1, int(days)))
        clauses = [
            "i.status_code IN ('issued','partial','due')",
            "i.balance_total > 0",
            "i.due_date <> ''",
            "i.due_date <= ?",
        ]
        params: list[Any] = [limit.isoformat()]
        if project_id:
            clauses.append("i.project_id = ?")
            params.append(int(project_id))
        rows = self.conn.execute(
            f"""
            SELECT i.id, i.invoice_number, i.project_id, i.project_name, i.customer_name,
                   i.customer_email, i.due_date, i.balance_total, i.currency,
                   MAX(r.sent_at) AS last_reminder_at
            FROM invoices i
            LEFT JOIN invoice_reminder_log r ON r.invoice_id = i.id AND r.reminder_type = 'payment'
            WHERE {' AND '.join(clauses)}
            GROUP BY i.id
            ORDER BY i.due_date, i.customer_name, i.id
            """,
            params,
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = row_to_dict(row)
            due = parse_date(item.get("due_date"))
            item["is_overdue"] = bool(due and due < today)
            item["days_to_due"] = (due - today).days if due else 0
            result.append(item)
        return result

    def automatic_payment_reminder_candidates(
        self,
        *,
        interval_days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return due invoices that have not been reminded inside the configured interval."""
        today = date.today()
        minimum_interval = max(1, min(90, int(interval_days or 7)))
        cutoff = datetime.now() - timedelta(days=minimum_interval)
        eligible: list[dict[str, Any]] = []
        for item in self.payment_reminder_candidates(days=0):
            due = parse_date(item.get("due_date"))
            recipient = str(item.get("customer_email") or "").strip()
            if not due or due > today or not recipient:
                continue
            last_sent = str(item.get("last_reminder_at") or "").strip()
            if last_sent:
                try:
                    if datetime.fromisoformat(last_sent) > cutoff:
                        continue
                except ValueError:
                    pass
            eligible.append(item)
        return eligible

    def record_payment_reminder(self, invoice_id: int, recipient_email: str, subject: str) -> None:
        invoice = self.get_invoice(int(invoice_id))
        if not invoice:
            raise ValueError("Faktura nije pronađena.")
        if invoice.get("status_code") not in {"issued", "partial", "due"}:
            raise ValueError("Podsetnik je moguće poslati samo za otvorenu, izdatu fakturu.")
        self._backup_before_change(f"payment_reminder_{invoice_id}", replaces_post_backup=True)
        self.conn.execute(
            """
            INSERT INTO invoice_reminder_log (invoice_id, recipient_email, reminder_type, subject, sent_at)
            VALUES (?, ?, 'payment', ?, ?)
            """,
            (int(invoice_id), str(recipient_email or "").strip(), str(subject or "").strip(), now_iso()),
        )
        self._record_invoice_audit(
            int(invoice_id),
            "payment_reminder_sent",
            f"Poslat podsetnik za plaćanje na {str(recipient_email or '').strip() or 'kontakt kupca'}.",
        )
        self.conn.commit()
        self._maybe_backup(f"payment_reminder_{invoice_id}")

    def daily_work_center(self, days: int = 7) -> dict[str, Any]:
        """Build one actionable control queue for the owner and finance team."""
        projects = self.list_projects(include_archived=False)
        missing_pdf: list[dict[str, Any]] = []
        over_budget: list[dict[str, Any]] = []
        for project in projects:
            reminders = self.project_reminders(int(project["id"]), days=days)
            missing_pdf.extend(
                {"project_id": project["id"], "project_name": project["name"], **row}
                for row in reminders["missing_pdf"]
            )
            over_budget.extend(
                {"project_id": project["id"], "project_name": project["name"], **row}
                for row in reminders["over_budget"]
            )
        reminders = self.payment_reminder_candidates(days=days)
        overdue = [item for item in reminders if item["is_overdue"]]
        due_soon = [item for item in reminders if not item["is_overdue"]]
        returned_for_revision = [row_to_dict(row) for row in self.conn.execute(
            """
            SELECT i.*
            FROM invoices i
            WHERE i.status_code = 'draft'
              AND EXISTS (
                  SELECT 1
                  FROM invoice_audit_log returned
                  WHERE returned.invoice_id = i.id
                    AND returned.action_code = 'returned_for_revision'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM invoice_audit_log resubmitted
                        WHERE resubmitted.invoice_id = i.id
                          AND resubmitted.action_code = 'submitted_for_approval'
                          AND resubmitted.id > returned.id
                    )
              )
            ORDER BY i.updated_at ASC, i.id ASC
            """
        ).fetchall()]
        bank_review = self.list_bank_transactions(include_closed=False)
        open_vendor_bills = self.list_vendor_bills(include_paid=False)
        vendor_evidence_missing = [
            row for row in open_vendor_bills
            if not int(row.get("source_project_document_id") or 0)
            and not str(row.get("attachment_path") or "").strip()
            and str(row.get("approval_status") or "approved") != "rejected"
        ]
        pending_vendor_approvals = [
            row for row in open_vendor_bills
            if str(row.get("approval_status") or "approved") == "pending"
            and row not in vendor_evidence_missing
        ]
        rejected_vendor_bills = [
            row for row in open_vendor_bills
            if str(row.get("approval_status") or "approved") == "rejected"
        ]
        return {
            "days": max(1, int(days)),
            "due_soon": due_soon,
            "overdue": overdue,
            "missing_pdf": missing_pdf,
            "over_budget": over_budget,
            "pending_invoice_approvals": self.pending_invoice_approvals(),
            "returned_for_revision": returned_for_revision,
            "pending_vendor_approvals": pending_vendor_approvals,
            "vendor_evidence_missing": vendor_evidence_missing,
            "rejected_vendor_bills": rejected_vendor_bills,
            "bank_review": bank_review,
            "total_count": (
                len(due_soon) + len(overdue) + len(missing_pdf) + len(over_budget)
                + len(self.pending_invoice_approvals()) + len(returned_for_revision)
                + len(pending_vendor_approvals) + len(vendor_evidence_missing)
                + len(rejected_vendor_bills) + len(bank_review)
            ),
        }

    @staticmethod
    def _add_months(value: date, months: int) -> date:
        target = value.month - 1 + max(1, int(months))
        year = value.year + target // 12
        month = target % 12 + 1
        return date(year, month, min(value.day, monthrange(year, month)[1]))

    def create_recurring_invoice_template(
        self,
        invoice_payload: dict[str, Any],
        items: list[dict[str, Any]],
        *,
        name: str,
        interval_months: int = 1,
        next_run_date: Any = None,
    ) -> int:
        """Save a reusable invoice recipe; generated instances always start as drafts."""
        self.assert_business_write_access()
        project_id = int(invoice_payload.get("project_id") or 0)
        if not project_id or not self.get_project(project_id):
            raise ValueError("Ponavljajuća faktura mora pripadati postojećem projektu.")
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Unesite naziv ponavljajuće fakture.")
        interval = max(1, min(60, int(interval_months or 1)))
        next_date = parse_date(next_run_date) or date.today()
        skipped = {
            "id", "invoice_seq", "invoice_number", "status_code", "issued_at", "cancelled_at",
            "created_at", "updated_at", "paid_total", "balance_total", "subtotal", "tax_base",
            "vat_total", "gross_total", "retention_amount", "due_before_paid",
        }
        payload = {key: value for key, value in invoice_payload.items() if key not in skipped}
        payload["project_id"] = project_id
        payload["status_code"] = "draft"
        now = now_iso()
        self._backup_before_change(f"recurring_invoice_{project_id}", replaces_post_backup=True)
        cur = self.conn.execute(
            """
            INSERT INTO recurring_invoice_templates (
                project_id, customer_id, name, interval_months, next_run_date, active,
                payload_json, items_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                project_id,
                int(payload["customer_id"]) if payload.get("customer_id") else None,
                clean_name,
                interval,
                next_date.isoformat(),
                json.dumps(payload, ensure_ascii=False, default=str),
                json.dumps(items, ensure_ascii=False, default=str),
                now,
                now,
            ),
        )
        template_id = int(cur.lastrowid)
        self.conn.commit()
        self._maybe_backup(f"recurring_invoice_{project_id}")
        return template_id

    def list_recurring_invoice_templates(self, project_id: int | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT t.*, p.name AS project_name, c.name AS customer_name
            FROM recurring_invoice_templates t
            JOIN projects p ON p.id = t.project_id
            LEFT JOIN customers c ON c.id = t.customer_id
        """
        params: list[Any] = []
        if project_id:
            sql += " WHERE t.project_id = ?"
            params.append(int(project_id))
        sql += " ORDER BY t.active DESC, t.next_run_date, t.id DESC"
        return [row_to_dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def set_recurring_invoice_template_active(self, template_id: int, active: bool) -> None:
        self._backup_before_change(f"recurring_template_{template_id}", replaces_post_backup=True)
        self.conn.execute(
            "UPDATE recurring_invoice_templates SET active = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, now_iso(), int(template_id)),
        )
        self.conn.commit()
        self._maybe_backup(f"recurring_template_{template_id}")

    def generate_due_recurring_invoices(self, run_date: Any = None, project_id: int | None = None) -> list[dict[str, Any]]:
        """Generate one editable draft for each due template, never an automatic issued invoice."""
        self.assert_business_write_access()
        due_on = parse_date(run_date) or date.today()
        clauses = ["active = 1", "next_run_date <> ''", "next_run_date <= ?"]
        params: list[Any] = [due_on.isoformat()]
        if project_id:
            clauses.append("project_id = ?")
            params.append(int(project_id))
        rows = self.conn.execute(
            f"SELECT * FROM recurring_invoice_templates WHERE {' AND '.join(clauses)} ORDER BY next_run_date, id",
            params,
        ).fetchall()
        generated: list[dict[str, Any]] = []
        for row in rows:
            template = row_to_dict(row)
            try:
                payload = json.loads(str(template.get("payload_json") or "{}"))
                items = json.loads(str(template.get("items_json") or "[]"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or not isinstance(items, list):
                continue
            customer_days = int(payload.get("customer_payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS)
            payload.update(
                {
                    "id": None,
                    "status_code": "draft",
                    "issue_date": due_on.isoformat(),
                    "tax_event_date": due_on.isoformat(),
                    "due_date": (due_on + timedelta(days=max(0, customer_days))).isoformat(),
                }
            )
            invoice_id = self.save_invoice(payload, items)
            next_run = self._add_months(due_on, int(template.get("interval_months") or 1))
            self.conn.execute(
                """
                UPDATE recurring_invoice_templates
                SET next_run_date = ?, last_invoice_id = ?, last_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_run.isoformat(), invoice_id, now_iso(), now_iso(), int(template["id"])),
            )
            self._record_invoice_audit(
                invoice_id,
                "recurring_invoice_generated",
                f"Nacrt je kreiran iz ponavljajuće fakture: {template.get('name') or 'bez naziva'}.",
            )
            self.conn.commit()
            generated.append(self.get_invoice(invoice_id))
        return generated

    def list_credit_notes(self, *, invoice_id: int | None = None, project_id: int | None = None) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if invoice_id:
            where.append("source_invoice_id = ?")
            params.append(int(invoice_id))
        if project_id:
            where.append("project_id = ?")
            params.append(int(project_id))
        sql = "SELECT * FROM credit_notes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY issue_date DESC, id DESC"
        return [row_to_dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_credit_note(self, credit_note_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM credit_notes WHERE id = ?", (int(credit_note_id),)).fetchone()
        payload = row_to_dict(row)
        if not payload:
            return {}
        try:
            snapshot = json.loads(str(payload.get("snapshot_json") or "{}"))
        except (TypeError, json.JSONDecodeError):
            snapshot = {}
        payload["snapshot"] = snapshot if isinstance(snapshot, dict) else {}
        return payload

    def credit_note_draft_info(self, invoice_id: int) -> dict[str, Any]:
        """Return the remaining refundable amount that may receive a formal credit note."""
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            raise ValueError("Faktura nije pronađena.")
        if invoice.get("status_code") == "draft":
            raise ValueError("Formalno odobrenje se izdaje samo za sačuvanu fakturu.")
        if str(invoice.get("currency") or DEFAULT_CURRENCY).upper() != DEFAULT_CURRENCY:
            raise ValueError("Formalno odobrenje je trenutno dostupno samo za EUR fakture.")
        project_id = int(invoice.get("project_id") or 0)
        if not project_id or not self.get_project(project_id):
            raise ValueError("Faktura mora biti vezana za postojeći projekat.")
        refunds = self.conn.execute(
            "SELECT COALESCE(SUM(-amount), 0) AS total FROM payments WHERE invoice_id = ? AND amount < 0",
            (int(invoice_id),),
        ).fetchone()
        issued = self.conn.execute(
            "SELECT COALESCE(SUM(gross_amount), 0) AS total FROM credit_notes WHERE source_invoice_id = ?",
            (int(invoice_id),),
        ).fetchone()
        refunded_total = money_round(refunds["total"] if refunds else 0)
        credited_total = money_round(issued["total"] if issued else 0)
        return {
            "invoice": invoice,
            "refunded_total": refunded_total,
            "credited_total": credited_total,
            "available_gross": money_round(max(Decimal("0"), refunded_total - credited_total)),
        }

    def preview_credit_note_amounts(self, invoice_id: int, gross_amount: Any) -> dict[str, Decimal]:
        """Split a credit amount proportionally using the tax base and VAT of its source invoice."""
        info = self.credit_note_draft_info(invoice_id)
        invoice = info["invoice"]
        gross = money_round(gross_amount)
        if gross <= 0:
            raise ValueError("Iznos odobrenja mora biti veći od nule.")
        if gross > info["available_gross"]:
            raise ValueError(
                f"Iznos odobrenja je veći od raspoloživog povraćaja "
                f"{format_currency(info['available_gross'], DEFAULT_CURRENCY)}."
            )
        source_net = money_round(invoice.get("tax_base") or 0)
        source_vat = money_round(invoice.get("vat_total") or 0)
        source_gross = money_round(source_net + source_vat)
        if source_gross <= 0:
            raise ValueError("Izvorna faktura nema raspoloživ iznos za obračun odobrenja.")
        net = money_round(gross * source_net / source_gross)
        vat = money_round(gross - net)
        vat_rate = money_round(source_vat / source_net * Decimal("100")) if source_net > 0 else Decimal("0")
        return {"net_amount": net, "vat_amount": vat, "gross_amount": gross, "vat_rate_percent": vat_rate}

    def create_credit_note(self, invoice_id: int, issue_date: Any, gross_amount: Any, reason: str) -> int:
        """Issue an immutable project credit note after an already recorded refund."""
        clean_reason = str(reason or "").strip()
        if not clean_reason:
            raise ValueError("Razlog formalnog odobrenja je obavezan.")
        info = self.credit_note_draft_info(invoice_id)
        invoice = info["invoice"]
        amounts = self.preview_credit_note_amounts(invoice_id, gross_amount)
        issue_date_iso = iso_from_date(issue_date) or today_iso()
        company = self.get_company()
        snapshot = {
            "company": company,
            "source_invoice": {
                key: invoice.get(key, "")
                for key in (
                    "id", "invoice_number", "issue_date", "tax_event_date", "customer_name", "customer_eik",
                    "customer_vat", "customer_address", "customer_contact", "customer_phone", "customer_email",
                    "project_name", "site_address", "contract_no", "protocol_no", "issue_place", "currency",
                    "tax_base", "vat_total", "gross_total", "vat_rate",
                )
            },
        }
        next_sequence_row = self.conn.execute(
            "SELECT next_credit_note_seq FROM company_settings WHERE id = 1"
        ).fetchone()
        next_sequence = int(next_sequence_row["next_credit_note_seq"] if next_sequence_row else 1)
        self._backup_before_change(
            f"credit_note_{credit_note_number_from_seq(next_sequence)}",
            replaces_post_backup=True,
        )
        now = now_iso()
        try:
            self.conn.execute("BEGIN")
            seq = self._reserve_credit_note_sequence()
            number = credit_note_number_from_seq(seq)
            cur = self.conn.execute(
                """
                INSERT INTO credit_notes (
                    credit_note_seq, credit_note_number, source_invoice_id, project_id, source_invoice_number,
                    customer_name, project_name, issue_date, currency, reason, net_amount, vat_rate,
                    vat_amount, gross_amount, snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seq, number, int(invoice_id), int(invoice["project_id"]), str(invoice.get("invoice_number") or ""),
                    str(invoice.get("customer_name") or ""), str(invoice.get("project_name") or ""), issue_date_iso,
                    DEFAULT_CURRENCY, clean_reason, float(amounts["net_amount"]), float(amounts["vat_rate_percent"] / Decimal("100")),
                    float(amounts["vat_amount"]), float(amounts["gross_amount"]),
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True), now,
                ),
            )
            credit_note_id = int(cur.lastrowid)
            self._record_invoice_audit(
                invoice_id,
                "credit_note_issued",
                f"Izdat je dokument {number}: {format_currency(amounts['gross_amount'], DEFAULT_CURRENCY)}. Razlog: {clean_reason}",
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self._maybe_backup(f"credit_note_{number}")
        return credit_note_id

    def credit_note_export_payload(self, credit_note_id: int) -> dict[str, Any]:
        note = self.get_credit_note(credit_note_id)
        if not note:
            return {}
        snapshot = note.get("snapshot") if isinstance(note.get("snapshot"), dict) else {}
        return {
            **note,
            "company": snapshot.get("company") if isinstance(snapshot.get("company"), dict) else self.get_company(),
            "source_invoice": snapshot.get("source_invoice") if isinstance(snapshot.get("source_invoice"), dict) else {},
        }

    def prepare_invoice_correction_draft(self, invoice_id: int) -> dict[str, Any]:
        """Return a source invoice for a new correction draft and log the workflow start."""
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            raise ValueError("Faktura nije pronađena.")
        if invoice.get("status_code") == "draft":
            raise ValueError("Nacrt možete urediti direktno; ispravka je namenjena izdatim fakturama.")
        if str(invoice.get("currency") or DEFAULT_CURRENCY).upper() != DEFAULT_CURRENCY:
            raise ValueError("Ispravka se trenutno može napraviti samo za EUR fakturu.")
        self._backup_before_change(
            f"correction_draft_{invoice.get('invoice_number', 'invoice')}",
            replaces_post_backup=True,
        )
        self._record_invoice_audit(
            invoice_id,
            "correction_draft_opened",
            "Otvoren je novi nacrt ispravke na osnovu ove fakture.",
        )
        self.conn.commit()
        self._maybe_backup(f"correction_draft_{invoice.get('invoice_number','invoice')}")
        return invoice

    def _invoice_paid_total(self, invoice_id: Optional[int]) -> Decimal:
        if not invoice_id:
            return Decimal("0")
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()
        return money_round(row["total"] if row else 0)

    def _prepare_invoice_snapshot(
        self,
        payload: dict[str, Any],
        *,
        existing_currency: str = "",
        existing_document_language: str = "",
    ) -> dict[str, Any]:
        company = self.get_company()
        customer = self.get_customer(int(payload["customer_id"])) if payload.get("customer_id") else {}
        project = self.get_project(int(payload["project_id"])) if payload.get("project_id") else {}
        issue_date = iso_from_date(payload.get("issue_date")) or today_iso()
        tax_event_date = iso_from_date(payload.get("tax_event_date")) or issue_date
        due_date = iso_from_date(payload.get("due_date")) or issue_date
        pay_days = int(payload.get("customer_payment_term_days") or customer.get("payment_term_days") or company.get("payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS)
        requested_currency = str(payload.get("currency") or DEFAULT_CURRENCY).strip().upper()
        historic_currency = str(existing_currency or "").strip().upper()
        if historic_currency:
            currency = normalize_currency(historic_currency, fallback=DEFAULT_CURRENCY)
        elif requested_currency in {"", "€"}:
            currency = DEFAULT_CURRENCY
        elif requested_currency not in SUPPORTED_CURRENCIES:
            raise ValueError("Izabrana valuta nije podržana. Izaberite valutu iz liste OpsNest-a.")
        else:
            currency = requested_currency

        vat_rate = decimal_from(payload.get("vat_rate") or company.get("default_vat_rate") or DEFAULT_VAT_RATE)
        # Forms normally send 0.20, but imports and direct integrations may send 20.
        # Normalize both representations before calculating document totals.
        if vat_rate > 1:
            vat_rate /= Decimal("100")
        vat_rate = max(Decimal("0"), vat_rate)
        invoice_kind = normalize_invoice_kind(payload.get("invoice_kind"))
        advance_source_invoice_id = int(payload.get("advance_source_invoice_id") or 0) or None
        if invoice_kind != "final":
            advance_source_invoice_id = None
        requested_language = str(payload.get("document_language") or existing_document_language or "").strip().lower()
        if requested_language not in {"sr", "bg", "en"}:
            requested_language = "bg" if normalize_country_code(company.get("country_code")) == "BG" else "sr"
        snapshot = {
            "issue_date": issue_date,
            "tax_event_date": tax_event_date,
            "due_date": due_date,
            "customer_id": int(payload["customer_id"]) if payload.get("customer_id") else None,
            "project_id": int(payload["project_id"]) if payload.get("project_id") else None,
            "customer_name": (payload.get("customer_name") or customer.get("name") or "").strip(),
            "customer_eik": (payload.get("customer_eik") or customer.get("eik") or "").strip(),
            "customer_vat": (payload.get("customer_vat") or customer.get("vat_number") or "").strip(),
            "customer_address": (payload.get("customer_address") or customer.get("address") or "").strip(),
            "customer_contact": (payload.get("customer_contact") or customer.get("contact_person") or "").strip(),
            "customer_phone": (payload.get("customer_phone") or customer.get("phone") or "").strip(),
            "customer_email": (payload.get("customer_email") or customer.get("email") or "").strip(),
            "customer_payment_term_days": pay_days,
            "project_name": (payload.get("project_name") or project.get("name") or "").strip(),
            "site_address": (payload.get("site_address") or project.get("site_address") or "").strip(),
            "contract_no": (payload.get("contract_no") or project.get("contract_no") or "").strip(),
            "protocol_no": (payload.get("protocol_no") or project.get("protocol_no") or "").strip(),
            "period_from": iso_from_date(payload.get("period_from")) or project.get("period_from") or "",
            "period_to": iso_from_date(payload.get("period_to")) or project.get("period_to") or "",
            "order_reference": (payload.get("order_reference") or project.get("order_reference") or "").strip(),
            "issue_place": (payload.get("issue_place") or company.get("issue_place") or DEFAULT_ISSUE_PLACE).strip(),
            "currency": currency,
            "payment_method": (payload.get("payment_method") or company.get("payment_method") or DEFAULT_PAYMENT_METHOD).strip(),
            "note": (payload.get("note") or "").strip(),
            "vat_rate": vat_rate,
            "discount_total": money_round(payload.get("discount_total") or 0),
            "retention_percent": decimal_from(payload.get("retention_percent") or 0),
            "advance_amount": money_round(payload.get("advance_amount") or 0),
            "invoice_kind": invoice_kind,
            "advance_source_invoice_id": advance_source_invoice_id,
            "document_language": requested_language,
            "exchange_rate": decimal_from(payload.get("exchange_rate") or company.get("exchange_rate") or DEFAULT_EXCHANGE_RATE),
        }
        return snapshot

    def save_invoice(self, payload: dict[str, Any], items: list[dict[str, Any]]) -> int:
        # New invoices always belong to a real project. This keeps accounting,
        # attachments, Excel copies, and PDFs in one project-owned archive.
        project_id = int(payload.get("project_id") or 0)
        if not project_id or not self.get_project(project_id):
            raise ValueError("Izaberite postojeći projekat. Fakture se mogu čuvati samo u projektu.")
        self.assert_business_write_access()
        now = now_iso()
        invoice_id = int(payload["id"]) if payload.get("id") else None
        company = self.get_company()
        existing_invoice = self.get_invoice(invoice_id) if invoice_id else {}
        if invoice_id and not existing_invoice:
            raise ValueError("Faktura nije pronađena.")
        if existing_invoice.get("status_code") == "cancelled":
            raise ValueError("Stornirana faktura je zaključana. Napravite novu ispravku umesto izmene.")
        snapshot = self._prepare_invoice_snapshot(
            payload,
            existing_currency=str(existing_invoice.get("currency") or ""),
            existing_document_language=str(existing_invoice.get("document_language") or ""),
        )
        if snapshot["invoice_kind"] == "advance":
            # An advance is defined by the project agreement, not by a user
            # entered material/labour line.  Before issue it is regenerated
            # from the stored contract net amount and percentage.
            snapshot["advance_amount"] = Decimal("0")
            issued_statuses = {"issued", "partial", "paid", "due"}
            if str(existing_invoice.get("status_code") or "") not in issued_statuses:
                items = [self.project_advance_invoice_item(project_id)]
        elif snapshot["invoice_kind"] == "final":
            source_id = snapshot.get("advance_source_invoice_id")
            if not source_id:
                raise ValueError("Za završni račun izaberite plaćeni avans.")
            advance = self.get_invoice(int(source_id))
            if not advance or normalize_invoice_kind(advance.get("invoice_kind")) != "advance":
                raise ValueError("Izabrani dokument nije avansni račun.")
            if str(advance.get("status_code") or "") not in {"issued", "partial", "paid", "due"}:
                raise ValueError("Završni račun može da odbije samo izdat avans.")
            if int(advance.get("project_id") or 0) != int(snapshot["project_id"] or 0):
                raise ValueError("Avans i završni račun moraju pripadati istom projektu.")
            source_customer = int(advance.get("customer_id") or 0)
            final_customer = int(snapshot.get("customer_id") or 0)
            if source_customer and final_customer and source_customer != final_customer:
                raise ValueError("Avans i završni račun moraju imati istog kupca.")
            if str(advance.get("currency") or DEFAULT_CURRENCY).upper() != snapshot["currency"]:
                raise ValueError("Avans i završni račun moraju biti u istoj valuti.")
            linked = self.conn.execute(
                """
                SELECT id FROM invoices
                WHERE advance_source_invoice_id = ? AND status_code <> 'cancelled' AND id <> ?
                LIMIT 1
                """,
                (int(source_id), invoice_id or 0),
            ).fetchone()
            if linked:
                raise ValueError("Ovaj avans je već odbijen na drugom završnom računu.")
            baseline = calculate_invoice_totals(
                items,
                vat_rate=snapshot["vat_rate"],
                discount_total=snapshot["discount_total"],
                retention_percent=snapshot["retention_percent"],
                advance_amount=0,
                paid_total=0,
                currency=snapshot["currency"],
            )
            deductible = money_round(max(Decimal("0"), baseline["gross_total"] - baseline["retention_amount"]))
            snapshot["advance_amount"] = min(money_round(advance.get("paid_total") or 0), deductible)
        self.assert_financial_date_open(snapshot.get("issue_date"))
        paid_total = self._invoice_paid_total(invoice_id)
        totals = calculate_invoice_totals(
            items,
            vat_rate=snapshot["vat_rate"],
            discount_total=snapshot["discount_total"],
            retention_percent=snapshot["retention_percent"],
            advance_amount=snapshot["advance_amount"],
            paid_total=paid_total,
            currency=snapshot["currency"],
        )
        issue_date = parse_date(snapshot["issue_date"]) or date.today()
        due_date = parse_date(snapshot["due_date"]) or issue_date
        status_code = normalize_status(payload.get("status_code") or "draft")
        if status_code == "draft" and payload.get("issued_at"):
            status_code = "issued"
        status_code = self._derive_status(
            status_code=status_code,
            due_date=due_date,
            paid_total=totals["paid_total"],
            balance_total=totals["balance_total"],
        )
        if status_code == "cancelled":
            raise ValueError("Za storno koristite posebno dugme Storniraj fakturu i unesite razlog.")

        issued_statuses = {"issued", "partial", "paid", "due"}
        was_issued = str(existing_invoice.get("status_code") or "") in issued_statuses
        previous_status = str(existing_invoice.get("status_code") or "draft")
        if snapshot["invoice_kind"] == "advance" and status_code in issued_statuses:
            active_advance = self.conn.execute(
                """
                SELECT id FROM invoices
                WHERE project_id = ?
                  AND invoice_kind = 'advance'
                  AND status_code IN ('issued', 'partial', 'paid', 'due')
                  AND id <> ?
                LIMIT 1
                """,
                (project_id, invoice_id or 0),
            ).fetchone()
            if active_advance:
                raise ValueError(
                    "Ovaj projekat već ima izdat avans. Uredite postojeći avans, "
                    "stornirajte ga ili napravite završni račun koji ga odbija."
                )
        # A document must first exist as a draft.  This makes the operational
        # path explicit: draft -> approval -> issue, and prevents a first click
        # on "Izdaj" from silently creating a numbered, final invoice.
        if not existing_invoice and status_code != "draft":
            raise ValueError("Prvo sačuvajte nacrt fakture. Zatim je odobrite i izdajte.")
        if not was_issued and status_code in issued_statuses and previous_status != "approved":
            raise ValueError("Faktura mora prvo biti sačuvana kao nacrt i odobrena pre izdavanja.")
        if was_issued and normalize_invoice_kind(existing_invoice.get("invoice_kind")) != snapshot["invoice_kind"]:
            raise ValueError("Vrsta izdatog računa je zaključana. Napravite ispravku ili storno.")
        approval_enabled = bool(int(company.get("team_invoice_approval_required") or 0)) and plan_includes(
            self.subscription_plan().get("effective_code", "starter"),
            "invoice_approval",
        )
        prepared_role = str(payload.get("prepared_by_role") or "").strip().lower()
        is_owner_or_administrator = prepared_role in {"", "owner", "administrator"}
        if approval_enabled:
            if status_code == "approved" and not is_owner_or_administrator:
                raise ValueError("Samo vlasnik ili administrator može odobriti fakturu.")
            if status_code in issued_statuses and not is_owner_or_administrator and previous_status != "approved":
                raise ValueError("Knjigovođa mora prvo poslati fakturu vlasniku na odobrenje.")
        if status_code in issued_statuses and not was_issued:
            self.assert_issued_invoice_allowed()
        if was_issued and status_code not in issued_statuses:
            raise ValueError("Izdatu fakturu nije moguće vratiti u nacrt ili tok odobravanja.")

        requested_template_id = payload.get("invoice_template_id")
        if requested_template_id in (None, ""):
            existing_template_id = existing_invoice.get("invoice_template_id") if existing_invoice else None
            template_id = int(
                existing_template_id
                if existing_template_id not in (None, "")
                else self.default_invoice_template_id()
            )
        else:
            template_id = int(requested_template_id)
        # An archived form remains readable for invoices that already use it;
        # it can never be selected for a different invoice.
        preserves_existing_template = bool(existing_invoice) and int(
            existing_invoice.get("invoice_template_id") or 0
        ) == template_id
        if template_id > 0 and not preserves_existing_template:
            # Personal forms are a Business/Pro feature. Existing invoices keep
            # their historical form available even after a later downgrade.
            self.assert_plan_feature("custom_invoice_templates")
        self.invoice_template_path(template_id, allow_archived=preserves_existing_template)
        prepared_by_role = str(
            payload.get("prepared_by_role") or existing_invoice.get("prepared_by_role") or ""
        ).strip()
        prepared_by_name = str(
            payload.get("prepared_by_name") or existing_invoice.get("prepared_by_name") or ""
        ).strip()
        approved_by_name = str(existing_invoice.get("approved_by_name") or "").strip()
        approved_at = str(existing_invoice.get("approved_at") or "").strip()
        if status_code == "approved":
            approved_by_name = str(payload.get("approved_by_name") or prepared_by_name or approved_by_name or "Vlasnik").strip()
            approved_at = now
        elif status_code in {"draft", "pending_approval"}:
            approved_by_name = ""
            approved_at = ""

        invoice_number_for_backup = str(
            existing_invoice.get("invoice_number") or self.preview_project_invoice_number(project_id)
        )
        self._backup_before_change(f"invoice_{invoice_number_for_backup}", replaces_post_backup=True)

        if invoice_id:
            if not existing_invoice:
                raise ValueError("Faktura nije pronađena.")
            seq = int(payload.get("invoice_seq") or existing_invoice["invoice_seq"])
            invoice_number = str(existing_invoice["invoice_number"] or invoice_number_from_seq(seq))
            self.conn.execute(
                """
                UPDATE invoices SET
                    invoice_seq=:invoice_seq,
                    invoice_number=:invoice_number,
                    status_code=:status_code,
                    invoice_kind=:invoice_kind,
                    advance_source_invoice_id=:advance_source_invoice_id,
                    issue_date=:issue_date,
                    tax_event_date=:tax_event_date,
                    due_date=:due_date,
                    customer_id=:customer_id,
                    project_id=:project_id,
                    customer_name=:customer_name,
                    customer_eik=:customer_eik,
                    customer_vat=:customer_vat,
                    customer_address=:customer_address,
                    customer_contact=:customer_contact,
                    customer_phone=:customer_phone,
                    customer_email=:customer_email,
                    customer_payment_term_days=:customer_payment_term_days,
                    project_name=:project_name,
                    site_address=:site_address,
                    contract_no=:contract_no,
                    protocol_no=:protocol_no,
                    period_from=:period_from,
                    period_to=:period_to,
                    order_reference=:order_reference,
                    issue_place=:issue_place,
                    currency=:currency,
                    payment_method=:payment_method,
                    note=:note,
                    vat_rate=:vat_rate,
                    discount_total=:discount_total,
                    retention_percent=:retention_percent,
                    advance_amount=:advance_amount,
                    subtotal=:subtotal,
                    tax_base=:tax_base,
                    vat_total=:vat_total,
                    gross_total=:gross_total,
                    retention_amount=:retention_amount,
                    due_before_paid=:due_before_paid,
                    paid_total=:paid_total,
                    balance_total=:balance_total,
                    exchange_rate=:exchange_rate,
                    invoice_template_id=:invoice_template_id,
                    document_language=:document_language,
                    prepared_by_role=:prepared_by_role,
                    prepared_by_name=:prepared_by_name,
                    approved_by_name=:approved_by_name,
                    approved_at=:approved_at,
                    updated_at=:updated_at,
                    issued_at=CASE WHEN :status_code IN ('issued','partial','paid','due') AND issued_at = '' THEN :updated_at ELSE issued_at END,
                    cancelled_at=CASE WHEN :status_code = 'cancelled' THEN COALESCE(NULLIF(cancelled_at,''), :updated_at) ELSE cancelled_at END
                WHERE id=:id
                """,
                {
                    "id": invoice_id,
                    "invoice_seq": seq,
                    "invoice_number": invoice_number,
                    "status_code": status_code,
                    "invoice_kind": snapshot["invoice_kind"],
                    "advance_source_invoice_id": snapshot["advance_source_invoice_id"],
                    "issue_date": snapshot["issue_date"],
                    "tax_event_date": snapshot["tax_event_date"],
                    "due_date": snapshot["due_date"],
                    "customer_id": snapshot["customer_id"],
                    "project_id": snapshot["project_id"],
                    "customer_name": snapshot["customer_name"],
                    "customer_eik": snapshot["customer_eik"],
                    "customer_vat": snapshot["customer_vat"],
                    "customer_address": snapshot["customer_address"],
                    "customer_contact": snapshot["customer_contact"],
                    "customer_phone": snapshot["customer_phone"],
                    "customer_email": snapshot["customer_email"],
                    "customer_payment_term_days": snapshot["customer_payment_term_days"],
                    "project_name": snapshot["project_name"],
                    "site_address": snapshot["site_address"],
                    "contract_no": snapshot["contract_no"],
                    "protocol_no": snapshot["protocol_no"],
                    "period_from": snapshot["period_from"],
                    "period_to": snapshot["period_to"],
                    "order_reference": snapshot["order_reference"],
                    "issue_place": snapshot["issue_place"],
                    "currency": snapshot["currency"],
                    "payment_method": snapshot["payment_method"],
                    "note": snapshot["note"],
                    "vat_rate": float(snapshot["vat_rate"]),
                    "discount_total": float(totals["discount_total"]),
                    "retention_percent": float(totals["retention_percent"]),
                    "advance_amount": float(totals["advance_amount"]),
                    "subtotal": float(totals["subtotal"]),
                    "tax_base": float(totals["tax_base"]),
                    "vat_total": float(totals["vat_total"]),
                    "gross_total": float(totals["gross_total"]),
                    "retention_amount": float(totals["retention_amount"]),
                    "due_before_paid": float(totals["due_before_paid"]),
                    "paid_total": float(totals["paid_total"]),
                    "balance_total": float(totals["balance_total"]),
                    "exchange_rate": float(snapshot["exchange_rate"]),
                    "invoice_template_id": template_id,
                    "document_language": snapshot["document_language"],
                    "prepared_by_role": prepared_by_role,
                    "prepared_by_name": prepared_by_name,
                    "approved_by_name": approved_by_name,
                    "approved_at": approved_at,
                    "updated_at": now,
                },
            )
        else:
            seq = self.bump_invoice_sequence()
            invoice_number = self.reserve_project_invoice_number(project_id)
            cur = self.conn.execute(
                """
                INSERT INTO invoices (
                    invoice_seq, invoice_number, status_code, invoice_kind, advance_source_invoice_id, issue_date, tax_event_date, due_date,
                    customer_id, project_id, customer_name, customer_eik, customer_vat, customer_address,
                    customer_contact, customer_phone, customer_email, customer_payment_term_days,
                    project_name, site_address, contract_no, protocol_no, period_from, period_to,
                    order_reference, issue_place, currency, payment_method, note, vat_rate,
                    discount_total, retention_percent, advance_amount, subtotal, tax_base, vat_total,
                    gross_total, retention_amount, due_before_paid, paid_total, balance_total,
                    exchange_rate, invoice_template_id, document_language, prepared_by_role, prepared_by_name,
                    approved_by_name, approved_at, created_at, updated_at, issued_at, cancelled_at
                ) VALUES (
                    :invoice_seq, :invoice_number, :status_code, :invoice_kind, :advance_source_invoice_id, :issue_date, :tax_event_date, :due_date,
                    :customer_id, :project_id, :customer_name, :customer_eik, :customer_vat, :customer_address,
                    :customer_contact, :customer_phone, :customer_email, :customer_payment_term_days,
                    :project_name, :site_address, :contract_no, :protocol_no, :period_from, :period_to,
                    :order_reference, :issue_place, :currency, :payment_method, :note, :vat_rate,
                    :discount_total, :retention_percent, :advance_amount, :subtotal, :tax_base, :vat_total,
                    :gross_total, :retention_amount, :due_before_paid, :paid_total, :balance_total,
                    :exchange_rate, :invoice_template_id, :document_language, :prepared_by_role, :prepared_by_name,
                    :approved_by_name, :approved_at, :created_at, :updated_at, :issued_at, :cancelled_at
                )
                """,
                {
                    "invoice_seq": seq,
                    "invoice_number": invoice_number,
                    "status_code": status_code,
                    "invoice_kind": snapshot["invoice_kind"],
                    "advance_source_invoice_id": snapshot["advance_source_invoice_id"],
                    "issue_date": snapshot["issue_date"],
                    "tax_event_date": snapshot["tax_event_date"],
                    "due_date": snapshot["due_date"],
                    "customer_id": snapshot["customer_id"],
                    "project_id": snapshot["project_id"],
                    "customer_name": snapshot["customer_name"],
                    "customer_eik": snapshot["customer_eik"],
                    "customer_vat": snapshot["customer_vat"],
                    "customer_address": snapshot["customer_address"],
                    "customer_contact": snapshot["customer_contact"],
                    "customer_phone": snapshot["customer_phone"],
                    "customer_email": snapshot["customer_email"],
                    "customer_payment_term_days": snapshot["customer_payment_term_days"],
                    "project_name": snapshot["project_name"],
                    "site_address": snapshot["site_address"],
                    "contract_no": snapshot["contract_no"],
                    "protocol_no": snapshot["protocol_no"],
                    "period_from": snapshot["period_from"],
                    "period_to": snapshot["period_to"],
                    "order_reference": snapshot["order_reference"],
                    "issue_place": snapshot["issue_place"],
                    "currency": snapshot["currency"],
                    "payment_method": snapshot["payment_method"],
                    "note": snapshot["note"],
                    "vat_rate": float(snapshot["vat_rate"]),
                    "discount_total": float(totals["discount_total"]),
                    "retention_percent": float(totals["retention_percent"]),
                    "advance_amount": float(totals["advance_amount"]),
                    "subtotal": float(totals["subtotal"]),
                    "tax_base": float(totals["tax_base"]),
                    "vat_total": float(totals["vat_total"]),
                    "gross_total": float(totals["gross_total"]),
                    "retention_amount": float(totals["retention_amount"]),
                    "due_before_paid": float(totals["due_before_paid"]),
                    "paid_total": float(totals["paid_total"]),
                    "balance_total": float(totals["balance_total"]),
                    "exchange_rate": float(snapshot["exchange_rate"]),
                    "invoice_template_id": template_id,
                    "document_language": snapshot["document_language"],
                    "prepared_by_role": prepared_by_role,
                    "prepared_by_name": prepared_by_name,
                    "approved_by_name": approved_by_name,
                    "approved_at": approved_at,
                    "created_at": now,
                    "updated_at": now,
                    "issued_at": now if status_code in issued_statuses else "",
                    "cancelled_at": now if status_code == "cancelled" else "",
                },
            )
            invoice_id = int(cur.lastrowid)

        self.conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))
        for idx, item in enumerate(items, start=1):
            line = calculate_line_item(
                item.get("quantity"),
                item.get("unit_price"),
                item.get("discount_percent", 0),
                snapshot["vat_rate"],
            )
            self.conn.execute(
                """
                INSERT INTO invoice_items (
                    invoice_id, line_no, category, description, unit, quantity, unit_price,
                    discount_percent, net_amount, vat_amount, gross_amount, code_stage, created_at
                ) VALUES (
                    :invoice_id, :line_no, :category, :description, :unit, :quantity, :unit_price,
                    :discount_percent, :net_amount, :vat_amount, :gross_amount, :code_stage, :created_at
                )
                """,
                {
                    "invoice_id": invoice_id,
                    "line_no": idx,
                    "category": item.get("category", ""),
                    "description": item.get("description", ""),
                    "unit": item.get("unit", ""),
                    "quantity": float(line["quantity"]),
                    "unit_price": float(line["unit_price"]),
                    "discount_percent": float(line["discount_percent"]),
                    "net_amount": float(line["net_amount"]),
                    "vat_amount": float(line["vat_amount"]),
                    "gross_amount": float(line["gross_amount"]),
                    "code_stage": item.get("code_stage", ""),
                    "created_at": now,
                },
            )

        if existing_invoice:
            previous_status = str(existing_invoice.get("status_code") or "draft")
            if previous_status != status_code:
                workflow_details = {
                    "pending_approval": ("submitted_for_approval", "Faktura je poslata na proveru."),
                    "approved": ("approved", "Faktura je odobrena za izdavanje."),
                    "issued": ("issued_after_approval", "Faktura je izdata."),
                }
                action_code, detail = workflow_details.get(
                    status_code,
                    ("status_changed", f"Status fakture je promenjen na: {status_label(status_code)}."),
                )
                self._record_invoice_audit(invoice_id, action_code, detail)
            else:
                self._record_invoice_audit(invoice_id, "updated", "Faktura je izmenjena i ponovo sačuvana.")
        else:
            detail = {
                "draft": "Kreiran je nacrt fakture.",
                "pending_approval": "Kreirana je faktura poslata na proveru.",
                "approved": "Kreirana je odobrena faktura.",
            }.get(status_code, "Faktura je izdata.")
            self._record_invoice_audit(invoice_id, "created", detail)
        if status_code == "pending_approval" and (
            not existing_invoice or str(existing_invoice.get("status_code") or "") != "pending_approval"
        ):
            self._create_owner_notification(
                event_code="invoice_pending_approval",
                title="Faktura čeka odobrenje",
                message=(
                    f"Faktura {invoice_number} za projekat "
                    f"{snapshot.get('project_name') or '-'} čeka pregled vlasnika."
                ),
                invoice_id=invoice_id,
                project_id=project_id,
            )
        self.conn.commit()
        self._maybe_backup(f"invoice_{invoice_number}")
        return invoice_id

    def _derive_status(self, *, status_code: str, due_date: date, paid_total: Decimal, balance_total: Decimal) -> str:
        if status_code == "cancelled":
            return "cancelled"
        if status_code in {"draft", "pending_approval", "approved"}:
            return status_code
        if balance_total <= 0 and paid_total > 0:
            return "paid"
        if paid_total > 0 and balance_total > 0:
            return "partial"
        if due_date and due_date < date.today() and balance_total > 0:
            return "due"
        return "issued"

    def mark_invoice_status(self, invoice_id: int, status_code: str) -> None:
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            return
        normalized_status = normalize_status(status_code)
        if normalized_status == "cancelled":
            raise ValueError("Za storno koristite cancel_invoice sa obaveznim razlogom.")
        issued_statuses = {"issued", "partial", "paid", "due"}
        if str(invoice.get("status_code") or "") in issued_statuses and normalized_status not in issued_statuses:
            raise ValueError("Izdatu fakturu nije moguće vratiti u tok odobravanja.")
        self._backup_before_change(
            f"status_{invoice.get('invoice_number', 'invoice')}",
            replaces_post_backup=True,
        )
        self.conn.execute(
            "UPDATE invoices SET status_code = ?, updated_at = ?, issued_at = CASE WHEN ? IN ('issued','partial','paid','due') AND issued_at = '' THEN ? ELSE issued_at END, cancelled_at = CASE WHEN ? = 'cancelled' THEN COALESCE(NULLIF(cancelled_at,''), ?) ELSE cancelled_at END WHERE id = ?",
            (
                normalized_status,
                now_iso(),
                normalized_status,
                now_iso(),
                normalized_status,
                now_iso(),
                invoice_id,
            ),
        )
        self._record_invoice_audit(invoice_id, "status_changed", f"Status fakture je promenjen na: {status_label(normalized_status)}.")
        self.conn.commit()
        self._maybe_backup(f"status_{invoice.get('invoice_number','invoice')}")

    def cancel_invoice(self, invoice_id: int, reason: str) -> None:
        """Cancel an unpaid issued invoice while keeping its number and history intact."""
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            raise ValueError("Faktura nije pronađena.")
        if invoice.get("status_code") == "cancelled":
            raise ValueError("Faktura je već stornirana.")
        if invoice.get("status_code") in {"draft", "pending_approval", "approved"}:
            raise ValueError("Neizdata faktura se ne stornira. Možete je obrisati ili vratiti na doradu.")
        if abs(money_round(invoice.get("paid_total"))) > Decimal("0.01"):
            raise ValueError(
                "Faktura ima nerazrešenu uplatu. Pre storna evidentirajte povraćaj tako da neto naplaćeno bude 0,00 EUR."
            )
        clean_reason = str(reason or "").strip()
        if not clean_reason:
            raise ValueError("Razlog storna je obavezan.")
        self._backup_before_change(
            f"cancelled_{invoice.get('invoice_number', 'invoice')}",
            replaces_post_backup=True,
        )
        cancelled_at = now_iso()
        storno_note = f"[STORNO {cancelled_at}] {clean_reason}"
        current_note = str(invoice.get("note") or "").strip()
        combined_note = f"{current_note}\n{storno_note}".strip()
        self.conn.execute(
            """
            UPDATE invoices
            SET status_code = 'cancelled', balance_total = 0, updated_at = ?, cancelled_at = ?, note = ?
            WHERE id = ?
            """,
            (cancelled_at, cancelled_at, combined_note, invoice_id),
        )
        self._record_invoice_audit(invoice_id, "cancelled", f"Faktura je stornirana. Razlog: {clean_reason}")
        self.conn.commit()
        self._maybe_backup(f"cancelled_{invoice.get('invoice_number','invoice')}")

    def add_payment(self, invoice_id: int, payment_date: Any, amount: Any, method: str, note: str = "") -> int:
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            raise ValueError("Faktura nije pronađena.")
        if invoice.get("status_code") not in {"issued", "partial", "paid", "due"}:
            raise ValueError("Uplatu je moguće evidentirati tek kada je faktura izdata.")
        payment_date_iso = iso_from_date(payment_date) or today_iso()
        self.assert_financial_date_open(payment_date_iso)
        amount_dec = money_round(amount)
        if amount_dec <= 0:
            raise ValueError("Iznos uplate mora biti veći od nule.")
        self._backup_before_change(f"payment_{invoice_id}", replaces_post_backup=True)
        now = now_iso()
        cur = self.conn.execute(
            """
            INSERT INTO payments (invoice_id, payment_date, amount, method, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (invoice_id, payment_date_iso, float(amount_dec), method, note, now),
        )
        self._record_invoice_audit(
            invoice_id,
            "payment_added",
            f"Dodana uplata {format_currency(amount_dec, invoice.get('currency', DEFAULT_CURRENCY))} ({method or 'bez načina'}).",
        )
        self.conn.commit()
        self.recalculate_invoice(invoice_id)
        self._maybe_backup(f"payment_{invoice_id}")
        return int(cur.lastrowid)

    def add_payment_refund(self, invoice_id: int, payment_date: Any, amount: Any, method: str, note: str = "") -> int:
        """Record a returned payment without deleting the original receipt record."""
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            raise ValueError("Faktura nije pronađena.")
        if invoice.get("status_code") not in {"issued", "partial", "paid", "due"}:
            raise ValueError("Povraćaj je moguće evidentirati samo za izdatu, nestorniranu fakturu.")
        amount_dec = money_round(amount)
        if amount_dec <= 0:
            raise ValueError("Iznos povraćaja mora biti veći od nule.")
        refundable = money_round(invoice.get("paid_total"))
        if refundable <= 0:
            raise ValueError("Na fakturi nema evidentirane uplate za povraćaj.")
        if amount_dec > refundable:
            raise ValueError(
                f"Povraćaj {format_currency(amount_dec, invoice.get('currency', DEFAULT_CURRENCY))} je veći od "
                f"naplaćenog iznosa {format_currency(refundable, invoice.get('currency', DEFAULT_CURRENCY))}."
            )
        payment_date_iso = iso_from_date(payment_date) or today_iso()
        self.assert_financial_date_open(payment_date_iso)
        clean_method = str(method or "Povraćaj uplate").strip() or "Povraćaj uplate"
        clean_note = str(note or "").strip()
        self._backup_before_change(f"refund_{invoice_id}", replaces_post_backup=True)
        now = now_iso()
        cur = self.conn.execute(
            """
            INSERT INTO payments (invoice_id, payment_date, amount, method, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (invoice_id, payment_date_iso, float(-amount_dec), f"Povraćaj - {clean_method}", clean_note, now),
        )
        self._record_invoice_audit(
            invoice_id,
            "refund_added",
            f"Evidentiran povraćaj {format_currency(amount_dec, invoice.get('currency', DEFAULT_CURRENCY))} ({clean_method}).",
        )
        self.conn.commit()
        self.recalculate_invoice(invoice_id)
        self._maybe_backup(f"refund_{invoice_id}")
        return int(cur.lastrowid)

    def delete_payment(self, payment_id: int) -> None:
        self.assert_business_write_access()
        row = self.conn.execute("SELECT invoice_id, amount FROM payments WHERE id = ?", (payment_id,)).fetchone()
        if not row:
            return
        invoice_id = int(row["invoice_id"])
        invoice = self.get_invoice(invoice_id)
        remaining_paid = money_round(invoice.get("paid_total")) - money_round(row["amount"])
        if remaining_paid < Decimal("-0.01"):
            raise ValueError("Prvo obrišite ili ispravite povraćaj. Saldo uplate ne može biti negativan.")
        self._backup_before_change(f"payment_deleted_{payment_id}")
        self.conn.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
        # A payment created from a bank statement must not leave a confirmed
        # statement line behind after it is deleted from the invoice screen.
        # Return that line to review instead of silently losing bank evidence.
        self.conn.execute(
            """UPDATE bank_transactions
               SET status='new', matched_invoice_id=NULL, payment_id=NULL, matched_at=NULL
               WHERE payment_id = ?""",
            (payment_id,),
        )
        self.conn.commit()
        self._record_invoice_audit(
            invoice_id,
            "payment_deleted",
            f"Obrisana uplata {format_currency(row['amount'], invoice.get('currency', DEFAULT_CURRENCY))}.",
        )
        self.conn.commit()
        self.recalculate_invoice(invoice_id)

    def recalculate_invoice(self, invoice_id: int) -> None:
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            return
        items = invoice.get("items", [])
        payments = invoice.get("payments", [])
        paid_total = money_round(sum(decimal_from(p["amount"]) for p in payments))
        totals = calculate_invoice_totals(
            items,
            vat_rate=invoice.get("vat_rate", DEFAULT_VAT_RATE),
            discount_total=invoice.get("discount_total", 0),
            retention_percent=invoice.get("retention_percent", 0),
            advance_amount=invoice.get("advance_amount", 0),
            paid_total=paid_total,
            currency=invoice.get("currency", DEFAULT_CURRENCY),
        )
        due_date = parse_date(invoice.get("due_date")) or date.today()
        status_code = self._derive_status(
            status_code=invoice.get("status_code", "draft"),
            due_date=due_date,
            paid_total=totals["paid_total"],
            balance_total=totals["balance_total"],
        )
        self.conn.execute(
            """
            UPDATE invoices SET
                subtotal=?, tax_base=?, vat_total=?, gross_total=?, retention_amount=?,
                due_before_paid=?, paid_total=?, balance_total=?, status_code=?,
                updated_at=?
            WHERE id=?
            """,
            (
                float(totals["subtotal"]),
                float(totals["tax_base"]),
                float(totals["vat_total"]),
                float(totals["gross_total"]),
                float(totals["retention_amount"]),
                float(totals["due_before_paid"]),
                float(totals["paid_total"]),
                float(totals["balance_total"]),
                status_code,
                now_iso(),
                invoice_id,
            ),
        )
        self.conn.commit()

    def delete_invoice(self, invoice_id: int) -> None:
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            return
        if invoice.get("status_code") not in {"draft", "pending_approval", "approved"}:
            raise ValueError("Izdatu fakturu nije moguće obrisati. Koristite storno da bi broj i istorija ostali sačuvani.")
        if invoice.get("payments"):
            raise ValueError("Nacrt sa evidentiranom uplatom nije moguće obrisati.")
        self._backup_before_change(f"invoice_deleted_{invoice.get('invoice_number', 'draft')}")
        self.conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        self.conn.commit()

    def list_invoices(
        self,
        *,
        search: str = "",
        status_code: str = "",
        customer_id: Optional[int] = None,
        project_id: Optional[int] = None,
        issue_from: Any = None,
        issue_to: Any = None,
        due_from: Any = None,
        due_to: Any = None,
        due_only: bool = False,
        overdue_only: bool = False,
        open_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if search:
            q = f"%{search}%"
            clauses.append(
                "(invoice_number LIKE ? OR customer_name LIKE ? OR project_name LIKE ? OR contract_no LIKE ? OR protocol_no LIKE ? OR site_address LIKE ? OR customer_email LIKE ?)"
            )
            params.extend([q, q, q, q, q, q, q])
        if status_code:
            clauses.append("status_code = ?")
            params.append(normalize_status(status_code))
        if customer_id:
            clauses.append("customer_id = ?")
            params.append(customer_id)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        issue_from_iso = iso_from_date(issue_from)
        issue_to_iso = iso_from_date(issue_to)
        due_from_iso = iso_from_date(due_from)
        due_to_iso = iso_from_date(due_to)
        if issue_from_iso:
            clauses.append("issue_date >= ?")
            params.append(issue_from_iso)
        if issue_to_iso:
            clauses.append("issue_date <= ?")
            params.append(issue_to_iso)
        if due_from_iso:
            clauses.append("due_date >= ?")
            params.append(due_from_iso)
        if due_to_iso:
            clauses.append("due_date <= ?")
            params.append(due_to_iso)
        if due_only:
            clauses.append("balance_total > 0 AND due_date <> '' AND due_date < date('now')")
        if overdue_only:
            clauses.append("balance_total > 0 AND due_date <> '' AND due_date < date('now')")
        if open_only:
            clauses.append("status_code NOT IN ('draft','pending_approval','approved','cancelled') AND balance_total > 0")
        sql = "SELECT * FROM invoices"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY issue_date DESC, id DESC"
        return [row_to_dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def list_customer_invoices(self, customer_name: str) -> list[dict[str, Any]]:
        """Return every invoice stored under one customer snapshot across all projects."""
        name = str(customer_name or "").strip()
        if not name:
            return []
        rows = self.conn.execute(
            """
            SELECT *
            FROM invoices
            WHERE customer_name = ? COLLATE NOCASE
            ORDER BY issue_date DESC, id DESC
            """,
            (name,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def list_available_advance_invoices(
        self,
        *,
        project_id: int,
        customer_id: int | None = None,
        include_invoice_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return paid advance invoices that have not been used by another final invoice."""
        clauses = [
            "a.project_id = ?",
            "a.invoice_kind = 'advance'",
            "a.status_code IN ('issued','partial','paid','due')",
            "a.paid_total > 0",
            "a.status_code <> 'cancelled'",
            "NOT EXISTS (SELECT 1 FROM invoices f WHERE f.advance_source_invoice_id = a.id AND f.status_code <> 'cancelled' AND f.id <> ?)",
        ]
        params: list[Any] = [int(project_id), int(include_invoice_id or 0)]
        if customer_id:
            clauses.append("a.customer_id = ?")
            params.append(int(customer_id))
        rows = self.conn.execute(
            f"""
            SELECT a.*
            FROM invoices a
            WHERE {' AND '.join(clauses)}
            ORDER BY a.issue_date DESC, a.id DESC
            """,
            params,
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def suggest_bank_invoice(self, *, amount: Any, payer_name: str = "", reference: str = "") -> dict[str, Any]:
        """Find one open invoice only when the bank evidence is strong enough to review."""
        amount_dec = money_round(amount)
        payer_key = bank_match_key(payer_name)
        reference_key = bank_match_key(reference)
        rows = self.conn.execute(
            """
            SELECT id, invoice_number, customer_name, project_name, currency, balance_total, due_date
            FROM invoices
            WHERE status_code NOT IN ('draft', 'pending_approval', 'approved', 'cancelled') AND balance_total > 0
            ORDER BY due_date, id
            """
        ).fetchall()
        best: dict[str, Any] = {}
        best_score = 0
        for row in rows:
            invoice = row_to_dict(row)
            score = 0
            reasons: list[str] = []
            invoice_key = bank_match_key(invoice.get("invoice_number"))
            customer_key = bank_match_key(invoice.get("customer_name"))
            if len(invoice_key) >= 4 and invoice_key in reference_key:
                score += 100
                reasons.append("broj fakture")
            balance = money_round(invoice.get("balance_total"))
            if abs(balance - amount_dec) <= Decimal("0.01"):
                score += 50
                reasons.append("tačan iznos")
            elif amount_dec < balance:
                score += 12
                reasons.append("moguća delimična uplata")
            if payer_key and customer_key:
                if payer_key in customer_key or customer_key in payer_key:
                    score += 45
                    reasons.append("naziv kupca")
                else:
                    similarity = SequenceMatcher(None, payer_key, customer_key).ratio()
                    if similarity >= 0.82:
                        score += 35
                        reasons.append("sličan naziv kupca")
                    elif similarity >= 0.68:
                        score += 18
                        reasons.append("mogući naziv kupca")
            score = min(score, 100)
            if score > best_score:
                best_score = score
                best = {
                    "invoice_id": int(invoice["id"]),
                    "invoice_number": invoice.get("invoice_number", ""),
                    "customer_name": invoice.get("customer_name", ""),
                    "project_name": invoice.get("project_name", ""),
                    "currency": invoice.get("currency", ""),
                    "balance_total": balance,
                    "score": score,
                    "reason": ", ".join(reasons),
                }
        # A number in the reference is decisive; otherwise require two corroborating signals.
        return best if best_score >= 60 else {}

    # ------------------------------------------------------------------
    # Owner finance: suppliers, liabilities, liquidity and period control

    @staticmethod
    def _financial_audit_entry_hash(
        *,
        previous_hash: str,
        record_id: int,
        record_type: str,
        linked_record_id: int,
        action_code: str,
        details: str,
        created_at: str,
    ) -> str:
        """Create one stable digest for a financial-audit chain entry."""
        canonical = json.dumps(
            {
                "previous_hash": str(previous_hash or ""),
                "id": int(record_id),
                "record_type": str(record_type or ""),
                "record_id": int(linked_record_id or 0),
                "action_code": str(action_code or ""),
                "details": str(details or ""),
                "created_at": str(created_at or ""),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _record_financial_audit(self, record_type: str, record_id: int, action_code: str, details: str) -> None:
        previous = self.conn.execute(
            "SELECT entry_hash FROM financial_audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(previous["entry_hash"] or "") if previous else ""
        created_at = now_iso()
        cursor = self.conn.execute(
            "INSERT INTO financial_audit_log (record_type, record_id, action_code, details, created_at, previous_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?, '')",
            (str(record_type), int(record_id or 0), str(action_code), str(details), created_at, previous_hash),
        )
        audit_id = int(cursor.lastrowid)
        entry_hash = self._financial_audit_entry_hash(
            previous_hash=previous_hash,
            record_id=audit_id,
            record_type=str(record_type),
            linked_record_id=int(record_id or 0),
            action_code=str(action_code),
            details=str(details),
            created_at=created_at,
        )
        self.conn.execute("UPDATE financial_audit_log SET entry_hash=? WHERE id=?", (entry_hash, audit_id))

    def verify_financial_audit_chain(self) -> dict[str, Any]:
        """Verify the complete local audit chain without changing business data."""
        rows = self.conn.execute(
            "SELECT id, record_type, record_id, action_code, details, created_at, previous_hash, entry_hash FROM financial_audit_log ORDER BY id"
        ).fetchall()
        prior = ""
        for row in rows:
            stored_previous = str(row["previous_hash"] or "")
            stored_hash = str(row["entry_hash"] or "")
            expected = self._financial_audit_entry_hash(
                previous_hash=prior,
                record_id=int(row["id"]),
                record_type=str(row["record_type"] or ""),
                linked_record_id=int(row["record_id"] or 0),
                action_code=str(row["action_code"] or ""),
                details=str(row["details"] or ""),
                created_at=str(row["created_at"] or ""),
            )
            if stored_previous != prior or stored_hash != expected:
                return {
                    "ok": False,
                    "count": len(rows),
                    "invalid_id": int(row["id"]),
                    "last_hash": prior,
                    "detail": f"Audit lanac nije ispravan kod zapisa #{int(row['id'])}.",
                }
            prior = stored_hash
        return {
            "ok": True,
            "count": len(rows),
            "invalid_id": 0,
            "last_hash": prior,
            "detail": f"Audit lanac je ispravan ({len(rows)} zapisa).",
        }

    def list_financial_audit(self, record_type: str = "", record_id: int = 0, *, limit: int = 200) -> list[dict[str, Any]]:
        clauses, params = [], []
        if record_type:
            clauses.append("record_type = ?")
            params.append(record_type)
        if record_id:
            clauses.append("record_id = ?")
            params.append(int(record_id))
        sql = "SELECT * FROM financial_audit_log"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [row_to_dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def record_financial_audit_export(self, exported_by_name: str, record_count: int) -> None:
        """Keep a durable trace whenever the review log leaves OpsNest.

        The export itself is a legitimate accountant action, but it must be
        visible in the same audit stream as approvals, bank matching and
        period controls.  The CSV content remains a local user-selected file;
        only its count and exporting actor are saved in the company database.
        """
        self.assert_business_write_access()
        actor = str(exported_by_name or "").strip() or "Korisnik"
        self._backup_before_change("financial_audit_export", replaces_post_backup=True)
        self._record_financial_audit(
            "financial_audit",
            0,
            "exported",
            f"Izvezen finansijski audit ({max(0, int(record_count))} stavki). Izvezao/la: {actor}.",
        )
        self.conn.commit()
        self._maybe_backup("financial_audit_export")

    def is_financial_date_locked(self, value: Any) -> bool:
        day = iso_from_date(value)
        if not day:
            return False
        return bool(self.conn.execute(
            "SELECT 1 FROM accounting_periods WHERE status = 'closed' AND period_from <= ? AND period_to >= ? LIMIT 1",
            (day, day),
        ).fetchone())

    def assert_financial_date_open(self, value: Any) -> None:
        if self.is_financial_date_locked(value):
            raise ValueError("Ovaj datum pripada zaključenom obračunskom periodu. Otključajte period samo uz kontrolu knjigovođe.")

    def list_accounting_periods(self) -> list[dict[str, Any]]:
        return [row_to_dict(row) for row in self.conn.execute(
            "SELECT * FROM accounting_periods ORDER BY period_from DESC, id DESC"
        ).fetchall()]

    @staticmethod
    def monthly_control_tasks() -> tuple[dict[str, str], ...]:
        """Non-negotiable month-end controls, independent of local country packs."""
        return (
            {"code": "bank_reconciled", "label": "Banka: svi prilivi i odlivi su povezani ili označeni kao izuzetak.", "owner_role": "Knjigovođa"},
            {"code": "receivables_reviewed", "label": "Potraživanja: dospele fakture, podsetnici i sporni iznosi su provereni.", "owner_role": "Knjigovođa"},
            {"code": "payables_reviewed", "label": "Obaveze: dokument, odobrenje i plan plaćanja su provereni.", "owner_role": "Knjigovođa"},
            {"code": "vat_reviewed", "label": "PDV radna evidencija je proverena sa lokalnim računovođom.", "owner_role": "Glavni knjigovođa"},
            {"code": "project_result_reviewed", "label": "Projekti: ugovor, avans, trošak i profitabilnost su pregledani.", "owner_role": "Menadžer projekta"},
            {"code": "documents_archived", "label": "Dokumenti i izvozi za knjigovođu su arhivirani u pripadajuće projekte.", "owner_role": "Knjigovođa"},
            {"code": "audit_integrity_verified", "label": "Finansijski audit: hash-lanac je provereno ispravan i izvoz je arhiviran.", "owner_role": "Glavni knjigovođa"},
            {"code": "approval_authority_reviewed", "label": "Ovlašćenja: limit vlasnika, strani troškovi i izuzetci odobrenja su pregledani.", "owner_role": "Vlasnik / administrator"},
            {"code": "owner_reviewed", "label": "Vlasnik/administrator je pregledao P&L, cash-flow i otvorene izuzetke.", "owner_role": "Vlasnik / administrator"},
            {"code": "backup_verified", "label": "Backup i centralna sinhronizacija su provereni pre zaključavanja perioda.", "owner_role": "Administrator"},
        )

    def monthly_control_checklist(self, period_key: str = "") -> list[dict[str, Any]]:
        """Return one persistent month-end checklist with safe virtual defaults."""
        requested = str(period_key or date.today().strftime("%Y-%m")).strip()
        try:
            parsed = datetime.strptime(requested, "%Y-%m")
        except ValueError as exc:
            raise ValueError("Period kontrole unesite u formatu gggg-mm.") from exc
        normalized = parsed.strftime("%Y-%m")
        saved = {
            str(row["task_code"]): row_to_dict(row)
            for row in self.conn.execute(
                "SELECT * FROM monthly_control_checklist WHERE period_key = ?",
                (normalized,),
            ).fetchall()
        }
        result: list[dict[str, Any]] = []
        for task in self.monthly_control_tasks():
            current = saved.get(task["code"], {})
            result.append({
                "period_key": normalized,
                **task,
                "status": str(current.get("status") or "pending"),
                "note": str(current.get("note") or ""),
                "completed_by": str(current.get("completed_by") or ""),
                "completed_at": str(current.get("completed_at") or ""),
                "updated_at": str(current.get("updated_at") or ""),
            })
        return result

    def set_monthly_control_task(
        self,
        period_key: str,
        task_code: str,
        status: str,
        *,
        note: str = "",
        completed_by: str = "",
    ) -> None:
        self.assert_business_write_access()
        allowed = {task["code"] for task in self.monthly_control_tasks()}
        if str(task_code or "") not in allowed:
            raise ValueError("Kontrolna stavka nije prepoznata.")
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"pending", "done", "blocked"}:
            raise ValueError("Status kontrolne stavke nije ispravan.")
        period = self.monthly_control_checklist(period_key)[0]["period_key"]
        clean_note = str(note or "").strip()
        if normalized_status == "blocked" and not clean_note:
            raise ValueError("Za blokiranu kontrolu unesite razlog.")
        actor = str(completed_by or "").strip() if normalized_status == "done" else ""
        timestamp = now_iso() if normalized_status == "done" else ""
        self._backup_before_change(f"monthly_control_{period}", replaces_post_backup=True)
        self.conn.execute(
            """
            INSERT INTO monthly_control_checklist
                (period_key, task_code, status, note, completed_by, completed_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(period_key, task_code) DO UPDATE SET
                status=excluded.status,
                note=excluded.note,
                completed_by=excluded.completed_by,
                completed_at=excluded.completed_at,
                updated_at=excluded.updated_at
            """,
            (period, str(task_code), normalized_status, clean_note, actor, timestamp, now_iso()),
        )
        self._record_financial_audit(
            "monthly_control",
            int(datetime.strptime(period, "%Y-%m").strftime("%Y%m")),
            f"checklist_{normalized_status}",
            f"{period}: {task_code}. {clean_note}".strip(),
        )
        self.conn.commit()
        self._maybe_backup(f"monthly_control_{period}")

    def save_accounting_period(self, data: dict[str, Any]) -> int:
        self.assert_business_write_access()
        start, end = iso_from_date(data.get("period_from")), iso_from_date(data.get("period_to"))
        if not start or not end or start > end:
            raise ValueError("Unesite važeći početak i kraj obračunskog perioda.")
        status = str(data.get("status") or "open").lower()
        if status not in {"open", "closed"}:
            status = "open"
        existing_id = int(data.get("id") or 0)
        existing = None
        if existing_id:
            existing = self.conn.execute("SELECT * FROM accounting_periods WHERE id=?", (existing_id,)).fetchone()
            if not existing:
                raise ValueError("Obračunski period ne postoji.")
            if str(existing["status"] or "open") == "closed":
                raise ValueError("Zaključani period se ne menja direktno. Za ponovno otvaranje koristite kontrolisanu komandu sa razlogom.")
        overlapping = self.conn.execute(
            "SELECT id,period_from,period_to,status FROM accounting_periods WHERE id<>? AND period_from<=? AND period_to>=? LIMIT 1",
            (existing_id, end, start),
        ).fetchone()
        if overlapping:
            raise ValueError(
                f"Period se preklapa sa postojećim periodom {overlapping['period_from']} – {overlapping['period_to']}. "
                "Preklapajući periodi nisu dozvoljeni."
            )
        if status == "closed":
            start_day, end_day = parse_date(start), parse_date(end)
            if start_day and end_day and start_day.day == 1 and end_day.day == monthrange(end_day.year, end_day.month)[1]:
                month_cursor = start_day
                while month_cursor <= end_day:
                    period_key = month_cursor.strftime("%Y-%m")
                    incomplete = [
                        task for task in self.monthly_control_checklist(period_key)
                        if task.get("status") != "done"
                    ]
                    if incomplete:
                        raise ValueError(
                            f"Period {period_key} ne može biti zaključen: ostalo je {len(incomplete)} mesečnih kontrola. "
                            "Otvorite Finansije → Mesečna kontrola i završite ih pre zaključavanja."
                        )
                    # A checklist is a review aid, never proof by itself.  Re-run
                    # the two integrity-critical controls at the exact point of
                    # closing so a manual tick or a stale report cannot make the
                    # accounting period look protected when it is not.
                    audit_report = self.verify_financial_audit_chain()
                    if not audit_report.get("ok"):
                        raise ValueError(
                            f"Period {period_key} ne može biti zaključen: finansijski audit nije ispravan. "
                            f"{audit_report.get('detail') or 'Proverite audit lanac i prijavite incident administratoru.'}"
                        )
                    backup_report = self.backup_health_report()
                    backup_age = backup_report.get("backup_age_minutes")
                    if (
                        not backup_report.get("ok")
                        or backup_age is None
                        or int(backup_age) > 24 * 60
                    ):
                        detail = str((backup_report.get("backup") or {}).get("detail") or "")
                        age_note = (
                            f" Poslednji backup je star {int(backup_age)} minuta."
                            if backup_age is not None
                            else " Nema važećeg lokalnog backupa."
                        )
                        raise ValueError(
                            f"Period {period_key} ne može biti zaključen: svež i čitljiv backup nije potvrđen."
                            f"{age_note} {detail}".strip()
                        )
                    month_cursor = (month_cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        self._backup_before_change("accounting_period", replaces_post_backup=True)
        now = now_iso()
        if existing_id:
            self.conn.execute(
                "UPDATE accounting_periods SET period_from=?, period_to=?, status=?, note=?, closed_by=?, closed_at=?, updated_at=? WHERE id=?",
                (start, end, status, str(data.get("note") or "").strip(), str(data.get("closed_by") or "").strip() if status == "closed" else "", now if status == "closed" else "", now, existing_id),
            )
            period_id = existing_id
        else:
            cur = self.conn.execute(
                "INSERT INTO accounting_periods (period_from, period_to, status, note, closed_by, closed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (start, end, status, str(data.get("note") or "").strip(), str(data.get("closed_by") or "").strip() if status == "closed" else "", now if status == "closed" else "", now, now),
            )
            period_id = int(cur.lastrowid)
        self._record_financial_audit("accounting_period", period_id, f"period_{status}", f"Period {start} – {end}: {status}.")
        self.conn.commit()
        self._maybe_backup("accounting_period")
        return period_id

    def reopen_accounting_period(self, period_id: int, *, reopened_by: str, reason: str) -> None:
        """Reopen a closed period only through a traceable accountant/owner action."""
        self.assert_business_write_access()
        period = self.conn.execute("SELECT * FROM accounting_periods WHERE id=?", (int(period_id),)).fetchone()
        if not period:
            raise ValueError("Obračunski period ne postoji.")
        if str(period["status"] or "open") != "closed":
            raise ValueError("Samo zaključani period može se ponovo otvoriti.")
        actor = str(reopened_by or "").strip()
        clean_reason = str(reason or "").strip()
        if not actor:
            raise ValueError("Za ponovno otvaranje mora biti upisana odgovorna osoba.")
        if not clean_reason:
            raise ValueError("Za ponovno otvaranje perioda obavezno unesite razlog.")
        self._backup_before_change("accounting_period_reopen", replaces_post_backup=True)
        timestamp = now_iso()
        prior_note = str(period["note"] or "").strip()
        audit_note = f"Ponovno otvorio {actor}: {clean_reason}"
        full_note = f"{prior_note}\n{audit_note}".strip()
        self.conn.execute(
            "UPDATE accounting_periods SET status='open',note=?,closed_by='',closed_at='',updated_at=? WHERE id=?",
            (full_note, timestamp, int(period_id)),
        )
        self._record_financial_audit("accounting_period", int(period_id), "period_reopened", audit_note[:180])
        self.conn.commit()
        self._maybe_backup("accounting_period_reopen")

    def list_vendors(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        where = "" if include_archived else "WHERE archived = 0"
        return [row_to_dict(row) for row in self.conn.execute(f"SELECT * FROM vendors {where} ORDER BY name COLLATE NOCASE").fetchall()]

    def get_vendor(self, vendor_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM vendors WHERE id = ?", (int(vendor_id),)).fetchone()
        return row_to_dict(row)

    def save_vendor(self, data: dict[str, Any]) -> int:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Naziv dobavljača je obavezan.")
        self._backup_before_change("vendor", replaces_post_backup=True)
        payload = {
            "name": name, "tax_id": str(data.get("tax_id") or "").strip(), "vat_number": str(data.get("vat_number") or "").strip(),
            "email": str(data.get("email") or "").strip(), "phone": str(data.get("phone") or "").strip(),
            "iban": str(data.get("iban") or "").strip(), "payment_term_days": max(0, int(data.get("payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS)),
            "note": str(data.get("note") or "").strip(), "archived": int(bool(data.get("archived"))), "updated_at": now_iso(),
        }
        vendor_id = int(data.get("id") or 0)
        if vendor_id:
            self.conn.execute("""UPDATE vendors SET name=:name, tax_id=:tax_id, vat_number=:vat_number, email=:email, phone=:phone,
                iban=:iban, payment_term_days=:payment_term_days, note=:note, archived=:archived, updated_at=:updated_at WHERE id=:id""", {**payload, "id": vendor_id})
        else:
            payload["created_at"] = payload["updated_at"]
            cur = self.conn.execute("""INSERT INTO vendors (name,tax_id,vat_number,email,phone,iban,payment_term_days,note,archived,created_at,updated_at)
                VALUES (:name,:tax_id,:vat_number,:email,:phone,:iban,:payment_term_days,:note,:archived,:created_at,:updated_at)""", payload)
            vendor_id = int(cur.lastrowid)
        self._record_financial_audit("vendor", vendor_id, "saved", f"Dobavljač: {name}")
        self.conn.commit()
        self._maybe_backup("vendor")
        return vendor_id

    def list_vendor_bills(self, *, status: str = "", due_to: Any = None, include_paid: bool = True) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status:
            clauses.append("b.status = ?")
            params.append(status)
        elif not include_paid:
            clauses.append("b.status IN ('open', 'partial')")
        due_limit = iso_from_date(due_to)
        if due_limit:
            clauses.append("b.due_date <> '' AND b.due_date <= ?")
            params.append(due_limit)
        sql = """SELECT b.*, v.name AS vendor_name, p.name AS project_name,
            ROUND(MAX(0, b.gross_amount - b.paid_amount), 2) AS balance_amount
            FROM vendor_bills b LEFT JOIN vendors v ON v.id=b.vendor_id LEFT JOIN projects p ON p.id=b.project_id"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY CASE b.status WHEN 'open' THEN 0 WHEN 'partial' THEN 1 WHEN 'paid' THEN 2 ELSE 3 END, b.due_date, b.id DESC"
        return [row_to_dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_vendor_bill(self, bill_id: int) -> dict[str, Any]:
        rows = self.list_vendor_bills(include_paid=True)
        return next((row for row in rows if int(row.get("id") or 0) == int(bill_id)), {})

    def save_vendor_bill(self, data: dict[str, Any]) -> int:
        self.assert_business_write_access()
        vendor_id = int(data.get("vendor_id") or 0)
        if not vendor_id or not self.get_vendor(vendor_id):
            raise ValueError("Izaberite postojećeg dobavljača.")
        bill_date = iso_from_date(data.get("bill_date")) or today_iso()
        self.assert_financial_date_open(bill_date)
        due_date = iso_from_date(data.get("due_date"))
        if due_date and due_date < bill_date:
            raise ValueError("Rok plaćanja ne može biti pre datuma računa.")
        net = money_round(data.get("net_amount") or 0)
        if net < 0:
            raise ValueError("Trošak ne može biti negativan.")
        rate = decimal_from(data.get("vat_rate") or 0)
        if rate > 1:
            rate /= Decimal("100")
        rate = max(Decimal("0"), rate)
        currency = normalize_currency(data.get("currency"), fallback=self.get_company().get("default_currency") or DEFAULT_CURRENCY)
        existing = self.get_vendor_bill(int(data.get("id") or 0)) if data.get("id") else {}
        source_document_id = int(data.get("source_project_document_id") or existing.get("source_project_document_id") or 0) or None
        if source_document_id:
            source_document = self.get_project_document(source_document_id)
            if not source_document or source_document.get("document_type") != "input":
                raise ValueError("Izvor obaveze mora biti postojeći ulazni dokument projekta.")
            if int(source_document.get("project_id") or 0) != int(data.get("project_id") or 0):
                raise ValueError("Obaveza mora ostati vezana za isti projekat kao izvorni dokument.")
        if existing and money_round(existing.get("paid_amount")) > 0:
            if currency != str(existing.get("currency") or "").upper() or net + (net * rate) < money_round(existing.get("paid_amount")):
                raise ValueError("Delimično plaćen račun ne može dobiti manji iznos ili drugu valutu.")
        gross = money_round(net + net * rate)
        paid = money_round(existing.get("paid_amount") if existing else 0)
        material_change = bool(existing) and any((
            int(existing.get("vendor_id") or 0) != vendor_id,
            int(existing.get("project_id") or 0) != int(data.get("project_id") or 0),
            str(existing.get("bill_number") or "").strip() != str(data.get("bill_number") or "").strip(),
            str(existing.get("bill_date") or "") != bill_date,
            str(existing.get("due_date") or "") != (due_date or ""),
            str(existing.get("category") or "") != str(data.get("category") or "Ostali troškovi").strip(),
            str(existing.get("description") or "") != str(data.get("description") or "").strip(),
            money_round(existing.get("net_amount")) != net,
            money_round(existing.get("vat_rate")) != money_round(rate),
            str(existing.get("currency") or "").upper() != currency,
        ))
        if paid > 0 and material_change:
            raise ValueError("Plaćena ili delimično plaćena obaveza ne može se menjati. Unesite korektivnu obavezu ili evidentirajte odobrenje.")
        status = "paid" if gross > 0 and paid >= gross else ("partial" if paid > 0 else "open")
        approval_status = str(data.get("approval_status") or existing.get("approval_status") or "approved").strip().lower()
        if approval_status not in {"pending", "approved", "rejected"}:
            approval_status = "approved"
        returned_for_review = False
        if existing and material_change and str(existing.get("approval_status") or "approved") == "approved" and paid <= 0:
            # An approved payable is a reviewed financial document.  A real
            # change starts a fresh approval cycle instead of preserving an
            # old owner's decision for a different document.
            approval_status = "pending"
            returned_for_review = True
        if paid > 0:
            approval_status = "approved"
        attachment_path = str(data.get("attachment_path") or existing.get("attachment_path") or "").strip()
        evidence_present = bool(source_document_id or attachment_path)
        evidence_required = False
        if approval_status == "approved" and paid <= 0 and gross > 0 and not evidence_present:
            # A payable without an original document is a planning item, not
            # an approved payment instruction.  Keep it visible for the team,
            # but force it through the evidence + owner-review path.
            approval_status = "pending"
            evidence_required = True
        prepared_by_name = str(data.get("prepared_by_name") or existing.get("prepared_by_name") or "").strip()
        approved_by_name = str(data.get("approved_by_name") or existing.get("approved_by_name") or "").strip() if approval_status == "approved" else ""
        approved_at = str(data.get("approved_at") or existing.get("approved_at") or now_iso()).strip() if approval_status == "approved" else ""
        rejection_reason = str(data.get("rejection_reason") or existing.get("rejection_reason") or "").strip() if approval_status == "rejected" else ""
        rejected_by_name = str(data.get("rejected_by_name") or existing.get("rejected_by_name") or "").strip() if approval_status == "rejected" else ""
        rejected_at = str(data.get("rejected_at") or existing.get("rejected_at") or now_iso()).strip() if approval_status == "rejected" else ""
        payload = {"vendor_id":vendor_id,"project_id":int(data.get("project_id") or 0) or None,"source_project_document_id":source_document_id,"approval_status":approval_status,"prepared_by_name":prepared_by_name,"approved_by_name":approved_by_name,"approved_at":approved_at,"rejection_reason":rejection_reason,"rejected_by_name":rejected_by_name,"rejected_at":rejected_at,"bill_number":str(data.get("bill_number") or "").strip(),
            "bill_date":bill_date,"due_date":due_date or "","category":str(data.get("category") or "Ostali troškovi").strip(),"description":str(data.get("description") or "").strip(),
            "net_amount":float(net),"vat_rate":float(rate),"vat_amount":float(money_round(net*rate)),"gross_amount":float(gross),"currency":currency,"status":status,
            "note":str(data.get("note") or "").strip(),"attachment_path":attachment_path,"updated_at":now_iso()}
        self._backup_before_change("vendor_bill", replaces_post_backup=True)
        bill_id=int(data.get("id") or 0)
        if bill_id:
            self.conn.execute("""UPDATE vendor_bills SET vendor_id=:vendor_id,project_id=:project_id,source_project_document_id=:source_project_document_id,approval_status=:approval_status,prepared_by_name=:prepared_by_name,approved_by_name=:approved_by_name,approved_at=:approved_at,rejection_reason=:rejection_reason,rejected_by_name=:rejected_by_name,rejected_at=:rejected_at,bill_number=:bill_number,bill_date=:bill_date,due_date=:due_date,
            category=:category,description=:description,net_amount=:net_amount,vat_rate=:vat_rate,vat_amount=:vat_amount,gross_amount=:gross_amount,currency=:currency,status=:status,note=:note,attachment_path=:attachment_path,updated_at=:updated_at WHERE id=:id""", {**payload,"id":bill_id})
        else:
            payload["created_at"]=payload["updated_at"]
            cur=self.conn.execute("""INSERT INTO vendor_bills (vendor_id,project_id,source_project_document_id,approval_status,prepared_by_name,approved_by_name,approved_at,rejection_reason,rejected_by_name,rejected_at,bill_number,bill_date,due_date,category,description,net_amount,vat_rate,vat_amount,gross_amount,currency,status,note,attachment_path,created_at,updated_at)
            VALUES (:vendor_id,:project_id,:source_project_document_id,:approval_status,:prepared_by_name,:approved_by_name,:approved_at,:rejection_reason,:rejected_by_name,:rejected_at,:bill_number,:bill_date,:due_date,:category,:description,:net_amount,:vat_rate,:vat_amount,:gross_amount,:currency,:status,:note,:attachment_path,:created_at,:updated_at)""",payload)
            bill_id=int(cur.lastrowid)
        action = "returned_for_review_after_edit" if returned_for_review else "saved"
        details = f"Obaveza {payload['bill_number'] or bill_id}: {currency} {gross}"
        if returned_for_review:
            details += " | Materijalna izmena vraćena je na novo odobravanje."
        if evidence_required:
            details += " | Čeka originalni dokument pre odobrenja za plaćanje."
        self._record_financial_audit("vendor_bill", bill_id, action, details)
        self.conn.commit(); self._maybe_backup("vendor_bill")
        return bill_id

    def pending_vendor_bill_approval_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS total FROM vendor_bills WHERE approval_status = 'pending' AND status <> 'cancelled'").fetchone()
        return int(row["total"] if row else 0)

    def list_vendor_bill_comments(self, bill_id: int) -> list[dict[str, Any]]:
        return [row_to_dict(row) for row in self.conn.execute(
            "SELECT * FROM vendor_bill_comments WHERE vendor_bill_id = ? ORDER BY id DESC",
            (int(bill_id),),
        ).fetchall()]

    def add_vendor_bill_comment(self, bill_id: int, author_name: str, comment_text: str, *, event_type: str = "comment") -> None:
        self.assert_business_write_access()
        if not self.get_vendor_bill(bill_id):
            raise ValueError("Obaveza dobavljača nije pronađena.")
        text = str(comment_text or "").strip()
        if not text:
            raise ValueError("Unesite komentar.")
        author = str(author_name or "").strip() or "Korisnik"
        self._backup_before_change(f"vendor_bill_comment_{bill_id}", replaces_post_backup=True)
        self.conn.execute(
            "INSERT INTO vendor_bill_comments (vendor_bill_id,author_name,event_type,comment_text,created_at) VALUES (?,?,?,?,?)",
            (int(bill_id), author, str(event_type or "comment").strip()[:40], text, now_iso()),
        )
        self._record_financial_audit("vendor_bill", int(bill_id), "commented", f"Komentar: {text[:180]}")
        self.conn.commit()
        self._maybe_backup(f"vendor_bill_comment_{bill_id}")

    def vendor_bill_approval_policy(self, bill: dict[str, Any], *, approver_role: str = "") -> dict[str, Any]:
        """Return a conservative approval decision without inventing FX values.

        A company can set a single owner ceiling in its primary currency.  Above
        that value an administrator cannot release a payable: the owner must
        make the decision.  A foreign-currency payable is also escalated while
        the ceiling is active because OpsNest must not quietly convert it with
        an undocumented exchange rate.
        """
        company = self.get_company()
        ceiling = money_round(company.get("vendor_bill_owner_approval_threshold") or 0)
        company_currency = normalize_currency(company.get("default_currency"), fallback=DEFAULT_CURRENCY)
        bill_currency = normalize_currency(bill.get("currency"), fallback=company_currency)
        gross = money_round(bill.get("gross_amount") or 0)
        normalized_role = str(approver_role or "").strip().lower()
        active = ceiling > 0
        requires_owner = active and (
            bill_currency != company_currency or gross >= ceiling
        )
        return {
            "active": active,
            "ceiling": ceiling,
            "company_currency": company_currency,
            "bill_currency": bill_currency,
            "gross_amount": gross,
            "requires_owner": requires_owner,
            "approver_role": normalized_role,
            "owner_can_approve": normalized_role in {"", "owner"},
        }

    def approve_vendor_bill(self, bill_id: int, approved_by_name: str, *, approver_role: str = "") -> None:
        self.assert_business_write_access()
        bill = self.get_vendor_bill(bill_id)
        if not bill:
            raise ValueError("Obaveza dobavljača nije pronađena.")
        if str(bill.get("approval_status") or "approved") != "pending":
            raise ValueError("Samo obaveza poslata na proveru može biti odobrena.")
        if not int(bill.get("source_project_document_id") or 0) and not str(bill.get("attachment_path") or "").strip():
            raise ValueError("Pre odobrenja priložite originalni račun ili povežite obavezu sa ulaznim dokumentom projekta.")
        approver = str(approved_by_name or "").strip() or "Vlasnik"
        preparer = str(bill.get("prepared_by_name") or "").strip()
        if preparer and preparer.casefold() == approver.casefold():
            raise ValueError(
                "Razdvajanje dužnosti: obavezu mora odobriti druga osoba od one koja ju je pripremila. "
                "Pošaljite je vlasniku ili administratoru na proveru."
            )
        policy = self.vendor_bill_approval_policy(bill, approver_role=approver_role)
        if policy["requires_owner"] and not policy["owner_can_approve"]:
            ceiling_text = format_currency(policy["ceiling"], policy["company_currency"])
            if policy["bill_currency"] != policy["company_currency"]:
                reason = (
                    f"Obaveza je u valuti {policy['bill_currency']}; vlasnik je mora odobriti dok je aktivan "
                    f"limit vlasnika ({ceiling_text}) jer OpsNest ne preračunava kurs bez dokumentovanog FX pravila."
                )
            else:
                reason = f"Obaveza je na/iznad limita vlasnika ({ceiling_text}) i mora je odobriti vlasnik."
            self._backup_before_change(f"vendor_bill_owner_gate_{bill_id}", replaces_post_backup=True)
            self._record_financial_audit("vendor_bill", int(bill_id), "owner_approval_required", reason)
            self.conn.commit()
            self._maybe_backup(f"vendor_bill_owner_gate_{bill_id}")
            raise ValueError(reason)
        self._backup_before_change(f"vendor_bill_approval_{bill_id}", replaces_post_backup=True)
        self.conn.execute(
            "UPDATE vendor_bills SET approval_status='approved', approved_by_name=?, approved_at=?, rejection_reason='', rejected_by_name='', rejected_at='', updated_at=? WHERE id=?",
            (approver, now_iso(), now_iso(), int(bill_id)),
        )
        self.conn.execute(
            "INSERT INTO vendor_bill_comments (vendor_bill_id,author_name,event_type,comment_text,created_at) VALUES (?,?,?,?,?)",
            (int(bill_id), approver, "approved", "Obaveza je odobrena za plaćanje.", now_iso()),
        )
        approval_note = f"Obavezu je odobrio/la: {approver}."
        if policy["requires_owner"]:
            approval_note += " Odobrenje vlasnika prema limitu firme."
        self._record_financial_audit("vendor_bill", int(bill_id), "approved", approval_note)
        self.conn.commit()
        self._maybe_backup(f"vendor_bill_approval_{bill_id}")

    def reject_vendor_bill(self, bill_id: int, rejected_by_name: str, reason: str) -> None:
        self.assert_business_write_access()
        bill = self.get_vendor_bill(bill_id)
        if not bill:
            raise ValueError("Obaveza dobavljača nije pronađena.")
        if str(bill.get("approval_status") or "approved") != "pending":
            raise ValueError("Samo obaveza poslata na proveru može biti odbijena.")
        if money_round(bill.get("paid_amount")) > 0:
            raise ValueError("Delimično plaćena obaveza ne može biti odbijena.")
        reason_text = str(reason or "").strip()
        if not reason_text:
            raise ValueError("Razlog odbijanja je obavezan.")
        reviewer = str(rejected_by_name or "").strip() or "Vlasnik"
        timestamp = now_iso()
        self._backup_before_change(f"vendor_bill_rejection_{bill_id}", replaces_post_backup=True)
        self.conn.execute(
            "UPDATE vendor_bills SET approval_status='rejected', approved_by_name='', approved_at='', rejection_reason=?, rejected_by_name=?, rejected_at=?, updated_at=? WHERE id=?",
            (reason_text, reviewer, timestamp, timestamp, int(bill_id)),
        )
        self.conn.execute(
            "INSERT INTO vendor_bill_comments (vendor_bill_id,author_name,event_type,comment_text,created_at) VALUES (?,?,?,?,?)",
            (int(bill_id), reviewer, "rejected", reason_text, timestamp),
        )
        self._record_financial_audit("vendor_bill", int(bill_id), "rejected", f"Odbio/la {reviewer}: {reason_text[:180]}")
        self.conn.commit()
        self._maybe_backup(f"vendor_bill_rejection_{bill_id}")

    def resubmit_vendor_bill(self, bill_id: int, prepared_by_name: str, comment: str = "") -> None:
        self.assert_business_write_access()
        bill = self.get_vendor_bill(bill_id)
        if not bill:
            raise ValueError("Obaveza dobavljača nije pronađena.")
        if str(bill.get("approval_status") or "approved") == "approved":
            raise ValueError("Odobrena obaveza se već može platiti; nema potrebe za ponovnom proverom.")
        if money_round(bill.get("paid_amount")) > 0:
            raise ValueError("Delimično plaćena obaveza ne može se vratiti na proveru.")
        preparer = str(prepared_by_name or "").strip() or str(bill.get("prepared_by_name") or "").strip() or "Korisnik"
        timestamp = now_iso()
        note = str(comment or "").strip() or "Obaveza je vraćena na proveru."
        self._backup_before_change(f"vendor_bill_resubmit_{bill_id}", replaces_post_backup=True)
        self.conn.execute(
            "UPDATE vendor_bills SET approval_status='pending', prepared_by_name=?, approved_by_name='', approved_at='', rejection_reason='', rejected_by_name='', rejected_at='', updated_at=? WHERE id=?",
            (preparer, timestamp, int(bill_id)),
        )
        self.conn.execute(
            "INSERT INTO vendor_bill_comments (vendor_bill_id,author_name,event_type,comment_text,created_at) VALUES (?,?,?,?,?)",
            (int(bill_id), preparer, "resubmitted", note, timestamp),
        )
        self._record_financial_audit("vendor_bill", int(bill_id), "resubmitted", note[:180])
        self.conn.commit()
        self._maybe_backup(f"vendor_bill_resubmit_{bill_id}")

    def vendor_payment_plan(self, *, days: int = 7) -> dict[str, Any]:
        """Return actionable approved liabilities, separated from pending/rejected review work."""
        today = date.today()
        horizon = today + timedelta(days=max(0, int(days)))
        ready, waiting, totals = [], [], {}
        for bill in self.list_vendor_bills(include_paid=False):
            approval = str(bill.get("approval_status") or "approved")
            has_evidence = bool(int(bill.get("source_project_document_id") or 0) or str(bill.get("attachment_path") or "").strip())
            if approval != "approved" or not has_evidence:
                waiting.append({
                    **bill,
                    "review_blocker": "missing_evidence" if not has_evidence else "approval_pending",
                })
                continue
            due = parse_date(bill.get("due_date"))
            if due is None:
                bucket, days_until = "without_due_date", None
            else:
                days_until = (due - today).days
                bucket = "overdue" if days_until < 0 else "today" if days_until == 0 else "next_7_days" if due <= horizon else "later"
            row = {**bill, "payment_bucket": bucket, "days_until_due": days_until}
            ready.append(row)
            currency = str(bill.get("currency") or DEFAULT_CURRENCY)
            totals[currency] = money_round(totals.get(currency, 0)) + money_round(bill.get("balance_amount"))
        return {"as_of": today.isoformat(), "through": horizon.isoformat(), "ready": ready, "waiting_for_approval": waiting, "totals": totals}

    def vendor_bill_for_project_document(self, document_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT id FROM vendor_bills WHERE source_project_document_id = ? LIMIT 1",
            (int(document_id),),
        ).fetchone()
        return self.get_vendor_bill(int(row["id"])) if row else {}

    def create_vendor_bill_from_project_document(
        self,
        document_id: int,
        vendor_id: int,
        due_date: Any = None,
        *,
        approval_status: str = "approved",
        prepared_by_name: str = "",
        approved_by_name: str = "",
    ) -> int:
        """Turn one reviewed project input document into one payable, without duplicating P&L.

        The project document remains the expense evidence.  The linked vendor bill
        is used only for due dates, payment control and bank reconciliation;
        company P&L explicitly excludes linked bills.
        """
        document = self.get_project_document(document_id)
        if not document or document.get("document_type") != "input":
            raise ValueError("Izaberite postojeći ulazni dokument projekta.")
        existing = self.vendor_bill_for_project_document(document_id)
        if existing:
            raise ValueError("Za ovaj dokument već postoji obaveza dobavljača.")
        vendor = self.get_vendor(int(vendor_id))
        if not vendor or vendor.get("archived"):
            raise ValueError("Izaberite aktivnog dobavljača.")
        bill_day = iso_from_date(document.get("document_date")) or today_iso()
        default_due = (parse_date(bill_day) + timedelta(days=int(vendor.get("payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS))).isoformat()
        bill_id = self.save_vendor_bill({
            "vendor_id": int(vendor_id),
            "project_id": int(document["project_id"]),
            "source_project_document_id": int(document_id),
            "bill_number": document.get("document_no") or "",
            "bill_date": bill_day,
            "due_date": iso_from_date(due_date) or default_due,
            "category": document.get("cost_group") or "Ostali troškovi",
            "description": document.get("description") or "",
            "net_amount": document.get("net_amount") or 0,
            "vat_rate": document.get("vat_rate") or 0,
            "currency": document.get("currency") or self.get_company().get("default_currency") or DEFAULT_CURRENCY,
            "note": f"Nastalo iz projektnog dokumenta #{int(document_id)}.",
            "approval_status": approval_status,
            "prepared_by_name": prepared_by_name,
            "approved_by_name": approved_by_name,
        })
        self._record_financial_audit("project_document", int(document_id), "payable_created", f"Kreirana obaveza dobavljača #{bill_id}.")
        self.conn.commit()
        return bill_id

    def record_vendor_bill_payment(
        self,
        bill_id: int,
        amount: Any,
        paid_date: Any,
        *,
        reference: str = "",
        recorded_by_name: str = "",
    ) -> None:
        self.assert_business_write_access()
        bill = self.get_vendor_bill(bill_id)
        if not bill or bill.get("status") == "cancelled":
            raise ValueError("Obaveza ne postoji ili je zatvorena.")
        if str(bill.get("approval_status") or "approved") != "approved":
            raise ValueError("Obaveza mora prvo biti odobrena pre evidentiranja plaćanja.")
        if not int(bill.get("source_project_document_id") or 0) and not str(bill.get("attachment_path") or "").strip():
            raise ValueError("Plaćanje nije dozvoljeno bez originalnog dokumenta ili povezanog ulaznog računa.")
        day = iso_from_date(paid_date) or today_iso()
        self.assert_financial_date_open(day)
        amount_dec = money_round(amount)
        remaining = money_round(bill.get("gross_amount")) - money_round(bill.get("paid_amount"))
        if amount_dec <= 0 or amount_dec - remaining > Decimal("0.01"):
            raise ValueError("Iznos plaćanja mora biti veći od nule i ne sme preći otvorenu obavezu.")
        paid = money_round(bill.get("paid_amount")) + amount_dec
        status = "paid" if paid + Decimal("0.01") >= money_round(bill.get("gross_amount")) else "partial"
        self._backup_before_change("vendor_bill_payment", replaces_post_backup=True)
        self.conn.execute("UPDATE vendor_bills SET paid_amount=?, status=?, updated_at=? WHERE id=?", (float(paid),status,now_iso(),bill_id))
        actor = str(recorded_by_name or "").strip()
        actor_note = f" Potvrdio/la: {actor}." if actor else ""
        self._record_financial_audit(
            "vendor_bill",
            bill_id,
            "payment_recorded",
            f"Plaćeno {format_currency(amount_dec,bill.get('currency') or DEFAULT_CURRENCY)} {reference}".strip() + actor_note,
        )
        self.conn.commit()
        self._maybe_backup("vendor_bill_payment")

    def list_cash_accounts(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        where = "WHERE active=1" if active_only else ""
        return [row_to_dict(row) for row in self.conn.execute(f"SELECT * FROM cash_accounts {where} ORDER BY currency,name").fetchall()]

    def save_cash_account(self, data: dict[str, Any]) -> int:
        self.assert_business_write_access()
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Naziv računa ili kase je obavezan.")
        currency = normalize_currency(data.get("currency"), fallback=self.get_company().get("default_currency") or DEFAULT_CURRENCY)
        payload={"name":name,"account_type":str(data.get("account_type") or "bank").strip(),"currency":currency,"opening_balance":float(money_round(data.get("opening_balance") or 0)),"opening_date":iso_from_date(data.get("opening_date")) or today_iso(),"iban_last4":str(data.get("iban_last4") or "").strip()[-4:],"active":int(bool(data.get("active",True))),"note":str(data.get("note") or "").strip(),"updated_at":now_iso()}
        self._backup_before_change("cash_account", replaces_post_backup=True)
        account_id=int(data.get("id") or 0)
        if account_id:
            self.conn.execute("""UPDATE cash_accounts SET name=:name,account_type=:account_type,currency=:currency,opening_balance=:opening_balance,opening_date=:opening_date,iban_last4=:iban_last4,active=:active,note=:note,updated_at=:updated_at WHERE id=:id""",{**payload,"id":account_id})
        else:
            payload["created_at"]=payload["updated_at"]
            cur=self.conn.execute("""INSERT INTO cash_accounts (name,account_type,currency,opening_balance,opening_date,iban_last4,active,note,created_at,updated_at) VALUES (:name,:account_type,:currency,:opening_balance,:opening_date,:iban_last4,:active,:note,:created_at,:updated_at)""",payload);account_id=int(cur.lastrowid)
        self._record_financial_audit("cash_account",account_id,"saved",name);self.conn.commit();self._maybe_backup("cash_account");return account_id

    def _advance_months(self, value: date, months: int) -> date:
        serial = value.month - 1 + max(1, int(months))
        year, month = value.year + serial // 12, serial % 12 + 1
        return date(year, month, min(value.day, monthrange(year, month)[1]))

    def list_recurring_expenses(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        where="WHERE r.active=1" if active_only else ""
        return [row_to_dict(row) for row in self.conn.execute(f"SELECT r.*,v.name AS vendor_name,p.name AS project_name FROM recurring_expenses r LEFT JOIN vendors v ON v.id=r.vendor_id LEFT JOIN projects p ON p.id=r.project_id {where} ORDER BY r.next_run_date,r.name").fetchall()]

    def save_recurring_expense(self, data: dict[str, Any]) -> int:
        self.assert_business_write_access()
        vendor_id=int(data.get("vendor_id") or 0)
        if not vendor_id or not self.get_vendor(vendor_id): raise ValueError("Izaberite dobavljača za ponavljajući trošak.")
        name=str(data.get("name") or "").strip()
        if not name: raise ValueError("Naziv ponavljajućeg troška je obavezan.")
        net=money_round(data.get("net_amount") or 0); rate=decimal_from(data.get("vat_rate") or 0); rate=rate/Decimal("100") if rate>1 else rate
        payload={"vendor_id":vendor_id,"project_id":int(data.get("project_id") or 0) or None,"name":name,"category":str(data.get("category") or "Ostali troškovi"),"interval_months":max(1,int(data.get("interval_months") or 1)),"next_run_date":iso_from_date(data.get("next_run_date")) or today_iso(),"net_amount":float(max(Decimal("0"),net)),"vat_rate":float(max(Decimal("0"),rate)),"currency":normalize_currency(data.get("currency"),fallback=self.get_company().get("default_currency") or DEFAULT_CURRENCY),"payment_term_days":max(0,int(data.get("payment_term_days") or DEFAULT_PAYMENT_TERM_DAYS)),"active":int(bool(data.get("active",True))),"note":str(data.get("note") or "").strip(),"updated_at":now_iso()}
        self._backup_before_change("recurring_expense", replaces_post_backup=True)
        template_id=int(data.get("id") or 0)
        if template_id:self.conn.execute("""UPDATE recurring_expenses SET vendor_id=:vendor_id,project_id=:project_id,name=:name,category=:category,interval_months=:interval_months,next_run_date=:next_run_date,net_amount=:net_amount,vat_rate=:vat_rate,currency=:currency,payment_term_days=:payment_term_days,active=:active,note=:note,updated_at=:updated_at WHERE id=:id""",{**payload,"id":template_id})
        else:
            payload["created_at"]=payload["updated_at"];cur=self.conn.execute("""INSERT INTO recurring_expenses (vendor_id,project_id,name,category,interval_months,next_run_date,net_amount,vat_rate,currency,payment_term_days,active,note,created_at,updated_at) VALUES (:vendor_id,:project_id,:name,:category,:interval_months,:next_run_date,:net_amount,:vat_rate,:currency,:payment_term_days,:active,:note,:created_at,:updated_at)""",payload);template_id=int(cur.lastrowid)
        self._record_financial_audit("recurring_expense",template_id,"saved",name);self.conn.commit();self._maybe_backup("recurring_expense");return template_id

    def run_due_recurring_expenses(self, *, through: Any = None) -> int:
        """Create each scheduled supplier bill exactly once, including after a restart.

        A recurring run is recorded before the payable is created.  If Windows,
        Excel, or the application stops between database commits, the next run
        recovers the same record and advances the schedule without duplicating
        the financial obligation.
        """
        self.assert_business_write_access()
        limit = parse_date(through) or date.today()
        created = 0
        for template in self.list_recurring_expenses(active_only=True):
            template_id = int(template["id"])
            run_date = parse_date(template.get("next_run_date"))
            while run_date and run_date <= limit:
                run_key = run_date.isoformat()
                now = now_iso()
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO recurring_expense_runs
                        (recurring_expense_id, run_date, status, created_at, updated_at)
                    VALUES (?, ?, 'pending', ?, ?)
                    """,
                    (template_id, run_key, now, now),
                )
                self.conn.commit()
                run = self.conn.execute(
                    "SELECT * FROM recurring_expense_runs WHERE recurring_expense_id=? AND run_date=?",
                    (template_id, run_key),
                ).fetchone()
                if not run:
                    raise ValueError("Nije moguće evidentirati pokretanje ponavljajućeg troška.")

                bill_id = int(run["vendor_bill_id"] or 0)
                if not bill_id or not self.conn.execute("SELECT 1 FROM vendor_bills WHERE id=?", (bill_id,)).fetchone():
                    # Recover a bill created just before a previous interruption.
                    bill_number = f"AUTO-{template_id}-{run_date:%Y%m%d}"
                    existing_bill = self.conn.execute(
                        "SELECT id FROM vendor_bills WHERE bill_number=? ORDER BY id DESC LIMIT 1",
                        (bill_number,),
                    ).fetchone()
                    if existing_bill:
                        bill_id = int(existing_bill["id"])
                    else:
                        due = run_date + timedelta(days=int(template.get("payment_term_days") or 0))
                        bill_id = self.save_vendor_bill(
                            {
                                "vendor_id": template["vendor_id"],
                                "project_id": template.get("project_id"),
                                "bill_number": bill_number,
                                "bill_date": run_key,
                                "due_date": due.isoformat(),
                                "category": template.get("category"),
                                "description": template.get("name"),
                                "net_amount": template.get("net_amount"),
                                "vat_rate": template.get("vat_rate"),
                                "currency": template.get("currency"),
                                "note": template.get("note"),
                            }
                        )
                        created += 1
                    self.conn.execute(
                        "UPDATE recurring_expense_runs SET vendor_bill_id=?,status='created',completed_at=?,updated_at=? WHERE id=?",
                        (bill_id, now_iso(), now_iso(), int(run["id"])),
                    )
                    self.conn.commit()

                next_day = self._advance_months(run_date, int(template.get("interval_months") or 1))
                self.conn.execute(
                    "UPDATE recurring_expenses SET next_run_date=?,last_bill_id=?,last_run_at=?,updated_at=? WHERE id=?",
                    (next_day.isoformat(), bill_id, now_iso(), now_iso(), template_id),
                )
                self._record_financial_audit(
                    "recurring_expense",
                    template_id,
                    "run_recovered" if int(run["vendor_bill_id"] or 0) else "run_created",
                    f"{run_key}: obaveza #{bill_id}.",
                )
                self.conn.commit()
                run_date = next_day
        if created:
            self._maybe_backup("recurring_expense_run")
        return created

    # These records form a reviewable working ledger.  They intentionally do
    # not auto-post tax documents: statutory account mappings and filing rules
    # remain country-specific and must be configured by a local accountant.
    def list_ledger_accounts(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE active=1" if active_only else ""
        return [row_to_dict(row) for row in self.conn.execute(f"SELECT * FROM ledger_accounts {where} ORDER BY code, name").fetchall()]

    def save_ledger_account(self, data: dict[str, Any]) -> int:
        self.assert_business_write_access()
        code = str(data.get("code") or "").strip()
        name = str(data.get("name") or "").strip()
        account_type = str(data.get("account_type") or "expense").strip().lower()
        if not code or not name:
            raise ValueError("Šifra i naziv konta su obavezni.")
        if account_type not in {"asset", "liability", "equity", "income", "expense"}:
            raise ValueError("Vrsta konta mora biti aktiva, obaveza, kapital, prihod ili rashod.")
        payload = {"code": code, "name": name, "account_type": account_type, "active": int(bool(data.get("active", True))), "note": str(data.get("note") or "").strip(), "updated_at": now_iso()}
        self._backup_before_change("ledger_account", replaces_post_backup=True)
        account_id = int(data.get("id") or 0)
        try:
            if account_id:
                self.conn.execute("UPDATE ledger_accounts SET code=:code,name=:name,account_type=:account_type,active=:active,note=:note,updated_at=:updated_at WHERE id=:id", {**payload, "id": account_id})
            else:
                payload["created_at"] = payload["updated_at"]
                cur = self.conn.execute("INSERT INTO ledger_accounts (code,name,account_type,active,note,created_at,updated_at) VALUES (:code,:name,:account_type,:active,:note,:created_at,:updated_at)", payload)
                account_id = int(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("Šifra konta već postoji.") from exc
        self._record_financial_audit("ledger_account", account_id, "saved", f"{code} {name}")
        self.conn.commit(); self._maybe_backup("ledger_account")
        return account_id

    def list_journal_entries(self, *, include_drafts: bool = True, limit: int = 300) -> list[dict[str, Any]]:
        where = "" if include_drafts else "WHERE e.status='posted'"
        return [row_to_dict(row) for row in self.conn.execute(f"""SELECT e.*, COUNT(l.id) AS line_count,
            GROUP_CONCAT(DISTINCT l.currency) AS currencies
            FROM journal_entries e LEFT JOIN journal_lines l ON l.entry_id=e.id {where}
            GROUP BY e.id ORDER BY e.entry_date DESC, e.id DESC LIMIT ?""", (max(1, int(limit)),)).fetchall()]

    def get_journal_entry(self, entry_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM journal_entries WHERE id=?", (int(entry_id),)).fetchone()
        if not row:
            raise ValueError("Stavka dnevnika ne postoji.")
        return row_to_dict(row)

    def list_journal_lines(self, entry_id: int) -> list[dict[str, Any]]:
        return [row_to_dict(row) for row in self.conn.execute(
            """SELECT l.*, a.code AS account_code, a.name AS account_name, a.account_type
            FROM journal_lines l JOIN ledger_accounts a ON a.id=l.account_id
            WHERE l.entry_id=? ORDER BY l.id""",
            (int(entry_id),),
        ).fetchall()]

    def save_journal_entry(self, data: dict[str, Any]) -> int:
        self.assert_business_write_access()
        entry_date = iso_from_date(data.get("entry_date")) or today_iso()
        self.assert_financial_date_open(entry_date)
        entry_id = int(data.get("id") or 0)
        status = str(data.get("status") or "draft").strip().lower()
        if status not in {"draft", "posted"}:
            raise ValueError("Status dnevnika mora biti nacrt ili proknjiženo.")
        if status == "posted":
            raise ValueError("Nova ili izmenjena stavka dnevnika čuva se kao nacrt. Za knjiženje koristite proverenu akciju 'Proknjiži nacrt'.")
        lines = data.get("lines") or []
        if len(lines) < 2:
            raise ValueError("Dnevnik mora imati najmanje dve stavke.")
        validated: list[dict[str, Any]] = []
        totals: dict[str, list[Decimal]] = {}
        for raw in lines:
            account_id = int(raw.get("account_id") or 0)
            if not account_id or not any(int(a["id"]) == account_id for a in self.list_ledger_accounts(active_only=True)):
                raise ValueError("Svaka stavka mora imati aktivno konto.")
            debit, credit = money_round(raw.get("debit") or 0), money_round(raw.get("credit") or 0)
            if debit < 0 or credit < 0 or (debit > 0) == (credit > 0):
                raise ValueError("Svaka stavka ima ili duguje ili potražuje iznos veći od nule.")
            currency = normalize_currency(raw.get("currency"), fallback=self.get_company().get("default_currency") or DEFAULT_CURRENCY)
            totals.setdefault(currency, [Decimal("0"), Decimal("0")]); totals[currency][0] += debit; totals[currency][1] += credit
            validated.append({"account_id": account_id, "debit": debit, "credit": credit, "currency": currency, "source_type": str(raw.get("source_type") or "manual"), "source_id": int(raw.get("source_id") or 0) or None})
        unbalanced = [currency for currency, pair in totals.items() if abs(pair[0] - pair[1]) > Decimal("0.01")]
        if unbalanced:
            raise ValueError("Duguje i potražuje moraju biti jednaki u svakoj valuti: " + ", ".join(unbalanced))
        if entry_id:
            existing = self.conn.execute("SELECT status FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
            if not existing:
                raise ValueError("Stavka dnevnika ne postoji.")
            if existing["status"] == "posted":
                raise ValueError("Proknjižena stavka je zaključana; unesite korektivnu stavku.")
        payload = {"entry_date": entry_date, "reference": str(data.get("reference") or "").strip(), "description": str(data.get("description") or "").strip(), "status": status, "note": str(data.get("note") or "").strip(), "updated_at": now_iso()}
        self._backup_before_change("journal_entry", replaces_post_backup=True)
        if entry_id:
            self.conn.execute("UPDATE journal_entries SET entry_date=:entry_date,reference=:reference,description=:description,status=:status,note=:note,updated_at=:updated_at WHERE id=:id", {**payload, "id": entry_id})
            self.conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (entry_id,))
        else:
            payload["created_at"] = payload["updated_at"]
            cur = self.conn.execute("INSERT INTO journal_entries (entry_date,reference,description,status,note,created_at,updated_at) VALUES (:entry_date,:reference,:description,:status,:note,:created_at,:updated_at)", payload)
            entry_id = int(cur.lastrowid)
        self.conn.executemany("INSERT INTO journal_lines (entry_id,account_id,debit_amount,credit_amount,currency,source_type,source_id,created_at) VALUES (?,?,?,?,?,?,?,?)", [(entry_id, item["account_id"], float(item["debit"]), float(item["credit"]), item["currency"], item["source_type"], item["source_id"], now_iso()) for item in validated])
        self._record_financial_audit("journal_entry", entry_id, "posted" if status == "posted" else "saved_draft", f"{entry_date}: {payload['description'] or payload['reference'] or 'Dnevnik'}")
        self.conn.commit(); self._maybe_backup("journal_entry")
        return entry_id

    def post_journal_entry(self, entry_id: int, *, posted_by: str, comment: str = "") -> None:
        """Post a reviewed journal draft without allowing a silent edit path.

        The document is revalidated here (not only when it was saved) so an
        entry cannot become posted after an account was deactivated or a
        financial period was closed.
        """
        self.assert_business_write_access()
        actor = str(posted_by or "").strip()
        if not actor:
            raise ValueError("Za knjiženje je potrebna odgovorna osoba.")
        entry = self.conn.execute("SELECT * FROM journal_entries WHERE id=?", (int(entry_id),)).fetchone()
        if not entry:
            raise ValueError("Stavka dnevnika ne postoji.")
        if str(entry["status"] or "").lower() != "draft":
            raise ValueError("Moguće je proknjižiti samo stavku u nacrtu.")
        entry_date = iso_from_date(entry["entry_date"]) or today_iso()
        self.assert_financial_date_open(entry_date)
        lines = self.conn.execute(
            """SELECT l.*, a.active AS account_active FROM journal_lines l
            JOIN ledger_accounts a ON a.id=l.account_id WHERE l.entry_id=? ORDER BY l.id""",
            (int(entry_id),),
        ).fetchall()
        if len(lines) < 2:
            raise ValueError("Dnevnik mora imati najmanje dve stavke pre knjiženja.")
        totals: dict[str, list[Decimal]] = {}
        for line in lines:
            if not bool(line["account_active"]):
                raise ValueError("Nije moguće knjižiti: jedno od konta više nije aktivno.")
            debit, credit = money_round(line["debit_amount"]), money_round(line["credit_amount"])
            if debit < 0 or credit < 0 or (debit > 0) == (credit > 0):
                raise ValueError("Dnevnik sadrži neispravnu duguje/potražuje stavku.")
            currency = normalize_currency(line["currency"], fallback=self.get_company().get("default_currency") or DEFAULT_CURRENCY)
            totals.setdefault(currency, [Decimal("0"), Decimal("0")])
            totals[currency][0] += debit; totals[currency][1] += credit
        unbalanced = [currency for currency, pair in totals.items() if abs(pair[0] - pair[1]) > Decimal("0.01")]
        if unbalanced:
            raise ValueError("Duguje i potražuje moraju biti jednaki u svakoj valuti: " + ", ".join(unbalanced))
        self._backup_before_change("journal_entry", replaces_post_backup=True)
        self.conn.execute("UPDATE journal_entries SET status='posted',updated_at=? WHERE id=?", (now_iso(), int(entry_id)))
        suffix = f" | komentar: {str(comment).strip()}" if str(comment).strip() else ""
        self._record_financial_audit("journal_entry", int(entry_id), "posted_after_review", f"{actor}: {entry_date}{suffix}")
        self.conn.commit(); self._maybe_backup("journal_entry")

    def create_reversing_journal_entry(
        self,
        entry_id: int,
        *,
        created_by: str,
        reason: str,
        entry_date: Any = None,
    ) -> int:
        """Create an editable correcting draft for a posted journal entry.

        Posted history stays intact.  The new document has debit and credit
        swapped on every source line and must still pass normal review before
        it is posted.
        """
        self.assert_business_write_access()
        actor, note = str(created_by or "").strip(), str(reason or "").strip()
        if not actor or not note:
            raise ValueError("Za korektivnu stavku su obavezni odgovorna osoba i razlog.")
        source = self.conn.execute("SELECT * FROM journal_entries WHERE id=?", (int(entry_id),)).fetchone()
        if not source:
            raise ValueError("Izvorna stavka dnevnika ne postoji.")
        if str(source["status"] or "").lower() != "posted":
            raise ValueError("Korektivna stavka se pravi samo iz proknjižene stavke.")
        correction_date = iso_from_date(entry_date) or today_iso()
        self.assert_financial_date_open(correction_date)
        source_lines = self.conn.execute("SELECT * FROM journal_lines WHERE entry_id=? ORDER BY id", (int(entry_id),)).fetchall()
        if len(source_lines) < 2:
            raise ValueError("Izvorna stavka nema dovoljno redova za korekciju.")
        correction_id = self.save_journal_entry({
            "entry_date": correction_date,
            "reference": f"KOR-{source['reference'] or entry_id}",
            "description": f"Korekcija #{entry_id}: {source['description'] or source['reference'] or 'dnevnik'}",
            "status": "draft",
            "note": f"Korekcija napravio: {actor}. Razlog: {note}",
            "lines": [
                {
                    "account_id": int(line["account_id"]),
                    "debit": line["credit_amount"],
                    "credit": line["debit_amount"],
                    "currency": line["currency"],
                    "source_type": "journal_reversal",
                    "source_id": int(entry_id),
                }
                for line in source_lines
            ],
        })
        self._record_financial_audit("journal_entry", int(entry_id), "correction_draft_created", f"{actor}: korektivni nacrt #{correction_id}. Razlog: {note}")
        self.conn.commit(); self._maybe_backup("journal_entry")
        return correction_id

    def ledger_trial_balance(self, *, through: Any = None) -> dict[str, Any]:
        through_iso = iso_from_date(through)
        sql = """SELECT a.id,a.code,a.name,a.account_type,l.currency,COALESCE(SUM(l.debit_amount),0) AS debit,COALESCE(SUM(l.credit_amount),0) AS credit
            FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN ledger_accounts a ON a.id=l.account_id
            WHERE e.status='posted'"""
        params: list[Any] = []
        if through_iso:
            sql += " AND e.entry_date <= ?"; params.append(through_iso)
        sql += " GROUP BY a.id,l.currency ORDER BY l.currency,a.code"
        rows = []
        for row in self.conn.execute(sql, params).fetchall():
            item = row_to_dict(row); debit, credit = money_round(item.get("debit")), money_round(item.get("credit")); item["balance"] = float(money_round(debit-credit)); rows.append(item)
        return {"through": through_iso, "rows": rows}

    def ledger_balance_sheet(self, *, through: Any = None) -> dict[str, dict[str, Decimal]]:
        result: dict[str, dict[str, Decimal]] = {}
        for row in self.ledger_trial_balance(through=through)["rows"]:
            currency = str(row.get("currency") or DEFAULT_CURRENCY); account_type = str(row.get("account_type") or "expense")
            bucket = result.setdefault(currency, {"assets": Decimal("0"), "liabilities": Decimal("0"), "equity": Decimal("0"), "income": Decimal("0"), "expenses": Decimal("0")})
            balance = money_round(row.get("balance"))
            key = {"asset": "assets", "liability": "liabilities", "equity": "equity", "income": "income", "expense": "expenses"}.get(account_type, "expenses")
            # Credit-normal accounts are shown with their natural sign.
            bucket[key] += balance if account_type in {"asset", "expense"} else -balance
        for values in result.values():
            values["current_result"] = values["income"] - values["expenses"]
        return result

    def company_financial_summary(self, *, period_from: Any = None, period_to: Any = None) -> dict[str, Any]:
        """Return an honest multi-currency P&L and open-liability picture.

        Amounts are kept by currency; no guessed FX conversion is ever used in
        a financial total.
        """
        start,end=iso_from_date(period_from),iso_from_date(period_to)
        def clause(field: str) -> tuple[str,list[Any]]:
            bits,params=[],[]
            if start: bits.append(f"{field} >= ?");params.append(start)
            if end: bits.append(f"{field} <= ?");params.append(end)
            return (" AND "+" AND ".join(bits) if bits else ""),params
        inv_where,inv_params=clause("issue_date"); doc_where,doc_params=clause("document_date"); bill_where,bill_params=clause("bill_date")
        rows=[]
        rows += [dict(row_to_dict(r),kind="income") for r in self.conn.execute(f"SELECT currency,COALESCE(SUM(tax_base),0) AS net,COALESCE(SUM(vat_total),0) AS vat,COALESCE(SUM(gross_total),0) AS gross FROM invoices WHERE status_code NOT IN ('draft','pending_approval','approved','cancelled') AND COALESCE(invoice_kind,'standard') <> 'advance'{inv_where} GROUP BY currency",inv_params).fetchall()]
        rows += [dict(row_to_dict(r),kind="expense") for r in self.conn.execute(f"SELECT currency,COALESCE(SUM(net_amount),0) AS net,COALESCE(SUM(vat_amount),0) AS vat,COALESCE(SUM(gross_amount),0) AS gross FROM project_documents WHERE document_type='input'{doc_where} GROUP BY currency",doc_params).fetchall()]
        # A bill created from a project input document is the payment-control
        # record for the same expense.  Counting it again would overstate the
        # company P&L, so only independent supplier bills are added here.
        rows += [dict(row_to_dict(r),kind="expense") for r in self.conn.execute(f"SELECT currency,COALESCE(SUM(net_amount),0) AS net,COALESCE(SUM(vat_amount),0) AS vat,COALESCE(SUM(gross_amount),0) AS gross FROM vendor_bills WHERE status <> 'cancelled' AND source_project_document_id IS NULL{bill_where} GROUP BY currency",bill_params).fetchall()]
        currencies: dict[str,dict[str,Decimal]]={}
        for row in rows:
            code=normalize_currency(row.get("currency"));bucket=currencies.setdefault(code,{"income_net":Decimal("0"),"income_vat":Decimal("0"),"expense_net":Decimal("0"),"expense_vat":Decimal("0")})
            prefix="income" if row["kind"]=="income" else "expense";bucket[f"{prefix}_net"]+=money_round(row.get("net"));bucket[f"{prefix}_vat"]+=money_round(row.get("vat"))
        for bucket in currencies.values(): bucket["profit_net"]=money_round(bucket["income_net"]-bucket["expense_net"]);bucket["vat_payable"]=money_round(bucket["income_vat"]-bucket["expense_vat"])
        return {"currencies":currencies,"open_bills":self.list_vendor_bills(include_paid=False),"generated_at":now_iso()}

    def cash_flow_forecast(self, *, days: int = 90, as_of: Any = None) -> dict[str, Any]:
        base=parse_date(as_of) or date.today(); horizon=base+timedelta(days=max(1,int(days)))
        buckets: dict[str,dict[str,Any]]={}
        def bucket(currency: Any) -> dict[str,Any]:
            code=normalize_currency(currency,fallback=self.get_company().get("default_currency") or DEFAULT_CURRENCY)
            return buckets.setdefault(code,{"opening_balance":Decimal("0"),"inflows":Decimal("0"),"outflows":Decimal("0"),"closing_balance":Decimal("0"),"events":[]})
        for account in self.list_cash_accounts(): bucket(account.get("currency"))["opening_balance"]+=money_round(account.get("opening_balance"))
        for invoice in self.list_invoices(open_only=True):
            day=parse_date(invoice.get("due_date")) or base
            if base <= day <= horizon:
                item=bucket(invoice.get("currency"));amount=money_round(invoice.get("balance_total"));item["inflows"]+=amount;item["events"].append({"date":day.isoformat(),"kind":"receivable","amount":amount,"label":invoice.get("customer_name") or invoice.get("invoice_number")})
        for bill in self.list_vendor_bills(include_paid=False):
            day=parse_date(bill.get("due_date")) or parse_date(bill.get("bill_date")) or base
            if base <= day <= horizon:
                item=bucket(bill.get("currency"));amount=money_round(bill.get("balance_amount"));item["outflows"]+=amount;item["events"].append({"date":day.isoformat(),"kind":"payable","amount":amount,"label":bill.get("vendor_name") or bill.get("bill_number")})
        for row in self.list_recurring_expenses(active_only=True):
            day=parse_date(row.get("next_run_date"))
            while day and day <= horizon:
                if day >= base:
                    item=bucket(row.get("currency"));amount=money_round(row.get("net_amount"))*(Decimal("1")+decimal_from(row.get("vat_rate")));item["outflows"]+=amount;item["events"].append({"date":day.isoformat(),"kind":"recurring","amount":amount,"label":row.get("name")})
                day=self._advance_months(day,int(row.get("interval_months") or 1))
        # Confirmed future-dated statement lines are actual cash movements.
        # Their linked invoice/bill is already closed, so include the bank line
        # once here instead of losing it from the forecast.
        confirmed = self.conn.execute(
            "SELECT transaction_date, amount, currency, direction, payer_name, reference FROM bank_transactions WHERE status='confirmed' AND transaction_date >= ? AND transaction_date <= ?",
            (base.isoformat(), horizon.isoformat()),
        ).fetchall()
        for row in confirmed:
            item = bucket(row["currency"])
            amount = money_round(row["amount"])
            if row["direction"] == "outflow":
                item["outflows"] += amount
                kind = "confirmed_outflow"
            else:
                item["inflows"] += amount
                kind = "confirmed_inflow"
            item["events"].append({"date": row["transaction_date"], "kind": kind, "amount": amount, "label": row["payer_name"] or row["reference"]})
        for item in buckets.values(): item["closing_balance"]=money_round(item["opening_balance"]+item["inflows"]-item["outflows"]);item["events"].sort(key=lambda x:x["date"])
        return {"as_of":base.isoformat(),"through":horizon.isoformat(),"days":max(1,int(days)),"currencies":buckets}

    def import_bank_transactions(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        source_file: str,
        source_hash: str,
    ) -> dict[str, int]:
        """Store imported inflows and outflows for review, without posting them."""
        self.assert_business_write_access()
        inserted = 0
        skipped = 0
        suggested = 0
        now = now_iso()
        default_currency = self.get_company().get("default_currency") or DEFAULT_CURRENCY
        ignored_non_eur = 0  # retained for compatibility with older UI messages
        for row in rows:
            amount = money_round(row.get("amount"))
            if amount <= 0:
                continue
            transaction_currency = normalize_currency(row.get("currency"), fallback=default_currency)
            direction = str(row.get("direction") or "inflow").strip().lower()
            if direction not in {"inflow", "outflow"}:
                direction = "inflow"
            source_row = int(row.get("source_row") or 0)
            fingerprint = hashlib.sha256(f"{source_hash}:{source_row}".encode("utf-8")).hexdigest()
            suggestion = self.suggest_bank_invoice(
                amount=amount, payer_name=str(row.get("payer_name") or ""), reference=str(row.get("reference") or ""),
            ) if direction == "inflow" else {}
            bill_suggestion = self.suggest_bank_vendor_bill(
                amount=amount, payee_name=str(row.get("payer_name") or ""), reference=str(row.get("reference") or ""), currency=transaction_currency,
            ) if direction == "outflow" else {}
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO bank_transactions (
                    source_fingerprint, source_file, source_row, transaction_date, amount, currency,
                    payer_name, payer_iban, reference, description, suggested_invoice_id, suggested_vendor_bill_id,
                    direction, match_score, match_reason, status, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fingerprint,
                    str(source_file),
                    source_row,
                    iso_from_date(row.get("transaction_date")) or "",
                    float(amount),
                    transaction_currency,
                    str(row.get("payer_name") or "").strip(),
                    str(row.get("payer_iban") or "").strip(),
                    str(row.get("reference") or "").strip(),
                    str(row.get("description") or "").strip(),
                    suggestion.get("invoice_id"),
                    bill_suggestion.get("bill_id"),
                    direction,
                    int((suggestion or bill_suggestion).get("score") or 0),
                    str((suggestion or bill_suggestion).get("reason") or ""),
                    "suggested" if suggestion or bill_suggestion else "new",
                    now,
                ),
            )
            if cursor.rowcount:
                inserted += 1
                suggested += int(bool(suggestion or bill_suggestion))
            else:
                skipped += 1
        self.conn.commit()
        if inserted:
            self._maybe_backup("bank_import")
        return {
            "inserted": inserted,
            "skipped": skipped,
            "suggested": suggested,
            "ignored_non_eur": ignored_non_eur,
        }

    def suggest_bank_vendor_bill(self, *, amount: Any, payee_name: str, reference: str, currency: str) -> dict[str, Any]:
        """Conservative outgoing-payment proposal; never confirms it automatically."""
        amount_dec = money_round(amount)
        reference_key, payee_key = bank_match_key(reference), bank_match_key(payee_name)
        candidates = self.list_vendor_bills(include_paid=False)
        best: dict[str, Any] = {}
        for bill in candidates:
            if str(bill.get("currency") or "").upper() != str(currency or "").upper():
                continue
            balance = money_round(bill.get("balance_amount"))
            if abs(balance - amount_dec) > Decimal("0.01"):
                continue
            score, reason = 65, "isti otvoreni iznos"
            if bank_match_key(bill.get("bill_number")) and bank_match_key(bill.get("bill_number")) in reference_key:
                score, reason = 95, "broj obaveze u referenci"
            elif payee_key and payee_key in bank_match_key(bill.get("vendor_name")):
                score, reason = 80, "dobavljač i iznos"
            if score > int(best.get("score") or 0):
                best = {"bill_id": int(bill["id"]), "score": score, "reason": reason}
        return best

    def list_bank_transactions(self, *, include_closed: bool = True) -> list[dict[str, Any]]:
        clauses = [] if include_closed else ["bt.status IN ('new', 'suggested')"]
        sql = """
            SELECT bt.*, si.invoice_number AS suggested_invoice_number,
                   si.customer_name AS suggested_customer_name, si.project_name AS suggested_project_name,
                   mi.invoice_number AS matched_invoice_number,
                   mi.customer_name AS matched_customer_name, mi.project_name AS matched_project_name,
                   svb.bill_number AS suggested_vendor_bill_number, sv.name AS suggested_vendor_name,
                   mvb.bill_number AS matched_vendor_bill_number, mv.name AS matched_vendor_name
            FROM bank_transactions bt
            LEFT JOIN invoices si ON si.id = bt.suggested_invoice_id
            LEFT JOIN invoices mi ON mi.id = bt.matched_invoice_id
            LEFT JOIN vendor_bills svb ON svb.id = bt.suggested_vendor_bill_id
            LEFT JOIN vendors sv ON sv.id = svb.vendor_id
            LEFT JOIN vendor_bills mvb ON mvb.id = bt.matched_vendor_bill_id
            LEFT JOIN vendors mv ON mv.id = mvb.vendor_id
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += """
            ORDER BY CASE bt.status WHEN 'new' THEN 0 WHEN 'suggested' THEN 1 WHEN 'ignored' THEN 2 ELSE 3 END,
                     bt.transaction_date DESC, bt.id DESC
        """
        return [row_to_dict(row) for row in self.conn.execute(sql).fetchall()]

    def get_bank_transaction(self, transaction_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM bank_transactions WHERE id = ?", (transaction_id,)).fetchone()
        return row_to_dict(row)

    def bank_transaction_summary(self) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
                SUM(CASE WHEN status IN ('new', 'suggested') THEN 1 ELSE 0 END) AS pending_count,
                COALESCE(SUM(CASE WHEN status IN ('new', 'suggested') THEN amount ELSE 0 END), 0) AS pending_amount,
                SUM(CASE WHEN status = 'suggested' THEN 1 ELSE 0 END) AS suggested_count,
                SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed_count
            FROM bank_transactions
            """
        ).fetchone()
        return row_to_dict(row)

    def confirm_bank_transaction(self, transaction_id: int, invoice_id: int) -> int:
        """Convert one reviewed incoming bank line into exactly one invoice payment."""
        transaction = self.get_bank_transaction(transaction_id)
        if not transaction:
            raise ValueError("Bankovna transakcija ne postoji.")
        if transaction.get("status") in {"confirmed", "ignored"}:
            raise ValueError("Ova bankovna transakcija je već obrađena.")
        if transaction.get("direction") == "outflow":
            raise ValueError("Odliv se povezuje sa obavezom dobavljača, ne sa fakturom kupca.")
        invoice = self.get_invoice(invoice_id)
        if not invoice or invoice.get("status_code") in {"draft", "cancelled"}:
            raise ValueError("Izaberite važeću izdatu fakturu.")
        amount = money_round(transaction.get("amount"))
        balance = money_round(invoice.get("balance_total"))
        if amount <= 0:
            raise ValueError("Možete povezati samo pozitivan priliv.")
        if amount - balance > Decimal("0.01"):
            raise ValueError(
                f"Uplata {format_currency(amount, transaction.get('currency') or invoice.get('currency'))} je veća od preostalog duga. "
                "Podelite je ili evidentirajte višak ručno."
            )
        transaction_currency = str(transaction.get("currency") or "").upper()
        invoice_currency = str(invoice.get("currency") or "").upper()
        if transaction_currency and invoice_currency and transaction_currency != invoice_currency:
            raise ValueError(f"Valuta izvoda ({transaction_currency}) se ne podudara sa valutom fakture ({invoice_currency}).")
        note_parts = ["Bankovni izvod"]
        if transaction.get("payer_name"):
            note_parts.append(str(transaction["payer_name"]))
        if transaction.get("reference"):
            note_parts.append(f"Ref: {transaction['reference']}")
        payment_id = self.add_payment(
            invoice_id,
            transaction.get("transaction_date"),
            amount,
            "Banka - izvod",
            " | ".join(note_parts),
        )
        self.conn.execute(
            """
            UPDATE bank_transactions
            SET status = 'confirmed', matched_invoice_id = ?, payment_id = ?, matched_at = ?
            WHERE id = ?
            """,
            (invoice_id, payment_id, now_iso(), transaction_id),
        )
        self.conn.commit()
        self._maybe_backup(f"bank_match_{transaction_id}")
        return payment_id

    def confirm_bank_outflow(self, transaction_id: int, vendor_bill_id: int, *, confirmed_by_name: str = "") -> None:
        transaction = self.get_bank_transaction(transaction_id)
        if not transaction or transaction.get("status") in {"confirmed", "ignored"}:
            raise ValueError("Bankovna transakcija ne postoji ili je već obrađena.")
        if transaction.get("direction") != "outflow":
            raise ValueError("Samo odliv se može povezati sa obavezom dobavljača.")
        bill = self.get_vendor_bill(vendor_bill_id)
        if not bill:
            raise ValueError("Izaberite postojeću obavezu dobavljača.")
        if str(bill.get("approval_status") or "approved") != "approved":
            raise ValueError("Obaveza dobavljača mora prvo biti odobrena pre povezivanja sa bankovnim odlivom.")
        if str(transaction.get("currency") or "").upper() != str(bill.get("currency") or "").upper():
            raise ValueError("Valuta odliva i obaveze mora biti ista.")
        amount = money_round(transaction.get("amount"))
        actor = str(confirmed_by_name or "").strip()
        self.record_vendor_bill_payment(
            vendor_bill_id,
            amount,
            transaction.get("transaction_date"),
            reference=str(transaction.get("reference") or ""),
            recorded_by_name=actor,
        )
        self.conn.execute("UPDATE bank_transactions SET status='confirmed', matched_vendor_bill_id=?, matched_at=? WHERE id=?", (vendor_bill_id, now_iso(), transaction_id))
        actor_note = f" Potvrdio/la: {actor}." if actor else ""
        self._record_financial_audit("bank_transaction", transaction_id, "outflow_confirmed", f"Odliv povezan sa obavezom {vendor_bill_id}." + actor_note)
        self.conn.commit(); self._maybe_backup(f"bank_outflow_{transaction_id}")

    def confirm_confident_bank_transactions(self, minimum_score: int = 90) -> dict[str, int]:
        """Confirm only proposals with invoice-number-level confidence after user approval."""
        rows = self.conn.execute(
            """
            SELECT id, suggested_invoice_id
            FROM bank_transactions
            WHERE status = 'suggested' AND direction = 'inflow' AND match_score >= ? AND suggested_invoice_id IS NOT NULL
            ORDER BY transaction_date, id
            """,
            (int(minimum_score),),
        ).fetchall()
        confirmed = 0
        skipped = 0
        for row in rows:
            try:
                self.confirm_bank_transaction(int(row["id"]), int(row["suggested_invoice_id"]))
                confirmed += 1
            except ValueError:
                skipped += 1
        return {"confirmed": confirmed, "skipped": skipped}

    def ignore_bank_transaction(self, transaction_id: int) -> None:
        transaction = self.get_bank_transaction(transaction_id)
        if not transaction or transaction.get("status") == "confirmed":
            raise ValueError("Samo nepotvrđenu bankovnu transakciju možete ignorisati.")
        self.assert_business_write_access()
        self.conn.execute("UPDATE bank_transactions SET status = 'ignored' WHERE id = ?", (transaction_id,))
        self.conn.commit()

    def delete_bank_transaction(self, transaction_id: int) -> None:
        """Delete one imported bank line and safely reverse its confirmed effect.

        Imported rows are source evidence, so a confirmed inflow first removes
        its generated invoice payment and a confirmed outflow first restores the
        supplier bill balance.  The audit log retains the fact that the bank row
        was removed.
        """
        self.assert_business_write_access()
        transaction = self.get_bank_transaction(transaction_id)
        if not transaction:
            raise ValueError("Bankovna transakcija ne postoji.")
        status = str(transaction.get("status") or "new")
        direction = str(transaction.get("direction") or "inflow")
        amount = money_round(transaction.get("amount"))
        self._backup_before_change(f"bank_transaction_deleted_{transaction_id}", replaces_post_backup=True)
        if status == "confirmed" and direction == "inflow":
            payment_id = int(transaction.get("payment_id") or 0)
            if not payment_id:
                raise ValueError("Ova potvrđena uplata nema vezanu evidenciju plaćanja. Otvorite fakturu i proverite istoriju pre brisanja.")
            self.delete_payment(payment_id)
        elif status == "confirmed" and direction == "outflow":
            bill_id = int(transaction.get("matched_vendor_bill_id") or 0)
            bill = self.get_vendor_bill(bill_id)
            if not bill:
                raise ValueError("Potvrđeni odliv nema vezanu obavezu dobavljača.")
            paid_before = money_round(bill.get("paid_amount"))
            if amount <= 0 or paid_before + Decimal("0.01") < amount:
                raise ValueError("Odliv nije moguće bezbedno poništiti jer se iznos obaveze ne podudara.")
            paid_after = money_round(paid_before - amount)
            gross = money_round(bill.get("gross_amount"))
            bill_status = "paid" if paid_after + Decimal("0.01") >= gross else ("partial" if paid_after > 0 else "open")
            self.conn.execute(
                "UPDATE vendor_bills SET paid_amount=?, status=?, updated_at=? WHERE id=?",
                (float(paid_after), bill_status, now_iso(), bill_id),
            )
            self._record_financial_audit("vendor_bill", bill_id, "bank_outflow_reversed", f"Vraćen odliv {format_currency(amount, bill.get('currency') or DEFAULT_CURRENCY)}.")
        self.conn.execute("DELETE FROM bank_transactions WHERE id = ?", (transaction_id,))
        self._record_financial_audit(
            "bank_transaction", transaction_id, "deleted",
            f"Obrisan {'odliv' if direction == 'outflow' else 'priliv'} {format_currency(amount, transaction.get('currency') or DEFAULT_CURRENCY)}.",
        )
        self.conn.commit()
        self._maybe_backup(f"bank_transaction_deleted_{transaction_id}")

    def dashboard_stats(self, *, period_from: Any = None, period_to: Any = None) -> dict[str, Any]:
        """Return one combined dashboard across every project for a selected period."""
        from_iso = iso_from_date(period_from)
        to_iso = iso_from_date(period_to)
        invoice_clauses = ["status_code NOT IN ('draft','pending_approval','approved','cancelled')"]
        invoice_params: list[Any] = []
        if from_iso:
            invoice_clauses.append("issue_date >= ?")
            invoice_params.append(from_iso)
        if to_iso:
            invoice_clauses.append("issue_date <= ?")
            invoice_params.append(to_iso)
        invoice_where = " AND ".join(invoice_clauses)
        income_invoice_where = f"{invoice_where} AND COALESCE(invoice_kind, 'standard') <> 'advance'"

        payment_clauses = ["i.status_code NOT IN ('draft','pending_approval','approved','cancelled')"]
        payment_params: list[Any] = []
        if from_iso:
            payment_clauses.append("p.payment_date >= ?")
            payment_params.append(from_iso)
        if to_iso:
            payment_clauses.append("p.payment_date <= ?")
            payment_params.append(to_iso)
        payment_where = " AND ".join(payment_clauses)

        cur = self.conn.cursor()
        issued = cur.execute(
            f"""
            SELECT
                COALESCE(SUM(gross_total), 0) AS total_issued,
                COALESCE(SUM(balance_total), 0) AS total_balance,
                COALESCE(SUM(vat_total), 0) AS total_vat,
                COALESCE(SUM(tax_base), 0) AS total_turnover,
                COUNT(*) AS invoice_count
            FROM invoices
            WHERE {income_invoice_where}
            """,
            invoice_params,
        ).fetchone()
        paid = cur.execute(
            f"""
            SELECT COALESCE(SUM(p.amount), 0) AS total_paid
            FROM payments p
            JOIN invoices i ON i.id = p.invoice_id
            WHERE {payment_where}
            """,
            payment_params,
        ).fetchone()
        overdue = cur.execute(
            f"""
            SELECT COALESCE(SUM(balance_total), 0) AS overdue_total, COUNT(*) AS overdue_count
            FROM invoices
            WHERE {invoice_where} AND balance_total > 0 AND due_date <> '' AND due_date < date('now')
            """
            ,
            invoice_params,
        ).fetchone()
        recent_payments = cur.execute(
            f"""
            SELECT p.id AS payment_id, p.payment_date, p.amount, p.method, i.id AS invoice_id, i.invoice_number, i.customer_name
            FROM payments p
            JOIN invoices i ON i.id = p.invoice_id
            WHERE {payment_where}
            ORDER BY p.payment_date DESC, p.id DESC
            LIMIT 8
            """,
            payment_params,
        ).fetchall()
        debtors = cur.execute(
            f"""
            SELECT
                customer_name,
                COUNT(*) AS invoice_count,
                MIN(NULLIF(due_date, '')) AS oldest_due_date,
                MIN(id) AS direct_invoice_id,
                COALESCE(SUM(balance_total), 0) AS balance
            FROM invoices
            WHERE {invoice_where} AND balance_total > 0
            GROUP BY CASE
                WHEN TRIM(COALESCE(customer_name, '')) = '' THEN 'invoice:' || id
                ELSE 'customer:' || customer_name
            END
            ORDER BY balance DESC, customer_name
            LIMIT 8
            """,
            invoice_params,
        ).fetchall()
        return {
            "month_issued": money_round(issued["total_issued"] if issued else 0),
            "month_paid": money_round(paid["total_paid"] if paid else 0),
            "month_balance": money_round(issued["total_balance"] if issued else 0),
            "month_turnover": money_round(issued["total_turnover"] if issued else 0),
            "month_vat": money_round(issued["total_vat"] if issued else 0),
            "invoice_count": int(issued["invoice_count"] if issued else 0),
            "overdue_total": money_round(overdue["overdue_total"] if overdue else 0),
            "overdue_count": int(overdue["overdue_count"] if overdue else 0),
            "recent_payments": [row_to_dict(r) for r in recent_payments],
            "debtors": [row_to_dict(r) for r in debtors],
        }

    def _maybe_backup(self, label: str = "auto") -> Optional[Path]:
        pending = getattr(self, "_pre_backup_replacements", set())
        if label in pending:
            pending.discard(label)
            return None
        if not self.db_path.exists():
            return None
        backup_dir().mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir() / f"{timestamp}_{safe_filename(label)}.db"
        # A plain file copy can omit committed WAL pages while SQLite is open.
        # Use SQLite's own online-backup API so every automatic snapshot is a
        # transactionally consistent database that can really be restored.
        self.conn.commit()
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(backup_path)
            self.conn.backup(target)
            target.commit()
        except sqlite3.Error:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            if target is not None:
                target.close()
        self._prune_backups()
        return backup_path

    def _backup_before_change(self, label: str, *, replaces_post_backup: bool = False) -> Optional[Path]:
        """Snapshot the database before a user-visible change, retaining the last safe state."""
        # Profile and access settings remain editable in read-only mode so the
        # owner can still recover access and update contact details. Every
        # business mutation that uses this backup path is subscription-gated.
        if label != "company":
            self.assert_business_write_access()
        if replaces_post_backup:
            if not hasattr(self, "_pre_backup_replacements"):
                self._pre_backup_replacements: set[str] = set()
            self._pre_backup_replacements.add(label)
        return self._maybe_backup(f"pre_{label}")

    def _prune_backups(self, keep: int = 30) -> None:
        files = sorted(backup_dir().glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[keep:]:
            try:
                path.unlink()
            except OSError:
                pass

    def backup_now(self) -> Optional[Path]:
        return self._maybe_backup("manual")

    @staticmethod
    def _verify_sqlite_database(path: str | Path) -> dict[str, Any]:
        """Read-only integrity and schema check used for backups and restores."""
        candidate = Path(path)
        if not candidate.is_file():
            return {"ok": False, "detail": "Fajl baze ne postoji."}
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            integrity_text = str(integrity[0] if integrity else "").strip()
            if integrity_text.lower() != "ok":
                return {"ok": False, "detail": f"SQLite integrity_check: {integrity_text or 'nije uspeo'}"}
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            missing = {"company_settings", "projects", "invoices"} - tables
            if missing:
                return {"ok": False, "detail": f"Nedostaju obavezne tabele: {', '.join(sorted(missing))}."}
            return {"ok": True, "detail": "SQLite integritet i OpsNest struktura su potvrđeni."}
        except sqlite3.Error as exc:
            return {"ok": False, "detail": f"Baza ne može da se pročita: {exc}"}
        finally:
            if connection is not None:
                connection.close()

    def create_and_verify_backup(self) -> dict[str, Any]:
        """Create a consistent snapshot, then test it before monthly close."""
        created = self.backup_now()
        if created is None:
            raise ValueError("OpsNest nije mogao da napravi lokalni backup.")
        report = self._verify_sqlite_database(created)
        report.update(
            {
                "path": str(created),
                "name": created.name,
                "created_at": datetime.fromtimestamp(created.stat().st_mtime).strftime("%d.%m.%Y %H:%M"),
                "size": created.stat().st_size,
            }
        )
        if not report["ok"]:
            raise ValueError(f"Backup je napravljen, ali provera nije uspela: {report['detail']}")
        return report

    def backup_health_report(self) -> dict[str, Any]:
        """Return an honest, non-mutating health report for the latest backup."""
        current = self._verify_sqlite_database(self.db_path)
        backups = self.list_backups()
        latest = backups[0] if backups else None
        backup_check = self._verify_sqlite_database(latest["path"]) if latest else {
            "ok": False,
            "detail": "Nema dostupnog OpsNest backupa.",
        }
        age_minutes = None
        if latest:
            age_minutes = max(0, int((datetime.now() - datetime.fromtimestamp(Path(latest["path"]).stat().st_mtime)).total_seconds() // 60))
        return {
            "ok": bool(current["ok"] and backup_check["ok"]),
            "current": current,
            "latest_backup": latest,
            "backup": backup_check,
            "backup_age_minutes": age_minutes,
            "cloud_sync": self.cloud_sync_state(),
        }

    def list_backups(self) -> list[dict[str, Any]]:
        """List local SQLite snapshots newest first for the restore screen."""
        result: list[dict[str, Any]] = []
        for path in sorted(backup_dir().glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                stat = path.stat()
            except OSError:
                continue
            result.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M"),
                    "size": stat.st_size,
                }
            )
        return result

    def restore_backup(self, backup_file: str | Path) -> Path:
        """Restore one local backup after first snapshotting the current database."""
        candidate = Path(backup_file).expanduser().resolve()
        expected_dir = backup_dir().resolve()
        if candidate.parent != expected_dir or candidate.suffix.lower() != ".db" or not candidate.is_file():
            raise ValueError("Možete vratiti samo backup iz OpsNest Backup foldera.")
        report = self._verify_sqlite_database(candidate)
        if not report["ok"]:
            raise ValueError(f"Izabrani fajl nije važeći OpsNest backup: {report['detail']}")
        source: sqlite3.Connection | None = None
        self._maybe_backup("pre_restore")
        try:
            self.conn.commit()
            source = sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True)
            source.backup(self.conn)
            self.conn.commit()
        finally:
            if source is not None:
                source.close()
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.initialize_schema()
        self.bootstrap_defaults()
        return candidate

    def invoice_export_payload(self, invoice_id: int) -> dict[str, Any]:
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            return {}
        payload = dict(invoice)
        payload["items"] = invoice.get("items", [])
        payload["payments"] = invoice.get("payments", [])
        payload["company"] = self.get_company()
        payload["amount_words"] = number_to_words_bg(
            invoice.get("balance_total", invoice.get("gross_total", 0)),
            invoice.get("currency", DEFAULT_CURRENCY),
        )
        return payload

    def preview_invoice_export_payload(self, payload: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a complete export payload without persisting or numbering an invoice."""
        invoice_id = int(payload["id"]) if payload.get("id") else None
        existing = self.get_invoice(invoice_id) if invoice_id else {}
        snapshot = self._prepare_invoice_snapshot(
            payload,
            existing_currency=str(existing.get("currency") or ""),
            existing_document_language=str(existing.get("document_language") or ""),
        )
        if snapshot["invoice_kind"] == "advance" and str(existing.get("status_code") or "") not in {"issued", "partial", "paid", "due"}:
            items = [self.project_advance_invoice_item(int(snapshot.get("project_id") or 0))]
        paid_total = self._invoice_paid_total(invoice_id)
        totals = calculate_invoice_totals(
            items,
            vat_rate=snapshot["vat_rate"],
            discount_total=snapshot["discount_total"],
            retention_percent=snapshot["retention_percent"],
            advance_amount=snapshot["advance_amount"],
            paid_total=paid_total,
            currency=snapshot["currency"],
        )
        if existing:
            sequence = int(existing.get("invoice_seq") or 0)
            invoice_number = existing.get("invoice_number") or invoice_number_from_seq(sequence)
        else:
            row = self.conn.execute("SELECT next_invoice_seq FROM company_settings WHERE id = 1").fetchone()
            sequence = int(row["next_invoice_seq"] if row else 1)
            project_id = int(snapshot.get("project_id") or 0)
            invoice_number = self.preview_project_invoice_number(project_id) if project_id else invoice_number_from_seq(sequence)
        prepared_items: list[dict[str, Any]] = []
        for item in items:
            line = calculate_line_item(
                item.get("quantity"),
                item.get("unit_price"),
                item.get("discount_percent", 0),
                snapshot["vat_rate"],
            )
            prepared_items.append(
                {
                    **item,
                    "quantity": float(line["quantity"]),
                    "unit_price": float(line["unit_price"]),
                    "discount_percent": float(line["discount_percent"]),
                    "net_amount": float(line["net_amount"]),
                    "vat_amount": float(line["vat_amount"]),
                    "gross_amount": float(line["gross_amount"]),
                }
            )
        requested_template_id = payload.get("invoice_template_id")
        if requested_template_id in (None, ""):
            requested_template_id = existing.get("invoice_template_id") if existing else None
        if requested_template_id in (None, ""):
            requested_template_id = self.default_invoice_template_id()
        preview = {
            **snapshot,
            **totals,
            "id": invoice_id,
            "invoice_seq": sequence,
            "invoice_number": invoice_number,
            "status_code": payload.get("status_code") or existing.get("status_code") or "draft",
            "invoice_template_id": int(requested_template_id),
            "prepared_by_role": payload.get("prepared_by_role") or existing.get("prepared_by_role") or "",
            "prepared_by_name": payload.get("prepared_by_name") or existing.get("prepared_by_name") or "",
            "approved_by_name": payload.get("approved_by_name") or existing.get("approved_by_name") or "",
            "approved_at": existing.get("approved_at") or "",
            "items": prepared_items,
            "payments": existing.get("payments", []),
            "company": self.get_company(),
        }
        preview["amount_words"] = number_to_words_bg(preview["balance_total"], preview["currency"])
        return preview


def ensure_template_assets() -> dict[str, Optional[Path]]:
    ensure_app_folders()
    logo = ensure_logo_source()
    return {"template": TEMPLATE_XLSX if TEMPLATE_XLSX.exists() else None, "logo": logo}
