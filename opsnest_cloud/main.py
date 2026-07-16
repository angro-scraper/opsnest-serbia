from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
from .database import (
    EmailChallenge,
    MemberSession,
    PayPalWebhookEvent,
    TeamInvitation,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceMember,
    WorkspaceSyncSnapshot,
    create_schema,
    get_session,
)
from .security import (
    new_client_token,
    new_email_code,
    new_member_session_token,
    password_hash,
    secret_hash,
    sign_checkout_session,
    verify_checkout_session,
    verify_password,
)
from .services import (
    effective_license,
    get_paypal_subscription,
    paypal_access_token,
    send_team_invitation,
    send_support_diagnostic,
    send_verification_email,
    start_trial,
    verify_paypal_plan_ids,
    verify_paypal_webhook,
    verify_turnstile_token,
)
from opsnest_plans import PLAN_CATALOG, TRIAL_DAYS, plan_details, public_plan_catalog


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
    allow_headers=["Authorization", "Content-Type", "X-OpsNest-Workspace", "X-OpsNest-Client", "X-OpsNest-Member"],
)


TEAM_ROLES = {"owner", "administrator", "project_manager", "accountant", "operator"}
TEAM_ROLE_LABELS = {
    "owner": "Owner / Administrator",
    "administrator": "Administrator",
    "project_manager": "Project manager",
    "accountant": "Accountant",
    "operator": "Operator",
}
TEAM_SESSION_DAYS = 30
TEAM_INVITATION_HOURS = 48


