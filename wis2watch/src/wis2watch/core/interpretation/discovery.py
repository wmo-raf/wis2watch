"""WCMP2 discovery metadata extraction.

A Global Discovery Catalogue returns one GeoJSON feature per dataset, and each
feature carries the node that publishes it. What the records actually contain
is looser than WCMP2 suggests, so every rule here is a response to real data:

- ``wmo:topicHierarchy`` is absent from most records, and the topic then lives
  in the ``name`` of a notification link, or only in its ``channel`` -- which
  names the cached mirror of the topic rather than the topic itself.
- ``centre-id`` is absent from some records, though the identifier URN
  (``urn:wmo:md:<centre-id>:<rest>``) always names the centre.
- Catalogues append a notification link per Global Broker, each titled as that
  broker's service. The node's own broker is the link that is not one of those,
  and plenty of records advertise no such link at all.
- Some records carry no topic anywhere. Nothing can be monitored from them, so
  they are skipped.
"""

from dataclasses import dataclass
from datetime import datetime

from ..countries import monitored_country_code_for_centre_id
from .brokers import BrokerConnection, parse_broker_url
from .timestamps import parse_timestamp
from .topics import parse_topic

#: The prefix every WIS2 metadata identifier carries: ``urn:wmo:md:<centre>:``.
IDENTIFIER_PREFIX = ("urn", "wmo", "md")

#: How a catalogue titles the notification links it adds for Global Brokers.
GLOBAL_BROKER_MARKER = "global broker"


@dataclass(frozen=True)
class DiscoveredNode:
    """The publishing centre a discovery record belongs to."""

    centre_id: str
    country: str

    @property
    def is_monitored(self):
        """Whether the centre belongs to the monitored region."""
        return bool(self.country)


@dataclass(frozen=True)
class DiscoveredDataset:
    """A dataset a node claims to publish."""

    identifier: str
    title: str
    data_policy: str
    topic: str
    canonical_link: str
    metadata_created: datetime | None
    metadata_updated: datetime | None
    raw: dict


@dataclass(frozen=True)
class DiscoveryRecord:
    """One usable discovery metadata record."""

    node: DiscoveredNode
    dataset: DiscoveredDataset
    origin_broker: BrokerConnection | None


def centre_id_from_identifier(identifier):
    """The centre a metadata identifier names, or an empty string.

    Identifiers in the wild sometimes repeat the URN prefix
    (``urn:wmo:md:gh-gmet:urn:wmo:md:gh-gmet:...``); the centre is always the
    fourth token, so the repetition is harmless.
    """
    if not identifier:
        return ""

    tokens = identifier.strip().lower().split(":")

    if len(tokens) < 4 or tuple(tokens[:3]) != IDENTIFIER_PREFIX:
        return ""

    return tokens[3]


def _broker_links(feature):
    return [
        link
        for link in feature.get("links", []) or []
        if (link.get("href") or "").lower().startswith("mqtt")
    ]


def _origin_broker_link(feature):
    """The link advertising the node's own broker, or None.

    Everything titled as a Global Broker service is the catalogue's own
    addition rather than something the node declared.
    """
    for link in _broker_links(feature):
        if GLOBAL_BROKER_MARKER not in (link.get("title") or "").lower():
            return link

    return None


def _topic(feature):
    """The origin topic the record publishes on, or an empty string."""
    declared = (feature.get("properties", {}) or {}).get("wmo:topicHierarchy")
    parsed = parse_topic(declared)

    if parsed:
        return parsed.as_origin().raw

    for link in _broker_links(feature):
        for candidate in (link.get("name"), link.get("channel")):
            parsed = parse_topic(candidate)
            if parsed:
                return parsed.as_origin().raw

    return ""


def _link_href(feature, rel):
    for link in feature.get("links", []) or []:
        if link.get("rel") == rel:
            return link.get("href", "") or ""

    return ""


def extract_discovery_record(feature):
    """A discovery metadata feature as a node and dataset, or None to skip it.

    A record is skipped when it names no dataset, no centre, or no topic --
    each of which leaves nothing that could be monitored.
    """
    if not feature:
        return None

    properties = feature.get("properties", {}) or {}

    identifier = properties.get("identifier") or feature.get("id") or ""
    if not identifier:
        return None

    centre_id = (properties.get("centre-id") or "").strip().lower()
    centre_id = centre_id or centre_id_from_identifier(identifier)
    if not centre_id:
        return None

    topic = _topic(feature)
    if not topic:
        return None

    broker_link = _origin_broker_link(feature)

    return DiscoveryRecord(
        node=DiscoveredNode(
            centre_id=centre_id,
            country=monitored_country_code_for_centre_id(centre_id),
        ),
        dataset=DiscoveredDataset(
            identifier=identifier,
            title=properties.get("title") or "",
            data_policy=properties.get("wmo:dataPolicy") or "",
            topic=topic,
            canonical_link=_link_href(feature, "canonical"),
            metadata_created=parse_timestamp(properties.get("created")),
            metadata_updated=parse_timestamp(properties.get("updated")),
            raw=feature,
        ),
        origin_broker=parse_broker_url(broker_link.get("href")) if broker_link else None,
    )


def extract_discovery_records(payload):
    """Every usable record in a catalogue response, in the order returned."""
    if not payload:
        return []

    records = [extract_discovery_record(f) for f in payload.get("features", []) or []]

    return [record for record in records if record is not None]
