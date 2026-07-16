from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from .config import settings


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_client_token() -> str:
    return secrets.token_urlsafe(48)


def new_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def sign_checkout_session(workspace_id: str, plan_code: str, *, expires_in_minutes: int = 20) -> str:
    payload = {
        "workspace_id": workspace_id,
        "plan_code": plan_code,
        "expires_at": int((datetime.utcnow() + timedelta(minutes=expires_in_minutes)).timestamp()),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(settings.signing_secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") + "." + base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def verify_checkout_session(value: str) -> dict[str, Any] | None:
    try:
        raw_part, signature_part = value.split(".", 1)
        raw = base64.urlsafe_b64decode(raw_part + "=" * (-len(raw_part) % 4))
        provided = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
        expected = hmac.new(settings.signing_secret.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(provided, expected):
            return None
        payload = json.loads(raw.decode("utf-8"))
        if int(payload.get("expires_at") or 0) < int(datetime.utcnow().timestamp()):
            return None
        if not payload.get("workspace_id") or not payload.get("plan_code"):
            return None
        return payload
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
