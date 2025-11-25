import logging
from datetime import datetime, timezone

from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone as dj_timezone

from ..core.models import StationMQTTMessageLog, Station, Dataset
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
    Checks GLOBAL state (Redis Locks) to prevent duplicate tasks across workers.
    Run this every 5 minutes.
    """
    from wis2watch.core.models import WIS2Node
    
    logger.info("Checking all active nodes for monitoring status")
    
    try:
        active_nodes = WIS2Node.objects.filter(status='active')
        logger.info(f"Found {active_nodes.count()} active nodes")
        
        started_count = 0
        for node in active_nodes:
            # Check Global Lock in Redis
            # Make sure this key format matches _get_lock_key in service.py exactly!
            lock_key = node.lock_key
            
            if cache.get(lock_key):
                # Lock exists -> Someone is already monitoring this. Do nothing.
                continue
            
            # No lock -> Node is truly unmonitored. Start it.
            logger.info(f"No global lock found for node {node.id}. Queueing start task.")
            start_mqtt_monitoring.delay(node.id)
            started_count += 1
        
        if started_count > 0:
            logger.info(f"Started monitoring for {started_count} nodes")
        else:
            logger.info("All active nodes are already being monitored (locks present)")
    
    except Exception as e:
        logger.error(f"Error in monitor_all_active_nodes: {e}", exc_info=True)


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


def _prepare_observation_record(node_id: int, payload: dict) -> StationMQTTMessageLog | None:
    """
    Helper function to parse payload and prepare a StationMQTTMessageLog instance.
    Returns None if validation fails or required objects (Station/Dataset) are missing.
    Does NOT save the record to the database.
    """
    # [cite_start]1. Extract IDs [cite: 865, 866, 867]
    message_id = payload.get('id')
    properties = payload.get('properties', {})
    wigos_id = properties.get('wigos_station_identifier')
    metadata_id = properties.get('metadata_id')
    
    if not message_id or not wigos_id or not metadata_id:
        logger.warning(
            f"Message missing required fields (ID: {message_id}, WIGOS: {wigos_id}, Metadata: {metadata_id})")
        return None
    
    # [cite_start]2. Find Station (with Sync Fallback) [cite: 868-872]
    try:
        station = Station.objects.get(wigos_id=wigos_id)
    except Station.DoesNotExist:
        # Attempt metadata sync if station missing
        try:
            logger.info(f"Station {wigos_id} missing. Triggering sync for node {node_id}...")
            sync_metadata(node_id)
            station = Station.objects.get(wigos_id=wigos_id)
        except Station.DoesNotExist:
            logger.error(f"Station {wigos_id} not found even after metadata sync.")
            return None
        except Exception as e:
            logger.error(f"Error during metadata sync resolution: {e}")
            raise e  # Let the caller handle retry logic
    
    # [cite_start]3. Find Dataset [cite: 872-873]
    try:
        dataset = Dataset.objects.get(identifier=metadata_id)
    except Dataset.DoesNotExist:
        logger.warning(f"Dataset not found for metadata_id {metadata_id}")
        return None
    
    # [cite_start]4. Parse Timestamps [cite: 874-878]
    observation_datetime = None
    publish_datetime = dj_timezone.now()
    
    try:
        if dt_str := properties.get('datetime'):
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            observation_datetime = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        
        if pubtime_str := properties.get('pubtime'):
            pt = datetime.fromisoformat(pubtime_str.replace('Z', '+00:00'))
            publish_datetime = pt if pt.tzinfo else pt.replace(tzinfo=timezone.utc)
    except ValueError as e:
        logger.warning(f"Error parsing timestamps for message {message_id}: {e}")
        # Continue with defaults if possible, or return None if critical
    
    # [cite_start]5. Extract Link [cite: 879]
    links = payload.get('links', [])
    canonical_link = next((link.get('href', '') for link in links if link.get('rel') == 'canonical'), '')
    
    # [cite_start]6. Instantiate Object (Unsaved) [cite: 880]
    return StationMQTTMessageLog(
        station=station,
        dataset=dataset,
        message_id=message_id,
        data_id=properties.get('data_id', ''),
        time=observation_datetime,
        publish_datetime=publish_datetime,
        canonical_link=canonical_link,
        raw_json=payload
    )


@shared_task(bind=True, max_retries=3)
def process_mqtt_message(self, node_id: int, topic: str, payload: dict, timestamp: str):
    """
    Process a single MQTT message.
    """
    try:
        record = _prepare_observation_record(node_id, payload)
        
        if record:
            # [cite_start]Atomic get_or_create logic to prevent duplicates [cite: 880]
            # Since _prepare returns an instance, we use its attributes for the lookup
            StationMQTTMessageLog.objects.get_or_create(
                message_id=record.message_id,
                station=record.station,
                defaults={
                    'dataset': record.dataset,
                    'data_id': record.data_id,
                    'time': record.time,
                    'publish_datetime': record.publish_datetime,
                    'canonical_link': record.canonical_link,
                    'raw_json': record.raw_json
                }
            )
            logger.info(f"Stored observation: {record.message_id}")
    
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=30)


@shared_task(bind=True, max_retries=3)
def process_mqtt_message_batch(self, batch_data: list):
    """
    Process a batch of MQTT messages in a single transaction.
    Args:
        batch_data: List of dicts, each containing:
                    {'node_id': int, 'topic': str, 'payload': dict, 'timestamp': str}
    """
    records_to_create = []
    
    try:
        # 1. Prepare all records in memory
        for item in batch_data:
            try:
                record = _prepare_observation_record(item['node_id'], item['payload'])
                if record:
                    records_to_create.append(record)
            except Exception as e:
                # Log individual failures but don't fail the whole batch
                logger.error(f"Failed to prepare record in batch: {e}")
        
        # 2. Bulk insert
        if records_to_create:
            with transaction.atomic():
                # ignore_conflicts=True handles duplicate message_ids gracefully
                created = StationMQTTMessageLog.objects.bulk_create(
                    records_to_create,
                    ignore_conflicts=True
                )
                logger.info(f"Batch processed: {len(created)} records created out of {len(batch_data)} received.")
    
    except Exception as e:
        logger.error(f"Critical error processing batch: {e}", exc_info=True)
        # We retry the batch on critical DB errors, though this might re-process good items
        # ignore_conflicts=True protects us from duplicates during retry
        raise self.retry(exc=e, countdown=60)
