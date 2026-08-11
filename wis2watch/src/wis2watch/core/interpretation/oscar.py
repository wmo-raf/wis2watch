"""OSCAR/Surface station record extraction.

OSCAR is what a country officially declares, which is one of the three sources
a station can be known from. Its reported operational status is kept as OSCAR
codes it, because the declared-but-silent report is only readable once closed
and unknown stations can be filtered out of it.

The station type is kept verbatim rather than mapped into a facility-type
vocabulary: OSCAR's types do not map one-to-one onto WIGOS facility types, and
the mapping is a decision for whatever stores the station, not for reading.
"""

from dataclasses import dataclass

#: The status code OSCAR reports for a station that is fully operational.
OPERATIONAL = "operational"


@dataclass(frozen=True)
class OscarStation:
    """A station as OSCAR/Surface declares it."""

    wigos_id: str
    other_wigos_ids: tuple[str, ...]
    name: str
    latitude: float | None
    longitude: float | None
    elevation: float | None
    territory: str
    wmo_region: str
    station_type: str
    operating_status: str
    raw: dict

    @property
    def is_operational(self):
        """Whether OSCAR reports the station as fully operational.

        Partly operational, closed and unknown are all not operational: the
        declared-but-silent report would otherwise be swamped by stations
        nobody expects to hear from.
        """
        return self.operating_status == OPERATIONAL


def _wigos_identifiers(record):
    """The station's primary identifier first, then any others it carries."""
    entries = record.get("wigosStationIdentifiers") or []
    declared = [
        entry.get("wigosStationIdentifier")
        for entry in entries
        if entry.get("wigosStationIdentifier")
    ]
    primary = next(
        (
            entry.get("wigosStationIdentifier")
            for entry in entries
            if entry.get("primary") and entry.get("wigosStationIdentifier")
        ),
        record.get("wigosId") or "",
    )

    if not primary:
        return "", ()

    return primary, tuple(identifier for identifier in declared if identifier != primary)


def extract_oscar_station(record):
    """An OSCAR search result as a station, or None when it names no station.

    The WIGOS station identifier is the station's identity, so a record without
    one cannot be resolved against anything and is skipped.
    """
    if not record:
        return None

    wigos_id, other_wigos_ids = _wigos_identifiers(record)
    if not wigos_id:
        return None

    return OscarStation(
        wigos_id=wigos_id,
        other_wigos_ids=other_wigos_ids,
        name=record.get("name") or "",
        latitude=record.get("latitude"),
        longitude=record.get("longitude"),
        elevation=record.get("elevation"),
        territory=record.get("territory") or "",
        wmo_region=record.get("region") or "",
        station_type=record.get("stationTypeName") or "",
        operating_status=record.get("stationStatusCode") or "",
        raw=record,
    )


def extract_oscar_stations(payload):
    """Every station in an OSCAR search response, in the order returned."""
    if not payload:
        return []

    stations = [
        extract_oscar_station(record)
        for record in payload.get("stationSearchResults", []) or []
    ]

    return [station for station in stations if station is not None]
