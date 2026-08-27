"""Interpretation: raw payloads from WIS2 sources, as normalised structures.

Everything WIS2Watch reads from outside itself -- notification messages from a
broker, discovery metadata from a Global Discovery Catalogue, station records
from OSCAR/Surface -- is interpreted here, by functions that take a payload and
return a structure. No database, no network, no model instances, so the rules
for reading these sources can be tested against real captured payloads and stay
in one place as the ingest around them changes.

The one thing read from outside a payload is the monitored-country list, which
``core.countries`` owns and takes from settings; region membership is
re-exported from there rather than duplicated. That is configuration, not I/O,
and the tests remain offline and database-free.
"""

from ..countries import (
    centre_id_prefix,
    is_monitored_centre_id,
    monitored_country_code_for_centre_id,
)
from .archive import archived_notifications
from .brokers import DEFAULT_PORTS, BrokerConnection, parse_broker_url
from .discovery import (
    DiscoveredDataset,
    DiscoveredNode,
    DiscoveryRecord,
    centre_id_from_identifier,
    extract_discovery_record,
    extract_discovery_records,
)
from .node_stations import NodeStation, extract_node_station, extract_node_stations
from .notifications import (
    ParsedNotification,
    canonical_link,
    parse_notification,
    station_attribution,
)
from .ogcapi import next_page_url
from .oscar import (
    OPERATIONAL,
    OscarStation,
    extract_oscar_station,
    extract_oscar_stations,
)
from .timestamps import parse_timestamp
from .topics import (
    CACHE,
    METADATA,
    ORIGIN,
    ParsedTopic,
    announces_catalogue_record,
    parse_topic,
    subscription_topic,
    sweep_topic,
)

__all__ = [
    "BrokerConnection",
    "CACHE",
    "DEFAULT_PORTS",
    "METADATA",
    "OPERATIONAL",
    "DiscoveredDataset",
    "DiscoveredNode",
    "DiscoveryRecord",
    "NodeStation",
    "ORIGIN",
    "OscarStation",
    "ParsedNotification",
    "ParsedTopic",
    "announces_catalogue_record",
    "archived_notifications",
    "canonical_link",
    "centre_id_from_identifier",
    "centre_id_prefix",
    "extract_discovery_record",
    "extract_discovery_records",
    "extract_node_station",
    "extract_node_stations",
    "extract_oscar_station",
    "extract_oscar_stations",
    "is_monitored_centre_id",
    "monitored_country_code_for_centre_id",
    "next_page_url",
    "parse_broker_url",
    "parse_notification",
    "parse_timestamp",
    "parse_topic",
    "station_attribution",
    "subscription_topic",
    "sweep_topic",
]
