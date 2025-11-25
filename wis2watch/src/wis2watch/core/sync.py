import logging
from datetime import datetime, timezone

import requests
from django.contrib.gis.geos import Point
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)


def sync_discovery_metadata(node_id):
    """
    Fetch and sync discovery metadata for a WIS2 node.
    
    Args:
        node_id: ID of the WIS2Node
    """
    
    from .models import WIS2Node, Dataset, SyncLog
    
    try:
        node = WIS2Node.objects.get(id=node_id)
        logger.info(f"Starting discovery metadata sync for {node.name}")
        
        # Create sync log
        sync_log = SyncLog.objects.create(
            node=node,
            sync_type='discovery_metadata',
            status='failed'  # Will update on success
        )
        
        start_time = dj_timezone.now()
        
        # Fetch discovery metadata
        response = requests.get(
            node.discovery_metadata_url,
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
        
        # Track current identifiers
        current_identifiers = set()
        
        for feature in features:
            try:
                identifier = feature.get('id') or feature.get('properties', {}).get('identifier')
                if not identifier:
                    logger.warning(f"Feature missing identifier: {feature}")
                    continue
                
                current_identifiers.add(identifier)
                
                # Extract relevant fields
                properties = feature.get('properties', {})
                wis2box_data = feature.get('wis2box', {})
                
                # Extract self link
                self_link = ''
                for link in feature.get('links', []):
                    if link.get('rel') == 'self' or link.get('rel') == 'canonical':
                        self_link = link.get('href', '')
                        break
                
                # Extract collection link
                collection_link = ''
                for link in feature.get('links', []):
                    if link.get('rel') == 'collection':
                        collection_link = link.get('href', '')
                        break
                
                # Parse timestamps
                metadata_created = None
                metadata_updated = None
                try:
                    created_at_str = properties.get('created', None)
                    if created_at_str:
                        metadata_created = datetime.fromisoformat(created_at_str).replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError):
                    pass
                
                try:
                    updated_at_str = properties.get('updated', None)
                    if updated_at_str:
                        metadata_updated = datetime.fromisoformat(updated_at_str).replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError):
                    pass
                
                defaults = {
                    'node': node,
                    'title': properties.get('title', ''),
                    'wmo_data_policy': properties.get('wmo:dataPolicy', 'core'),
                    'wmo_topic_hierarchy': properties.get('wmo:topicHierarchy', ''),
                    'self_link': self_link,
                    'collection_link': collection_link,
                    'raw_json': feature,
                    'metadata_created': metadata_created,
                    'metadata_updated': metadata_updated,
                    'last_synced': dj_timezone.now(),
                    'status': 'active'
                }
                
                # Create or update dataset
                dataset, created = Dataset.objects.update_or_create(identifier=identifier, defaults=defaults)
                
                if created:
                    stats['created'] += 1
                    logger.info(f"Created dataset: {identifier}")
                else:
                    stats['updated'] += 1
                    logger.info(f"Updated dataset: {identifier}")
            
            except Exception as e:
                logger.error(f"Error processing feature {e}")
                continue
        
        # Mark datasets not in current fetch as deleted
        deleted_count = Dataset.objects.filter(
            node=node,
            status='active'
        ).exclude(
            identifier__in=current_identifiers
        ).update(
            status='deleted',
            modified=dj_timezone.now()
        )
        
        stats['deleted'] = deleted_count
        
        # Update sync log
        end_time = dj_timezone.now()
        sync_log.status = 'success'
        sync_log.items_found = stats['found']
        sync_log.items_created = stats['created']
        sync_log.items_updated = stats['updated']
        sync_log.items_deleted = stats['deleted']
        sync_log.completed_at = end_time
        sync_log.save()
        
        # Update node status
        node.status = 'active'
        node.last_check = dj_timezone.now()
        node.last_error = ''
        node.save()
        
        logger.info(
            f"Sync completed for {node.name}: "
            f"Found={stats['found']}, Created={stats['created']}, "
            f"Updated={stats['updated']}, Deleted={stats['deleted']}"
        )
        
        return stats, None
    
    except Exception as e:
        logger.error(f"Error syncing discovery metadata for node {node_id}: {e}")
        
        # Update node with error
        try:
            node = WIS2Node.objects.get(id=node_id)
            node.status = 'error'
            node.last_error = str(e)
            node.last_check = dj_timezone.now()
            node.save()
            
            # Update sync log
            if 'sync_log' in locals():
                sync_log.status = 'failed'
                sync_log.error_message = str(e)
                sync_log.completed_at = dj_timezone.now()
                sync_log.save()
            return None, e
        
        except Exception as e:
            logger.error(f"Error updating node status for node {node_id}: {e}")
            return None, e


