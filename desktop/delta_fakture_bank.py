from __future__ import annotations

import csv
import hashlib
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _fold(value: Any) -> str:
    return re.sub(r"[\W_]+", " ", str(value or "").lower(), flags=re.UNICODE).strip()


_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "date": (
        "datum", "date", "booking date", "value date", "transaction date", "data", "дата",
        "buchungsdatum", "wertstellung",
    ),
    "amount": (
        "iznos", "amount", "transaction amount", "iznos transakcije", "suma", "sum", "сума",
        "стойност", "betrag",
    ),
    "credit": (
        "priliv", "credit", "incoming", "inflow", "kredit", "credited", "uplaceno", "кредит",
        "постъпление", "einzahlung", "gutschrift",
    ),
    "debit": (
        "odliv", "debit", "outgoing", "outflow", "duguje", "zaduzenje", "дебит", "разход",
        "auszahlung", "lastschrift",
    ),
    "currency": ("valuta", "currency", "ccy", "валута", "wahrung", "währung"),
    "payer": (
        "platioc", "uplatilac", "nalogodavac", "payer", "sender", "counterparty", "partner",
        "sender name", "ime platioca", "ime uplatilac", "наредител", "платец", "контрагент",
        "auftraggeber", "zahler",
    ),
    "payer_iban": (
        "iban platioca", "iban uplatilac", "payer iban", "sender iban", "account number", "racun platioca",
        "iban на наредител", "iban des auftraggebers",
    ),
    "reference": (
        "poziv na broj", "reference", "payment reference", "remittance", "remittance information",
        "referenca", "ref", "payment details", "osnov placanja", "основание", "референция",
        "verwendungszweck", "referenz",
    ),
    "description": (
        "opis", "description", "details", "transaction details", "narrative", "poruka", "note", "описание",
        "beschreibung",
    ),
}


