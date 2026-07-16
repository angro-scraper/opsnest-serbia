"""Public, signed metadata for the current Windows desktop release.

The values are deliberately public: the desktop client still verifies the
SHA-256 digest before it ever starts an installer. Environment variables can
override this manifest after a release, while the checked-in fallback keeps
the update route usable when a hosting dashboard has stale metadata.
"""

from __future__ import annotations

import re


FALLBACK_RELEASE = {
    "latest_version": "2.8.10",
    "installer_url": "https://github.com/angro-scraper/opsnest-serbia/releases/download/v2.8.10/OpsNest-Setup-2.8.10.exe",
    "installer_sha256": "cc1dd6e1ae2c5e9f662e1b16e47224e3dcf42755ac83633064729a47d5d5c1a1",
}


def current_desktop_release(version: str, installer_url: str, sha256: str) -> dict[str, str]:
    """Prefer complete hosting metadata, otherwise return the signed release manifest."""
    normalized_hash = (sha256 or "").strip().lower()
    normalized_url = (installer_url or "").strip()
    if normalized_url.startswith("https://") and re.fullmatch(r"[a-f0-9]{64}", normalized_hash):
        return {
            "latest_version": (version or FALLBACK_RELEASE["latest_version"]).strip(),
            "installer_url": normalized_url,
            "installer_sha256": normalized_hash,
        }
    return dict(FALLBACK_RELEASE)
