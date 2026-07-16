from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


def _value(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    public_url: str
    signing_secret: str
    allowed_origins: tuple[str, ...]
    paypal_mode: str
    paypal_client_id: str
    paypal_client_secret: str
    paypal_webhook_id: str
    paypal_plan_starter: str
    paypal_plan_business: str
    paypal_plan_pro: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from_email: str
    smtp_from_name: str
    turnstile_site_key: str
    turnstile_secret_key: str

    @classmethod
    def from_env(cls) -> "Settings":
        origins = tuple(value.strip() for value in _value("CORS_ORIGINS").split(",") if value.strip())
        database_url = _value("DATABASE_URL", "sqlite:///./opsnest_cloud.db")
        if database_url.startswith("postgres://"):
            database_url = "postgresql+psycopg://" + database_url.removeprefix("postgres://")
        elif database_url.startswith("postgresql://"):
            database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
        return cls(
            app_env=_value("APP_ENV", "development").lower(),
            database_url=database_url,
            public_url=_value("APP_PUBLIC_URL", "http://localhost:8000").rstrip("/"),
            signing_secret=_value("APP_SIGNING_SECRET", "development-only-change-me"),
            allowed_origins=origins,
            paypal_mode=_value("PAYPAL_MODE", "sandbox").lower(),
            paypal_client_id=_value("PAYPAL_CLIENT_ID"),
            paypal_client_secret=_value("PAYPAL_CLIENT_SECRET"),
            paypal_webhook_id=_value("PAYPAL_WEBHOOK_ID"),
            paypal_plan_starter=_value("PAYPAL_PLAN_STARTER"),
            paypal_plan_business=_value("PAYPAL_PLAN_BUSINESS"),
            paypal_plan_pro=_value("PAYPAL_PLAN_PRO"),
            smtp_host=_value("SMTP_HOST"),
            smtp_port=int(_value("SMTP_PORT", "587") or 587),
            smtp_username=_value("SMTP_USERNAME"),
            smtp_password=_value("SMTP_PASSWORD"),
            smtp_from_email=_value("SMTP_FROM_EMAIL"),
            smtp_from_name=_value("SMTP_FROM_NAME", "OpsNest"),
            turnstile_site_key=_value("TURNSTILE_SITE_KEY"),
            turnstile_secret_key=_value("TURNSTILE_SECRET_KEY"),
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def paypal_api_base(self) -> str:
        return "https://api-m.paypal.com" if self.paypal_mode == "live" else "https://api-m.sandbox.paypal.com"

    @property
    def paypal_plan_ids(self) -> dict[str, str]:
        return {
            "starter": self.paypal_plan_starter,
            "business": self.paypal_plan_business,
            "pro": self.paypal_plan_pro,
        }

    def validate_production(self) -> None:
        if not self.is_production:
            return
        public_url = urlparse(self.public_url)
        database_is_postgres = self.database_url.startswith("postgresql+psycopg://")
        required = {
            "DATABASE_URL": self.database_url if database_is_postgres else "",
            "APP_SIGNING_SECRET": self.signing_secret if self.signing_secret != "development-only-change-me" else "",
            "APP_PUBLIC_URL": self.public_url if public_url.scheme == "https" and public_url.netloc else "",
            "PAYPAL_MODE=live": self.paypal_mode if self.paypal_mode == "live" else "",
            "PAYPAL_CLIENT_ID": self.paypal_client_id,
            "PAYPAL_CLIENT_SECRET": self.paypal_client_secret,
            "PAYPAL_WEBHOOK_ID": self.paypal_webhook_id,
            "PAYPAL_PLAN_STARTER": self.paypal_plan_starter,
            "PAYPAL_PLAN_BUSINESS": self.paypal_plan_business,
            "PAYPAL_PLAN_PRO": self.paypal_plan_pro,
            "SMTP_HOST": self.smtp_host,
            "SMTP_FROM_EMAIL": self.smtp_from_email,
            "TURNSTILE_SITE_KEY": self.turnstile_site_key,
            "TURNSTILE_SECRET_KEY": self.turnstile_secret_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError("Missing production environment variables: " + ", ".join(missing))


settings = Settings.from_env()
