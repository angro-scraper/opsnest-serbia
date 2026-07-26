from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings


engine_options: dict[str, object] = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(240), default="")
    # A workspace starts international and then selects a country pack.  This
    # is intentionally separate from bookkeeping data, which remains local
    # until a country-specific cloud module is enabled.
    country_code: Mapped[str] = mapped_column(String(8), default="INTL", index=True)
    default_currency: Mapped[str] = mapped_column(String(8), default="EUR")
    business_profile: Mapped[str] = mapped_column(String(32), default="general")
    client_token_hash: Mapped[str] = mapped_column(String(64), default="")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    subscription_status: Mapped[str] = mapped_column(String(32), default="verification_pending", index=True)
    plan_code: Mapped[str] = mapped_column(String(32), default="starter")
    # A unique subscription ID must be NULL until PayPal assigns a real one.
    # Using one shared empty string would allow only a single unpaid workspace
    # on databases that enforce the unique constraint.
    paypal_subscription_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    ai_advisor_status: Mapped[str] = mapped_column(String(32), default="disabled", index=True)
    ai_advisor_tier: Mapped[str] = mapped_column(String(32), default="")
    ai_advisor_paypal_subscription_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    ai_advisor_requests_used: Mapped[int] = mapped_column(Integer, default=0)
    ai_advisor_period_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    billing_provider: Mapped[str] = mapped_column(String(32), default="")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailChallenge(Base):
    __tablename__ = "email_challenges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)


class PayPalWebhookEvent(Base):
    __tablename__ = "paypal_webhook_events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    subscription_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)


class WorkspaceMember(Base):
    """A named person who can sign in to one company workspace."""

    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "email", name="uq_workspace_member_email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str] = mapped_column(String(160), default="")
    role: Mapped[str] = mapped_column(String(32), default="operator", index=True)
    status: Mapped[str] = mapped_column(String(32), default="invited", index=True)
    password_hash: Mapped[str] = mapped_column(Text, default="")
    invited_by_member_id: Mapped[str] = mapped_column(String(36), default="")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow)


class TeamInvitation(Base):
    """Single-use e-mail invitation. Only its hash is retained in the database."""

    __tablename__ = "team_invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str] = mapped_column(String(160), default="")
    role: Mapped[str] = mapped_column(String(32), default="operator")
    code_hash: Mapped[str] = mapped_column(String(64))
    invited_by_member_id: Mapped[str] = mapped_column(String(36), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)


class PasswordResetChallenge(Base):
    """Short-lived, single-use recovery code for a named team account."""

    __tablename__ = "password_reset_challenges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    member_id: Mapped[str] = mapped_column(String(36), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)


class MemberSession(Base):
    """Revocable device session, separate from the existing workspace license token."""

    __tablename__ = "member_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    member_id: Mapped[str] = mapped_column(String(36), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_name: Mapped[str] = mapped_column(String(160), default="OpsNest Desktop")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow)


class WorkspaceAuditEvent(Base):
    """Append-only operational audit trail with a per-workspace integrity chain.

    The hash values are intentionally metadata-only.  They make an unexpected
    change, deletion or reordering of an event visible to the control API;
    they do not turn the portal into a statutory archive.
    """

    __tablename__ = "workspace_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    actor_member_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[str] = mapped_column(String(80), default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, index=True)
    previous_hash: Mapped[str] = mapped_column(String(64), default="")
    entry_hash: Mapped[str] = mapped_column(String(64), default="", index=True)


class WorkflowItem(Base):
    """Non-accounting work coordination for owner, accountant and team."""

    __tablename__ = "workflow_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(240))
    workflow_type: Mapped[str] = mapped_column(String(32), default="document", index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal", index=True)
    due_date: Mapped[str] = mapped_column(String(10), default="")
    assigned_member_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    created_by_member_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkflowComment(Base):
    """Short operational comments. Financial documents or credentials do not belong here."""

    __tablename__ = "workflow_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_item_id: Mapped[str] = mapped_column(String(36), index=True)
    author_member_id: Mapped[str] = mapped_column(String(36), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, index=True)


class WorkspaceDocument(Base):
    """Metadata for a private object-storage file; no file body is stored in SQL."""

    __tablename__ = "workspace_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_item_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    uploaded_by_member_id: Mapped[str] = mapped_column(String(36), index=True)
    document_type: Mapped[str] = mapped_column(String(32), default="other", index=True)
    original_filename: Mapped[str] = mapped_column(String(240))
    content_type: Mapped[str] = mapped_column(String(120))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, index=True)


