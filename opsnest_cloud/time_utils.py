"""UTC helpers that retain the existing naive-UTC database contract.

The cloud schema stores timestamps without a timezone. Returning a naive UTC
value keeps comparisons with existing rows correct while avoiding Python's
deprecated ``datetime.utcnow()`` API.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return a naive UTC value for existing SQL timestamp columns."""
    return datetime.now(UTC).replace(tzinfo=None)
