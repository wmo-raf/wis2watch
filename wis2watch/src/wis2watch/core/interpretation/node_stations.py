"""A node's own station registry, as station structures.

What a node's registry declares is the second of the three station pictures:
what the centre itself claims to operate, as opposed to what its country
declares in OSCAR/Surface and what is actually observed transmitting.

The WIGOS station identifier is the station's identity here as everywhere, and
the name and traditional identifier beside it are the operator's own. They are
read out separately rather than folded into the station, because a finding
against a centre has to be readable in that centre's own naming -- which
routinely differs from OSCAR's.

The facility type is kept verbatim: registries publish WIGOS facility types
already, and translating them would be a decision for whatever stores the
station rather than for reading.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeStation:
    """A station as a node's own registry declares it."""

    wigos_id: str
    name: str
    local_id: str
    latitude: float | None
    longitude: float | None
    elevation: float | None
    facility_type: str
    territory: str
    wmo_region: str
    raw: dict


def _position(geometry):
    """The feature's position as ``(longitude, latitude, elevation)``.

    GeoJSON orders coordinates longitude first, and the elevation is optional.
    Anything shorter than a pair places the station nowhere, which is recorded
    as absence rather than guessed at -- a station whose position the registry
    omits is still a station.
    """
    coordinates = (geometry or {}).get("coordinates") or []

    if len(coordinates) < 2:
        return None, None, None

    longitude, latitude = coordinates[0], coordinates[1]
    elevation = coordinates[2] if len(coordinates) > 2 else None

    return longitude, latitude, elevation


def extract_node_station(feature):
    """A registry feature as a station, or None when it names no station.

    A feature without a WIGOS station identifier cannot be resolved against
    OSCAR or against observed traffic, so it is skipped: the whole point of the
    canonical record is that all three sources meet on that identifier.
    """
    if not feature:
        return None

    properties = feature.get("properties") or {}

    wigos_id = (properties.get("wigos_station_identifier") or "").strip()
    if not wigos_id:
        return None

    longitude, latitude, elevation = _position(feature.get("geometry"))

    return NodeStation(
        wigos_id=wigos_id,
        name=properties.get("name") or "",
        local_id=properties.get("traditional_station_identifier") or "",
        latitude=latitude,
        longitude=longitude,
        elevation=elevation,
        facility_type=properties.get("facility_type") or "",
        territory=properties.get("territory_name") or "",
        wmo_region=properties.get("wmo_region") or "",
        raw=feature,
    )


def extract_node_stations(payload):
    """Every station in a registry response, in the order returned."""
    if not payload:
        return []

    stations = [
        extract_node_station(feature) for feature in payload.get("features") or []
    ]

    return [station for station in stations if station is not None]
