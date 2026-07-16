from __future__ import annotations

import json
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from threading import Lock
from html import escape
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import EmailChallenge, PayPalWebhookEvent, Workspace, create_schema, get_session
from .security import new_client_token, new_email_code, secret_hash, sign_checkout_session, verify_checkout_session
from .services import (
    effective_license,
    get_paypal_subscription,
    paypal_access_token,
    send_support_diagnostic,
    send_verification_email,
    start_trial,
    verify_paypal_webhook,
    verify_turnstile_token,
)
from opsnest_plans import PLAN_CATALOG, TRIAL_DAYS, public_plan_catalog


PLAN_PRICES = {code: f"{data['price_eur']} EUR / month" for code, data in PLAN_CATALOG.items()}


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_production()
    create_schema()
    yield


app = FastAPI(title="OpsNest Cloud", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins) or [settings.public_url],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-OpsNest-Workspace", "X-OpsNest-Client"],
)


class RequestEmailCode(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    company_name: str = Field(min_length=2, max_length=240)
    email: str = Field(min_length=5, max_length=320)
    turnstile_token: str = Field(default="", max_length=4096)


class ConfirmEmailCode(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    email: str = Field(min_length=5, max_length=320)
    code: str = Field(min_length=6, max_length=6)


_desktop_activation_attempts: dict[str, deque[datetime]] = defaultdict(deque)
_desktop_activation_lock = Lock()
_DESKTOP_ACTIVATION_WINDOW = timedelta(minutes=15)
_DESKTOP_ACTIVATION_LIMIT = 3


class RecordPayPalApproval(BaseModel):
    session: str = Field(min_length=20, max_length=2048)
    subscription_id: str = Field(min_length=3, max_length=128)


class DiagnosticReport(BaseModel):
    app_version: str = Field(default="", max_length=64)
    operating_system: str = Field(default="", max_length=240)
    license_status: str = Field(default="", max_length=64)
    message: str = Field(default="", max_length=800)


def _validate_workspace_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid workspace ID.") from exc


def _normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid e-mail address.")
    return email


def _limit_desktop_activation(remote_ip: str) -> None:
    """Desktop sign-up has no browser CAPTCHA, so strictly limit code requests."""
    identifier = remote_ip.strip() or "unknown"
    now = datetime.utcnow()
    with _desktop_activation_lock:
        attempts = _desktop_activation_attempts[identifier]
        cutoff = now - _DESKTOP_ACTIVATION_WINDOW
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= _DESKTOP_ACTIVATION_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="Too many registration requests. Try again in 15 minutes.",
            )
        attempts.append(now)


def _get_authenticated_workspace(
    db: Session,
    workspace_id: Annotated[str, Header(alias="X-OpsNest-Workspace")],
    authorization: Annotated[str, Header(alias="Authorization")],
) -> Workspace:
    normalized_id = _validate_workspace_id(workspace_id)
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    workspace = db.get(Workspace, normalized_id)
    if not workspace or not token or secret_hash(token) != workspace.client_token_hash:
        raise HTTPException(status_code=401, detail="Invalid workspace credentials.")
    return workspace


def _workspace_dependency(
    db: Session = Depends(get_session),
    workspace_id: Annotated[str, Header(alias="X-OpsNest-Workspace")] = "",
    authorization: Annotated[str, Header(alias="Authorization")] = "",
) -> Workspace:
    return _get_authenticated_workspace(db, workspace_id, authorization)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "opsnest-cloud"}


@app.get("/v1/public/plans")
def public_plans() -> dict[str, Any]:
    """Public, payment-safe catalog for the desktop app and website."""
    return {"currency": "EUR", "trial_days": TRIAL_DAYS, "plans": public_plan_catalog()}


@app.get("/v1/public/desktop-update")
def desktop_update() -> dict[str, str]:
    """Public update metadata used by the Windows app; no workspace data is needed."""
    return {
        "latest_version": settings.desktop_latest_version,
        "installer_url": settings.desktop_installer_url,
    }