def statement_file_hash(path: str | Path) -> str:
    source = Path(path)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv_rows(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    text = ""
    for encoding in ("utf-8-sig", "utf-16", "cp1251", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        raise ValueError("Bankovni CSV nije moguće pročitati.")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if text.count(";") > text.count(",") else ","
    return [[str(cell or "").strip() for cell in row] for row in csv.reader(text.splitlines(), dialect)]


def _xlsx_column_index(reference: str) -> int:
    letters = re.match(r"([A-Z]+)", reference.upper())
    if not letters:
        return 0
    value = 0
    for letter in letters.group(1):
        value = value * 26 + ord(letter) - ord("A") + 1
    return value - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall(f"{{{_NS_MAIN}}}si"):
        values.append("".join(node.text or "" for node in item.iter(f"{{{_NS_MAIN}}}t")))
    return values


def _first_worksheet_path(archive: zipfile.ZipFile) -> str:
    root = ET.fromstring(archive.read("xl/workbook.xml"))
    sheet = root.find(f".//{{{_NS_MAIN}}}sheet")
    if sheet is None:
        raise ValueError("Excel izvod nema radni list.")
    relationship_id = sheet.attrib.get(f"{{{_NS_REL}}}id", "")
    relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = ""
    for relation in relations.findall(f"{{{_NS_PKG_REL}}}Relationship"):
        if relation.attrib.get("Id") == relationship_id:
            target = relation.attrib.get("Target", "")
            break
    if not target:
        raise ValueError("Excel izvod nema dostupan radni list.")
    return "xl/" + target.lstrip("/")


def _read_xlsx_rows(path: Path) -> list[list[str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            shared = _shared_strings(archive)
            root = ET.fromstring(archive.read(_first_worksheet_path(archive)))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ValueError("Excel izvod nije važeći XLSX fajl.") from exc
    output: list[list[str]] = []
    for row in root.findall(f".//{{{_NS_MAIN}}}sheetData/{{{_NS_MAIN}}}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{_NS_MAIN}}}c"):
            index = _xlsx_column_index(cell.attrib.get("r", "A1"))
            cell_type = cell.attrib.get("t", "")
            raw_value = cell.findtext(f"{{{_NS_MAIN}}}v", default="")
            if cell_type == "s" and raw_value.isdigit():
                value = shared[int(raw_value)] if int(raw_value) < len(shared) else ""
            elif cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter(f"{{{_NS_MAIN}}}t"))
            else:
                value = raw_value
            values[index] = value.strip()
        if values:
            output.append([values.get(index, "") for index in range(max(values) + 1)])
    return output


def _header_map(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    best: tuple[int, dict[str, int]] = (-1, {})
    for row_index, row in enumerate(rows[:12]):
        mapped: dict[str, int] = {}
        for column_index, value in enumerate(row):
            header = _fold(value)
            for key, aliases in _HEADER_ALIASES.items():
                if key in mapped:
                    continue
                if any(alias in header or header in alias for alias in aliases if header):
                    mapped[key] = column_index
        if len(mapped) > len(best[1]):
            best = (row_index, mapped)
    if "date" not in best[1] or not ({"amount", "credit", "debit"} & set(best[1])):
        raise ValueError(
            "Kolone Datum i Iznos/Priliv nisu prepoznate. "
            "Izvezite bankovni izvod kao CSV ili XLSX sa zaglavljima kolona."
        )
    return best


def _cell(row: list[str], mapping: dict[str, int], key: str) -> str:
    index = mapping.get(key, -1)
    return row[index].strip() if 0 <= index < len(row) else ""


def _parse_amount(value: str) -> float | None:
    raw = re.sub(r"[^0-9,.'()\-]", "", str(value or "")).replace("'", "")
    if not raw:
        return None
    negative = raw.startswith("-") or raw.endswith("-") or (raw.startswith("(") and raw.endswith(")"))
    raw = raw.strip("()-")
    comma, dot = raw.rfind(","), raw.rfind(".")
    if comma >= 0 and dot >= 0:
        decimal = "," if comma > dot else "."
        raw = raw.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif comma >= 0:
        raw = raw.replace(".", "").replace(",", ".")
    elif dot >= 0 and len(raw) - dot - 1 == 3 and raw.count(".") == 1:
        raw = raw.replace(".", "")
    try:
        amount = float(raw)
    except ValueError:
        return None
    return -amount if negative else amount


def _parse_date(value: str) -> str:
    raw = str(value or "").strip()
    if raw.replace(".", "", 1).isdigit() and float(raw) > 20_000:
        return (datetime(1899, 12, 30) + timedelta(days=float(raw))).date().isoformat()
    for format_name in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, format_name).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def read_bank_statement(path: str | Path) -> list[dict[str, Any]]:
    """Read a generic CSV/XLSX bank statement, retaining both inflows and outflows.

    A bank export is evidence, not an accounting entry.  The caller still
    requires a human confirmation before an inflow becomes an invoice payment
    or an outflow settles a supplier bill.
    """
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        rows = _read_csv_rows(source)
    elif suffix == ".xlsx":
        rows = _read_xlsx_rows(source)
    else:
        raise ValueError("Podržani su bankovni izvodi u CSV ili XLSX formatu.")
    header_row, mapping = _header_map(rows)
    transactions: list[dict[str, Any]] = []
    for source_row, row in enumerate(rows[header_row + 1 :], start=header_row + 2):
        transaction_date = _parse_date(_cell(row, mapping, "date"))
        amount = _parse_amount(_cell(row, mapping, "amount"))
        if amount is None:
            credit = _parse_amount(_cell(row, mapping, "credit"))
            debit = _parse_amount(_cell(row, mapping, "debit"))
            amount = credit if credit not in (None, 0) else (-abs(debit) if debit else None)
        if not transaction_date or amount is None or amount == 0:
            continue
        transactions.append(
            {
                "source_row": source_row,
                "transaction_date": transaction_date,
                "amount": round(abs(amount), 2),
                "direction": "inflow" if amount > 0 else "outflow",
                "currency": _cell(row, mapping, "currency").upper(),
                "payer_name": _cell(row, mapping, "payer"),
                "payer_iban": _cell(row, mapping, "payer_iban"),
                "reference": _cell(row, mapping, "reference"),
                "description": _cell(row, mapping, "description"),
            }
        )
    return transactions
