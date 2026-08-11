"""Timestamp reading, shared by every WIS2 source.

Notification messages, discovery metadata and OSCAR all date their records with
ISO 8601 strings, and all of them are read the same way.
"""

from datetime import datetime, timezone


def parse_timestamp(value):
    """An ISO 8601 timestamp as an aware datetime, or None when unusable.

    A timestamp with no zone is read as UTC, which is what WIS2 requires of it.
    A malformed or absent timestamp is reported as absent rather than
    substituted with the current time.
    """
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
