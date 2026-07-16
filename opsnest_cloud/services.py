from __future__ import annotations

import base64
import json
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException

from .config import settings
from .database import Workspace


TRIAL_DAYS = 7
PAYPAL_WRITE_STATUSES = {"trial", "active"}


def effective_license(workspace: Workspace, *, now: datetime | None = None) -> dict[str, Any]:
    reference = now or datetime.utcnow()
    status = str(workspace.subscription_status or "verification_pending").lower()
    days_remaining = 0
    if status == "trial":
        if not workspace.trial_ends_at or workspace.trial_ends_at <= reference:
            status = "expired"
        else:
            seconds = (workspace.trial_ends_at - reference).total_seconds()
            days_remaining = max(1, int((seconds + 86_399) // 86_400))
    return {
        "workspace_id": workspace.id,
        "plan_code": workspace.plan_code,
        "status": status,
        "can_write": status in PAYPAL_WRITE_STATUSES,
        "days_remaining": days_remaining,
        "trial_ends_at": workspace.trial_ends_at.isoformat() if workspace.trial_ends_at else "",
        "last_verified_at": workspace.last_verified_at.isoformat() if workspace.last_verified_at else "",
    }


def start_trial(workspace: Workspace) -> None:
    now = datetime.utcnow()
    workspace.trial_started_at = now
    workspace.trial_ends_at = now + timedelta(days=TRIAL_DAYS)
    workspace.subscription_status = "trial"
    workspace.plan_code = "starter"
    workspace.last_verified_at = now


def send_verification_email(email: str, code: str, company_name: str) -> None:
    if not settings.smtp_host or not settings.smtp_from_email:
        raise HTTPException(status_code=503, detail="E-mail verification is not configured yet.")
    message = EmailMessage()
    message["Subject"] = "OpsNest verification code"
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = email
    message.set_content(
        f"Your OpsNest verification code is: {code}\n\n"
        f"Company: {company_name}\n"
        "The code expires in 15 minutes. If you did not start this registration, you can ignore this message."
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
            client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=502, detail="Verification e-mail could not be sent.") from exc


def verify_turnstile_token(token: str, remote_ip: str = "") -> None:
    """Reject production registrations that have not passed Cloudflare Turnstile."""
    if not settings.turnstile_secret_key:
        raise HTTPException(status_code=503, detail="Bot protection is not configured yet.")
    if not token.strip():
        raise HTTPException(status_code=400, detail="Complete the bot protection check and try again.")
    request = Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=urlencode(
            {
                "secret": settings.turnstile_secret_key,
                "response": token.strip(),
                "remoteip": remote_ip.strip(),
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Bot protection could not be verified. Try again.") from exc
    if not bool(result.get("success")):
        raise HTTPException(status_code=400, detail="Bot protection check was not accepted. Try again.")


def _paypal_request(path: str, *, method: str = "GET", body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    if not settings.paypal_client_id or not settings.paypal_client_secret:
        raise HTTPException(status_code=503, detail="PayPal is not configured yet.")
    url = settings.paypal_api_base + path
    raw_body = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = {"Accept": "application/json"}
    if raw_body is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = Request(url, data=raw_body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="PayPal request failed.") from exc


def paypal_access_token() -> str:
    if not settings.paypal_client_id or not settings.paypal_client_secret:
        raise HTTPException(status_code=503, detail="PayPal is not configured yet.")
    raw_credentials = f"{settings.paypal_client_id}:{settings.paypal_client_secret}".encode("utf-8")
    request = Request(
        settings.paypal_api_base + "/v1/oauth2/token",
        data=urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Accept-Language": "en_US",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + base64.b64encode(raw_credentials).decode("ascii"),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="PayPal authentication failed.") from exc
    token = str(payload.get("access_token") or "")
    if not token:
        raise HTTPException(status_code=502, detail="PayPal did not return an access token.")
    return token


def get_paypal_subscription(subscription_id: str) -> dict[str, Any]:
    token = paypal_access_token()
    return _paypal_request(
        f"/v1/billing/subscriptions/{subscription_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def verify_paypal_webhook(headers: dict[str, str], payload: dict[str, Any]) -> bool:
    if not settings.paypal_webhook_id:
        return False
    token = paypal_access_token()
    required_headers = {
        "auth_algo": headers.get("paypal-auth-algo", ""),
        "cert_url": headers.get("paypal-cert-url", ""),
        "transmission_id": headers.get("paypal-transmission-id", ""),
        "transmission_sig": headers.get("paypal-transmission-sig", ""),
        "transmission_time": headers.get("paypal-transmission-time", ""),
    }
    if not all(required_headers.values()):
        return False
    verification = _paypal_request(
        "/v1/notifications/verify-webhook-signature",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        body={
            **required_headers,
            "webhook_id": settings.paypal_webhook_id,
            "webhook_event": payload,
        },
    )
    return str(verification.get("verification_status") or "").upper() == "SUCCESS"
