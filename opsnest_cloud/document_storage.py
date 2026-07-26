"""Private S3-compatible storage boundary for the future Document Inbox.

Only opaque object keys and file metadata belong in Postgres.  File bodies stay
in the configured private bucket and downloads are short-lived signed URLs.
"""

from __future__ import annotations

import re
from typing import Final

from fastapi import HTTPException

from .config import settings


MAX_DOCUMENT_BYTES: Final = 15 * 1024 * 1024
ALLOWED_DOCUMENT_TYPES: Final = {
    "application/pdf",
    "image/jpeg",
    "image/png",
}


def document_storage_status() -> dict[str, object]:
    return {
        "enabled": settings.document_storage_enabled,
        "max_bytes": MAX_DOCUMENT_BYTES,
        "allowed_content_types": sorted(ALLOWED_DOCUMENT_TYPES),
    }


def document_storage_readiness() -> str:
    """Return a safe, non-secret readiness state for the private bucket.

    A configured endpoint is not enough for financial-document retention. The
    service must be able to authenticate against the exact bucket before the
    Workspace describes the inbox as available.
    """
    if not settings.document_storage_enabled:
        return "not_configured"
    try:
        _client().head_bucket(Bucket=settings.document_storage_bucket)
    except HTTPException:
        return "unavailable"
    except Exception:
        return "unavailable"
    return "ready"


def _client():
    if not settings.document_storage_enabled:
        raise HTTPException(
            status_code=503,
            detail="Document Inbox is not enabled yet. Configure the private document-storage bucket first.",
        )
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # deployment installs boto3 from requirements
        raise HTTPException(status_code=503, detail="Document storage dependency is not installed.") from exc
    return boto3.client(
        "s3",
        endpoint_url=settings.document_storage_endpoint,
        region_name=settings.document_storage_region,
        aws_access_key_id=settings.document_storage_access_key,
        aws_secret_access_key=settings.document_storage_secret_key,
        config=Config(signature_version="s3v4"),
    )


def safe_filename(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "document").split("/")[-1].split("\\")[-1])
    return (name.strip(".-") or "document")[:180]


def valid_document_signature(content_type: str, content: bytes) -> bool:
    """Verify bytes, not only the browser-supplied MIME declaration."""
    return (
        (content_type == "application/pdf" and content.startswith(b"%PDF-"))
        or (content_type == "image/jpeg" and content.startswith(b"\xff\xd8\xff"))
        or (content_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
    )


def put_private_document(*, storage_key: str, content: bytes, content_type: str) -> None:
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document is larger than the 15 MB upload limit.")
    if content_type not in ALLOWED_DOCUMENT_TYPES:
        raise HTTPException(status_code=415, detail="Only PDF, JPEG and PNG documents can be uploaded.")
    if not valid_document_signature(content_type, content):
        raise HTTPException(status_code=415, detail="The uploaded file does not match an allowed PDF, JPEG or PNG format.")
    try:
        _client().put_object(
            Bucket=settings.document_storage_bucket,
            Key=storage_key,
            Body=content,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="The document could not be stored securely.") from exc


def signed_document_download(*, storage_key: str, filename: str) -> str:
    try:
        return str(
            _client().generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": settings.document_storage_bucket,
                    "Key": storage_key,
                    "ResponseContentDisposition": f'attachment; filename="{safe_filename(filename)}"',
                },
                ExpiresIn=300,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="A secure document link could not be created.") from exc
