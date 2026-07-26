from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from html import escape
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .admin_console import (
    ADMIN_COOKIE,
    ADMIN_SESSION_HOURS,
    admin_dashboard_html,
    admin_login_html,
    admin_session_email,
    new_admin_session,
    platform_overview,
    require_admin,
    verify_admin_credentials,
)
from .desktop_release import current_desktop_release
from .document_storage import (
    MAX_DOCUMENT_BYTES,
    document_storage_status,
    put_private_document,
    safe_filename,
    signed_document_download,
)
from .workspace_portal import workspace_portal_html
from .database import (
    EmailChallenge,
    MemberSession,
    PayPalWebhookEvent,
    PasswordResetChallenge,
    TeamInvitation,
    Workspace,
    WorkspaceAuditEvent,
    CountryPackControl,
    WorkspaceDocument,
    WorkspaceFinancialOverview,
    WorkspaceMember,
    WorkspaceSyncSnapshot,
    WorkflowComment,
    WorkflowItem,
    create_schema,
    get_session,
    SessionLocal,
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
    consume_ai_advisor_request,
    effective_license,
    get_paypal_subscription,
    paypal_access_token,
    send_team_invitation,
    send_support_diagnostic,
    send_team_password_reset,
    send_verification_email,
    start_trial,
    verify_paypal_plan_ids,
    verify_paypal_webhook,
    verify_turnstile_token,
)
from opsnest_plans import AI_ADVISOR_ADDONS, PLAN_CATALOG, TRIAL_DAYS, ai_advisor_addon_details, plan_details, public_plan_catalog


PLAN_PRICES = {
    **{code: f"{data['price_eur']} EUR / month" for code, data in PLAN_CATALOG.items()},
    **{code: f"{data['price_eur']} EUR / month" for code, data in AI_ADVISOR_ADDONS.items()},
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_production()
    create_schema()
    _migrate_workspace_audit_chain()
    yield


app = FastAPI(title="OpsNest Cloud", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins) or [settings.public_url],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-OpsNest-Workspace", "X-OpsNest-Client", "X-OpsNest-Member"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Keep browser/workspace responses out of shared caches and frames.

    The Workspace uses a short-lived bearer session in browser memory.  These
    headers reduce exposure from cached pages, MIME sniffing and embedding;
    API authorization remains enforced independently by every protected route.
    """
    response: Response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Pragma", "no-cache")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' https://www.paypal.com https://www.sandbox.paypal.com; "
        "connect-src 'self' https://www.paypal.com https://www.sandbox.paypal.com; "
        "frame-src https://www.paypal.com https://www.sandbox.paypal.com",
    )
    return response


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
WORKFLOW_TYPES = {"document", "payment", "vat", "review", "other"}
WORKFLOW_STATUSES = {"open", "in_progress", "waiting", "done"}
WORKFLOW_PRIORITIES = {"low", "normal", "high", "urgent"}
WORKFLOW_MANAGER_ROLES = {"owner", "administrator", "project_manager", "accountant"}
DOCUMENT_TYPES = {"invoice", "receipt", "contract", "statement", "other"}
COUNTRY_PACK_CONTROL_STATUSES = {"not_started", "in_review", "ready", "blocked", "not_applicable"}
COUNTRY_PACKS = {
    "RS": {"label": "Serbia", "currency": "RSD", "stage": "SEF and VAT workspace"},
    "BG": {"label": "Bulgaria", "currency": "BGN", "stage": "VAT and e-invoice workspace"},
    "HR": {"label": "Croatia", "currency": "EUR", "stage": "VAT and fiscalization foundation"},
    "BA": {"label": "Bosnia and Herzegovina", "currency": "BAM", "stage": "Country-pack foundation"},
    "ME": {"label": "Montenegro", "currency": "EUR", "stage": "Country-pack foundation"},
    "MK": {"label": "North Macedonia", "currency": "MKD", "stage": "Country-pack foundation"},
    "SI": {"label": "Slovenia", "currency": "EUR", "stage": "Country-pack foundation"},
    "INTL": {"label": "International", "currency": "EUR", "stage": "International core"},
}

# This is a governance checklist, never a declaration of statutory compliance.
# Country-specific connectors only become available after the listed local
# validation and commercial setup have actually been completed.
COUNTRY_PACK_CONTROL_LIBRARY: dict[str, tuple[dict[str, str], ...]] = {
    "RS": (
        {"key": "local_adviser", "title": "Local accountant validation", "title_sr": "Potvrda lokalnog knjigovođe", "detail": "Confirm the selected Serbian workflows with the company's accountant before activation.", "detail_sr": "Pre aktivacije potvrdite izabrane tokove za Srbiju sa knjigovođom firme."},
        {"key": "e_invoice", "title": "SEF connection readiness", "title_sr": "Spremnost SEF veze", "detail": "Register and test the authorised company connection before any production e-invoice exchange.", "detail_sr": "Registrujte i testirajte ovlašćenu vezu firme pre bilo kakve produkcione razmene e-faktura."},
        {"key": "vat_period", "title": "VAT period and export review", "title_sr": "Provera PDV perioda i izvoza", "detail": "Agree the reporting period, reconciliation and export review with the accountant.", "detail_sr": "Dogovorite obračunski period, usaglašavanje i proveru izvoza sa knjigovođom."},
        {"key": "archive_policy", "title": "Archive and retention policy", "title_sr": "Politika arhive i čuvanja", "detail": "Approve document retention, backup and access rules before storing accounting documents.", "detail_sr": "Odobrite pravila čuvanja, rezervnih kopija i pristupa pre skladištenja knjigovodstvenih dokumenata."},
    ),
    "BG": (
        {"key": "local_adviser", "title": "Local accountant validation", "title_sr": "Potvrda lokalnog knjigovođe", "detail": "Confirm the selected Bulgarian workflows with the company's accountant before activation.", "detail_sr": "Pre aktivacije potvrdite izabrane tokove za Bugarsku sa knjigovođom firme."},
        {"key": "e_invoice", "title": "E-invoice connection readiness", "title_sr": "Spremnost veze za e-fakture", "detail": "Validate the local e-invoice workflow and authorised company credentials before production use.", "detail_sr": "Potvrdite lokalni tok e-faktura i ovlašćene podatke firme pre produkcione upotrebe."},
        {"key": "vat_period", "title": "VAT period and ledger review", "title_sr": "Provera PDV perioda i evidencija", "detail": "Agree VAT period controls, ledger review and accountant sign-off.", "detail_sr": "Dogovorite PDV kontrole perioda, proveru evidencija i potvrdu knjigovođe."},
        {"key": "archive_policy", "title": "Archive and retention policy", "title_sr": "Politika arhive i čuvanja", "detail": "Approve document retention, backup and access rules before storing accounting documents.", "detail_sr": "Odobrite pravila čuvanja, rezervnih kopija i pristupa pre skladištenja knjigovodstvenih dokumenata."},
    ),
    "HR": (
        {"key": "local_adviser", "title": "Local accountant validation", "title_sr": "Potvrda lokalnog knjigovođe", "detail": "Confirm the selected Croatian workflows with the company's accountant before activation.", "detail_sr": "Pre aktivacije potvrdite izabrane tokove za Hrvatsku sa knjigovođom firme."},
        {"key": "e_invoice", "title": "E-invoice and fiscalisation review", "title_sr": "Provera e-faktura i fiskalizacije", "detail": "Validate the local invoice and fiscalisation obligations with an authorised adviser before production use.", "detail_sr": "Potvrdite lokalne obaveze za račune i fiskalizaciju sa ovlašćenim savetnikom pre produkcione upotrebe."},
        {"key": "vat_period", "title": "VAT period and export review", "title_sr": "Provera PDV perioda i izvoza", "detail": "Agree reporting-period controls and export review with the accountant.", "detail_sr": "Dogovorite kontrole obračunskog perioda i proveru izvoza sa knjigovođom."},
        {"key": "archive_policy", "title": "Archive and retention policy", "title_sr": "Politika arhive i čuvanja", "detail": "Approve document retention, backup and access rules before storing accounting documents.", "detail_sr": "Odobrite pravila čuvanja, rezervnih kopija i pristupa pre skladištenja knjigovodstvenih dokumenata."},
    ),
}
_GENERIC_COUNTRY_PACK_CONTROLS: tuple[dict[str, str], ...] = (
    {"key": "local_adviser", "title": "Local accountant validation", "title_sr": "Potvrda lokalnog knjigovođe", "detail": "Confirm the selected workflows with the company's local accountant before activation.", "detail_sr": "Pre aktivacije potvrdite izabrane tokove sa lokalnim knjigovođom firme."},
    {"key": "e_invoice", "title": "E-invoice readiness review", "title_sr": "Provera spremnosti za e-fakture", "detail": "Validate the local e-invoice route and company authorisation before production use.", "detail_sr": "Potvrdite lokalni tok e-faktura i ovlašćenja firme pre produkcione upotrebe."},
    {"key": "vat_period", "title": "VAT period and export review", "title_sr": "Provera PDV perioda i izvoza", "detail": "Agree reporting-period controls and export review with the accountant.", "detail_sr": "Dogovorite kontrole obračunskog perioda i proveru izvoza sa knjigovođom."},
    {"key": "archive_policy", "title": "Archive and retention policy", "title_sr": "Politika arhive i čuvanja", "detail": "Approve document retention, backup and access rules before storing accounting documents.", "detail_sr": "Odobrite pravila čuvanja, rezervnih kopija i pristupa pre skladištenja knjigovodstvenih dokumenata."},
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


class PasswordResetRequest(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    email: str = Field(min_length=5, max_length=320)


class PasswordResetConfirm(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    email: str = Field(min_length=5, max_length=320)
    code: str = Field(min_length=6, max_length=6)
    password: str = Field(min_length=10, max_length=256)


class WorkflowItemCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    workflow_type: str = Field(default="document", max_length=32)
    priority: str = Field(default="normal", max_length=16)
    due_date: str = Field(default="", max_length=10)
    assigned_member_id: str = Field(default="", max_length=36)


class WorkflowItemUpdate(BaseModel):
    status: str = Field(default="open", max_length=32)
    priority: str = Field(default="normal", max_length=16)
    due_date: str = Field(default="", max_length=10)
    assigned_member_id: str = Field(default="", max_length=36)


class WorkflowCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2_000)


class WorkspaceProfileUpdate(BaseModel):
    country_code: str = Field(default="INTL", min_length=2, max_length=8)
    default_currency: str = Field(default="EUR", min_length=3, max_length=8)
    business_profile: str = Field(default="general", pattern=r"^(construction|general|services|trade)$")


class CountryPackControlUpdate(BaseModel):
    """Accountability metadata only; statutory evidence stays in the local archive."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="not_started", max_length=32)
    due_date: str = Field(default="", max_length=10)
    owner_member_id: str = Field(default="", max_length=36)
    note: str = Field(default="", max_length=1_000)


class FinancialOverviewUpload(BaseModel):
    """A privacy-minimal Desktop → web summary; no accounting rows allowed."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3,8}$")
    horizon_days: int = Field(default=90, ge=7, le=365)
    income_net: float = Field(default=0, ge=0, le=1_000_000_000)
    expense_net: float = Field(default=0, ge=0, le=1_000_000_000)
    profit_net: float = Field(default=0, ge=-1_000_000_000, le=1_000_000_000)
    vat_payable: float = Field(default=0, ge=-1_000_000_000, le=1_000_000_000)
    open_receivables: float = Field(default=0, ge=0, le=1_000_000_000)
    overdue_receivables: float = Field(default=0, ge=0, le=1_000_000_000)
    open_payables: float = Field(default=0, ge=0, le=1_000_000_000)
    opening_cash: float = Field(default=0, ge=-1_000_000_000, le=1_000_000_000)
    forecast_inflows: float = Field(default=0, ge=0, le=1_000_000_000)
    forecast_outflows: float = Field(default=0, ge=0, le=1_000_000_000)
    forecast_closing: float = Field(default=0, ge=-1_000_000_000, le=1_000_000_000)


class UploadSyncSnapshot(BaseModel):
    expected_revision: int = Field(ge=0)
    snapshot_b64: str = Field(min_length=1, max_length=35_000_000)
    sha256: str = Field(min_length=64, max_length=64)


_desktop_activation_attempts: dict[str, deque[datetime]] = defaultdict(deque)
_desktop_activation_lock = Lock()
_DESKTOP_ACTIVATION_WINDOW = timedelta(minutes=15)
_DESKTOP_ACTIVATION_LIMIT = 3

_ai_advice_attempts: dict[str, deque[datetime]] = defaultdict(deque)
_ai_advice_lock = Lock()
_AI_ADVICE_WINDOW = timedelta(hours=1)
_AI_ADVICE_LIMIT = 12


class RecordPayPalApproval(BaseModel):
    session: str = Field(min_length=20, max_length=2048)
    subscription_id: str = Field(min_length=3, max_length=128)


class DiagnosticReport(BaseModel):
    app_version: str = Field(default="", max_length=64)
    operating_system: str = Field(default="", max_length=240)
    license_status: str = Field(default="", max_length=64)
    message: str = Field(default="", max_length=800)


class FinancialAdviceRequest(BaseModel):
    """Strictly aggregate-only input for the Pro AI adviser endpoint."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    language: str = Field(default="en", pattern=r"^[a-z]{2}(-[A-Za-z]{2,4})?$")
    business_profile: str = Field(default="general", pattern=r"^(construction|general)$")
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3,8}$")
    invoice_count: int = Field(ge=0, le=1_000_000)
    issued_total: float = Field(ge=0, le=1_000_000_000)
    paid_total: float = Field(ge=0, le=1_000_000_000)
    outstanding_total: float = Field(ge=0, le=1_000_000_000)
    overdue_total: float = Field(ge=0, le=1_000_000_000)
    output_vat_total: float = Field(ge=0, le=1_000_000_000)
    collection_rate_percent: int = Field(ge=0, le=100)
    overdue_share_percent: int = Field(ge=0, le=100)
    top_debtor_share_percent: int = Field(ge=0, le=100)
    expense_total: float = Field(default=0, ge=0, le=1_000_000_000)
    open_payables_total: float = Field(default=0, ge=0, le=1_000_000_000)
    cash_opening_total: float = Field(default=0, ge=0, le=1_000_000_000)
    cash_forecast_closing_total: float = Field(default=0, ge=-1_000_000_000, le=1_000_000_000)
    cash_flow_horizon_days: int = Field(default=30, ge=1, le=365)