@app.get("/activate", response_class=HTMLResponse)
def activation_page(workspace_id: str) -> HTMLResponse:
    """Browser-only e-mail verification keeps bot checks outside the desktop app."""
    normalized_workspace_id = _validate_workspace_id(workspace_id)
    defaults = json.dumps(
        {
            "workspace_id": normalized_workspace_id,
            "site_key": settings.turnstile_site_key,
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return HTMLResponse(
        """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Activate OpsNest</title><style>body{font-family:Segoe UI,sans-serif;background:#edf5f2;color:#10241c;margin:0;padding:32px}.card{max-width:560px;margin:auto;background:#fff;border-radius:18px;padding:32px;box-shadow:0 14px 40px #1232}label{display:block;margin-top:16px;font-weight:600}input{box-sizing:border-box;width:100%;padding:12px;border:1px solid #b9cbc4;border-radius:8px;font:inherit}button{margin-top:20px;padding:12px 18px;background:#057b70;color:#fff;border:0;border-radius:8px;font-weight:700;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.muted{color:#55706a}.error{color:#ae2b2b}</style><script src=\"https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit\" async defer></script></head><body><main class=\"card\"><h1>Activate OpsNest</h1><p class=\"muted\">Verify your company e-mail to start the free seven-day trial. No card is required.</p><form id=\"activation-form\"><label>Company name<input id=\"company\" required maxlength=\"240\"></label><label>Business e-mail<input id=\"email\" type=\"email\" required maxlength=\"320\"></label><div id=\"turnstile\" style=\"margin-top:18px\"></div><button id=\"submit\" type=\"submit\">Send verification code</button></form><p id=\"status\" class=\"muted\" role=\"status\"></p></main><script>const defaults="""
        + defaults
        + """;let captcha='';const company=document.getElementById('company'),email=document.getElementById('email'),status=document.getElementById('status'),submit=document.getElementById('submit');function renderCaptcha(){if(!defaults.site_key){status.textContent='Activation is not configured yet. Please contact OpsNest support.';status.className='error';submit.disabled=true;return;}turnstile.render('#turnstile',{sitekey:defaults.site_key,callback:token=>{captcha=token;},'expired-callback':()=>{captcha='';}});}window.addEventListener('load',renderCaptcha);document.getElementById('activation-form').addEventListener('submit',async event=>{event.preventDefault();if(!captcha){status.textContent='Complete the security check first.';status.className='error';return;}submit.disabled=true;status.className='muted';status.textContent='Sending verification code...';try{const response=await fetch('/v1/auth/request-email-code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace_id:defaults.workspace_id,company_name:company.value,email:email.value,turnstile_token:captcha})});const result=await response.json();if(!response.ok)throw Error(result.detail||'Verification could not be started.');status.textContent='Code sent. Return to OpsNest and enter the six-digit code.';}catch(error){status.textContent=error.message;status.className='error';submit.disabled=false;}});</script></body></html>"""
    )


@app.post("/v1/auth/request-email-code")
def request_email_code(
    payload: RequestEmailCode,
    request: Request,
    desktop_client: Annotated[str | None, Header(alias="X-OpsNest-Client")] = None,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    workspace_id = _validate_workspace_id(payload.workspace_id)
    email = _normalize_email(payload.email)
    if settings.is_production and payload.turnstile_token.strip():
        verify_turnstile_token(payload.turnstile_token, request.client.host if request.client else "")
    elif settings.is_production:
        if str(desktop_client or "").strip().lower() != "desktop":
            raise HTTPException(status_code=400, detail="Complete the bot protection check and try again.")
        _limit_desktop_activation(request.client.host if request.client else "")
    if not settings.is_development and not settings.resend_api_key:
        raise HTTPException(
            status_code=503,
            detail="E-mail activation is not ready yet. OpsNest support must configure secure e-mail delivery.",
        )
    existing_email = db.scalar(select(Workspace).where(Workspace.owner_email == email))
    if existing_email and existing_email.id != workspace_id:
        raise HTTPException(status_code=409, detail="This e-mail is already linked to another workspace.")

    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        workspace = Workspace(id=workspace_id, owner_email=email, company_name=payload.company_name.strip())
        db.add(workspace)
    else:
        workspace.owner_email = email
        workspace.company_name = payload.company_name.strip()

    latest = db.scalar(
        select(EmailChallenge)
        .where(EmailChallenge.workspace_id == workspace_id, EmailChallenge.email == email)
        .order_by(EmailChallenge.created_at.desc())
    )
    if latest and latest.created_at > datetime.utcnow() - timedelta(seconds=60):
        raise HTTPException(status_code=429, detail="Wait one minute before requesting another verification code.")

    code = new_email_code()
    challenge = EmailChallenge(
        id=uuid.uuid4().hex,
        workspace_id=workspace_id,
        email=email,
        code_hash=secret_hash(code),
        expires_at=datetime.utcnow() + timedelta(minutes=15),
    )
    db.add(challenge)
    try:
        send_verification_email(email, code, workspace.company_name)
    except HTTPException:
        if not settings.is_development:
            db.rollback()
            raise
    db.commit()
    response: dict[str, Any] = {"ok": True, "message": "Verification code sent."}
    if settings.is_development and not settings.smtp_host:
        response["development_code"] = code
    return response


@app.post("/v1/auth/confirm-email-code")
def confirm_email_code(payload: ConfirmEmailCode, db: Session = Depends(get_session)) -> dict[str, Any]:
    workspace_id = _validate_workspace_id(payload.workspace_id)
    email = _normalize_email(payload.email)
    challenge = db.scalar(
        select(EmailChallenge)
        .where(EmailChallenge.workspace_id == workspace_id, EmailChallenge.email == email, EmailChallenge.used_at.is_(None))
        .order_by(EmailChallenge.created_at.desc())
    )
    if not challenge or challenge.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="Verification code expired. Request a new code.")
    if challenge.attempts >= 5:
        raise HTTPException(status_code=429, detail="Too many attempts. Request a new code.")
    challenge.attempts += 1
    if secret_hash(payload.code) != challenge.code_hash:
        db.commit()
        raise HTTPException(status_code=400, detail="Verification code is not correct.")

    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    challenge.used_at = datetime.utcnow()
    workspace.email_verified_at = datetime.utcnow()
    # The trial starts at registration confirmation, not when a user later opens billing.
    if not workspace.trial_started_at:
        start_trial(workspace)
    token = new_client_token()
    workspace.client_token_hash = secret_hash(token)
    workspace.last_verified_at = datetime.utcnow()
    db.commit()
    return {"workspace_token": token, "license": effective_license(workspace)}


@app.get("/v1/license")
def license_status(workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, Any]:
    return effective_license(workspace)


@app.get("/v1/billing/summary")
def billing_summary(workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, Any]:
    """Return subscription dates and the safe PayPal self-service cancellation route."""
    license_data = effective_license(workspace)
    next_billing_at = ""
    if workspace.paypal_subscription_id:
        try:
            paypal_subscription = get_paypal_subscription(workspace.paypal_subscription_id)
            billing_info = paypal_subscription.get("billing_info") if isinstance(paypal_subscription.get("billing_info"), dict) else {}
            next_billing_at = str(billing_info.get("next_billing_time") or "")
        except HTTPException:
            # A temporary payment-provider error must not hide the local license state.
            next_billing_at = ""
    return {
        **license_data,
        "next_billing_at": next_billing_at,
        "can_manage_in_paypal": bool(workspace.paypal_subscription_id),
        "cancellation_url": "https://www.paypal.com/myaccount/autopay/",
    }


@app.get("/v1/billing/readiness")
def billing_readiness(workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, Any]:
    """Expose only safe capability flags to an authenticated desktop workspace."""
    plan_ready = {plan: bool(plan_id) for plan, plan_id in settings.paypal_plan_ids.items()}
    configured = bool(
        settings.paypal_client_id
        and settings.paypal_client_secret
        and settings.paypal_webhook_id
        and all(plan_ready.values())
    )
    credentials_valid = False
    if configured:
        try:
            paypal_access_token()
            credentials_valid = True
        except HTTPException:
            # Do not expose provider details or credentials to a desktop client.
            credentials_valid = False
    return {
        "provider": "paypal",
        "mode": settings.paypal_mode,
        "configured": configured,
        "credentials_valid": credentials_valid,
        "ready": configured and credentials_valid,
        "plans": plan_ready,
    }


@app.post("/v1/support/diagnostic")
def support_diagnostic(payload: DiagnosticReport, workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, bool]:
    send_support_diagnostic(workspace=workspace, diagnostic=payload.model_dump())
    return {"ok": True}


@app.post("/v1/billing/checkout-session/{plan_code}")
def create_checkout_session(plan_code: str, workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, str]:
    plan = plan_code.lower().strip()
    if plan not in PLAN_CATALOG:
        raise HTTPException(status_code=422, detail="Unknown plan.")
    if not settings.paypal_plan_ids.get(plan) or not settings.paypal_client_id:
        raise HTTPException(status_code=503, detail="PayPal plans are not configured yet.")
    session = sign_checkout_session(workspace.id, plan)
    return {"checkout_url": f"{settings.public_url}/checkout?session={session}"}


@app.get("/v1/billing/checkout-context")
def checkout_context(session: str) -> dict[str, str]:
    payload = verify_checkout_session(session)
    if not payload:
        raise HTTPException(status_code=400, detail="Checkout session expired. Return to OpsNest and try again.")
    plan_code = str(payload["plan_code"])
    plan_id = settings.paypal_plan_ids.get(plan_code)
    if not plan_id or not settings.paypal_client_id:
        raise HTTPException(status_code=503, detail="PayPal plans are not configured yet.")
    return {
        "workspace_id": str(payload["workspace_id"]),
        "plan_code": plan_code,
        "plan_id": plan_id,
        "client_id": settings.paypal_client_id,
        "price": PLAN_PRICES[plan_code],
    }


@app.post("/v1/billing/record-paypal-approval")
def record_paypal_approval(payload: RecordPayPalApproval, db: Session = Depends(get_session)) -> dict[str, Any]:
    checkout = verify_checkout_session(payload.session)
    if not checkout:
        raise HTTPException(status_code=400, detail="Checkout session expired. Return to OpsNest and try again.")
    paypal_subscription = get_paypal_subscription(payload.subscription_id)
    expected_plan = settings.paypal_plan_ids.get(str(checkout["plan_code"]))
    if paypal_subscription.get("plan_id") != expected_plan:
        raise HTTPException(status_code=400, detail="PayPal plan does not match this checkout.")
    workspace = db.get(Workspace, str(checkout["workspace_id"]))
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    workspace.paypal_subscription_id = payload.subscription_id
    workspace.billing_provider = "paypal"
    workspace.plan_code = str(checkout["plan_code"])
    workspace.last_verified_at = datetime.utcnow()
    if str(paypal_subscription.get("status") or "").upper() == "ACTIVE":
        workspace.subscription_status = "active"
    db.commit()
    return {"ok": True, "license": effective_license(workspace)}


@app.post("/v1/paypal/webhook")
async def paypal_webhook(request: Request, db: Session = Depends(get_session)) -> dict[str, bool]:
    payload = await request.json()
    headers = {key.lower(): value for key, value in request.headers.items()}
    if not verify_paypal_webhook(headers, payload):
        raise HTTPException(status_code=400, detail="Webhook signature is not valid.")
    event_id = str(payload.get("id") or "")
    if not event_id:
        raise HTTPException(status_code=400, detail="Webhook event ID is missing.")
    if db.get(PayPalWebhookEvent, event_id):
        return {"ok": True}
    event_type = str(payload.get("event_type") or "")
    resource = payload.get("resource") if isinstance(payload.get("resource"), dict) else {}
    subscription_id = str(resource.get("id") or resource.get("billing_agreement_id") or "")
    db.add(
        PayPalWebhookEvent(
            id=event_id,
            event_type=event_type,
            subscription_id=subscription_id,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
    )
    workspace = db.scalar(select(Workspace).where(Workspace.paypal_subscription_id == subscription_id)) if subscription_id else None
    if workspace:
        if event_type in {"BILLING.SUBSCRIPTION.ACTIVATED", "PAYMENT.SALE.COMPLETED"}:
            workspace.subscription_status = "active"
        elif event_type == "BILLING.SUBSCRIPTION.PAYMENT.FAILED":
            workspace.subscription_status = "past_due"
        elif event_type == "BILLING.SUBSCRIPTION.SUSPENDED":
            workspace.subscription_status = "suspended"
        elif event_type == "BILLING.SUBSCRIPTION.CANCELLED":
            workspace.subscription_status = "cancelled"
        elif event_type == "BILLING.SUBSCRIPTION.EXPIRED":
            workspace.subscription_status = "expired"
        workspace.last_verified_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@app.get("/checkout", response_class=HTMLResponse)
def checkout_page(session: str) -> HTMLResponse:
    safe_session = escape(session, quote=True)
    paypal_sdk_host = "www.sandbox.paypal.com" if settings.paypal_mode == "sandbox" else "www.paypal.com"
    return HTMLResponse(
        """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>OpsNest subscription</title><style>body{font-family:Segoe UI,sans-serif;background:#f3f7f6;color:#10241c;margin:0;padding:32px}.card{max-width:540px;margin:auto;background:#fff;border-radius:18px;padding:32px;box-shadow:0 14px 40px #1232}.muted{color:#55706a}</style></head><body><main class=\"card\"><h1>OpsNest</h1><p id=\"plan\" class=\"muted\">Preparing secure checkout...</p><div id=\"paypal-button-container\"></div><p id=\"status\" class=\"muted\"></p></main><script>const session='"""
        + safe_session
        + """';fetch('/v1/billing/checkout-context?session='+encodeURIComponent(session)).then(r=>r.json()).then(data=>{if(data.detail)throw Error(data.detail);document.getElementById('plan').textContent=data.plan_code.toUpperCase()+' - '+data.price;const script=document.createElement('script');script.src='https://"""
        + paypal_sdk_host
        + """/sdk/js?client-id='+encodeURIComponent(data.client_id)+'&vault=true&intent=subscription&currency=EUR';script.onload=()=>paypal.Buttons({createSubscription:(d,a)=>a.subscription.create({plan_id:data.plan_id}),onApprove:(data)=>fetch('/v1/billing/record-paypal-approval',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:session,subscription_id:data.subscriptionID})}).then(r=>r.json()).then(result=>{document.getElementById('status').textContent=result.ok?'Subscription activated. Return to OpsNest.':'Activation is being verified.';})}).render('#paypal-button-container');document.head.appendChild(script);}).catch(error=>document.getElementById('status').textContent=error.message);</script></body></html>"""
    )
