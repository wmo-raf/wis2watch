"""WIS2 Notification Message parsing.

A notification announces that a centre has published data. Its own UUID is what
identifies it across vantage points, which is what makes "published at origin
but never seen on the Global Broker" answerable, and its publication time is
the message's own, never the time we happened to receive it.

Station attribution is deliberately narrow: only the explicit
``wigos_station_identifier`` property attributes a message to a station. The
data identifier and the canonical link frequently embed something that looks
like a station identifier, and reading it back out would invent attributions
this tool exists to report the absence of.
"""

from dataclasses import dataclass
from datetime import datetime

from .timestamps import parse_timestamp

#: The link relation naming the published data itself.
CANONICAL = "canonical"


@dataclass(frozen=True)
class ParsedNotification:
    """A notification message, reduced to what is worth storing."""

    notification_id: str
    publication_time: datetime
    data_id: str
    metadata_id: str
    canonical_link: str
    wigos_station_id: str

    @property
    def is_attributed(self):
        """Whether the message names the station it came from."""
        return bool(self.wigos_station_id)


def station_attribution(payload):
    """The WIGOS station identifier a message declares, or an empty string.

    Absence is reported, never inferred: whole data categories carry no station
    at all, and the rate of unattributed messages is itself a finding.
    """
    if not payload:
        return ""

    declared = (payload.get("properties") or {}).get("wigos_station_identifier")

    return declared.strip() if isinstance(declared, str) else ""


def canonical_link(payload):
    """The canonical link a message advertises, or an empty string.

    Global Caches republish notifications advertising their own copy under
    ``update`` rather than ``canonical``; that is not the publisher's canonical
    link and is not reported as one.
    """
    if not payload:
        return ""

    for link in payload.get("links", []) or []:
        if link.get("rel") == CANONICAL:
            return link.get("href", "") or ""

    return ""


def parse_notification(payload):
    """A notification message, or None when it cannot be identified in time.

    A message with no UUID cannot be de-duplicated or matched across vantage
    points, and one with no usable publication time would have to be stored
    under a time we invented -- which would differ between redeliveries of the
    same notification and defeat that matching. Both are unusable.
    """
    if not payload:
        return None

    notification_id = payload.get("id")
    if not notification_id:
        return None

    properties = payload.get("properties", {}) or {}

    publication_time = parse_timestamp(properties.get("pubtime"))
    if publication_time is None:
        return None

    return ParsedNotification(
        notification_id=notification_id,
        publication_time=publication_time,
        data_id=properties.get("data_id") or "",
        metadata_id=properties.get("metadata_id") or "",
        canonical_link=canonical_link(payload),
        wigos_station_id=station_attribution(payload),
    )