class AdminLogin(BaseModel):
    """Private product-owner credentials supplied only through Render Environment."""

    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=1, max_length=512)


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


def _normalize_country_code(value: str) -> str:
    country = str(value or "INTL").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,8}", country):
        raise HTTPException(status_code=422, detail="Enter a valid country code.")
    return country


def _normalize_currency_code(value: str) -> str:
    currency = str(value or "EUR").strip().upper()
    if not re.fullmatch(r"[A-Z]{3,8}", currency):
        raise HTTPException(status_code=422, detail="Enter a valid currency code.")
    return currency


def _country_pack_controls(country_code: str) -> tuple[dict[str, str], ...]:
    """Return preparation controls for a country without implying compliance."""
    return COUNTRY_PACK_CONTROL_LIBRARY.get(_normalize_country_code(country_code), _GENERIC_COUNTRY_PACK_CONTROLS)


def _normalize_workflow_option(value: str, allowed: set[str], label: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise HTTPException(status_code=422, detail=f"Enter a valid {label}.")
    return normalized


def _normalize_workflow_due_date(value: str) -> str:
    due_date = str(value or "").strip()
    if not due_date:
        return ""
    try:
        return datetime.strptime(due_date, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Enter the due date as YYYY-MM-DD.") from exc


def _active_workspace_member(db: Session, workspace_id: str, member_id: str) -> WorkspaceMember | None:
    normalized_id = str(member_id or "").strip()
    if not normalized_id:
        return None
    return db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.id == normalized_id,
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.status == "active",
        )
    )


