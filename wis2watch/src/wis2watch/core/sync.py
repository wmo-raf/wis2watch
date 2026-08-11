"""Synchronisation against a node's own endpoints.

Nodes and datasets are no longer read from here: the Global Discovery
Catalogue is the sole writer of the registry, and lives in
:mod:`wis2watch.core.catalogue`. What a node is still asked directly is its
station registry, which no catalogue carries.
"""

import logging

import requests
from django.contrib.gis.geos import Point
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)


def sync_stations(node_id):
    """
    Fetch and sync stations declared by a WIS2 node's own station registry.

    Each station resolves to the canonical Station keyed on its WIGOS station
    identifier, and the node's declaration of it is recorded as provenance.

    Args:
        node_id: ID of the WIS2Node
    """

    from .models import WIS2Node, Station, StationSource, SyncLog

    try:
        node = WIS2Node.objects.get(id=node_id)
        logger.info(f"Starting stations sync for {node.name}")

        # Create sync log
        sync_log = SyncLog.objects.create(
            node=node,
            sync_type=SyncLog.NODE_STATIONS,
            status=SyncLog.FAILED
        )

        # Fetch stations
        response = requests.get(
            node.stations_url,
            timeout=30,
            headers={'Accept': 'application/json'},
            verify=node.verify_ssl,
        )
        response.raise_for_status()

        data = response.json()
        features = data.get('features', [])

        stats = {
            'found': len(features),
            'created': 0,
            'updated': 0,
            'deleted': 0
        }

        for feature in features:
            try:
                properties = feature.get('properties', {})
                geometry = feature.get('geometry', {})

                wigos_id = properties.get('wigos_station_identifier')
                if not wigos_id:
                    logger.warning(f"Station missing WIGOS ID: {feature}")
                    continue

                # Parse coordinates (lon, lat, altitude)
                location = None
                coords = geometry.get('coordinates', [])
                if len(coords) >= 2:
                    lon, lat = coords[0], coords[1]
                    alt = coords[2] if len(coords) >= 3 else 0
                    location = Point(lon, lat, alt, srid=4326)

                defaults = {
                    'name': properties.get('name', ''),
                    'facility_type': properties.get('facility_type', ''),
                    'territory': properties.get('territory_name', ''),
                    'wmo_region': properties.get('wmo_region', ''),
                }

                if location:
                    defaults['location'] = location

                station, _station_created = Station.objects.update_or_create(
                    wigos_id=wigos_id, defaults=defaults
                )

                # Record that this node declares the station. The counts track
                # declarations rather than canonical stations, so a station
                # another node already created still counts as new here.
                _declaration, declared = StationSource.objects.update_or_create(
                    station=station,
                    source_type=StationSource.NODE_REGISTRY,
                    node=node,
                    defaults={
                        'local_name': properties.get('name', ''),
                        'local_id': properties.get('traditional_station_identifier', ''),
                        'raw_json': feature,
                        'last_seen': dj_timezone.now(),
                    }
                )

                if declared:
                    stats['created'] += 1
                    logger.info(f"Node {node.centre_id} now declares station: {wigos_id}")
                else:
                    stats['updated'] += 1
                    logger.info(f"Updated station declaration: {wigos_id}")

            except Exception as e:
                logger.error(f"Error processing station {e}")
                continue

        # Update sync log
        sync_log.status = SyncLog.SUCCESS
        sync_log.items_found = stats['found']
        sync_log.items_created = stats['created']
        sync_log.items_updated = stats['updated']
        sync_log.items_deleted = stats['deleted']
        sync_log.completed_at = dj_timezone.now()
        sync_log.save()

        logger.info(
            f"Stations sync completed for {node.name}: "
            f"Found={stats['found']}, Created={stats['created']}, "
            f"Updated={stats['updated']}"
        )

        return stats, None

    except Exception as e:
        logger.error(f"Error syncing stations for node {node_id}: {e}")

        # Update sync log
        try:
            if 'sync_log' in locals():
                sync_log.status = SyncLog.FAILED
                sync_log.error_message = str(e)
                sync_log.completed_at = dj_timezone.now()
                sync_log.save()
            return None, e
        except Exception as e:
            return None, e


def health_check_nodes():
    """
    Perform health checks on all active nodes.
    """

    from .models import WIS2Node

    nodes = WIS2Node.objects.all()
    results = []

    for node in nodes:
        try:
            # Try to fetch discovery metadata endpoint
            response = requests.get(
                node.discovery_metadata_url,
                timeout=10,
                headers={'Accept': 'application/json'},
                verify=node.verify_ssl,
            )

            if response.status_code == 200:
                node.status = 'active'
                node.last_check = dj_timezone.now()
                node.last_error = ''
                results.append({'node': node.name, 'status': 'healthy'})
            else:
                node.status = 'error'
                node.last_error = f"HTTP {response.status_code}"
                results.append({'node': node.name, 'status': 'unhealthy'})

            node.save()

        except Exception as e:
            node.status = 'error'
            node.last_error = str(e)
            node.last_check = dj_timezone.now()
            node.save()
            results.append({'node': node.name, 'status': 'error', 'error': str(e)})

    logger.info(f"Health check completed for {len(results)} nodes")

    return results
