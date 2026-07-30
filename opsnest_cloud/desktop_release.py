"""Public, signed metadata for the current Windows desktop release.

The values are deliberately public: the desktop client still verifies the
SHA-256 digest before it ever starts an installer. Environment variables can
override this manifest after a release, while the checked-in fallback keeps
the update route usable when a hosting dashboard has stale metadata.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


FALLBACK_RELEASE = {
    "latest_version": "2.13.12",
    "installer_url": "https://opsnestone.com/downloads/OpsNest-Setup-2.13.12.exe",
    "installer_sha256": "a08b48b147b87a53f33eda353174d081458f49004c44db2f3a37946215d34771",
}
_RELEASE_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_TRUSTED_DOWNLOAD_HOSTS = {"opsnestone.com", "www.opsnestone.com"}


def _version_key(value: str) -> tuple[int, int, int] | None:
    """Return a sortable release version or reject ambiguous dashboard values."""
    if not _RELEASE_VERSION.fullmatch(value):
        return None
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _is_trusted_installer_url(value: str, expected_filename: str) -> bool:
    """Allow only the public OpsNest download endpoint for automatic updates."""
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in _TRUSTED_DOWNLOAD_HOSTS
        and parsed.path == f"/downloads/{expected_filename}"
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def current_desktop_release(version: str, installer_url: str, sha256: str) -> dict[str, str]:
    """Prefer complete hosting metadata, otherwise return the signed release manifest."""
    normalized_hash = (sha256 or "").strip().lower()
    normalized_url = (installer_url or "").strip()
    normalized_version = (version or FALLBACK_RELEASE["latest_version"]).strip()
    expected_filename = f"OpsNest-Setup-{normalized_version}.exe"
    fallback_version = _version_key(FALLBACK_RELEASE["latest_version"])
    requested_version = _version_key(normalized_version)
    # A stale environment URL/hash must never be mixed with a newer version.
    # This can happen while a hosted dashboard applies environment values and
    # a Git-based deployment at slightly different times.
    if (
        requested_version is not None
        and fallback_version is not None
        and requested_version >= fallback_version
        and _is_trusted_installer_url(normalized_url, expected_filename)
        and re.fullmatch(r"[a-f0-9]{64}", normalized_hash)
    ):
        return {
            "latest_version": normalized_version,
            "installer_url": normalized_url,
            "installer_sha256": normalized_hash,
        }
    return dict(FALLBACK_RELEASE)