def _serialize_workflow_item(db: Session, item: WorkflowItem) -> dict[str, Any]:
    assignee = _active_workspace_member(db, item.workspace_id, item.assigned_member_id)
    creator = _active_workspace_member(db, item.workspace_id, item.created_by_member_id)
    comment_count = db.scalar(
        select(func.count()).select_from(WorkflowComment).where(WorkflowComment.workflow_item_id == item.id)
    ) or 0
    return {
        "id": item.id,
        "title": item.title,
        "workflow_type": item.workflow_type,
        "status": item.status,
        "priority": item.priority,
        "due_date": item.due_date,
        "assigned_member_id": item.assigned_member_id,
        "assigned_member_name": assignee.display_name if assignee else "Unassigned",
        "created_by_member_id": item.created_by_member_id,
        "created_by_member_name": creator.display_name if creator else "",
        "comment_count": int(comment_count),
        "created_at": item.created_at.isoformat(timespec="seconds"),
        "updated_at": item.updated_at.isoformat(timespec="seconds"),
        "closed_at": item.closed_at.isoformat(timespec="seconds") if item.closed_at else "",
    }


def _serialize_country_pack_control(
    db: Session,
    workspace_id: str,
    definition: dict[str, str],
    record: CountryPackControl | None,
) -> dict[str, Any]:
    owner = _active_workspace_member(db, workspace_id, record.owner_member_id) if record else None
    return {
        "key": definition["key"],
        "title": definition["title"],
        "title_sr": definition["title_sr"],
        "detail": definition["detail"],
        "detail_sr": definition["detail_sr"],
        "status": record.status if record else "not_started",
        "due_date": record.due_date if record else "",
        "owner_member_id": record.owner_member_id if record else "",
        "owner_member_name": owner.display_name or owner.email if owner else "Unassigned",
        "note": record.note if record else "",
        "updated_at": record.updated_at.isoformat(timespec="seconds") if record and record.updated_at else "",
    }


def _serialize_workspace_document(db: Session, document: WorkspaceDocument) -> dict[str, Any]:
    uploader = _active_workspace_member(db, document.workspace_id, document.uploaded_by_member_id)
    return {
        "id": document.id,
        "workflow_item_id": document.workflow_item_id,
        "uploaded_by_member_id": document.uploaded_by_member_id,
        "uploaded_by_name": uploader.display_name if uploader else "Former member",
        "document_type": document.document_type,
        "original_filename": document.original_filename,
        "content_type": document.content_type,
        "byte_size": document.byte_size,
        "sha256": document.sha256,
        "created_at": document.created_at.isoformat(timespec="seconds"),
    }


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


def _serialize_member_session(db: Session, session: MemberSession, current_session_id: str = "") -> dict[str, Any]:
    member = _active_workspace_member(db, session.workspace_id, session.member_id)
    return {
        "id": session.id,
        "member_id": session.member_id,
        "member_name": member.display_name or member.email if member else "Former member",
        "device_name": session.device_name,
        "created_at": session.created_at.isoformat(timespec="seconds"),
        "last_seen_at": session.last_seen_at.isoformat(timespec="seconds"),
        "expires_at": session.expires_at.isoformat(timespec="seconds"),
        "current": session.id == current_session_id,
    }


def _workspace_overview(db: Session, context: MemberContext) -> dict[str, Any]:
    """Safe platform data only; financial documents never leave the desktop here."""
    workspace = context.workspace
    country_code = _normalize_country_code(workspace.country_code)
    country_pack = COUNTRY_PACKS.get(
        country_code,
        {"label": country_code, "currency": workspace.default_currency or "EUR", "stage": "International core"},
    )
    members = db.scalars(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
    ).all()
    snapshot = db.get(WorkspaceSyncSnapshot, workspace.id)
    license_data = effective_license(workspace)
    can_manage = context.member.role in {"owner", "administrator"}
    sync_revision = int(snapshot.revision) if snapshot else 0
    last_sync = snapshot.updated_at.isoformat(timespec="seconds") if snapshot and snapshot.updated_at else ""
    return {
        "workspace": {
            "id": workspace.id,
            "company_name": workspace.company_name,
            "country_code": country_code,
            "country_label": country_pack["label"],
            "default_currency": workspace.default_currency or country_pack["currency"],
            "business_profile": workspace.business_profile or "general",
            "country_pack_stage": country_pack["stage"],
        },
        "member": _serialize_member(context.member),
        "license": license_data,
        "team": {
            "seats_used": sum(1 for member in members if member.status in {"active", "invited"}),
            "seat_limit": _team_seat_limit(workspace),
            "can_manage": can_manage,
        },
        "sync": {"revision": sync_revision, "last_sync_at": last_sync, "enabled": bool(sync_revision)},
        "modules": [
            {"key": "projects", "title": "Projects and contracts", "state": "desktop", "detail": "Operational project records stay available in OpsNest Desktop."},
            {"key": "workflow", "title": "Operational work queue", "state": "ready", "detail": "Assign document checks, payments, VAT controls and reviews with comments and deadlines."},
            {
                "key": "documents",
                "title": "Document Inbox",
                "state": "ready" if bool(document_storage_status()["enabled"]) else "configuration_required",
                "detail": "Private PDF/image intake is ready after the EU document-storage bucket is configured.",
            },
            {"key": "money", "title": "Money and cash-flow", "state": "desktop", "detail": "Bank, cash and forecasts remain in the controlled desktop workspace."},
            {"key": "accountant", "title": "Accountant collaboration", "state": "ready" if can_manage else "member", "detail": "Team roles, access control and audit are active."},
        ],
    }


def _workspace_audit_hash(event: WorkspaceAuditEvent, previous_hash: str) -> str:
    """Create a deterministic, secret-bound hash for one audit event.

    Unlike an ordinary checksum, the HMAC cannot be recreated from a copied
    database alone.  The signed values remain safe to expose as verification
    evidence because the signing secret itself never leaves Render.
    """
    material = {
        "previous_hash": str(previous_hash or ""),
        "id": event.id,
        "workspace_id": event.workspace_id,
        "actor_member_id": event.actor_member_id,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "details_json": event.details_json,
        "created_at": event.created_at.isoformat(timespec="microseconds"),
    }
    encoded = json.dumps(material, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hmac.new(settings.signing_secret.encode("utf-8"), encoded, hashlib.sha256).hexdigest()


def _migrate_workspace_audit_chain() -> None:
    """Create an explicit integrity baseline for audit events made before this release.

    This migration deliberately fills only a legacy workspace that has no
    chain at all.  A partially missing or modified live chain is preserved so
    the verifier can report it as an integrity incident rather than hiding it.
    """
    db = SessionLocal()
    try:
        events = db.scalars(
            select(WorkspaceAuditEvent).order_by(
                WorkspaceAuditEvent.workspace_id.asc(),
                WorkspaceAuditEvent.created_at.asc(),
                WorkspaceAuditEvent.id.asc(),
            )
        ).all()
        grouped: dict[str, list[WorkspaceAuditEvent]] = defaultdict(list)
        for event in events:
            grouped[event.workspace_id].append(event)
        changed = False
        for workspace_events in grouped.values():
            if not workspace_events or any(event.entry_hash or event.previous_hash for event in workspace_events):
                continue
            previous_hash = ""
            for event in workspace_events:
                event.previous_hash = previous_hash
                event.entry_hash = _workspace_audit_hash(event, previous_hash)
                previous_hash = event.entry_hash
            changed = True
        if changed:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _verify_workspace_audit_chain(db: Session, workspace_id: str) -> dict[str, Any]:
    events = db.scalars(
        select(WorkspaceAuditEvent)
        .where(WorkspaceAuditEvent.workspace_id == workspace_id)
        .order_by(WorkspaceAuditEvent.created_at.asc(), WorkspaceAuditEvent.id.asc())
    ).all()
    previous_hash = ""
    for event in events:
        if event.previous_hash != previous_hash or not event.entry_hash:
            return {
                "ok": False,
                "count": len(events),
                "invalid_event_id": event.id,
                "last_hash": previous_hash,
                "detail": "The audit sequence is missing or out of order.",
            }
        expected_hash = _workspace_audit_hash(event, previous_hash)
        if not hmac.compare_digest(event.entry_hash, expected_hash):
            return {
                "ok": False,
                "count": len(events),
                "invalid_event_id": event.id,
                "last_hash": previous_hash,
                "detail": "The audit event integrity check failed.",
            }
        previous_hash = event.entry_hash
    return {"ok": True, "count": len(events), "invalid_event_id": "", "last_hash": previous_hash, "detail": "Audit integrity verified."}


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
    """Record metadata and attach it to the workspace integrity chain."""
    event = WorkspaceAuditEvent(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        actor_member_id=actor_member_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details_json=json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )
    db.add(event)
    db.flush()
    previous = db.scalar(
        select(WorkspaceAuditEvent.entry_hash)
        .where(WorkspaceAuditEvent.workspace_id == workspace_id, WorkspaceAuditEvent.id != event.id)
        .order_by(WorkspaceAuditEvent.created_at.desc(), WorkspaceAuditEvent.id.desc())
        .limit(1)
    )
    event.previous_hash = str(previous or "")
    event.entry_hash = _workspace_audit_hash(event, event.previous_hash)


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
    return {"member_id": member.id, "member_token": token, "member_role": member.role, "session_id": session.id}


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


def _limit_ai_advice(workspace_id: str) -> None:
    """Bound model spend per workspace without retaining any financial data."""
    now = datetime.utcnow()
    with _ai_advice_lock:
        attempts = _ai_advice_attempts[workspace_id]
        cutoff = now - _AI_ADVICE_WINDOW
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= _AI_ADVICE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="AI financial adviser limit reached. Try again in about an hour.",
            )
        attempts.append(now)


