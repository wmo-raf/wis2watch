import logging
from datetime import datetime, timezone

from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone as dj_timezone

from ..core.sync import sync_metadata

logger = logging.getLogger(__name__)

from .service import mqtt_monitoring_service


class NodeNotFoundError(Exception):
    """Raised when a node is not found in the database"""
    pass


class ConnectionError(Exception):
    """Raised when connection to MQTT broker fails"""
    pass


@shared_task(
    bind=True,
    max_retries=3,
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True
)
def start_mqtt_monitoring(self, node_id: int):
    """
    Start MQTT monitoring for a node (Celery task)
    
    Automatically retries on ConnectionError with exponential backoff.
    Does not retry on NodeNotFoundError.
    """
    try:
        result = mqtt_monitoring_service.start_node(node_id)
        
        if not result:
            # Check if it's because node doesn't exist
            from wis2watch.core.models import WIS2Node
            try:
                WIS2Node.objects.get(id=node_id)
                # Node exists but connection failed
                raise ConnectionError(f"Failed to start monitoring for node {node_id}")
            except WIS2Node.DoesNotExist:
                # Node doesn't exist - don't retry
                raise NodeNotFoundError(f"Node {node_id} not found")
        
        logger.info(f"Successfully started monitoring for node {node_id}")
        return result
    
    except NodeNotFoundError as e:
        logger.error(f"Node {node_id} not found, won't retry: {e}")
        # Don't retry for missing nodes
        return False
    
    except ConnectionError as e:
        logger.error(f"Connection failed for node {node_id}: {e}")
        # This will be auto-retried due to autoretry_for
        raise
    
    except Exception as e:
        logger.error(f"Unexpected error starting monitoring for node {node_id}: {e}", exc_info=True)
        # Retry for other unexpected errors
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True)
def stop_mqtt_monitoring(self, node_id: int):
    """Stop MQTT monitoring for a node (Celery task)"""
    try:
        result = mqtt_monitoring_service.stop_node(node_id)
        logger.info(f"Stopped monitoring for node {node_id}: {result}")
        return result
    except Exception as e:
        logger.error(f"Error stopping monitoring for node {node_id}: {e}", exc_info=True)
        return False


