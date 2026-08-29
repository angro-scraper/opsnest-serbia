"""Versioned, conservative Serbia country-pack rules.

This module is operational guidance, not tax or legal advice.  A licensed
accountant must confirm each filing and legal interpretation before submission.
"""
from __future__ import annotations

from dataclasses import dataclass


SERBIA_RULES_VERSION = "RS-2026.08"
KD2010_SOURCE_URL = "https://www.stat.gov.rs/sr-cyrl/istrazivanja/klasifikacije/"
SEF_SOURCE_URL = "https://www.efaktura.gov.rs/"


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
    code = "".join(char for char in str(activity_code or "") if char.isdigit())
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