def _generate_ai_financial_advice(payload: FinancialAdviceRequest) -> str:
    """Call OpenAI from the server only, using an intentionally tiny payload."""
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="AI financial adviser is not configured yet.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="AI financial adviser is being prepared. Try again shortly.") from exc

    instructions = (
        "You are the OpsNest Financial Adviser for small and medium businesses. "
        "Use only the aggregate numeric JSON supplied by the application. "
        "Return three concise, practical operational priorities in the requested language. "
        "Do not infer customer identities, invoices, or facts not present in the JSON. "
        "Do not give tax, legal, lending, investment, or regulatory advice; where relevant, say to confirm with a qualified accountant. "
        "Do not ask for personal or accounting-document data. "
        "Use plain text with a short heading and numbered recommendations."
    )
    try:
        response = OpenAI(api_key=settings.openai_api_key).responses.create(
            model=settings.openai_model,
            instructions=instructions,
            input=json.dumps(payload.model_dump(), ensure_ascii=False, separators=(",", ":")),
            max_output_tokens=550,
            store=False,
        )
        advice = str(getattr(response, "output_text", "") or "").strip()
    except Exception as exc:
        # Provider errors may include operational details. Do not return or log
        # them alongside a workspace's financial request.
        raise HTTPException(status_code=502, detail="AI financial adviser is temporarily unavailable. Try again later.") from exc
    if not advice:
        raise HTTPException(status_code=502, detail="AI financial adviser returned no advice. Try again later.")
    return advice[:8000]


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


@app.get("/admin", response_class=HTMLResponse)
def admin_console(request: Request) -> HTMLResponse:
    """Private platform console. It remains disabled until Render configures it."""
    if not settings.admin_enabled:
        raise HTTPException(status_code=404, detail="Not found.")
    operator_email = admin_session_email(request)
    response = HTMLResponse(admin_dashboard_html(operator_email) if operator_email else admin_login_html())
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.post("/admin/login")
def admin_login(payload: AdminLogin) -> JSONResponse:
    """Start a short-lived, signed, HttpOnly session for the product owner."""
    if not settings.admin_enabled:
        raise HTTPException(status_code=404, detail="Not found.")
    if not verify_admin_credentials(payload.email, payload.password):
        raise HTTPException(status_code=401, detail="Administrator e-mail or password is not correct.")
    response = JSONResponse({"ok": True})
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=ADMIN_COOKIE,
        value=new_admin_session(settings.admin_email),
        max_age=ADMIN_SESSION_HOURS * 60 * 60,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/admin",
    )
    return response


@app.post("/admin/logout")
def admin_logout(request: Request) -> JSONResponse:
    """Clear the browser cookie. No customer session or data is touched."""
    require_admin(request)
    response = JSONResponse({"ok": True})
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(ADMIN_COOKIE, path="/admin", httponly=True, secure=settings.is_production, samesite="lax")
    return response


@app.get("/admin/api/overview")
def admin_overview(request: Request, db: Session = Depends(get_session)) -> JSONResponse:
    """Return only privacy-safe product operations metadata to the control console."""
    require_admin(request)
    response = JSONResponse(platform_overview(db))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/v1/public/plans")
def public_plans() -> dict[str, Any]:
    """Public, payment-safe catalog for the desktop app and website."""
    return {
        "currency": "EUR",
        "trial_days": TRIAL_DAYS,
        "plans": public_plan_catalog(),
        "ai_advisor_addons": [ai_advisor_addon_details(code) for code in AI_ADVISOR_ADDONS],
    }


@app.get("/v1/public/desktop-update")
def desktop_update() -> dict[str, str]:
    """Public update metadata used by the Windows app; no workspace data is needed."""
    return current_desktop_release(
        settings.desktop_latest_version,
        settings.desktop_installer_url,
        settings.desktop_installer_sha256,
    )


@app.get("/workspace", response_class=HTMLResponse)
def workspace_portal() -> HTMLResponse:
    """The first cloud surface for owners, accountants and project teams."""
    response = HTMLResponse(workspace_portal_html())
    response.headers["Cache-Control"] = "no-store"
    return response


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


