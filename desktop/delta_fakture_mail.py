from __future__ import annotations

import mimetypes
import smtplib
import ssl
import tempfile
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Iterable

from delta_fakture_core import LOGO_FILE, TEMPLATE_XLSX, format_currency, format_date


def _sender_address(company: dict[str, Any]) -> tuple[str, str]:
    display_name = str(company.get("smtp_from_name") or company.get("name") or "OpsNest").strip()
    sender_email = str(company.get("smtp_from_email") or company.get("email") or company.get("smtp_username") or "").strip()
    return display_name, sender_email


def build_invoice_email_defaults(invoice: dict[str, Any], company: dict[str, Any]) -> dict[str, str]:
    customer_name = str(invoice.get("customer_name") or "").strip()
    invoice_number = str(invoice.get("invoice_number") or "").strip()
    currency = str(invoice.get("currency") or "EUR").strip() or "EUR"
    balance = format_currency(invoice.get("balance_total", invoice.get("gross_total", 0)), currency)
    due_date = format_date(invoice.get("due_date"))
    issue_date = format_date(invoice.get("issue_date"))
    subject = f"Faktura {invoice_number}" if invoice_number else "Faktura"
    if customer_name:
        subject = f"{subject} - {customer_name}"
    body_lines = [
        "Poštovani,",
        "",
        f"U prilogu je faktura {invoice_number}." if invoice_number else "U prilogu je faktura.",
        f"Datum izdavanja: {issue_date}" if issue_date else "",
        f"Rok plaćanja: {due_date}" if due_date else "",
        f"Iznos za plaćanje: {balance}",
        "",
        "Ako je potrebno, uz fakturu mogu biti priloženi i dodatni dokumenti iz kartice Prilozi.",
        "",
        "Srdačan pozdrav,",
        str(company.get("smtp_from_name") or company.get("name") or "OpsNest").strip(),
    ]
    body = "\n".join(line for line in body_lines if line is not None)
    recipient = str(invoice.get("customer_email") or "").strip()
    return {"recipient": recipient, "subject": subject, "body": body}


def _attach_path(message: EmailMessage, path: Path) -> None:
    mime_type, encoding = mimetypes.guess_type(str(path))
    if mime_type:
        maintype, subtype = mime_type.split("/", 1)
    else:
        maintype, subtype = "application", "octet-stream"
    message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)


def compose_invoice_email_message(
    invoice: dict[str, Any],
    company: dict[str, Any],
    recipient: str,
    subject: str,
    body: str,
    *,
    include_pdf: bool = True,
    include_xlsx: bool = False,
    include_invoice_attachments: bool = True,
    extra_attachment_paths: Iterable[Path] = (),
) -> EmailMessage:
    recipient = str(recipient or "").strip()
    if not recipient:
        raise ValueError("Recipient e-mail address is missing.")

    display_name, sender_email = _sender_address(company)
    if not sender_email:
        raise ValueError("Sender e-mail address is missing in company settings.")

    message = EmailMessage()
    message["To"] = recipient
    message["From"] = formataddr((display_name, sender_email))
    message["Subject"] = subject.strip() or f"Faktura {invoice.get('invoice_number', '')}".strip()
    reply_to = str(company.get("smtp_reply_to") or sender_email).strip()
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body.strip() or build_invoice_email_defaults(invoice, company)["body"])

    attachments: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="delta_mail_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        if include_pdf or include_xlsx:
            # PDF/XLSX rendering libraries are intentionally loaded only when
            # the sender asks to attach an invoice.  Importing them during app
            # startup delayed the first usable desktop screen.
            from delta_fakture_export import export_invoice_bundle

            bundle = export_invoice_bundle(
                invoice,
                tmpdir_path,
                template_path=TEMPLATE_XLSX,
                logo_path=Path(company.get("logo_path") or LOGO_FILE) if company.get("logo_path") or LOGO_FILE.exists() else None,
            )
            if include_pdf and bundle.get("pdf"):
                attachments.append(bundle["pdf"])
            if include_xlsx and bundle.get("xlsx"):
                attachments.append(bundle["xlsx"])
        if include_invoice_attachments:
            for attachment in invoice.get("attachments", []):
                stored = Path(attachment.get("stored_path") or "")
                if stored.exists():
                    attachments.append(stored)
        for extra_path in extra_attachment_paths:
            path = Path(extra_path)
            if path.exists():
                attachments.append(path)

        seen: set[str] = set()
        for path in attachments:
            marker = str(path.resolve())
            if marker in seen:
                continue
            seen.add(marker)
            _attach_path(message, path)

    return message


def send_message_via_smtp(company: dict[str, Any], message: EmailMessage, timeout: int = 30) -> None:
    host = str(company.get("smtp_host") or "").strip()
    if not host:
        raise ValueError("SMTP host is not configured.")

    security = str(company.get("smtp_security") or "tls").strip().lower()
    port = int(company.get("smtp_port") or (465 if security == "ssl" else 587))
    username = str(company.get("smtp_username") or "").strip()
    password = str(company.get("smtp_password") or "").strip()
    context = ssl.create_default_context()

    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        smtp.ehlo()
        if security == "tls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def send_invoice_email(
    invoice: dict[str, Any],
    company: dict[str, Any],
    recipient: str,
    subject: str,
    body: str,
    *,
    include_pdf: bool = True,
    include_xlsx: bool = False,
    include_invoice_attachments: bool = True,
    extra_attachment_paths: Iterable[Path] = (),
) -> None:
    message = compose_invoice_email_message(
        invoice,
        company,
        recipient,
        subject,
        body,
        include_pdf=include_pdf,
        include_xlsx=include_xlsx,
        include_invoice_attachments=include_invoice_attachments,
        extra_attachment_paths=extra_attachment_paths,
    )
    send_message_via_smtp(company, message)
