from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
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
    send_verification_email,
    start_trial,
    verify_paypal_webhook,
    verify_turnstile_token,
)


PLAN_PRICES = {
    "starter": "9.90 EUR / month",
    "business": "19.90 EUR / month",
    "pro": "29.90 EUR / month",
}


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
    allow_headers=["Authorization", "Content-Type", "X-OpsNest-Workspace"],
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


class RecordPayPalApproval(BaseModel):
    session: str = Field(min_length=20, max_length=2048)
    subscription_id: str = Field(min_length=3, max_length=128)


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


@app.post("/v1/auth/request-email-code")
def request_email_code(
    payload: RequestEmailCode,
    request: Request,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    workspace_id = _validate_workspace_id(payload.workspace_id)
    email = _normalize_email(payload.email)
    if settings.is_production:
        verify_turnstile_token(payload.turnstile_token, request.client.host if request.client else "")
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
        if settings.is_production:
            db.rollback()
            raise
    db.commit()
    response: dict[str, Any] = {"ok": True, "message": "Verification code sent."}
    if not settings.is_production and not settings.smtp_host:
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
    if workspace.subscription_status in {"verification_pending", "expired", "cancelled"} and not workspace.trial_started_at:
        start_trial(workspace)
    token = new_client_token()
    workspace.client_token_hash = secret_hash(token)
    workspace.last_verified_at = datetime.utcnow()
    db.commit()
    return {"workspace_token": token, "license": effective_license(workspace)}


@app.get("/v1/license")
def license_status(workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, Any]:
    return effective_license(workspace)


@app.post("/v1/billing/checkout-session/{plan_code}")
def create_checkout_session(plan_code: str, workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, str]:
    plan = plan_code.lower().strip()
    if plan not in PLAN_PRICES:
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
    return HTMLResponse(
        """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>OpsNest subscription</title><style>body{font-family:Segoe UI,sans-serif;background:#f3f7f6;color:#10241c;margin:0;padding:32px}.card{max-width:540px;margin:auto;background:#fff;border-radius:18px;padding:32px;box-shadow:0 14px 40px #1232}.muted{color:#55706a}</style></head><body><main class=\"card\"><h1>OpsNest</h1><p id=\"plan\" class=\"muted\">Preparing secure checkout...</p><div id=\"paypal-button-container\"></div><p id=\"status\" class=\"muted\"></p></main><script>const session='"""
        + safe_session
        + """';fetch('/v1/billing/checkout-context?session='+encodeURIComponent(session)).then(r=>r.json()).then(data=>{if(data.detail)throw Error(data.detail);document.getElementById('plan').textContent=data.plan_code.toUpperCase()+' - '+data.price;const script=document.createElement('script');script.src='https://www.paypal.com/sdk/js?client-id='+encodeURIComponent(data.client_id)+'&vault=true&intent=subscription&currency=EUR';script.onload=()=>paypal.Buttons({createSubscription:(d,a)=>a.subscription.create({plan_id:data.plan_id}),onApprove:(data)=>fetch('/v1/billing/record-paypal-approval',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:session,subscription_id:data.subscriptionID})}).then(r=>r.json()).then(result=>{document.getElementById('status').textContent=result.ok?'Subscription activated. Return to OpsNest.':'Activation is being verified.';})}).render('#paypal-button-container');document.head.appendChild(script);}).catch(error=>document.getElementById('status').textContent=error.message);</script></body></html>"""
    )