class WorkspaceFinancialOverview(Base):
    """Small aggregate-only finance snapshot for the web command center.

    This table deliberately never stores invoice, vendor, customer, project,
    bank-transaction or attachment rows.  It contains only the numeric totals
    the desktop owner explicitly chooses to synchronize.
    """

    __tablename__ = "workspace_financial_overviews"

    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    horizon_days: Mapped[int] = mapped_column(Integer, default=90)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_by_member_id: Mapped[str] = mapped_column(String(36), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow)


class CountryPackControl(Base):
    """Accountable readiness item for one local country-pack control.

    This register is deliberately a preparation and evidence tracker.  It does
    not assert that a company is tax, e-invoice or fiscalisation compliant.
    The actual statutory connector and local professional validation remain
    separate, country-specific work.
    """

    __tablename__ = "country_pack_controls"
    __table_args__ = (UniqueConstraint("workspace_id", "country_code", "control_key", name="uq_country_pack_control"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    country_code: Mapped[str] = mapped_column(String(8), default="INTL", index=True)
    control_key: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(32), default="not_started", index=True)
    due_date: Mapped[str] = mapped_column(String(10), default="")
    owner_member_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by_member_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkspaceSyncSnapshot(Base):
    """Versioned encrypted-in-transit workspace data supplied by the desktop app."""

    __tablename__ = "workspace_sync_snapshots"

    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_b64: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    updated_by_member_id: Mapped[str] = mapped_column(String(36), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=datetime.utcnow, onupdate=datetime.utcnow)


def create_schema() -> None:
    Base.metadata.create_all(bind=engine)
    # Existing Render databases predate the AI add-on. Keep this migration
    # additive and idempotent until the project adopts a migration framework.
    existing = {column["name"] for column in inspect(engine).get_columns("workspaces")}
    additions = {
        "country_code": "VARCHAR(8) NOT NULL DEFAULT 'INTL'",
        "default_currency": "VARCHAR(8) NOT NULL DEFAULT 'EUR'",
        "business_profile": "VARCHAR(32) NOT NULL DEFAULT 'general'",
        "ai_advisor_status": "VARCHAR(32) NOT NULL DEFAULT 'disabled'",
        "ai_advisor_tier": "VARCHAR(32) NOT NULL DEFAULT ''",
        "ai_advisor_paypal_subscription_id": "VARCHAR(128) NOT NULL DEFAULT ''",
        "ai_advisor_requests_used": "INTEGER NOT NULL DEFAULT 0",
        "ai_advisor_period_started_at": "TIMESTAMP",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE workspaces ADD COLUMN {name} {definition}"))
        # Existing releases used an empty string for a not-yet-assigned PayPal
        # ID. Convert it to NULL so the unique index permits many workspaces
        # that have not subscribed yet.
        connection.execute(text("UPDATE workspaces SET paypal_subscription_id = NULL WHERE paypal_subscription_id = ''"))
        audit_existing = {
            column["name"]
            for column in inspect(engine).get_columns("workspace_audit_events")
        }
        audit_additions = {
            "previous_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
            "entry_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
        }
        for name, definition in audit_additions.items():
            if name not in audit_existing:
                connection.execute(text(f"ALTER TABLE workspace_audit_events ADD COLUMN {name} {definition}"))
        country_control_tables = set(inspect(engine).get_table_names())
        if "country_pack_controls" in country_control_tables:
            country_control_existing = {
                column["name"]
                for column in inspect(engine).get_columns("country_pack_controls")
            }
            if "country_code" not in country_control_existing:
                connection.execute(
                    text("ALTER TABLE country_pack_controls ADD COLUMN country_code VARCHAR(8) NOT NULL DEFAULT 'INTL'")
                )


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
