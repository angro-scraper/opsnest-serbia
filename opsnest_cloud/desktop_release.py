"""Public, signed metadata for the current Windows desktop release.

The values are deliberately public: the desktop client still verifies the
SHA-256 digest before it ever starts an installer. Environment variables can
override this manifest after a release, while the checked-in fallback keeps
the update route usable when a hosting dashboard has stale metadata.
"""

from __future__ import annotations

import re


FALLBACK_RELEASE = {
    "latest_version": "2.13.4",
    "installer_url": "https://opsnestone.com/downloads/OpsNest-Setup-2.13.4.exe",
    "installer_sha256": "c7a7a7dfe935c3f6b486eb6b2181cb55cfc0df4862a97ff8750da4b64eaccaac",
}


def current_desktop_release(version: str, installer_url: str, sha256: str) -> dict[str, str]:
    """Prefer complete hosting metadata, otherwise return the signed release manifest."""
    normalized_hash = (sha256 or "").strip().lower()
    normalized_url = (installer_url or "").strip()
    normalized_version = (version or FALLBACK_RELEASE["latest_version"]).strip()
    expected_filename = f"OpsNest-Setup-{normalized_version}.exe"
    # A stale environment URL/hash must never be mixed with a newer version.
    # This can happen while a hosted dashboard applies environment values and
    # a Git-based deployment at slightly different times.
    if (
        normalized_url.startswith("https://")
        and normalized_url.rsplit("/", 1)[-1] == expected_filename
        and re.fullmatch(r"[a-f0-9]{64}", normalized_hash)
    ):
        return {
            "latest_version": normalized_version,
            "installer_url": normalized_url,
            "installer_sha256": normalized_hash,
        }
    return dict(FALLBACK_RELEASE)