@app.post("/v1/ai/financial-advice")
def ai_financial_advice(
    payload: FinancialAdviceRequest,
    workspace: Workspace = Depends(_workspace_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Generate add-on advice from a deliberately aggregate-only snapshot."""
    license_data = effective_license(workspace)
    ai_license = dict(license_data.get("ai_advisor") or {})
    if not bool(ai_license.get("enabled")):
        raise HTTPException(status_code=403, detail="AI financial adviser requires the AI Adviser add-on.")
    if int(ai_license.get("requests_remaining") or 0) <= 0:
        raise HTTPException(status_code=429, detail="Your AI Adviser monthly limit has been reached. It renews with the next billing period.")
    _limit_ai_advice(workspace.id)
    advice = _generate_ai_financial_advice(payload)
    usage_workspace = db.get(Workspace, workspace.id)
    if not usage_workspace:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    remaining = consume_ai_advisor_request(usage_workspace)
    _record_audit(
        db,
        workspace_id=workspace.id,
        action="ai_financial_advice_requested",
        entity_type="ai_financial_advice",
        details={"model": settings.openai_model, "payload": "aggregate_only"},
    )
    db.commit()
    return {
        "advice": advice,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "requests_remaining": remaining,
    }


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


@app.get("/v1/team/sessions")
def list_team_sessions(
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Minimal device/session inventory for owners and administrators."""
    _require_team_role(context, "owner", "administrator")
    sessions = db.scalars(
        select(MemberSession)
        .where(
            MemberSession.workspace_id == context.workspace.id,
            MemberSession.revoked_at.is_(None),
            MemberSession.expires_at > datetime.utcnow(),
        )
        .order_by(MemberSession.last_seen_at.desc(), MemberSession.created_at.desc())
        .limit(100)
    ).all()
    return {
        "sessions": [_serialize_member_session(db, session, context.session.id) for session in sessions],
        "can_manage": True,
    }


@app.post("/v1/team/sessions/{session_id}/revoke")
def revoke_team_session(
    session_id: str,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, bool]:
    """Immediately revoke one lost or no-longer-needed device session."""
    _require_team_role(context, "owner", "administrator")
    session = db.scalar(
        select(MemberSession).where(
            MemberSession.id == str(session_id or "").strip(),
            MemberSession.workspace_id == context.workspace.id,
            MemberSession.revoked_at.is_(None),
        )
    )
    if not session:
        raise HTTPException(status_code=404, detail="Active device session was not found.")
    session.revoked_at = datetime.utcnow()
    _record_audit(
        db,
        workspace_id=context.workspace.id,
        actor_member_id=context.member.id,
        action="team.session_revoked",
        entity_type="member_session",
        entity_id=session.id,
        details={"member_id": session.member_id, "self_revoke": session.id == context.session.id},
    )
    db.commit()
    return {"ok": True}


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


@app.post("/v1/team/password-reset/request")
def request_team_password_reset(
    payload: PasswordResetRequest,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Deliver a short-lived recovery code without revealing whether an account exists."""
    workspace_id = _validate_workspace_id(payload.workspace_id)
    email = _normalize_email(payload.email)
    workspace = db.get(Workspace, workspace_id)
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.email == email,
            WorkspaceMember.status == "active",
        )
    )
    response: dict[str, Any] = {
        "ok": True,
        "message": "If this e-mail belongs to an active OpsNest team account, a recovery code has been sent.",
    }
    if not workspace or not member:
        return response
    latest = db.scalar(
        select(PasswordResetChallenge)
        .where(PasswordResetChallenge.workspace_id == workspace_id, PasswordResetChallenge.email == email)
        .order_by(PasswordResetChallenge.created_at.desc())
    )
    if latest and latest.created_at > datetime.utcnow() - timedelta(seconds=60):
        raise HTTPException(status_code=429, detail="Wait one minute before requesting another recovery code.")
    code = new_email_code()
    challenge = PasswordResetChallenge(
        id=uuid.uuid4().hex,
        workspace_id=workspace_id,
        member_id=member.id,
        email=email,
        code_hash=secret_hash(code),
        expires_at=datetime.utcnow() + timedelta(minutes=15),
    )
    db.add(challenge)
    try:
        send_team_password_reset(email=email, company_name=workspace.company_name, code=code)
    except HTTPException:
        if not settings.is_development:
            db.rollback()
            raise
    db.commit()
    if settings.is_development and not settings.smtp_host:
        response["development_code"] = code
    return response


@app.post("/v1/team/password-reset/confirm")
def confirm_team_password_reset(
    payload: PasswordResetConfirm,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Replace one member password and revoke every old session for that account."""
    workspace_id = _validate_workspace_id(payload.workspace_id)
    email = _normalize_email(payload.email)
    challenge = db.scalar(
        select(PasswordResetChallenge)
        .where(
            PasswordResetChallenge.workspace_id == workspace_id,
            PasswordResetChallenge.email == email,
            PasswordResetChallenge.used_at.is_(None),
        )
        .order_by(PasswordResetChallenge.created_at.desc())
    )
    if not challenge or challenge.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="Recovery code expired. Request a new code.")
    if challenge.attempts >= 5:
        raise HTTPException(status_code=429, detail="Too many attempts. Request a new recovery code.")
    challenge.attempts += 1
    if secret_hash(payload.code) != challenge.code_hash:
        db.commit()
        raise HTTPException(status_code=400, detail="Recovery code is not correct.")
    member = db.get(WorkspaceMember, challenge.member_id)
    workspace = db.get(Workspace, workspace_id)
    if not workspace or not member or member.workspace_id != workspace_id or member.status != "active":
        raise HTTPException(status_code=400, detail="This account can no longer reset its password.")
    challenge.used_at = datetime.utcnow()
    member.password_hash = password_hash(payload.password)
    active_sessions = db.scalars(
        select(MemberSession).where(
            MemberSession.workspace_id == workspace_id,
            MemberSession.member_id == member.id,
            MemberSession.revoked_at.is_(None),
        )
    ).all()
    for session in active_sessions:
        session.revoked_at = datetime.utcnow()
    _record_audit(
        db,
        workspace_id=workspace_id,
        actor_member_id=member.id,
        action="team.password_reset",
        entity_type="member",
        entity_id=member.id,
    )
    db.commit()
    return {"ok": True, "message": "Password changed. Sign in with the new password."}


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


@app.get("/v1/workspace/overview")
def workspace_overview(
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Platform landing data with collaboration metadata, never invoice payloads."""
    return _workspace_overview(db, context)


@app.post("/v1/workspace/profile")
def update_workspace_profile(
    payload: WorkspaceProfileUpdate,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Owners choose the country pack before statutory modules are enabled."""
    _require_team_role(context, "owner", "administrator")
    workspace = context.workspace
    workspace.country_code = _normalize_country_code(payload.country_code)
    workspace.default_currency = _normalize_currency_code(payload.default_currency)
    workspace.business_profile = payload.business_profile
    _record_audit(
        db,
        workspace_id=workspace.id,
        actor_member_id=context.member.id,
        action="workspace.profile_updated",
        entity_type="workspace",
        entity_id=workspace.id,
        details={
            "country_code": workspace.country_code,
            "default_currency": workspace.default_currency,
            "business_profile": workspace.business_profile,
        },
    )
    db.commit()
    return _workspace_overview(db, context)


@app.get("/v1/workspace/country-pack-readiness")
def get_country_pack_readiness(
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Show the local activation checklist without claiming legal compliance."""
    country_code = _normalize_country_code(context.workspace.country_code)
    definitions = _country_pack_controls(country_code)
    control_keys = [definition["key"] for definition in definitions]
    records = db.scalars(
        select(CountryPackControl).where(
            CountryPackControl.workspace_id == context.workspace.id,
            CountryPackControl.country_code == country_code,
            CountryPackControl.control_key.in_(control_keys),
        )
    ).all()
    records_by_key = {record.control_key: record for record in records}
    active_members = db.scalars(
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == context.workspace.id, WorkspaceMember.status == "active")
        .order_by(WorkspaceMember.display_name, WorkspaceMember.email)
    ).all()
    return {
        "country_code": country_code,
        "country_label": COUNTRY_PACKS.get(country_code, {"label": country_code})["label"],
        "disclaimer": "This is a readiness register, not a legal, tax, fiscalisation or e-invoice compliance declaration.",
        "disclaimer_sr": "Ovo je registar spremnosti, a ne potvrda pravne, poreske, fiskalizacione ili e-faktura usklađenosti.",
        "controls": [
            _serialize_country_pack_control(db, context.workspace.id, definition, records_by_key.get(definition["key"]))
            for definition in definitions
        ],
        "members": [_serialize_member(member) for member in active_members],
        "can_manage": context.member.role in WORKFLOW_MANAGER_ROLES,
    }


@app.put("/v1/workspace/country-pack-readiness/{control_key}")
def update_country_pack_readiness(
    control_key: str,
    payload: CountryPackControlUpdate,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Assign and record local-readiness work with an accountable audit entry."""
    _require_team_role(context, *WORKFLOW_MANAGER_ROLES)
    country_code = _normalize_country_code(context.workspace.country_code)
    definitions = {definition["key"]: definition for definition in _country_pack_controls(country_code)}
    definition = definitions.get(str(control_key or "").strip())
    if not definition:
        raise HTTPException(status_code=404, detail="Country-pack control not found for the selected country.")
    owner = _active_workspace_member(db, context.workspace.id, payload.owner_member_id)
    if payload.owner_member_id.strip() and not owner:
        raise HTTPException(status_code=422, detail="Choose an active member of this workspace.")
    control = db.scalar(
        select(CountryPackControl).where(
            CountryPackControl.workspace_id == context.workspace.id,
            CountryPackControl.country_code == country_code,
            CountryPackControl.control_key == definition["key"],
        )
    )
    if control is None:
        control = CountryPackControl(
            id=str(uuid.uuid4()),
            workspace_id=context.workspace.id,
            country_code=country_code,
            control_key=definition["key"],
        )
        db.add(control)
    old_status = control.status
    control.status = _normalize_workflow_option(payload.status, COUNTRY_PACK_CONTROL_STATUSES, "country-pack control status")
    control.due_date = _normalize_workflow_due_date(payload.due_date)
    control.owner_member_id = owner.id if owner else ""
    control.note = payload.note.strip()
    control.updated_by_member_id = context.member.id
    _record_audit(
        db,
        workspace_id=context.workspace.id,
        actor_member_id=context.member.id,
        action="country_pack.control_updated",
        entity_type="country_pack_control",
        entity_id=control.id,
        details={
            "country_code": country_code,
            "control_key": control.control_key,
            "from_status": old_status,
            "to_status": control.status,
            "assigned": bool(control.owner_member_id),
        },
    )
    db.commit()
    return {"control": _serialize_country_pack_control(db, context.workspace.id, definition, control)}


@app.get("/v1/workspace/financial-overview")
def get_workspace_financial_overview(
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Read the optional aggregate-only finance summary for this workspace."""
    overview = db.get(WorkspaceFinancialOverview, context.workspace.id)
    if not overview:
        return {"summary": None, "message": "No Desktop financial summary has been synchronized yet."}
    try:
        summary = json.loads(overview.summary_json or "{}")
    except json.JSONDecodeError:
        summary = {}
    return {
        "summary": summary,
        "currency": overview.currency,
        "horizon_days": overview.horizon_days,
        "updated_at": overview.updated_at.isoformat(timespec="seconds") if overview.updated_at else "",
        "updated_by_member_id": overview.updated_by_member_id,
    }


@app.post("/v1/workspace/financial-overview")
def upload_workspace_financial_overview(
    payload: FinancialOverviewUpload,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Store explicit company-level totals; never rows, people or documents."""
    _require_team_role(context, *WORKFLOW_MANAGER_ROLES)
    summary = payload.model_dump()
    overview = db.get(WorkspaceFinancialOverview, context.workspace.id)
    if overview is None:
        overview = WorkspaceFinancialOverview(workspace_id=context.workspace.id)
        db.add(overview)
    overview.currency = _normalize_currency_code(payload.currency)
    overview.horizon_days = int(payload.horizon_days)
    overview.summary_json = json.dumps(summary, separators=(",", ":"), sort_keys=True)
    overview.updated_by_member_id = context.member.id
    _record_audit(
        db,
        workspace_id=context.workspace.id,
        actor_member_id=context.member.id,
        action="workspace.financial_overview_synchronized",
        entity_type="financial_overview",
        entity_id=context.workspace.id,
        details={"currency": overview.currency, "horizon_days": overview.horizon_days},
    )
    db.commit()
    return {"ok": True, "currency": overview.currency, "horizon_days": overview.horizon_days}


@app.get("/v1/workflow-items")
def list_workflow_items(
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Shared operational queue; it stores workflow metadata, never invoice files."""
    items = db.scalars(
        select(WorkflowItem)
        .where(WorkflowItem.workspace_id == context.workspace.id)
        .order_by(WorkflowItem.status, WorkflowItem.due_date, WorkflowItem.created_at.desc())
        .limit(200)
    ).all()
    active_members = db.scalars(
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == context.workspace.id, WorkspaceMember.status == "active")
        .order_by(WorkspaceMember.display_name, WorkspaceMember.email)
    ).all()
    return {
        "items": [_serialize_workflow_item(db, item) for item in items],
        "members": [_serialize_member(member) for member in active_members],
        "can_manage": context.member.role in WORKFLOW_MANAGER_ROLES,
    }


@app.post("/v1/workflow-items")
def create_workflow_item(
    payload: WorkflowItemCreate,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Create an accountable work item for a document, payment, VAT or review."""
    _require_team_role(context, *WORKFLOW_MANAGER_ROLES)
    assignee = _active_workspace_member(db, context.workspace.id, payload.assigned_member_id)
    if payload.assigned_member_id.strip() and not assignee:
        raise HTTPException(status_code=422, detail="Choose an active member of this workspace.")
    item = WorkflowItem(
        id=str(uuid.uuid4()),
        workspace_id=context.workspace.id,
        title=payload.title.strip(),
        workflow_type=_normalize_workflow_option(payload.workflow_type, WORKFLOW_TYPES, "work type"),
        priority=_normalize_workflow_option(payload.priority, WORKFLOW_PRIORITIES, "priority"),
        due_date=_normalize_workflow_due_date(payload.due_date),
        assigned_member_id=assignee.id if assignee else "",
        created_by_member_id=context.member.id,
    )
    db.add(item)
    _record_audit(
        db,
        workspace_id=context.workspace.id,
        actor_member_id=context.member.id,
        action="workflow.item_created",
        entity_type="workflow_item",
        entity_id=item.id,
        details={"workflow_type": item.workflow_type, "priority": item.priority, "assigned": bool(item.assigned_member_id)},
    )
    db.commit()
    return {"item": _serialize_workflow_item(db, item)}


@app.patch("/v1/workflow-items/{item_id}")
def update_workflow_item(
    item_id: str,
    payload: WorkflowItemUpdate,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Managers can assign, return to work or close a queue item with an audit trail."""
    _require_team_role(context, *WORKFLOW_MANAGER_ROLES)
    item = db.scalar(
        select(WorkflowItem).where(WorkflowItem.id == item_id, WorkflowItem.workspace_id == context.workspace.id)
    )
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found.")
    assignee = _active_workspace_member(db, context.workspace.id, payload.assigned_member_id)
    if payload.assigned_member_id.strip() and not assignee:
        raise HTTPException(status_code=422, detail="Choose an active member of this workspace.")
    old_status = item.status
    item.status = _normalize_workflow_option(payload.status, WORKFLOW_STATUSES, "status")
    item.priority = _normalize_workflow_option(payload.priority, WORKFLOW_PRIORITIES, "priority")
    item.due_date = _normalize_workflow_due_date(payload.due_date)
    item.assigned_member_id = assignee.id if assignee else ""
    item.closed_at = datetime.utcnow() if item.status == "done" else None
    _record_audit(
        db,
        workspace_id=context.workspace.id,
        actor_member_id=context.member.id,
        action="workflow.item_updated",
        entity_type="workflow_item",
        entity_id=item.id,
        details={"from_status": old_status, "to_status": item.status, "assigned": bool(item.assigned_member_id)},
    )
    db.commit()
    return {"item": _serialize_workflow_item(db, item)}


@app.get("/v1/workflow-items/{item_id}/comments")
def list_workflow_comments(
    item_id: str,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    item = db.scalar(
        select(WorkflowItem).where(WorkflowItem.id == item_id, WorkflowItem.workspace_id == context.workspace.id)
    )
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found.")
    comments = db.scalars(
        select(WorkflowComment)
        .where(WorkflowComment.workspace_id == context.workspace.id, WorkflowComment.workflow_item_id == item.id)
        .order_by(WorkflowComment.created_at.asc())
    ).all()
    member_names = {
        member.id: member.display_name or member.email
        for member in db.scalars(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == context.workspace.id)
        ).all()
    }
    return {
        "comments": [
            {
                "id": comment.id,
                "body": comment.body,
                "author_name": member_names.get(comment.author_member_id, "Former member"),
                "created_at": comment.created_at.isoformat(timespec="seconds"),
            }
            for comment in comments
        ]
    }


@app.post("/v1/workflow-items/{item_id}/comments")
def add_workflow_comment(
    item_id: str,
    payload: WorkflowCommentCreate,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    item = db.scalar(
        select(WorkflowItem).where(WorkflowItem.id == item_id, WorkflowItem.workspace_id == context.workspace.id)
    )
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found.")
    comment = WorkflowComment(
        id=str(uuid.uuid4()),
        workspace_id=context.workspace.id,
        workflow_item_id=item.id,
        author_member_id=context.member.id,
        body=payload.body.strip(),
    )
    db.add(comment)
    _record_audit(
        db,
        workspace_id=context.workspace.id,
        actor_member_id=context.member.id,
        action="workflow.comment_added",
        entity_type="workflow_item",
        entity_id=item.id,
        details={"comment_length": len(comment.body)},
    )
    db.commit()
    return {"ok": True, "comment_id": comment.id}


def _valid_document_signature(content_type: str, content: bytes) -> bool:
    return (
        (content_type == "application/pdf" and content.startswith(b"%PDF-"))
        or (content_type == "image/jpeg" and content.startswith(b"\xff\xd8\xff"))
        or (content_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
    )


@app.get("/v1/documents/status")
def documents_status(context: MemberContext = Depends(_member_dependency)) -> dict[str, object]:
    """Safe capability status; does not reveal bucket credentials or configuration."""
    return document_storage_status()


@app.get("/v1/documents")
def list_workspace_documents(
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    documents = db.scalars(
        select(WorkspaceDocument)
        .where(WorkspaceDocument.workspace_id == context.workspace.id)
        .order_by(WorkspaceDocument.created_at.desc())
        .limit(200)
    ).all()
    return {
        "storage": document_storage_status(),
        "documents": [_serialize_workspace_document(db, document) for document in documents],
    }


@app.post("/v1/documents")
async def upload_workspace_document(
    file: UploadFile = File(...),
    document_type: str = Form(default="other"),
    workflow_item_id: str = Form(default=""),
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Store an allowed PDF/image in private object storage and keep only metadata in SQL."""
    content_type = str(file.content_type or "").lower().strip()
    original_filename = safe_filename(file.filename or "document")
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document is larger than the 15 MB upload limit.")
    if not _valid_document_signature(content_type, content):
        raise HTTPException(status_code=415, detail="The uploaded file does not match an allowed PDF, JPEG or PNG format.")
    workflow_id = str(workflow_item_id or "").strip()
    if workflow_id and not db.scalar(
        select(WorkflowItem).where(WorkflowItem.id == workflow_id, WorkflowItem.workspace_id == context.workspace.id)
    ):
        raise HTTPException(status_code=422, detail="Choose a work item from this workspace.")
    document_id = str(uuid.uuid4())
    digest = hashlib.sha256(content).hexdigest()
    storage_key = f"workspaces/{context.workspace.id}/documents/{document_id}/{digest[:16]}-{original_filename}"
    normalized_type = _normalize_workflow_option(document_type, DOCUMENT_TYPES, "document type")
    put_private_document(storage_key=storage_key, content=content, content_type=content_type)
    document = WorkspaceDocument(
        id=document_id,
        workspace_id=context.workspace.id,
        workflow_item_id=workflow_id,
        uploaded_by_member_id=context.member.id,
        document_type=normalized_type,
        original_filename=original_filename,
        content_type=content_type,
        byte_size=len(content),
        sha256=digest,
        storage_key=storage_key,
    )
    db.add(document)
    _record_audit(
        db,
        workspace_id=context.workspace.id,
        actor_member_id=context.member.id,
        action="document.uploaded",
        entity_type="workspace_document",
        entity_id=document.id,
        details={"document_type": document.document_type, "byte_size": document.byte_size, "workflow_linked": bool(workflow_id)},
    )
    db.commit()
    return {"document": _serialize_workspace_document(db, document)}


@app.get("/v1/documents/{document_id}/download")
def download_workspace_document(
    document_id: str,
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, str | int]:
    document = db.scalar(
        select(WorkspaceDocument).where(
            WorkspaceDocument.id == document_id,
            WorkspaceDocument.workspace_id == context.workspace.id,
        )
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    _record_audit(
        db,
        workspace_id=context.workspace.id,
        actor_member_id=context.member.id,
        action="document.download_link_created",
        entity_type="workspace_document",
        entity_id=document.id,
    )
    db.commit()
    return {"url": signed_document_download(storage_key=document.storage_key, filename=document.original_filename), "expires_in_seconds": 300}


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
    member_names = {
        member.id: member.display_name or member.email
        for member in db.scalars(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == context.workspace.id)
        ).all()
    }
    return {
        "events": [
            {
                "at": event.created_at.isoformat(),
                "action": event.action,
                "actor_member_id": event.actor_member_id,
                "actor_name": member_names.get(event.actor_member_id, "System or former member"),
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "details": json.loads(event.details_json or "{}"),
            }
            for event in events
        ]
    }


@app.get("/v1/team/audit/integrity")
def team_audit_integrity(
    context: MemberContext = Depends(_member_dependency),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Verify the complete workspace audit chain for an accountable control."""
    _require_team_role(context, "owner", "administrator")
    result = _verify_workspace_audit_chain(db, context.workspace.id)
    if not result["ok"]:
        raise HTTPException(
            status_code=409,
            detail="Workspace audit integrity needs investigation before this control can be relied on.",
        )
    return result


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
        "can_manage_in_paypal": bool(workspace.paypal_subscription_id or workspace.ai_advisor_paypal_subscription_id),
        "cancellation_url": "https://www.paypal.com/myaccount/autopay/",
    }


@app.get("/v1/billing/readiness")
def billing_readiness(workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, Any]:
    """Expose only safe capability flags to an authenticated desktop workspace."""
    plan_configured = {plan: bool(plan_id) for plan, plan_id in settings.paypal_plan_ids.items()}
    base_plans_configured = all(plan_configured.get(code) for code in PLAN_CATALOG)
    configured = bool(
        settings.paypal_client_id
        and settings.paypal_client_secret
        and settings.paypal_webhook_id
        and base_plans_configured
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
        "ready": settings.paypal_mode == "live" and configured and credentials_valid and all(plan_ready.get(code) for code in PLAN_CATALOG),
        "ai_advisor_ready": {code: bool(plan_ready.get(code)) for code in AI_ADVISOR_ADDONS},
        "plans": plan_ready,
    }


@app.post("/v1/support/diagnostic")
def support_diagnostic(payload: DiagnosticReport, workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, bool]:
    send_support_diagnostic(workspace=workspace, diagnostic=payload.model_dump())
    return {"ok": True}


@app.post("/v1/billing/checkout-session/{plan_code}")
def create_checkout_session(plan_code: str, workspace: Workspace = Depends(_workspace_dependency)) -> dict[str, str]:
    plan = plan_code.lower().strip()
    if plan not in PLAN_CATALOG and plan not in AI_ADVISOR_ADDONS:
        raise HTTPException(status_code=422, detail="Unknown plan.")
    if not settings.paypal_plan_ids.get(plan) or not settings.paypal_client_id:
        raise HTTPException(status_code=503, detail="PayPal plans are not configured yet.")
    session = sign_checkout_session(workspace.id, plan)
    return {"checkout_url": f"{settings.public_url}/checkout?session={session}"}


@app.get("/v1/billing/checkout-context")
def checkout_context(session: str) -> dict[str, Any]:
    payload = verify_checkout_session(session)
    if not payload:
        raise HTTPException(status_code=400, detail="Checkout session expired. Return to OpsNest and try again.")
    plan_code = str(payload["plan_code"])
    plan_id = settings.paypal_plan_ids.get(plan_code)
    if not plan_id or not settings.paypal_client_id:
        raise HTTPException(status_code=503, detail="PayPal plans are not configured yet.")
    is_ai_addon = plan_code in AI_ADVISOR_ADDONS
    item = ai_advisor_addon_details(plan_code) if is_ai_addon else plan_details(plan_code)
    return {
        "workspace_id": str(payload["workspace_id"]),
        "plan_code": plan_code,
        "plan_id": plan_id,
        "client_id": settings.paypal_client_id,
        "price": PLAN_PRICES[plan_code],
        "plan_name": str(item["name"]),
        "seats": None if is_ai_addon else int(item["seats"]),
        "highlights": list(item["highlights"]),
        "is_addon": is_ai_addon,
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
    is_ai_addon = str(checkout["plan_code"]) in AI_ADVISOR_ADDONS
    if is_ai_addon:
        workspace.ai_advisor_paypal_subscription_id = payload.subscription_id
        workspace.ai_advisor_tier = str(checkout["plan_code"])
        workspace.ai_advisor_status = "active" if str(paypal_subscription.get("status") or "").upper() == "ACTIVE" else "pending"
        workspace.ai_advisor_requests_used = 0
        workspace.ai_advisor_period_started_at = datetime.utcnow()
    else:
        workspace.paypal_subscription_id = payload.subscription_id
        workspace.billing_provider = "paypal"
        workspace.plan_code = str(checkout["plan_code"])
    workspace.last_verified_at = datetime.utcnow()
    if not is_ai_addon and str(paypal_subscription.get("status") or "").upper() == "ACTIVE":
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
    ai_workspace = None if workspace else (
        db.scalar(select(Workspace).where(Workspace.ai_advisor_paypal_subscription_id == subscription_id)) if subscription_id else None
    )
    if ai_workspace:
        if event_type in {"BILLING.SUBSCRIPTION.ACTIVATED", "PAYMENT.SALE.COMPLETED"}:
            ai_workspace.ai_advisor_status = "active"
            if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
                ai_workspace.ai_advisor_requests_used = 0
                ai_workspace.ai_advisor_period_started_at = datetime.utcnow()
        elif event_type in {"BILLING.SUBSCRIPTION.PAYMENT.FAILED", "BILLING.SUBSCRIPTION.SUSPENDED"}:
            ai_workspace.ai_advisor_status = "past_due"
        elif event_type in {"BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.EXPIRED"}:
            ai_workspace.ai_advisor_status = "cancelled"
        ai_workspace.last_verified_at = datetime.utcnow()
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
        """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <meta name=\"theme-color\" content=\"#0c8278\">
  <title>OpsNest | Secure subscription checkout</title>
  <style>
    :root { --ink:#112f36; --muted:#587078; --teal:#0c8278; --teal-dark:#06645d; --mint:#e7f5f0; --line:#d6e5e1; --warm:#fff6df; --paper:#ffffff; }
    * { box-sizing:border-box; }
    body { min-width:320px; margin:0; color:var(--ink); background:radial-gradient(circle at 11% 14%,#fff4d4 0,transparent 30%),radial-gradient(circle at 90% 87%,#d7f2e9 0,transparent 35%),#f5fbf9; font-family:Segoe UI,Arial,sans-serif; }
    .page { width:min(1120px,calc(100% - 40px)); min-height:100vh; margin:0 auto; padding:36px 0 46px; }
    .brand { display:inline-flex; align-items:center; gap:12px; color:inherit; text-decoration:none; }
    .brand img { width:42px; height:42px; object-fit:contain; }
    .brand strong { display:block; font-size:1.32rem; line-height:1; letter-spacing:-.03em; }
    .brand span { display:block; margin-top:4px; color:var(--muted); font-size:.79rem; }
    .back { float:right; margin-top:11px; color:var(--teal-dark); font-size:.9rem; font-weight:700; text-decoration:none; }
    .back:hover { text-decoration:underline; }
    .checkout { display:grid; grid-template-columns:minmax(0,1.04fr) minmax(380px,.96fr); gap:24px; align-items:stretch; margin-top:34px; }
    .intro { position:relative; overflow:hidden; min-height:530px; padding:48px; border:1px solid #cfe8df; border-radius:28px; background:linear-gradient(148deg,#e5f5ef 0%,#fafdff 58%,#fff3d9 100%); }
    .intro::after { position:absolute; right:-90px; bottom:-100px; width:290px; height:290px; border:34px solid rgba(12,130,120,.12); border-radius:50%; content:\"\"; }
    .eyebrow { color:var(--teal-dark); font-size:.78rem; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
    h1 { max-width:560px; margin:12px 0 14px; font-size:clamp(2.3rem,4.2vw,3.55rem); line-height:.98; letter-spacing:-.065em; }
    .lede { max-width:530px; margin:0; color:var(--muted); font-size:1.06rem; line-height:1.58; }
    .summary { position:relative; z-index:1; margin-top:34px; padding:23px; border:1px solid rgba(124,185,171,.52); border-radius:18px; background:rgba(255,255,255,.8); box-shadow:0 13px 30px rgba(15,70,66,.06); }
    .summary-top { display:flex; justify-content:space-between; gap:18px; align-items:flex-start; }
    .summary h2 { margin:5px 0 0; font-size:1.55rem; letter-spacing:-.04em; }
    .price { color:var(--teal-dark); font-size:1.18rem; font-weight:800; text-align:right; white-space:nowrap; }
    .summary ul { display:grid; gap:9px; margin:20px 0 0; padding:0; list-style:none; }
    .summary li { position:relative; padding-left:23px; color:#46666c; font-size:.92rem; line-height:1.4; }
    .summary li::before { position:absolute; left:0; color:var(--teal); content:\"+\"; font-size:1.1rem; font-weight:900; }
    .payment { display:flex; flex-direction:column; padding:42px; border:1px solid var(--line); border-radius:28px; background:var(--paper); box-shadow:0 22px 48px rgba(18,63,61,.12); }
    .secure { display:inline-flex; align-items:center; gap:7px; width:max-content; padding:7px 10px; border-radius:999px; color:var(--teal-dark); background:var(--mint); font-size:.76rem; font-weight:800; }
    .secure::before { content:\"\u2713\"; font-size:.93rem; }
    .payment h2 { margin:18px 0 9px; font-size:2rem; letter-spacing:-.05em; }
    .payment > p { margin:0; color:var(--muted); line-height:1.55; }
    .paypal-area { margin-top:29px; padding:20px; border:1px solid var(--line); border-radius:16px; background:#fbfefd; }
    #paypal-button-container { min-height:88px; }
    .loading { padding:18px 0; color:var(--muted); text-align:center; }
    .status { min-height:22px; margin:17px 0 0; color:var(--muted); font-size:.9rem; line-height:1.45; }
    .status.error { color:#a23535; }
    .success { display:none; margin-top:24px; padding:18px; border:1px solid #9cd7c4; border-radius:15px; color:#175e4f; background:#e7f7ef; line-height:1.5; }
    .success strong { display:block; margin-bottom:4px; font-size:1.05rem; }
    .payment-footer { margin-top:auto; padding-top:24px; color:var(--muted); font-size:.82rem; line-height:1.5; }
    .payment-footer b { color:var(--ink); }
    @media (max-width:820px) { .page { width:min(100% - 28px,620px); padding-top:24px; } .checkout { grid-template-columns:1fr; margin-top:25px; } .intro { min-height:auto; padding:31px 25px; } .payment { padding:31px 25px; } }
    @media (max-width:460px) { .back { float:none; display:block; width:max-content; margin:20px 0 0; } .summary-top { display:block; } .price { margin-top:12px; text-align:left; } .payment h2 { font-size:1.75rem; } }
  </style>
</head>
<body>
  <main class=\"page\">
    <a class=\"brand\" href=\"https://opsnestone.com/\" aria-label=\"OpsNest home\"><img src=\"https://opsnestone.com/assets/opsnest-mark.png\" alt=\"OpsNest logo\"><span><strong>OpsNest</strong><span>Project accounting</span></span></a>
    <a class=\"back\" href=\"https://opsnestone.com/pricing.html\">Back to packages</a>
    <section class=\"checkout\">
      <article class=\"intro\">
        <div class=\"eyebrow\">Your selected package</div>
        <h1>A clear subscription, built for practical work.</h1>
        <p class=\"lede\">Review the monthly package before payment. The owner can manage members, change package or cancel future renewal directly in PayPal.</p>
        <div class=\"summary\" aria-live=\"polite\">
          <div class=\"summary-top\"><div><div class=\"eyebrow\">OpsNest plan</div><h2 id=\"plan-name\">Preparing your package...</h2></div><div id=\"plan-price\" class=\"price\">EUR</div></div>
          <ul id=\"plan-features\"></ul>
        </div>
      </article>
      <article class=\"payment\">
        <div class=\"secure\">PayPal secure checkout</div>
        <h2>Complete payment</h2>
        <p>Choose PayPal or a debit or credit card below. OpsNest never receives or stores your card details.</p>
        <div class=\"paypal-area\"><div id=\"paypal-button-container\"><div class=\"loading\">Preparing secure PayPal checkout...</div></div></div>
        <p id=\"status\" class=\"status\" role=\"status\"></p>
        <div id=\"success\" class=\"success\"><strong>Subscription activated.</strong>Return to OpsNest and choose Refresh in Plans and billing. Your package will be available shortly.</div>
        <p class=\"payment-footer\"><b>Monthly subscription.</b> Your payment is handled by PayPal. You can stop future renewals at any time from your PayPal automatic payments.</p>
      </article>
    </section>
  </main>
  <script>const session='"""
        + safe_session
        + """';
const paypalSdkHost='"""
        + paypal_sdk_host
        + """';
const statusNode=document.getElementById('status');
const setStatus=(message,isError=false)=>{statusNode.textContent=message||'';statusNode.className='status'+(isError?' error':'');};
const renderPlan=(data)=>{document.getElementById('plan-name').textContent=data.plan_name+' plan';document.getElementById('plan-price').textContent=data.price;const features=document.getElementById('plan-features');features.replaceChildren(...(data.highlights||[]).map(item=>{const li=document.createElement('li');li.textContent=item;return li;}));};
const showSuccess=()=>{document.getElementById('paypal-button-container').style.display='none';document.getElementById('success').style.display='block';setStatus('');};
const loadPayPal=(data)=>{const script=document.createElement('script');script.src='https://'+paypalSdkHost+'/sdk/js?client-id='+encodeURIComponent(data.client_id)+'&vault=true&intent=subscription&currency=EUR';script.onload=()=>paypal.Buttons({createSubscription:(details,actions)=>actions.subscription.create({plan_id:data.plan_id}),onApprove:(approval)=>fetch('/v1/billing/record-paypal-approval',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:session,subscription_id:approval.subscriptionID})}).then(response=>response.json()).then(result=>{if(!result.ok)throw Error(result.detail||'We could not verify the subscription yet.');showSuccess();}).catch(error=>setStatus(error.message,true)),onError:()=>setStatus('PayPal could not open the payment form. Please try again or return to OpsNest.',true)}).render('#paypal-button-container');script.onerror=()=>setStatus('PayPal checkout could not be loaded. Check your connection and try again.',true);document.head.appendChild(script);};
fetch('/v1/billing/checkout-context?session='+encodeURIComponent(session)).then(response=>response.json()).then(data=>{if(data.detail)throw Error(data.detail);renderPlan(data);loadPayPal(data);}).catch(error=>setStatus(error.message||'Checkout is unavailable. Return to OpsNest and try again.',true));
  </script>
</body>
</html>"""
    )
