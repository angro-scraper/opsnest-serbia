"""Versioned, conservative Serbia country-pack rules.

This module is operational guidance, not tax or legal advice.  A licensed
accountant must confirm each filing and legal interpretation before submission.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


SERBIA_RULES_VERSION = "RS-2026.08"
KD2010_SOURCE_URL = "https://www.stat.gov.rs/sr-cyrl/istrazivanja/klasifikacije/"
SEF_SOURCE_URL = "https://www.efaktura.gov.rs/"
KD2010_CATALOG_VERSION = "KD2010-RZS-2026.08"

_CYRILLIC_TO_LATIN = str.maketrans({
    "А": "A", "а": "a", "Б": "B", "б": "b", "В": "V", "в": "v", "Г": "G", "г": "g",
    "Д": "D", "д": "d", "Ђ": "Đ", "ђ": "đ", "Е": "E", "е": "e", "Ж": "Ž", "ж": "ž",
    "З": "Z", "з": "z", "И": "I", "и": "i", "Ј": "J", "ј": "j", "К": "K", "к": "k",
    "Л": "L", "л": "l", "Љ": "Lj", "љ": "lj", "М": "M", "м": "m", "Н": "N", "н": "n",
    "Њ": "Nj", "њ": "nj", "О": "O", "о": "o", "П": "P", "п": "p", "Р": "R", "р": "r",
    "С": "S", "с": "s", "Т": "T", "т": "t", "Ћ": "Ć", "ћ": "ć", "У": "U", "у": "u",
    "Ф": "F", "ф": "f", "Х": "H", "х": "h", "Ц": "C", "ц": "c", "Ч": "Č", "ч": "č",
    "Џ": "Dž", "џ": "dž", "Ш": "Š", "ш": "š",
})


def _serbian_latin(value: str) -> str:
    return str(value or "").translate(_CYRILLIC_TO_LATIN)


@dataclass(frozen=True)
class SerbiaActivityCode:
    """One official KD 2010 row bundled for offline company setup."""

    code: str
    title: str


def _asset_path(name: str) -> Path:
    """Resolve a bundled asset both from source and a PyInstaller build."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "assets" / name


@lru_cache(maxsize=1)
def kd2010_catalog() -> tuple[SerbiaActivityCode, ...]:
    """Return the official KD 2010 hierarchy without an internet dependency."""
    path = _asset_path("kd2010.json")
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("KD 2010 šifarnik nije dostupan u instalaciji.") from exc
    return tuple(
        SerbiaActivityCode(str(row.get("code") or "").strip(), _serbian_latin(str(row.get("title") or "").strip()))
        for row in rows
        if str(row.get("code") or "").strip() and str(row.get("title") or "").strip()
    )


@lru_cache(maxsize=1)
def kd2010_code_index() -> dict[str, SerbiaActivityCode]:
    return {item.code: item for item in kd2010_catalog()}


def normalize_kd2010_code(value: str) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())[:5]


def kd2010_activity(code: str) -> SerbiaActivityCode | None:
    """Find a specific KD 2010 code.  Hierarchy rows are valid too."""
    return kd2010_code_index().get(normalize_kd2010_code(code))


def is_kd2010_activity_code(code: str) -> bool:
    """A registered primary activity is the four-digit KD 2010 class."""
    normalized = normalize_kd2010_code(code)
    return len(normalized) == 4 and normalized in kd2010_code_index()


def search_kd2010(query: str, *, limit: int = 250) -> list[SerbiaActivityCode]:
    """Search code or Serbian title; retaining hierarchy helps users navigate."""
    normalized = re.sub(r"\s+", " ", str(query or "").strip().casefold())
    if not normalized:
        return list(kd2010_catalog()[:limit])
    tokens = tuple(normalized.split(" "))
    matches = [
        item
        for item in kd2010_catalog()
        if normalized in item.code.casefold()
        or all(token in item.title.casefold() for token in tokens)
    ]
    return matches[:limit]


