import logging
from datetime import datetime, timezone

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone as dj_timezone

from ..core.sync import sync_metadata

logger = logging.getLogger(__name__)

from .service import mqtt_monitoring_service


@shared_task(bind=True, max_retries=3)
def start_mqtt_monitoring(self, node_id: int):
    """Start MQTT monitoring for a node (Celery task)"""
    try:
        return mqtt_monitoring_service.start_node(node_id)
    except Exception as e:
        logger.error(f"Failed to start monitoring for node {node_id}: {e}")
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True)
def stop_mqtt_monitoring(self, node_id: int):
    """Stop MQTT monitoring for a node (Celery task)"""
    return mqtt_monitoring_service.stop_node(node_id)


@shared_task(bind=True)
def restart_mqtt_monitoring(self, node_id: int):
    """Restart MQTT monitoring for a node (Celery task)"""
    return mqtt_monitoring_service.restart_node(node_id)


@shared_task
def monitor_all_active_nodes():
    """
    Celery beat task to ensure all active nodes are being monitored.
    Run this every 5 minutes.
    """
    from wis2watch.core.models import WIS2Node
    
    active_nodes = WIS2Node.objects.filter(status='active')
    
    for node in active_nodes:
        lock_key = f"mqtt_node_{node.id}_lock"
        
        # Check if node is already being monitored
        if not cache.get(lock_key):
            logger.info(f"Starting monitoring for node {node.id}")
            start_mqtt_monitoring.delay(node.id)


@shared_task
def refresh_mqtt_locks():
    """
    Celery beat task to refresh locks for active connections.
    Run this every 4 minutes.
    """
    for node_id, client in mqtt_monitoring_service.clients.items():
        if client.is_connected:
            lock_key = f"mqtt_node_{node_id}_lock"
            cache.set(lock_key, "locked", timeout=300)


@shared_task
def cleanup_stale_mqtt_locks():
    """
    Celery beat task to clean up stale locks.
    Run this every 10 minutes.
    """
    mqtt_monitoring_service.cleanup_stale_locks()


@shared_task
def process_mqtt_message(node_id: int, topic: str, payload: dict, timestamp: str):
    """
    Process a received MQTT message and store it in the database.
    
    Args:
        node_id (int): Node ID
        topic: MQTT topic the message was received on
        payload: Parsed JSON message data
    """
    from wis2watch.core.models import Station, Dataset, StationMQTTMessageLog
    
    try:
        # Extract key fields from message
        message_id = payload.get('id')
        properties = payload.get('properties', {})
        links = payload.get('links', [])
        
        # Get station identifier
        wigos_id = properties.get('wigos_station_identifier')
        if not wigos_id:
            logger.warning(f"Message missing WIGOS station identifier: {message_id}")
            return
        
        # Get metadata identifier to find dataset
        metadata_id = properties.get('metadata_id')
        if not metadata_id:
            logger.warning(f"Message missing metadata_id: {message_id}")
            return
        
        # Find station and dataset
        try:
            station = Station.objects.get(wigos_id=wigos_id)
        except Station.DoesNotExist:
            logger.warning(
                f"Station not found for WIGOS ID {wigos_id}. Message: {message_id}. "
                f"Attempting metadata sync..."
            )
            try:
                sync_metadata(node_id)
                station = Station.objects.get(wigos_id=wigos_id)
            except Station.DoesNotExist:
                logger.error(
                    f"Station {wigos_id} not found even after metadata sync. "
                    f"Message: {message_id}"
                )
                return
            except Exception as e:
                logger.error(f"Error during metadata sync: {e}", exc_info=True)
                return
        except Exception as e:
            logger.error(f"Error retrieving station: {e}", exc_info=True)
            return
        
        try:
            dataset = Dataset.objects.get(identifier=metadata_id)
        except Dataset.DoesNotExist:
            logger.warning(
                f"Dataset not found for metadata_id {metadata_id}. "
                f"Message: {message_id}"
            )
            return
        except Exception as e:
            logger.error(f"Error retrieving dataset: {e}", exc_info=True)
            return
        
        # Parse timestamps
        observation_datetime = None
        publish_datetime = None
        
        try:
            dt_str = properties.get('datetime')
            if dt_str:
                observation_datetime = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError) as e:
            logger.error(f"Error parsing datetime: {e}")
            return
        
        try:
            pubtime_str = properties.get('pubtime')
            if pubtime_str:
                publish_datetime = datetime.fromisoformat(pubtime_str).replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError) as e:
            logger.warning(f"Error parsing pubtime: {e}")
            publish_datetime = dj_timezone.now()
        
        # Extract canonical link
        canonical_link = ''
        for link in links:
            if link.get('rel') == 'canonical':
                canonical_link = link.get('href', '')
                break
        
        # Create observation record
        observation, created = StationMQTTMessageLog.objects.get_or_create(
            message_id=message_id,
            station=station,
            defaults={
                'dataset': dataset,
                'data_id': properties.get('data_id', ''),
                'time': observation_datetime,
                'publish_datetime': publish_datetime,
                'canonical_link': canonical_link,
                'raw_json': payload
            }
        )
        
        if created:
            logger.info(
                f"Stored observation: {message_id} from station {wigos_id} "
                f"at {observation_datetime}"
            )
        else:
            logger.debug(f"Duplicate message ignored: {message_id}")
    
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
