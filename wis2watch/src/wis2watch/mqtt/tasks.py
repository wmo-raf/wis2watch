import logging
from datetime import datetime, timezone

from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone as dj_timezone

from ..core.models import Dataset, NotificationMessage, Station

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
    from wis2watch.core.models import MessageSource, WIS2Node

    logger.info("Checking all active nodes for monitoring status")

    try:
        # Only nodes with a reachable-in-principle origin broker can be monitored
        active_nodes = WIS2Node.objects.filter(
            message_sources__source_type=MessageSource.ORIGIN_BROKER,
            message_sources__is_active=True,
        ).distinct()
        logger.info(f"Found {active_nodes.count()} nodes")
        
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


def _parse_datetime(value: str | None):
    """Parse a WIS2 timestamp, returning None when it is absent or malformed."""
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _prepare_notification_message(item: dict) -> NotificationMessage | None:
    """
    Parse a received payload into an unsaved NotificationMessage.

    Only the notification's own UUID is required. An unknown topic, an unknown
    dataset and a missing station identifier are all recorded rather than
    rejected -- unknown-topic traffic is a finding, not noise.

    Args:
        item: {'node_id': int, 'source_id': int, 'topic': str,
               'payload': dict, 'timestamp': str}
    """
    payload = item['payload']
    properties = payload.get('properties', {})

    notification_id = payload.get('id')
    if not notification_id:
        logger.warning("Discarding message with no notification UUID")
        return None

    # The publication time partitions the hypertable and takes part in the
    # per-source uniqueness of a notification, so it has to be a property of
    # the notification itself. A receipt-time fallback would differ between
    # redeliveries of the same notification and defeat that uniqueness, so a
    # message with no usable pubtime is discarded rather than stored under a
    # time we invented.
    publication_time = _parse_datetime(properties.get('pubtime'))
    if publication_time is None:
        logger.warning(f"Discarding message {notification_id}: no usable pubtime")
        return None

    metadata_id = properties.get('metadata_id', '') or ''
    dataset = Dataset.objects.filter(identifier=metadata_id).first() if metadata_id else None

    # Station attribution comes only from the message's own WIGOS station
    # identifier. A message without one is unattributed, never guessed at.
    wigos_station_id = properties.get('wigos_station_identifier', '') or ''
    station = Station.objects.filter(wigos_id=wigos_station_id).first() if wigos_station_id else None

    links = payload.get('links', [])
    canonical_link = next((link.get('href', '') for link in links if link.get('rel') == 'canonical'), '')

    return NotificationMessage(
        source_id=item['source_id'],
        node_id=item.get('node_id'),
        dataset=dataset,
        station=station,
        notification_id=notification_id,
        topic=item.get('topic', ''),
        wigos_station_id=wigos_station_id,
        data_id=properties.get('data_id', '') or '',
        metadata_id=metadata_id,
        time=publication_time,
        canonical_link=canonical_link,
        raw_json=payload
    )


@shared_task(bind=True, max_retries=3)
def process_mqtt_message(self, node_id: int, source_id: int, topic: str, payload: dict, timestamp: str):
    """
    Process a single MQTT message.
    """
    try:
        record = _prepare_notification_message({
            'node_id': node_id,
            'source_id': source_id,
            'topic': topic,
            'payload': payload,
            'timestamp': timestamp,
        })

        if record:
            # The unique constraint on (source, notification UUID, time) makes
            # a redelivered notification a no-op rather than a duplicate row.
            NotificationMessage.objects.bulk_create([record], ignore_conflicts=True)
            logger.info(f"Stored notification message: {record.notification_id}")

    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=30)


@shared_task(bind=True, max_retries=3)
def process_mqtt_message_batch(self, batch_data: list):
    """
    Process a batch of MQTT messages in a single transaction.
    Args:
        batch_data: List of dicts, each containing:
                    {'node_id': int, 'source_id': int, 'topic': str,
                     'payload': dict, 'timestamp': str}
    """
    records_to_create = []

    try:
        # 1. Prepare all records in memory
        for item in batch_data:
            try:
                record = _prepare_notification_message(item)
                if record:
                    records_to_create.append(record)
            except Exception as e:
                # Log individual failures but don't fail the whole batch
                logger.error(f"Failed to prepare record in batch: {e}")

        # 2. Bulk insert
        if records_to_create:
            with transaction.atomic():
                # ignore_conflicts=True handles redelivered notifications gracefully
                created = NotificationMessage.objects.bulk_create(
                    records_to_create,
                    ignore_conflicts=True
                )
                logger.info(f"Batch processed: {len(created)} records created out of {len(batch_data)} received.")

    except Exception as e:
        logger.error(f"Critical error processing batch: {e}", exc_info=True)
        # We retry the batch on critical DB errors, though this might re-process good items
        # ignore_conflicts=True protects us from duplicates during retry
        raise self.retry(exc=e, countdown=60)