@dataclass(frozen=True)
class SerbiaActivityProfile:
    code_prefix: str
    profile: str
    title: str
    controls: tuple[str, ...]


_PROFILES = (
    SerbiaActivityProfile("01", "agriculture", "Poljoprivreda", ("Proveriti poseban status gazdinstva i podsticaje.",)),
    SerbiaActivityProfile("41", "construction", "Građevinarstvo", ("Ugovor, situacije, avansi i zapisnici po projektu.", "Proveriti PDV tretman građevinskih radova.")),
    SerbiaActivityProfile("42", "construction", "Građevinski radovi niskogradnje", ("Ugovor, situacije, avansi i zapisnici po projektu.",)),
    SerbiaActivityProfile("43", "construction", "Specijalizovani građevinski radovi", ("Ugovor, situacije, avansi i zapisnici po projektu.",)),
    SerbiaActivityProfile("45", "retail_trade", "Trgovina motornim vozilima", ("Kontrola robe, dokumentacije i fiskalizacije gde je primenljivo.",)),
    SerbiaActivityProfile("46", "retail_trade", "Trgovina na veliko", ("Kontrola ulaznih računa, zaliha i rokova dobavljača.",)),
    SerbiaActivityProfile("47", "retail_trade", "Trgovina na malo", ("Proveriti obaveze fiskalizacije i evidencije prometa.",)),
    SerbiaActivityProfile("49", "transport", "Kopneni saobraćaj", ("Pratiti putne naloge, gorivo i dokumentaciju transporta.",)),
    SerbiaActivityProfile("55", "hospitality", "Smeštaj", ("Proveriti fiskalizaciju i turističke lokalne obaveze.",)),
    SerbiaActivityProfile("56", "hospitality", "Usluge ishrane i pića", ("Proveriti fiskalizaciju i evidenciju prometa.",)),
    SerbiaActivityProfile("58", "digital_creative", "Izdavaštvo", ("Kontrola autorskih ugovora i prava.",)),
    SerbiaActivityProfile("62", "digital_creative", "Računarsko programiranje", ("Ugovori, usluge i devizni promet po klijentu.",)),
    SerbiaActivityProfile("69", "professional_services", "Pravne i računovodstvene delatnosti", ("Razdvojiti klijentsku dokumentaciju i obavezno odobravanje.",)),
    SerbiaActivityProfile("70", "professional_services", "Upravljanje i savetovanje", ("Ugovori o uslugama i rokovi naplate.",)),
    SerbiaActivityProfile("85", "professional_services", "Obrazovanje", ("Kontrola ugovora, kurseva i oslobođenja samo uz proveru knjigovođe.",)),
    SerbiaActivityProfile("86", "professional_services", "Zdravstvena zaštita", ("Dodatna zaštita dokumentacije i provera posebnih propisa.",)),
)


def serbia_activity_profile(activity_code: str) -> SerbiaActivityProfile | None:
    code = normalize_kd2010_code(activity_code)
    return next((item for item in _PROFILES if code.startswith(item.code_prefix)), None)


def serbia_company_checklist(*, activity_code: str, legal_form: str, tax_mode: str, vat_registered: bool) -> list[str]:
    profile = serbia_activity_profile(activity_code)
    checks = ["Potvrditi KD 2010 šifru delatnosti prema APR/RZS šifarniku.", "Čuvati računovodstvenu dokumentaciju i audit trag promena."]
    if profile:
        checks.extend(profile.controls)
    if legal_form == "entrepreneur":
        checks.append("Potvrditi status preduzetnika i način oporezivanja sa knjigovođom.")
    if tax_mode == "lump_sum":
        checks.append("Paušalni režim je informativan dok ga ne potvrde Poreska uprava i knjigovođa.")
    if vat_registered:
        checks.append("Voditi PDV evidencije i pripremiti SEF/PDV kontrolu pre slanja.")
    return checks