@shared_task(bind=True, max_retries=3)
def restart_mqtt_monitoring(self, node_id: int):
    """Restart MQTT monitoring for a node (Celery task)"""
    try:
        result = mqtt_monitoring_service.restart_node(node_id)
        logger.info(f"Restarted monitoring for node {node_id}: {result}")
        return result
    except Exception as e:
        logger.error(f"Error restarting monitoring for node {node_id}: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=60)


@shared_task
def monitor_all_active_nodes():
    """
    Celery beat task to ensure all active nodes are being monitored.
    Run this every 5 minutes.
    """
    from wis2watch.core.models import WIS2Node
    
    logger.info("Checking all active nodes for monitoring status")
    
    try:
        active_nodes = WIS2Node.objects.filter(status='active')
        logger.info(f"Found {active_nodes.count()} active nodes")
        
        currently_monitored = set(mqtt_monitoring_service.get_all_node_ids())
        logger.info(f"Currently monitoring {len(currently_monitored)} nodes")
        
        started_count = 0
        for node in active_nodes:
            if node.id not in currently_monitored:
                logger.info(f"Starting monitoring for unmonitored node {node.id}")
                start_mqtt_monitoring.delay(node.id)
                started_count += 1
        
        if started_count > 0:
            logger.info(f"Started monitoring for {started_count} nodes")
        else:
            logger.info("All active nodes are already being monitored")
    
    except Exception as e:
        logger.error(f"Error in monitor_all_active_nodes: {e}", exc_info=True)


@shared_task
def refresh_mqtt_locks():
    """
    Celery beat task to refresh locks for active connections.
    Run this every 4 minutes (well before the 10-minute lock timeout).
    """
    try:
        logger.debug("Refreshing MQTT locks")
        mqtt_monitoring_service.refresh_all_locks()
    except Exception as e:
        logger.error(f"Error refreshing MQTT locks: {e}", exc_info=True)


@shared_task
def cleanup_stale_mqtt_locks():
    """
    Celery beat task to clean up stale locks and unhealthy clients.
    Run this every 10 minutes.
    """
    try:
        logger.info("Cleaning up stale MQTT locks and unhealthy clients")
        mqtt_monitoring_service.cleanup_stale_locks()
    except Exception as e:
        logger.error(f"Error cleaning up stale locks: {e}", exc_info=True)


@shared_task
def health_check_mqtt_clients():
    """
    Celery beat task to check health of all MQTT clients.
    Run this every 5 minutes.
    """
    try:
        logger.info("Running MQTT client health check")
        health_report = mqtt_monitoring_service.get_health_report()
        
        logger.info(
            f"Health check complete: {health_report['healthy_nodes']}/{health_report['total_nodes']} "
            f"nodes healthy, {health_report['unhealthy_nodes']} unhealthy"
        )
        
        # Log details of unhealthy nodes
        for node_id, node_info in health_report['nodes'].items():
            if not node_info['healthy']:
                stats = node_info['stats']
                logger.warning(
                    f"Unhealthy node {node_id} ({stats['node_name']}): "
                    f"state={stats['state']}, error_count={stats['error_count']}, "
                    f"last_error={stats['last_error']}"
                )
        
        return health_report
    
    except Exception as e:
        logger.error(f"Error in health check: {e}", exc_info=True)
        return None


@shared_task(bind=True, max_retries=3)
def process_mqtt_message(self, node_id: int, topic: str, payload: dict, timestamp: str):
    """
    Process a received MQTT message and store it in the database.
    
    Args:
        node_id: Node ID
        topic: MQTT topic the message was received on
        payload: Parsed JSON message data
        timestamp: ISO format timestamp when message was received
    """
    from wis2watch.core.models import Station, Dataset, StationMQTTMessageLog
    
    try:
        # Extract key fields from message
        message_id = payload.get('id')
        if not message_id:
            logger.warning(f"Message missing ID field from node {node_id}")
            return
        
        properties = payload.get('properties', {})
        links = payload.get('links', [])
        
        # Get station identifier
        wigos_id = properties.get('wigos_station_identifier')
        if not wigos_id:
            logger.warning(f"Message {message_id} missing WIGOS station identifier")
            return
        
        # Get metadata identifier to find dataset
        metadata_id = properties.get('metadata_id')
        if not metadata_id:
            logger.warning(f"Message {message_id} missing metadata_id")
            return
        
        # Find station
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
                logger.info(f"Station {wigos_id} found after metadata sync")
            except Station.DoesNotExist:
                logger.error(
                    f"Station {wigos_id} not found even after metadata sync. "
                    f"Message: {message_id}"
                )
                return
            except Exception as e:
                logger.error(f"Error during metadata sync: {e}", exc_info=True)
                raise self.retry(exc=e, countdown=60)
        except Exception as e:
            logger.error(f"Error retrieving station: {e}", exc_info=True)
            raise self.retry(exc=e, countdown=30)
        
        # Find dataset
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
            raise self.retry(exc=e, countdown=30)
        
        # Parse timestamps with proper timezone handling
        observation_datetime = None
        publish_datetime = None
        
        try:
            dt_str = properties.get('datetime')
            if dt_str:
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                # Ensure timezone is set
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                observation_datetime = dt
        except (ValueError, AttributeError) as e:
            logger.error(f"Error parsing datetime for message {message_id}: {e}")
            return
        
        try:
            pubtime_str = properties.get('pubtime')
            if pubtime_str:
                pt = datetime.fromisoformat(pubtime_str.replace('Z', '+00:00'))
                # Ensure timezone is set
                if pt.tzinfo is None:
                    pt = pt.replace(tzinfo=timezone.utc)
                publish_datetime = pt
            else:
                publish_datetime = dj_timezone.now()
        except (ValueError, AttributeError) as e:
            logger.warning(f"Error parsing pubtime for message {message_id}: {e}")
            publish_datetime = dj_timezone.now()
        
        # Extract canonical link
        canonical_link = ''
        for link in links:
            if link.get('rel') == 'canonical':
                canonical_link = link.get('href', '')
                break
        
        # Create observation record (use transaction for safety)
        with transaction.atomic():
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
        logger.error(f"Error processing message {message_id}: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=120)
