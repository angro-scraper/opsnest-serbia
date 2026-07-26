from __future__ import annotations

import re
import shutil
import subprocess
import os
import sys
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import fitz


class PdfInvoiceReadError(ValueError):
    """Raised when an incoming PDF cannot provide usable invoice data."""


_AMOUNT_RE = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:[.\s']\d{3})+|\d+)(?:[,.]\d{1,2})?(?![\d.,])")
_DATE_RE = re.compile(r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{4})\b")


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _number(value: str) -> float | None:
    raw = value.strip().replace(" ", "").replace("'", "")
    if not raw:
        return None
    last_comma = raw.rfind(",")
    last_dot = raw.rfind(".")
    if last_comma >= 0 and last_dot >= 0:
        decimal_mark = "," if last_comma > last_dot else "."
        thousands_mark = "." if decimal_mark == "," else ","
        raw = raw.replace(thousands_mark, "").replace(decimal_mark, ".")
    elif last_comma >= 0:
        digits_after = len(raw) - last_comma - 1
        raw = raw.replace(",", "") if digits_after == 3 and raw.count(",") == 1 else raw.replace(".", "").replace(",", ".")
    elif last_dot >= 0:
        digits_after = len(raw) - last_dot - 1
        raw = raw.replace(".", "") if digits_after == 3 and raw.count(".") == 1 else raw
    try:
        return float(raw)
    except ValueError:
        return None


def _amounts_from_line(line: str) -> list[float]:
    values: list[float] = []
    for match in _AMOUNT_RE.findall(line):
        value = _number(match)
        if value is not None:
            values.append(value)
    return values


def _label_amount(lines: list[str], labels: tuple[str, ...]) -> float | None:
    folded_labels = tuple(_fold(label) for label in labels)
    matches: list[float] = []
    for index, line in enumerate(lines):
        line_folded = _fold(line)
        if not any(label in line_folded for label in folded_labels):
            continue
        values = _amounts_from_line(line)
        if not values and index + 1 < len(lines):
            values = _amounts_from_line(lines[index + 1])
        if len(values) == 1 and "%" in line and values[0] <= 100:
            # A line such as "PDV stopa 20%" names a rate, not a VAT amount.
            continue
        if values:
            # A VAT line often contains both its rate and value; the last number is the amount.
            matches.append(values[-1])
    return matches[-1] if matches else None


def _priority_label_amount(lines: list[str], label_groups: tuple[tuple[str, ...], ...]) -> float | None:
    """Prefer specific payment labels before generic totals on a crowded invoice."""
    for labels in label_groups:
        value = _label_amount(lines, labels)
        if value is not None:
            return value
    return None


def _label_text(lines: list[str], labels: tuple[str, ...]) -> str:
    folded_labels = tuple(_fold(label) for label in labels)
    for index, line in enumerate(lines):
        line_folded = _fold(line)
        matching_label = next((label for label in folded_labels if label in line_folded), "")
        if not matching_label:
            continue
        label_start = line_folded.find(matching_label)
        inline_value = line[label_start + len(matching_label) :].strip(" :\t-#№")
        if inline_value and not _DATE_RE.search(inline_value):
            return inline_value
        if ":" in line:
            value = line.split(":", 1)[1].strip(" -#№\t")
            if value:
                return value
        if index + 1 < len(lines):
            candidate = lines[index + 1].strip()
            if candidate and not _DATE_RE.search(candidate):
                return candidate
    return ""


def _invoice_number(text: str, lines: list[str]) -> str:
    number_labels = (
        "broj racuna", "broj fakture", "faktura broj", "invoice number", "invoice no", "invoice #",
        "invoice nr", "rechnung nr", "rechnungsnummer", "rechnungs-nr", "фактура №", "фактура бр",
        "номер фактура", "номер на фактура",
    )
    for line in lines:
        folded_line = _fold(line)
        label = next((item for item in number_labels if _fold(item) in folded_line), "")
        if not label:
            continue
        label_start = folded_line.find(_fold(label))
        candidate = line[label_start + len(label) :].strip(" :#№-\t")
        match = re.search(r"([A-Z0-9][A-Z0-9./_-]{1,})", candidate, re.IGNORECASE)
        if match:
            return match.group(1)
    patterns = (
        r"(?:broj\s*(?:racuna|fakture)?|racun\s*(?:broj|br\.?|no\.?)?|faktura\s*(?:broj|br\.?|no\.?)?|invoice\s*(?:no\.?|number|#))\s*[:#№-]?\s*([A-Z0-9][A-Z0-9./_-]{1,})",
        r"(?:invoice\s*nr\.?|rechnung(?:snummer|\s*nr\.?)?)\s*[:#№-]?\s*([A-Z0-9][A-Z0-9./_-]{1,})",
        r"(?:фактура|сметка)\s*(?:№|no\.?|бр\.?)\s*[:#№-]?\s*([A-Z0-9][A-Z0-9./_-]{1,})",
        r"(?:номер\s*(?:на\s*)?фактура)\s*[:#№-]?\s*([A-Z0-9][A-Z0-9./_-]{1,})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    value = _label_text(lines, ("broj racuna", "broj fakture", "invoice number", "invoice no"))
    return value.split()[0] if value else ""


def _invoice_date(text: str, lines: list[str]) -> str:
    labels = (
        "datum racuna", "datum fakture", "datum izdavanja", "invoice date", "date of issue",
        "rechnungsdatum", "ausstellungsdatum", "datum der rechnung", "дата на фактура", "дата на издаване",
    )
    folded_labels = tuple(_fold(label) for label in labels)
    for index, line in enumerate(lines):
        if not any(label in _fold(line) for label in folded_labels):
            continue
        match = _DATE_RE.search(line)
        if not match and index + 1 < len(lines):
            match = _DATE_RE.search(lines[index + 1])
        if match:
            return match.group(1).replace("/", ".").replace("-", ".")
    fallback = _DATE_RE.search(text)
    return fallback.group(1).replace("/", ".").replace("-", ".") if fallback else ""


def _vat_rate(text: str, lines: list[str], net_amount: float | None, vat_amount: float | None) -> float | None:
    labels = ("pdv", "vat", "ddv", "ддс", "mwst", "ust", "mehrwertsteuer", "umsatzsteuer")
    for line in lines:
        if not any(label in _fold(line) for label in labels):
            continue
        match = re.search(r"(\d{1,2}(?:[,.]\d+)?)\s*%", line)
        if match:
            value = _number(match.group(1))
            if value is not None:
                return value
    if net_amount and vat_amount is not None:
        return round(vat_amount / net_amount * 100, 2)
    return None


def _currency(text: str) -> str:
    upper = text.upper()
    if "BGN" in upper or "ЛВ" in upper:
        return "BGN"
    if "RSD" in upper or "DIN" in upper:
        return "RSD"
    if "EUR" in upper or "€" in text:
        return "EUR"
    return ""


def _fallback_partner(lines: list[str]) -> str:
    """Pick a likely supplier heading only when the PDF did not label it explicitly."""
    ignored = (
        "faktura", "invoice", "racun", "rechnung", "datum", "date", "adresa", "address", "telefon",
        "email", "iban", "swift", "pdv", "vat", "ddv", "ukupno", "total", "netto", "brutto", "placanje",
    )
    candidates: list[tuple[int, str]] = []
    for index, line in enumerate(lines[:20]):
        folded = _fold(line)
        letters = len(re.findall(r"[a-zа-яа-яўё]", folded))
        if letters < 4 or len(line) > 90 or any(word in folded for word in ignored):
            continue
        if _DATE_RE.search(line) or len(_amounts_from_line(line)) > 1:
            continue
        score = 40 - index
        if re.search(r"\b(ltd|eood|ood|doo|ad|gmbh|ag|llc|inc)\b", folded):
            score += 25
        if line.isupper():
            score += 8
        candidates.append((score, line.strip(" -#")))
    return max(candidates, default=(0, ""))[1]


def _partner_key(value: str) -> str:
    ignored = {"ltd", "eood", "ood", "doo", "ad", "gmbh", "ag", "llc", "inc", "company", "firma"}
    return " ".join(token for token in re.findall(r"[a-z0-9]+", _fold(value)) if token not in ignored)


def match_known_partner(extracted_partner: str, known_names: list[str]) -> str:
    """Return a saved company name only for a confident, non-destructive name match."""
    key = _partner_key(extracted_partner)
    if len(key) < 3:
        return ""
    best_name = ""
    best_score = 0.0
    for name in known_names:
        candidate = str(name or "").strip()
        candidate_key = _partner_key(candidate)
        if len(candidate_key) < 3:
            continue
        if key == candidate_key or key in candidate_key or candidate_key in key:
            return candidate
        score = SequenceMatcher(None, key, candidate_key).ratio()
        if score > best_score:
            best_name = candidate
            best_score = score
    return best_name if best_score >= 0.82 else ""


def parse_invoice_text(
    text: str,
    *,
    source_name: str = "racun.pdf",
    extraction_method: str = "tekst",
    document_type: str = "input",
) -> dict[str, Any]:
    """Build a conservative draft from invoice text; callers always let the user review it."""
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    normalized_text = "\n".join(lines)
    net_amount = _priority_label_amount(
        lines,
        (
            (
                "ukupno bez pdv", "ukupno bez ddv", "iznos bez pdv", "osnovica za pdv", "poreska osnovica",
                "porezna osnovica", "subtotal", "net amount", "taxable amount", "danacna osnova",
                "oblagаema stoynost", "облагаема стойност", "стойност без ддс", "общо без ддс",
            ),
            ("neto iznos", "netto betrag", "nettobetrag", "summe ohne mwst", "betrag ohne mwst", "netto"),
        ),
    )
    vat_amount = _priority_label_amount(
        lines,
        (
            ("iznos pdv", "pdv iznos", "pdv ukupno", "ukupno pdv", "vat amount", "vat total", "ddv suma", "ддс сума", "ддс общо"),
            ("mwst-betrag", "ust-betrag", "mehrwertsteuer", "umsatzsteuer", "mwst", "ust"),
        ),
    )
    gross_amount = _priority_label_amount(
        lines,
        (
            (
                "ukupno za placanje", "iznos za placanje", "svega za uplatu", "total due", "amount due",
                "total payable", "grand total", "zahlbetrag", "endbetrag", "rechnungsbetrag", "gesamtbetrag",
                "общо за плащане", "сума за плащане",
            ),
            ("ukupno sa pdv", "ukupno s pdv", "total incl", "total amount", "brutto", "общо с ддс"),
            ("ukupno", "gesamt", "total"),
        ),
    )
    if net_amount is None and gross_amount is not None and vat_amount is not None:
        net_amount = round(gross_amount - vat_amount, 2)
    if vat_amount is None and net_amount is not None and gross_amount is not None:
        vat_amount = round(gross_amount - net_amount, 2)
    vat_rate = _vat_rate(normalized_text, lines, net_amount, vat_amount)
    if gross_amount is None and net_amount is not None and vat_rate is not None:
        gross_amount = round(net_amount * (1 + vat_rate / 100), 2)

    is_output = document_type == "output"
    partner_labels = (
        "kupac", "narucilac", "klijent", "customer", "client", "buyer", "bill to", "recipient",
        "rechnungsempfanger", "rechnungsempfänger", "kunde", "auftraggeber", "клиент", "купувач",
        "получател", "възложител",
    ) if is_output else (
        "dobavljac", "prodavac", "izdavalac", "supplier", "seller", "vendor", "lieferant",
        "rechnungssteller", "verkaufer", "verkaeufer", "доставчик", "продавач", "издател",
    )
    partner = _label_text(lines, partner_labels)
    warnings: list[str] = []
    partner_label = "kupac" if is_output else "dobavljač"
    if not partner:
        partner = _fallback_partner(lines)
        if partner:
            warnings.append(f"{partner_label} je procenjen iz zaglavlja PDF-a")
        else:
            warnings.append(f"{partner_label} nije pronađen")
    if not net_amount and gross_amount is None:
        warnings.append("iznosi nisu pronađeni")
    if not _invoice_number(normalized_text, lines):
        warnings.append("broj računa nije pronađen")

    return {
        "document_no": _invoice_number(normalized_text, lines),
        "document_date": _invoice_date(normalized_text, lines),
        "partner_name": partner,
        "description": f"Uvezeno iz PDF-a: {Path(source_name).stem}",
        "net_amount": net_amount,
        "vat_amount": vat_amount,
        "vat_rate_percent": vat_rate,
        "gross_amount": gross_amount,
        "currency": _currency(normalized_text),
        "extraction_method": extraction_method,
        "warnings": warnings,
        "source_name": Path(source_name).name,
    }


def _tesseract_executable() -> str | None:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates = [
        root / "assets" / "ocr_runtime" / "tesseract.exe",
        shutil.which("tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _bundled_tessdata_dir() -> Path | None:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    directory = root / "assets" / "tessdata"
    return directory if directory.is_dir() else None


def _tesseract_environment() -> dict[str, str]:
    environment = os.environ.copy()
    bundled_data = _bundled_tessdata_dir()
    if bundled_data:
        environment["TESSDATA_PREFIX"] = str(bundled_data)
    return environment


def _tesseract_languages(executable: str, environment: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            [executable, "--list-langs"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
            env=environment,
        )
    except OSError:
        return ""
    available = {line.strip() for line in result.stdout.splitlines() if line.strip() and not line.startswith("List of")}
    selected = [language for language in ("eng", "deu", "bul", "srp", "rus") if language in available]
    return "+".join(selected)


def _ocr_pdf(document: fitz.Document) -> str:
    executable = _tesseract_executable()
    if not executable:
        raise PdfInvoiceReadError(
            "PDF nema čitljiv tekst, a Tesseract OCR nije pronađen. "
            "Uvezite originalni PDF ili instalirajte Tesseract OCR za skenirane račune."
        )
    environment = _tesseract_environment()
    languages = _tesseract_languages(executable, environment)
    with tempfile.TemporaryDirectory(prefix="opsnest_ocr_") as temp_dir:
        temp_root = Path(temp_dir)
        pages: list[str] = []
        for index in range(min(len(document), 4)):
            page = document.load_page(index)
            image_path = temp_root / f"page_{index}.png"
            page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False).save(str(image_path))
            candidates: list[str] = []
            for psm in ("3", "6"):
                command = [executable, str(image_path), "stdout"]
                if languages:
                    command.extend(["-l", languages])
                command.extend(["--oem", "1", "--dpi", "300", "--psm", psm])
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=75,
                    env=environment,
                )
                if result.returncode == 0 and result.stdout.strip():
                    candidates.append(result.stdout)
            if candidates:
                markers = ("faktura", "invoice", "rechnung", "pdv", "vat", "ddv", "ukupno", "total", "dobavljac", "supplier")
                def quality(value: str) -> int:
                    folded = _fold(value)
                    return (
                        len(re.findall(r"[a-zа-я]", folded))
                        + len(_AMOUNT_RE.findall(value)) * 10
                        + sum(marker in folded for marker in markers) * 40
                    )
                pages.append(max(candidates, key=quality))
        return "\n".join(pages).strip()


def _native_pdf_text(document: fitz.Document) -> str:
    """Use positioned text blocks so digital invoices retain their visual reading order."""
    blocks: list[str] = []
    for page in document:
        for block in page.get_text("blocks", sort=True):
            value = str(block[4]).strip()
            if value:
                blocks.append(value)
    return "\n".join(blocks)


def extract_invoice_fields_from_pdf(pdf_path: str | Path, *, document_type: str = "input") -> dict[str, Any]:
    """Extract PDF text first, then use optional local Tesseract OCR for scans."""
    path = Path(pdf_path)
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise PdfInvoiceReadError("Izaberite postojeći PDF račun.")
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise PdfInvoiceReadError(f"PDF nije moguće otvoriti: {exc}") from exc
    try:
        text = _native_pdf_text(document).strip()
        method = "tekst iz PDF-a"
        if len(re.sub(r"\s+", "", text)) < 30:
            text = _ocr_pdf(document)
            method = "OCR skeniranog PDF-a"
    finally:
        document.close()
    if len(re.sub(r"\s+", "", text)) < 10:
        raise PdfInvoiceReadError("Na PDF računu nije pronađen čitljiv tekst.")
    return parse_invoice_text(
        text,
        source_name=path.name,
        extraction_method=method,
        document_type="output" if document_type == "output" else "input",
    )
