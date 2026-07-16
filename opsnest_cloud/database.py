from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from sqlalchemy import DateTime, Integer, String, Text, create_engine
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
    client_token_hash: Mapped[str] = mapped_column(String(64), default="")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    subscription_status: Mapped[str] = mapped_column(String(32), default="verification_pending", index=True)
    plan_code: Mapped[str] = mapped_column(String(32), default="starter")
    paypal_subscription_id: Mapped[str] = mapped_column(String(128), unique=True, default="")
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


def create_schema() -> None:
    Base.metadata.create_all(bind=engine)


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