def sync_stations(node_id):
    """
    Fetch and sync stations for a WIS2 node.
    
    Args:
        node_id: ID of the WIS2Node
    """
    
    from .models import WIS2Node, Station, Dataset, SyncLog
    
    try:
        node = WIS2Node.objects.get(id=node_id)
        logger.info(f"Starting stations sync for {node.name}")
        
        # Create sync log
        sync_log = SyncLog.objects.create(
            node=node,
            sync_type='stations',
            status='failed'
        )
        
        start_time = dj_timezone.now()
        
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
        
        # Track current station IDs
        current_wigos_ids = set()
        
        for feature in features:
            try:
                properties = feature.get('properties', {})
                geometry = feature.get('geometry', {})
                
                wigos_id = properties.get('wigos_station_identifier')
                if not wigos_id:
                    logger.warning(f"Station missing WIGOS ID: {feature}")
                    continue
                
                topics = properties.get('topics', [])
                if not topics:
                    logger.warning(f"Station missing topics: {wigos_id}")
                    continue
                
                current_wigos_ids.add(wigos_id)
                
                # Parse coordinates (lon, lat, altitude)
                coords = geometry.get('coordinates', [])
                if len(coords) >= 2:
                    lon, lat = coords[0], coords[1]
                    alt = coords[2] if len(coords) >= 3 else 0
                    location = Point(lon, lat, alt, srid=4326)
                else:
                    logger.warning(f"Invalid coordinates for station {wigos_id}")
                    continue
                
                defaults = {
                    'name': properties.get('name', ''),
                    'facility_type': properties.get('facility_type', 'landFixed'),
                    'location': location,
                    'raw_json': feature,
                    'last_synced': dj_timezone.now()
                }
                
                # Create or update station
                station, created = Station.objects.update_or_create(wigos_id=wigos_id, defaults=defaults)
                
                # Link station to datasets based on topics
                if topics:
                    matching_datasets = Dataset.objects.filter(
                        node=node,
                        wmo_topic_hierarchy__in=topics,
                        status='active'
                    )
                    station.datasets.set(matching_datasets)
                
                if created:
                    stats['created'] += 1
                    logger.info(f"Created station: {wigos_id}")
                else:
                    stats['updated'] += 1
                    logger.info(f"Updated station: {wigos_id}")
            
            except Exception as e:
                logger.error(f"Error processing station {e}")
                continue
        
        # Mark stations not in current fetch as deleted (optional)
        # For now, we'll just track them but not delete
        
        # Update sync log
        end_time = dj_timezone.now()
        sync_log.status = 'success'
        sync_log.items_found = stats['found']
        sync_log.items_created = stats['created']
        sync_log.items_updated = stats['updated']
        sync_log.items_deleted = stats['deleted']
        sync_log.completed_at = end_time
        sync_log.duration_seconds = (end_time - start_time).total_seconds()
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
                sync_log.status = 'failed'
                sync_log.error_message = str(e)
                sync_log.completed_at = dj_timezone.now()
                sync_log.save()
            return None, e
        except Exception as e:
            return None, e


def sync_metadata(node_id):
    """
    Sync both discovery metadata and stations for a WIS2 node.
    
    Args:
        node_id: ID of the WIS2Node
    """
    stats_metadata, exc_metadata = sync_discovery_metadata(node_id)
    
    if exc_metadata:
        logger.error(f"Discovery metadata sync failed for node {node_id}: {exc_metadata}")
        return None, exc_metadata
    
    stats_stations, exc_stations = sync_stations(node_id)
    
    if exc_stations:
        logger.error(f"Stations sync failed for node {node_id}: {exc_stations}")
        return None, exc_stations
    
    combined_stats = {
        'discovery_metadata': stats_metadata,
        'stations': stats_stations
    }
    
    return combined_stats, None


def sync_all_nodes():
    """
    Trigger synchronization for all active nodes.
    Should be run periodically (e.g., every hour).
    """
    from .models import WIS2Node
    
    active_nodes = WIS2Node.objects.all()
    
    logger.info(f"Starting sync for {active_nodes.count()} active nodes")
    
    for node in active_nodes:
        # Chain the tasks: first sync metadata, then stations
        sync_discovery_metadata(node.id)
        sync_stations(node.id)
    
    logger.info("Sync completed for all active nodes")


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
