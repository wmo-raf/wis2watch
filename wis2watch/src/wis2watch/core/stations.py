def dataset_stations_as_csv(dataset, output_file):
    """
    Convert a dataset of stations to CSV format.

    Args:
        dataset (Dataset): Dataset object
        output_file file-like: File-like object to write CSV data to

    Returns:
        str: CSV formatted string of stations
    """
    import csv
    
    writer = csv.writer(output_file)
    
    # Write header
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
    
    stations = dataset.stations.all()
    
    s = {
        "id": "0-566-0-65281",
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [8.11368, 6.32485, 44.0]},
        "properties": {
            "id": "0-566-0-65281",
            "url": "https://oscar.wmo.int/surface/#/search/station/stationReportDetails/0-566-0-65281",
            "name": "ABAKALIKI",
            "topic": "origin/a/wis2/ng-nimet/data/core/weather/surface-based-observations/synop",
            "status": "operational",
            "topics": ["origin/a/wis2/ng-nimet/data/core/weather/surface-based-observations/synop"],
            "wmo_region": "africa", "facility_type": "landFixed", "territory_name": "NGA",
            "barometer_height": 91.0, "wigos_station_identifier": "0-566-0-65281",
            "traditional_station_identifier": ""
        }
    }
    
    # Write station data
    for station in stations:
        raw_json = station.raw_json
        properties = raw_json.get("properties", {})
        geometry = raw_json.get("geometry", {})
        coordinates = geometry.get("coordinates", None)
        
        if not coordinates:
            continue
        
        row = [
            properties.get("name", "").replace(",", ""),
            properties.get("wigos_station_identifier", ""),
            properties.get("traditional_station_identifier", ""),
            properties.get("facility_type", ""),
            coordinates[1],  # latitude
            coordinates[0],  # longitude
            coordinates[2],  # elevation
            properties.get("barometer_height", ""),
            properties.get("territory_name", ""),
            properties.get("wmo_region", "")
        ]
        
        writer.writerow(row)
