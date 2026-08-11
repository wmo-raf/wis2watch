import csv


def node_stations_as_csv(node, output_file):
    """
    Write the stations a node's own registry declares, as CSV.

    Args:
        node (WIS2Node): the node whose declared stations to export
        output_file (file-like): file-like object to write CSV data to
    """
    from .models import StationSource

    writer = csv.writer(output_file)

    header = [
        "station_name",
        "wigos_station_identifier",
        "traditional_station_identifier",
        "facility_type",
        "latitude",
        "longitude",
        "elevation",
        "barometer_height",
        "territory_name",
        "wmo_region"
    ]
    writer.writerow(header)

    for declaration in StationSource.objects.declared_by_node_registry(node):
        station = declaration.station
        location = station.location
        properties = (declaration.raw_json or {}).get("properties", {})

        row = [
            (declaration.local_name or station.name).replace(",", ""),
            station.wigos_id,
            declaration.local_id,
            station.facility_type,
            location.y if location else "",  # latitude
            location.x if location else "",  # longitude
            location.z if location else "",  # elevation
            properties.get("barometer_height", ""),
            station.territory,
            station.wmo_region,
        ]

        writer.writerow(row)
