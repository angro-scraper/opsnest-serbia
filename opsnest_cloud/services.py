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
from opsnest_plans import AI_ADVISOR_ADDONS, TRIAL_DAYS, effective_plan_code, normalize_plan_code, plan_details


PAYPAL_WRITE_STATUSES = {"trial", "active"}


def is_founder_workspace(workspace: Workspace) -> bool:
    """Grant the product owner permanent Pro access through server configuration only."""
    owner_email = str(workspace.owner_email or "").strip().lower()
    return bool(owner_email and owner_email in settings.founder_workspace_emails)


def effective_license(workspace: Workspace, *, now: datetime | None = None) -> dict[str, Any]:
    reference = now or datetime.utcnow()
    status = str(workspace.subscription_status or "verification_pending").lower()
    days_remaining = 0
    founder_access = is_founder_workspace(workspace)
    plan_code = "pro" if founder_access else normalize_plan_code(workspace.plan_code)
    if founder_access:
        # This is an internal owner entitlement, not a customer subscription.
        status = "active"
    elif status == "trial":
        if not workspace.trial_ends_at or workspace.trial_ends_at <= reference:
            status = "expired"
        else:
            seconds = (workspace.trial_ends_at - reference).total_seconds()
            days_remaining = max(1, int((seconds + 86_399) // 86_400))
    ai_enabled = founder_access or str(workspace.ai_advisor_status or "").lower() == "active"
    ai_tier_code = "ai_pro" if founder_access else str(workspace.ai_advisor_tier or "").lower()
    ai_tier = AI_ADVISOR_ADDONS.get(ai_tier_code)
    ai_enabled = bool(ai_enabled and ai_tier)
    period_started = workspace.ai_advisor_period_started_at
    used = max(0, int(workspace.ai_advisor_requests_used or 0)) if ai_enabled else 0
    if period_started and period_started <= reference - timedelta(days=30):
        used = 0
    monthly_limit = int(ai_tier["monthly_requests"]) if ai_tier else 0
    return {
        "workspace_id": workspace.id,
        "plan_code": plan_code,
        "effective_plan_code": "pro" if founder_access else effective_plan_code(status, plan_code),
        "plan_name": plan_details(plan_code)["name"],
        "status": status,
        "can_write": founder_access or status in PAYPAL_WRITE_STATUSES,
        "access_source": "founder" if founder_access else "subscription",
        "days_remaining": days_remaining,
        "trial_started_at": workspace.trial_started_at.isoformat() if workspace.trial_started_at else "",
        "trial_ends_at": workspace.trial_ends_at.isoformat() if workspace.trial_ends_at else "",
        "last_verified_at": workspace.last_verified_at.isoformat() if workspace.last_verified_at else "",
        "ai_advisor": {
            "enabled": ai_enabled,
            "status": "active" if ai_enabled else str(workspace.ai_advisor_status or "disabled").lower(),
            "tier_code": ai_tier_code,
            "tier_name": str(ai_tier["name"]) if ai_tier else "",
            "price_eur": str(ai_tier["price_eur"]) if ai_tier else "",
            "monthly_requests": monthly_limit,
            "requests_used": used,
            "requests_remaining": max(0, monthly_limit - used) if ai_enabled else 0,
            "period_started_at": period_started.isoformat() if period_started else "",
        },
    }


def consume_ai_advisor_request(workspace: Workspace, *, now: datetime | None = None) -> int:
    """Consume one included request after a successful response is generated."""
    reference = now or datetime.utcnow()
    tier = AI_ADVISOR_ADDONS.get("ai_pro" if is_founder_workspace(workspace) else str(workspace.ai_advisor_tier or "").lower())
    if not tier or not (is_founder_workspace(workspace) or str(workspace.ai_advisor_status or "").lower() == "active"):
        raise HTTPException(status_code=403, detail="AI financial adviser requires the AI Adviser add-on.")
    period_started = workspace.ai_advisor_period_started_at
    if not period_started or period_started <= reference - timedelta(days=30):
        workspace.ai_advisor_period_started_at = reference
        workspace.ai_advisor_requests_used = 0
    limit = int(tier["monthly_requests"])
    if int(workspace.ai_advisor_requests_used or 0) >= limit:
        raise HTTPException(status_code=429, detail="Your AI Adviser monthly limit has been reached. It renews with the next billing period.")
    workspace.ai_advisor_requests_used = int(workspace.ai_advisor_requests_used or 0) + 1
    return max(0, limit - int(workspace.ai_advisor_requests_used))


def start_trial(workspace: Workspace) -> None:
    now = datetime.utcnow()
    workspace.trial_started_at = now
    workspace.trial_ends_at = now + timedelta(days=TRIAL_DAYS)
    workspace.subscription_status = "trial"
    workspace.plan_code = "starter"
    workspace.last_verified_at = now


def send_verification_email(email: str, code: str, company_name: str) -> None:
    if not settings.smtp_from_email:
        raise HTTPException(status_code=503, detail="E-mail verification is not configured yet.")
    subject = "OpsNest verification code"
    body = (
        f"Your OpsNest verification code is: {code}\n\n"
        f"Company: {company_name}\n"
        "The code expires in 15 minutes. If you did not start this registration, you can ignore this message."
    )
    if settings.resend_api_key:
        _send_resend_email(email, subject, body)
        return
    if not settings.smtp_host:
        raise HTTPException(status_code=503, detail="E-mail verification is not configured yet.")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = email
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
            client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=502, detail="Verification e-mail could not be sent.") from exc


def send_team_invitation(*, email: str, company_name: str, role: str, code: str) -> None:
    """Send a minimal invite without including a password, token, or accounting data."""
    if not settings.smtp_from_email:
        raise HTTPException(status_code=503, detail="Team invitation e-mail is not configured yet.")
    subject = f"You are invited to {company_name} on OpsNest"
    body = (
        f"You have been invited to the OpsNest workspace for: {company_name}\n"
        f"Role: {role}\n\n"
        f"Your one-time team invitation code is: {code}\n"
        "Open OpsNest on your Windows computer, choose Team sign in, and enter your e-mail and this code. "
        "You will then choose your own password. The code expires in 48 hours.\n\n"
        "If you were not expecting this invitation, you can ignore this message."
    )
    if settings.resend_api_key:
        _send_resend_email(email, subject, body)
        return
    if not settings.smtp_host:
        raise HTTPException(status_code=503, detail="Team invitation e-mail is not configured yet.")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = email
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
            client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=502, detail="Team invitation e-mail could not be sent.") from exc


