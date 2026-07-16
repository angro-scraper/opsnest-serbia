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


def new_member_session_token() -> str:
    """Return a high-entropy, revocable token for one named team member."""
    return secrets.token_urlsafe(48)


def new_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def password_hash(password: str) -> str:
    """Store passwords with salted scrypt; never retain plaintext credentials."""
    value = str(password or "")
    if len(value) < 10:
        raise ValueError("Password must contain at least 10 characters.")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(value.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode("ascii") + "$" + base64.urlsafe_b64encode(derived).decode("ascii")


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, raw_n, raw_r, raw_p, salt_raw, hash_raw = str(stored or "").split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_raw.encode("ascii"))
        candidate = hashlib.scrypt(
            str(password or "").encode("utf-8"),
            salt=salt,
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
        return hmac.compare_digest(candidate, expected)
    except (ValueError, TypeError, UnicodeDecodeError):
        return False


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