class RequestEmailCode(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    company_name: str = Field(min_length=2, max_length=240)
    email: str = Field(min_length=5, max_length=320)
    turnstile_token: str = Field(default="", max_length=4096)


class ConfirmEmailCode(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    email: str = Field(min_length=5, max_length=320)
    code: str = Field(min_length=6, max_length=6)


class OwnerAccountSetup(BaseModel):
    display_name: str = Field(default="", max_length=160)
    password: str = Field(min_length=10, max_length=256)
    device_name: str = Field(default="OpsNest Desktop", max_length=160)


class TeamInvitationRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    display_name: str = Field(default="", max_length=160)
    role: str = Field(default="operator", max_length=32)


class AcceptTeamInvitation(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    code: str = Field(min_length=6, max_length=6)
    password: str = Field(min_length=10, max_length=256)
    device_name: str = Field(default="OpsNest Desktop", max_length=160)


class TeamLogin(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    device_name: str = Field(default="OpsNest Desktop", max_length=160)


class UploadSyncSnapshot(BaseModel):
    expected_revision: int = Field(ge=0)
    snapshot_b64: str = Field(min_length=1, max_length=35_000_000)
    sha256: str = Field(min_length=64, max_length=64)


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


def _normalize_team_role(value: str) -> str:
    role = str(value or "").strip().lower()
    if role not in TEAM_ROLES:
        raise HTTPException(status_code=422, detail="Unknown team role.")
    return role


def _serialize_member(member: WorkspaceMember) -> dict[str, Any]:
    return {
        "id": member.id,
        "email": member.email,
        "display_name": member.display_name,
        "role": member.role,
        "role_label": TEAM_ROLE_LABELS.get(member.role, member.role),
        "status": member.status,
        "last_login_at": member.last_login_at.isoformat() if member.last_login_at else "",
        "created_at": member.created_at.isoformat() if member.created_at else "",
    }


def _record_audit(
    db: Session,
    *,
    workspace_id: str,
    action: str,
    actor_member_id: str = "",
    entity_type: str = "",
    entity_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Record only operational metadata; billing credentials and accounting data stay out."""
    db.add(
        WorkspaceAuditEvent(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            actor_member_id=actor_member_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details_json=json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
        )
    )


def _ensure_owner_member(db: Session, workspace: Workspace) -> WorkspaceMember:
    owner_email = _normalize_email(workspace.owner_email)
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.email == owner_email,
        )
    )
    if member is None:
        member = WorkspaceMember(
            id=str(uuid.uuid4()),
            workspace_id=workspace.id,
            email=owner_email,
            display_name=workspace.company_name,
            role="owner",
            status="active",
        )
        db.add(member)
        db.flush()
    elif member.role != "owner":
        member.role = "owner"
        member.status = "active"
    return member


def _team_seat_limit(workspace: Workspace) -> int:
    license_data = effective_license(workspace)
    return int(plan_details(license_data["effective_plan_code"])["seats"])


def _team_seats_used(db: Session, workspace_id: str) -> int:
    active_states = {"active", "invited"}
    members = db.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id)).all()
    return sum(1 for member in members if member.status in active_states)


def _new_member_session(db: Session, member: WorkspaceMember, device_name: str) -> dict[str, str]:
    token = new_member_session_token()
    now = datetime.utcnow()
    session = MemberSession(
        id=str(uuid.uuid4()),
        workspace_id=member.workspace_id,
        member_id=member.id,
        token_hash=secret_hash(token),
        device_name=str(device_name or "OpsNest Desktop").strip()[:160] or "OpsNest Desktop",
        expires_at=now + timedelta(days=TEAM_SESSION_DAYS),
        last_seen_at=now,
    )
    member.last_login_at = now
    db.add(session)
    return {"member_id": member.id, "member_token": token, "member_role": member.role}


@dataclass(frozen=True)
class MemberContext:
    workspace: Workspace
    member: WorkspaceMember
    session: MemberSession


def _member_dependency(
    db: Session = Depends(get_session),
    workspace_id: Annotated[str, Header(alias="X-OpsNest-Workspace")] = "",
    team_member_id: Annotated[str, Header(alias="X-OpsNest-Member")] = "",
    authorization: Annotated[str, Header(alias="Authorization")] = "",
) -> MemberContext:
    normalized_workspace_id = _validate_workspace_id(workspace_id)
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    member = db.get(WorkspaceMember, str(team_member_id or "").strip())
    session = db.scalar(
        select(MemberSession).where(
            MemberSession.member_id == str(team_member_id or "").strip(),
            MemberSession.workspace_id == normalized_workspace_id,
            MemberSession.token_hash == secret_hash(token),
            MemberSession.revoked_at.is_(None),
        )
    )
    workspace = db.get(Workspace, normalized_workspace_id)
    if (
        not workspace
        or not member
        or not session
        or member.workspace_id != normalized_workspace_id
        or member.status != "active"
        or session.expires_at <= datetime.utcnow()
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired team session.")
    session.last_seen_at = datetime.utcnow()
    db.commit()
    return MemberContext(workspace=workspace, member=member, session=session)


def _require_team_role(context: MemberContext, *allowed_roles: str) -> MemberContext:
    if context.member.role not in set(allowed_roles):
        raise HTTPException(status_code=403, detail="This team role is not allowed to perform that action.")
    return context


def _require_team_sync(context: MemberContext) -> None:
    license_data = effective_license(context.workspace)
    features = set(plan_details(license_data["effective_plan_code"]).get("features") or set())
    if "team_users" not in features:
        raise HTTPException(status_code=403, detail="Shared team synchronization requires a Business or Pro package.")


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
    owner = _ensure_owner_member(db, workspace)
    _record_audit(
        db,
        workspace_id=workspace.id,
        actor_member_id=owner.id,
        action="workspace.email_verified",
        entity_type="workspace",
        entity_id=workspace.id,
    )
    db.commit()
    return {"workspace_token": token, "license": effective_license(workspace)}


@app.get("/v1/license")
def license_status(workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, Any]:
    return effective_license(workspace)


@app.post("/v1/team/owner-account")
def setup_owner_account(
    payload: OwnerAccountSetup,
    workspace: Workspace = Depends(_workspace_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Create the owner's central sign-in without changing the legacy license token."""
    owner = _ensure_owner_member(db, workspace)
    try:
        owner.password_hash = password_hash(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    owner.display_name = payload.display_name.strip() or owner.display_name or workspace.company_name
    owner.status = "active"
    _record_audit(
        db,
        workspace_id=workspace.id,
        actor_member_id=owner.id,
        action="team.owner_account_configured",
        entity_type="member",
        entity_id=owner.id,
    )
    response = _new_member_session(db, owner, payload.device_name)
    db.commit()
    return {"ok": True, "member": _serialize_member(owner), **response}


@app.get("/v1/team/members")
def list_team_members(
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Owners and administrators can view the centrally stored team roster."""
    _require_team_role(context, "owner", "administrator")
    members = db.scalars(
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == context.workspace.id)
        .order_by(WorkspaceMember.created_at.asc())
    ).all()
    seat_limit = _team_seat_limit(context.workspace)
    db.commit()
    return {
        "members": [_serialize_member(member) for member in members],
        "seat_limit": seat_limit,
        "seats_used": _team_seats_used(db, context.workspace.id),
        "can_manage": True,
    }


@app.post("/v1/team/invitations")
def invite_team_member(
    payload: TeamInvitationRequest,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Invite a person into one workspace, enforcing the package seat limit."""
    _require_team_role(context, "owner", "administrator")
    workspace = context.workspace
    email = _normalize_email(payload.email)
    role = _normalize_team_role(payload.role)
    if role == "owner":
        raise HTTPException(status_code=422, detail="Only the original workspace owner can have the Owner role.")
    if email == workspace.owner_email.lower():
        raise HTTPException(status_code=409, detail="This e-mail already belongs to the workspace owner.")
    existing = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.email == email,
        )
    )
    if existing and existing.status == "active":
        raise HTTPException(status_code=409, detail="This person is already an active team member.")
    pending = db.scalar(
        select(TeamInvitation)
        .where(
            TeamInvitation.workspace_id == workspace.id,
            TeamInvitation.email == email,
            TeamInvitation.accepted_at.is_(None),
            TeamInvitation.expires_at > datetime.utcnow(),
        )
        .order_by(TeamInvitation.created_at.desc())
    )
    if not existing and not pending and _team_seats_used(db, workspace.id) >= _team_seat_limit(workspace):
        raise HTTPException(status_code=409, detail="All team seats in this package are already used. Upgrade the package to invite another person.")
    code = new_email_code()
    invitation = TeamInvitation(
        id=str(uuid.uuid4()),
        workspace_id=workspace.id,
        email=email,
        display_name=payload.display_name.strip(),
        role=role,
        code_hash=secret_hash(code),
        invited_by_member_id=context.member.id,
        expires_at=datetime.utcnow() + timedelta(hours=TEAM_INVITATION_HOURS),
    )
    if existing:
        existing.display_name = invitation.display_name or existing.display_name
        existing.role = role
        existing.status = "invited"
    else:
        db.add(
            WorkspaceMember(
                id=str(uuid.uuid4()),
                workspace_id=workspace.id,
                email=email,
                display_name=invitation.display_name,
                role=role,
                status="invited",
                invited_by_member_id=context.member.id,
            )
        )
    db.add(invitation)
    try:
        send_team_invitation(
            email=email,
            company_name=workspace.company_name,
            role=TEAM_ROLE_LABELS[role],
            code=code,
        )
    except HTTPException:
        db.rollback()
        raise
    _record_audit(
        db,
        workspace_id=workspace.id,
        actor_member_id=context.member.id,
        action="team.invitation_sent",
        entity_type="invitation",
        entity_id=invitation.id,
        details={"email": email, "role": role},
    )
    db.commit()
    return {"ok": True, "expires_at": invitation.expires_at.isoformat(), "role": role}


@app.post("/v1/team/invitations/accept")
def accept_team_invitation(payload: AcceptTeamInvitation, db: Session = Depends(get_session)) -> dict[str, Any]:
    email = _normalize_email(payload.email)
    invitations = db.scalars(
        select(TeamInvitation)
        .where(
            TeamInvitation.email == email,
            TeamInvitation.accepted_at.is_(None),
            TeamInvitation.expires_at > datetime.utcnow(),
        )
        .order_by(TeamInvitation.created_at.desc())
    ).all()
    invitation = next((item for item in invitations if secret_hash(payload.code) == item.code_hash), None)
    if invitation is None:
        raise HTTPException(status_code=400, detail="The invitation code is not valid or has expired.")
    workspace = db.get(Workspace, invitation.workspace_id)
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == invitation.workspace_id,
            WorkspaceMember.email == email,
        )
    )
    if not workspace or not member:
        raise HTTPException(status_code=404, detail="The workspace invitation is no longer available.")
    try:
        member.password_hash = password_hash(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    member.display_name = invitation.display_name or member.display_name or email.split("@", 1)[0]
    member.role = invitation.role
    member.status = "active"
    invitation.accepted_at = datetime.utcnow()
    _record_audit(
        db,
        workspace_id=workspace.id,
        actor_member_id=member.id,
        action="team.invitation_accepted",
        entity_type="member",
        entity_id=member.id,
        details={"role": member.role},
    )
    response = _new_member_session(db, member, payload.device_name)
    db.commit()
    return {
        "ok": True,
        "workspace_id": workspace.id,
        "company_name": workspace.company_name,
        "member": _serialize_member(member),
        **response,
    }


@app.post("/v1/team/members/{member_id}/revoke")
def revoke_team_member(
    member_id: str,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, bool]:
    """Owner or administrator action; access is revoked but the audit remains."""
    _require_team_role(context, "owner", "administrator")
    workspace = context.workspace
    member = db.get(WorkspaceMember, member_id)
    if not member or member.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Team member was not found.")
    if member.id == context.member.id or member.role == "owner":
        raise HTTPException(status_code=422, detail="The workspace owner cannot be removed.")
    member.status = "revoked"
    sessions = db.scalars(
        select(MemberSession).where(
            MemberSession.workspace_id == workspace.id,
            MemberSession.member_id == member.id,
            MemberSession.revoked_at.is_(None),
        )
    ).all()
    for session in sessions:
        session.revoked_at = datetime.utcnow()
    _record_audit(
        db,
        workspace_id=workspace.id,
        actor_member_id=context.member.id,
        action="team.member_revoked",
        entity_type="member",
        entity_id=member.id,
        details={"email": member.email},
    )
    db.commit()
    return {"ok": True}


@app.post("/v1/team/login")
def team_login(payload: TeamLogin, db: Session = Depends(get_session)) -> dict[str, Any]:
    workspace_id = _validate_workspace_id(payload.workspace_id)
    email = _normalize_email(payload.email)
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.email == email,
        )
    )
    workspace = db.get(Workspace, workspace_id)
    if not workspace or not member or member.status != "active" or not verify_password(payload.password, member.password_hash):
        raise HTTPException(status_code=401, detail="E-mail or password is not correct.")
    _record_audit(
        db,
        workspace_id=workspace.id,
        actor_member_id=member.id,
        action="team.member_logged_in",
        entity_type="member",
        entity_id=member.id,
    )
    response = _new_member_session(db, member, payload.device_name)
    db.commit()
    return {
        "ok": True,
        "workspace_id": workspace.id,
        "company_name": workspace.company_name,
        "member": _serialize_member(member),
        **response,
    }


@app.get("/v1/team/me")
def team_me(context: MemberContext = Depends(_member_dependency)) -> dict[str, Any]:
    return {
        "workspace_id": context.workspace.id,
        "company_name": context.workspace.company_name,
        "member": _serialize_member(context.member),
        "permissions": {
            "manage_billing": context.member.role == "owner",
            "manage_team": context.member.role in {"owner", "administrator"},
            "manage_projects": context.member.role in {"owner", "administrator", "project_manager"},
            "manage_accounting": context.member.role in {"owner", "administrator", "project_manager", "accountant"},
            "delete_documents": context.member.role in {"owner", "administrator", "project_manager", "accountant"},
        },
    }


@app.get("/v1/team/sync")
def download_team_snapshot(
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return the current full workspace revision only to an authenticated team member."""
    _require_team_sync(context)
    snapshot = db.get(WorkspaceSyncSnapshot, context.workspace.id)
    if snapshot is None:
        return {"revision": 0, "sha256": "", "snapshot_b64": "", "updated_at": ""}
    return {
        "revision": snapshot.revision,
        "sha256": snapshot.sha256,
        "snapshot_b64": snapshot.snapshot_b64,
        "updated_at": snapshot.updated_at.isoformat() if snapshot.updated_at else "",
    }


@app.post("/v1/team/sync")
def upload_team_snapshot(
    payload: UploadSyncSnapshot,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Compare-and-swap write prevents one computer silently overwriting another."""
    _require_team_sync(context)
    try:
        raw_snapshot = base64.b64decode(payload.snapshot_b64.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise HTTPException(status_code=422, detail="The team snapshot is not valid base64 data.") from exc
    if len(raw_snapshot) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The synchronized database is larger than the 25 MB team limit.")
    if hashlib.sha256(raw_snapshot).hexdigest() != payload.sha256.lower():
        raise HTTPException(status_code=422, detail="The team snapshot checksum does not match.")
    snapshot = db.get(WorkspaceSyncSnapshot, context.workspace.id)
    current_revision = int(snapshot.revision) if snapshot else 0
    if payload.expected_revision != current_revision:
        raise HTTPException(
            status_code=409,
            detail="A newer workspace revision exists. Download the latest team data before uploading your changes.",
        )
    if snapshot is None:
        snapshot = WorkspaceSyncSnapshot(workspace_id=context.workspace.id)
        db.add(snapshot)
    snapshot.revision = current_revision + 1
    snapshot.snapshot_b64 = payload.snapshot_b64
    snapshot.sha256 = payload.sha256.lower()
    snapshot.updated_by_member_id = context.member.id
    snapshot.updated_at = datetime.utcnow()
    _record_audit(
        db,
        workspace_id=context.workspace.id,
        actor_member_id=context.member.id,
        action="team.sync_uploaded",
        entity_type="workspace_sync",
        entity_id=str(snapshot.revision),
        details={"revision": snapshot.revision, "sha256": snapshot.sha256},
    )
    db.commit()
    return {"ok": True, "revision": snapshot.revision, "sha256": snapshot.sha256}


@app.get("/v1/team/audit")
def team_audit(
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Administrator operational audit; no invoices, PDF bodies, or passwords are returned."""
    _require_team_role(context, "owner", "administrator")
    events = db.scalars(
        select(WorkspaceAuditEvent)
        .where(WorkspaceAuditEvent.workspace_id == context.workspace.id)
        .order_by(WorkspaceAuditEvent.created_at.desc())
        .limit(100)
    ).all()
    return {
        "events": [
            {
                "at": event.created_at.isoformat(),
                "action": event.action,
                "actor_member_id": event.actor_member_id,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "details": json.loads(event.details_json or "{}"),
            }
            for event in events
        ]
    }


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
    plan_configured = {plan: bool(plan_id) for plan, plan_id in settings.paypal_plan_ids.items()}
    configured = bool(
        settings.paypal_client_id
        and settings.paypal_client_secret
        and settings.paypal_webhook_id
        and all(plan_configured.values())
    )
    credentials_valid = False
    plan_ready = {plan: False for plan in settings.paypal_plan_ids}
    if configured:
        try:
            # The plan lookup is a read-only PayPal Live API call. It proves the
            # exact configured plan IDs before any customer checkout is opened.
            paypal_access_token()
            credentials_valid = True
            plan_ready = verify_paypal_plan_ids()
        except HTTPException:
            # Do not expose provider details or credentials to a desktop client.
            credentials_valid = False
    return {
        "provider": "paypal",
        "mode": settings.paypal_mode,
        "configured": configured,
        "credentials_valid": credentials_valid,
        "ready": settings.paypal_mode == "live" and configured and credentials_valid and all(plan_ready.values()),
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
        """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Secure checkout | OpsNest</title><style>@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@500&family=Manrope:wght@400;500;600;700;800&display=swap');:root{--ink:#102f36;--muted:#527176;--line:#dce9e5;--mint:#e8f6f1;--mint-strong:#d6efe6;--paper:#fffdf8;--teal:#087f76;--teal-dark:#05665f;--gold:#f7c24b;--shadow:0 28px 70px rgba(24,66,64,.15)}*{box-sizing:border-box}body{min-width:320px;margin:0;background:radial-gradient(circle at 8% 0,#fff9e7 0,transparent 30rem),linear-gradient(135deg,#edf8f5 0,#f9fbf8 48%,#eef6f2 100%);color:var(--ink);font-family:Manrope,Arial,sans-serif}.page{max-width:1120px;margin:0 auto;padding:28px 24px 42px}.topbar{display:flex;align-items:center;justify-content:space-between;gap:20px;margin:0 0 24px}.brand{display:flex;align-items:center;gap:12px;color:var(--ink);font-size:1.18rem;font-weight:800;letter-spacing:-.04em}.brand-mark{position:relative;width:38px;height:38px;overflow:hidden;border-radius:13px;background:#0a2d43;box-shadow:0 8px 16px rgba(8,68,72,.16)}.brand-mark:before{content:'';position:absolute;width:23px;height:11px;left:7px;top:9px;border:5px solid #13a99c;border-top:0;border-radius:0 0 12px 12px;transform:rotate(42deg)}.brand-mark:after{content:'';position:absolute;width:20px;height:10px;left:12px;top:16px;border:5px solid var(--gold);border-top:0;border-radius:0 0 12px 12px;transform:rotate(42deg)}.secure-label{display:flex;align-items:center;gap:8px;padding:9px 12px;border:1px solid #cde7df;border-radius:999px;background:rgba(255,255,255,.65);font-size:.78rem;font-weight:700;color:#26695f}.secure-label:before{content:'+';display:grid;place-items:center;width:17px;height:17px;border-radius:50%;background:#d9f2e8;color:#087f76;font-size:.8rem}.checkout{overflow:hidden;border:1px solid rgba(203,224,217,.95);border-radius:28px;background:rgba(255,255,255,.78);box-shadow:var(--shadow)}.checkout-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(390px,.92fr)}.summary{padding:45px;background:linear-gradient(145deg,#f4fbf8 0,#e6f5ef 100%);border-right:1px solid var(--line)}.eyebrow{margin:0 0 14px;color:var(--teal);font-family:'DM Mono',monospace;font-size:.72rem;font-weight:500;letter-spacing:.1em;text-transform:uppercase}.summary h1{max-width:470px;margin:0;color:#12363d;font-size:clamp(2rem,4vw,3.35rem);line-height:1.06;letter-spacing:-.065em}.summary-copy{max-width:500px;margin:18px 0 26px;color:var(--muted);font-size:1rem;line-height:1.7}.plan-strip{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:17px 19px;border:1px solid #cce8de;border-radius:17px;background:rgba(255,255,255,.8)}.plan-name{display:block;font-size:1.12rem;font-weight:800;letter-spacing:-.025em}.plan-detail{display:block;margin-top:4px;color:var(--muted);font-size:.8rem}.price{white-space:nowrap;color:var(--teal-dark);font-family:'DM Mono',monospace;font-size:.92rem;font-weight:500}.included{margin:28px 0 0;padding:0;list-style:none}.included li{position:relative;padding:10px 0 10px 31px;border-bottom:1px solid rgba(160,203,190,.45);color:#244d52;font-size:.91rem;line-height:1.45}.included li:before{content:'+';position:absolute;left:0;top:10px;display:grid;place-items:center;width:20px;height:20px;border-radius:50%;background:#d2efe3;color:#087f76;font-size:.82rem;font-weight:800}.billing-note{margin:26px 0 0;padding:15px 17px;border-left:3px solid var(--gold);border-radius:0 12px 12px 0;background:rgba(255,253,242,.78);color:#51676a;font-size:.82rem;line-height:1.55}.payment{padding:45px;background:var(--paper)}.payment h2{margin:0;color:#12363d;font-size:1.45rem;letter-spacing:-.04em}.payment-intro{margin:11px 0 24px;color:var(--muted);font-size:.9rem;line-height:1.6}.payment-box{min-height:126px;padding:18px;border:1px solid #e0ece8;border-radius:16px;background:#fff;box-shadow:0 8px 20px rgba(35,80,75,.05)}#paypal-button-container{min-height:52px}.status{min-height:20px;margin:16px 0 0;padding:0;color:var(--muted);font-size:.82rem;line-height:1.55}.status.success{padding:12px 13px;border:1px solid #bce4d3;border-radius:11px;background:#ecfaf3;color:#176048}.status.error{padding:12px 13px;border:1px solid #f0d4c7;border-radius:11px;background:#fff6f1;color:#974125}.trust-list{margin:27px 0 0;padding:0;list-style:none}.trust-list li{position:relative;margin:12px 0;padding-left:27px;color:#557174;font-size:.81rem;line-height:1.55}.trust-list li:before{content:'+';position:absolute;left:0;top:1px;color:#0b9084;font-weight:800}.support{margin-top:24px;padding-top:19px;border-top:1px solid var(--line);color:#688083;font-size:.78rem;line-height:1.6}.support a{color:var(--teal-dark);font-weight:800;text-decoration:none}.footer{display:flex;justify-content:space-between;gap:20px;padding:17px 5px 0;color:#688083;font-size:.75rem}.footer a{color:var(--teal-dark);font-weight:700;text-decoration:none}@media(max-width:820px){.page{padding:18px 14px 28px}.topbar{margin-bottom:16px}.checkout{border-radius:20px}.checkout-grid{grid-template-columns:1fr}.summary{padding:31px 25px;border-right:0;border-bottom:1px solid var(--line)}.payment{padding:31px 25px}.footer{display:block;line-height:1.8}.secure-label{font-size:.7rem}}@media(max-width:460px){.topbar{align-items:flex-start}.secure-label{display:none}.summary h1{font-size:2.05rem}.plan-strip{align-items:flex-start;flex-direction:column}.payment,.summary{padding:27px 20px}}</style></head><body><div class=\"page\"><header class=\"topbar\"><div class=\"brand\"><span class=\"brand-mark\" aria-hidden=\"true\"></span><span>OpsNest</span></div><div class=\"secure-label\">Secure PayPal checkout</div></header><main class=\"checkout\"><div class=\"checkout-grid\"><section class=\"summary\"><p class=\"eyebrow\">Your monthly plan</p><h1>Keep every project under control.</h1><p class=\"summary-copy\">Finish your subscription securely with PayPal. Your accounting data and local project documents stay in OpsNest, on your computer.</p><div class=\"plan-strip\"><div><span id=\"plan-name\" class=\"plan-name\">Preparing your plan</span><span id=\"plan-detail\" class=\"plan-detail\">Loading package details...</span></div><span id=\"plan-price\" class=\"price\">--</span></div><ul id=\"plan-benefits\" class=\"included\"><li>Preparing your selected package...</li></ul><p class=\"billing-note\">This is a monthly recurring subscription. You can cancel future renewals at any time from <strong>Plans and billing</strong> in OpsNest.</p></section><section class=\"payment\"><p class=\"eyebrow\">Payment</p><h2>Complete payment securely</h2><p class=\"payment-intro\">Choose PayPal or debit / credit card below. Payment details are processed by PayPal and are never stored in OpsNest.</p><div class=\"payment-box\"><div id=\"paypal-button-container\"></div></div><p id=\"status\" class=\"status\" aria-live=\"polite\">Loading the secure payment form...</p><ul class=\"trust-list\"><li>PayPal handles the payment and recurring billing authorization.</li><li>Your invoice, customer and project data is not shared with PayPal.</li><li>After approval, return to OpsNest and refresh the license status.</li></ul><p class=\"support\">Need help before paying? Contact <a href=\"mailto:support@opsnestone.com\">support@opsnestone.com</a>.</p></section></div></main><footer class=\"footer\"><span>OpsNest project invoicing and accounting</span><a href=\"https://opsnestone.com\">opsnestone.com</a></footer></div><script>const session='"""
        + safe_session
        + """';const planCopy={starter:{name:'Starter',detail:'For one owner and focused project work',benefits:['1 owner workspace','Up to 3 active projects','30 invoices and 30 PDF imports each month','Invoices, PDF / Excel export and project dashboard']},business:{name:'Business',detail:'For small teams managing several projects',benefits:['Up to 5 team seats','Up to 15 active projects','250 invoices and 250 PDF imports each month','Budgets, bank matching, VAT register and accountant export']},pro:{name:'Pro',detail:'For established teams with no workflow limits',benefits:['Up to 20 team seats','Unlimited projects, invoices and PDF imports','Advanced PDF processing and complete project reporting','Priority support']}};const status=document.getElementById('status');const showStatus=(message,kind)=>{status.textContent=message;status.className='status'+(kind?' '+kind:'')};const setPlan=(data)=>{const copy=planCopy[data.plan_code]||{name:String(data.plan_code||'OpsNest').toUpperCase(),detail:'Your selected OpsNest subscription',benefits:['OpsNest subscription']};document.getElementById('plan-name').textContent=copy.name;document.getElementById('plan-detail').textContent=copy.detail;document.getElementById('plan-price').textContent=data.price;const benefits=document.getElementById('plan-benefits');benefits.replaceChildren(...copy.benefits.map(item=>{const li=document.createElement('li');li.textContent=item;return li}))};fetch('/v1/billing/checkout-context?session='+encodeURIComponent(session)).then(async response=>{const data=await response.json();if(!response.ok||data.detail)throw Error('Checkout unavailable');return data}).then(data=>{setPlan(data);const script=document.createElement('script');script.src='https://"""
        + paypal_sdk_host
        + """/sdk/js?client-id='+encodeURIComponent(data.client_id)+'&vault=true&intent=subscription&currency=EUR';script.onload=()=>{if(!window.paypal)throw Error('Payment form unavailable');paypal.Buttons({style:{layout:'vertical',shape:'rect',color:'gold',label:'paypal'},createSubscription:(d,a)=>a.subscription.create({plan_id:data.plan_id}),onApprove:approval=>fetch('/v1/billing/record-paypal-approval',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:session,subscription_id:approval.subscriptionID})}).then(async response=>{const result=await response.json();if(!response.ok||!result.ok)throw Error('Subscription verification pending');return result}).then(()=>showStatus('Payment approved. Return to OpsNest and refresh the license status.','success')).catch(()=>showStatus('Payment was approved, but activation is still being verified. Return to OpsNest and refresh in a moment.','')),onError:()=>showStatus('PayPal could not load the payment form. Please refresh this page or try again later.','error')}).render('#paypal-button-container');showStatus('', '')};script.onerror=()=>showStatus('PayPal could not load the payment form. Please refresh this page or try again later.','error');document.head.appendChild(script)}).catch(()=>showStatus('Checkout could not be prepared. Return to OpsNest and try again, or contact support@opsnestone.com.','error'));</script></body></html>"""
    )