def send_support_diagnostic(*, workspace: Workspace, diagnostic: dict[str, Any]) -> None:
    """Deliver a deliberately small, non-accounting support report to OpsNest."""
    if not settings.support_email or not settings.smtp_from_email:
        raise HTTPException(status_code=503, detail="OpsNest support e-mail is not configured yet.")
    body = "\n".join(
        [
            "OpsNest desktop diagnostic",
            f"Workspace: ...{str(workspace.id)[-8:]}",
            f"Application version: {diagnostic.get('app_version') or '-'}",
            f"Operating system: {diagnostic.get('operating_system') or '-'}",
            f"License status: {diagnostic.get('license_status') or '-'}",
            f"Customer note: {diagnostic.get('message') or '-'}",
            "Privacy: no invoices, PDF files, passwords, PINs, or payment data were attached.",
        ]
    )
    if settings.resend_api_key:
        _send_resend_email(settings.support_email, "OpsNest diagnostic report", body)
        return
    if not settings.smtp_host:
        raise HTTPException(status_code=503, detail="OpsNest support e-mail is not configured yet.")
    message = EmailMessage()
    message["Subject"] = "OpsNest diagnostic report"
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = settings.support_email
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as client:
            client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=502, detail="Diagnostic report could not be sent.") from exc


def _send_resend_email(email: str, subject: str, body: str) -> None:
    """Send through HTTPS so Render free services never need SMTP egress."""
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps(
            {
                "from": f"{settings.smtp_from_name} <{settings.smtp_from_email}>",
                "to": [email],
                "subject": subject,
                "text": body,
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "OpsNest-Cloud/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20):
            return
    except (HTTPError, URLError, OSError) as exc:
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


def verify_paypal_plan_ids() -> dict[str, bool]:
    """Confirm configured billing plans exist and are active without charging anyone."""
    token = paypal_access_token()
    result: dict[str, bool] = {}
    for plan_code, plan_id in settings.paypal_plan_ids.items():
        if not plan_id:
            result[plan_code] = False
            continue
        try:
            plan = _paypal_request(
                f"/v1/billing/plans/{plan_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            result[plan_code] = str(plan.get("status") or "").upper() == "ACTIVE"
        except HTTPException:
            result[plan_code] = False
    return result


def verify_paypal_webhook(headers: dict[str, str], payload: dict[str, Any]) -> bool:
    if not settings.paypal_webhook_id:
        return False
    required_headers = {
        "auth_algo": headers.get("paypal-auth-algo", ""),
        "cert_url": headers.get("paypal-cert-url", ""),
        "transmission_id": headers.get("paypal-transmission-id", ""),
        "transmission_sig": headers.get("paypal-transmission-sig", ""),
        "transmission_time": headers.get("paypal-transmission-time", ""),
    }
    if not all(required_headers.values()):
        return False
    # Reject malformed requests locally before using the PayPal API. This keeps
    # forged webhook traffic cheap and makes the authentication check explicit.
    token = paypal_access_token()
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
