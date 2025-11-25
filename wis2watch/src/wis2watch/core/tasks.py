from celery import shared_task
from celery.schedules import crontab
from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.core.management import call_command

from wis2watch.config.celery import app
from .cleanup import cleanup_old_station_message_logs
from .sync import sync_discovery_metadata, sync_stations

logger = get_task_logger(__name__)


@app.task(base=Singleton, bind=True)
def run_backup(self):
    # Run the `dbbackup` command
    logger.info("[BACKUP] Running backup")
    call_command('dbbackup', '--clean', '--noinput')
    
    # Run the `mediabackup` command
    logger.info("[BACKUP] Running mediabackup")
    call_command('mediabackup', '--clean', '--noinput')


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(
        crontab(hour=0, minute=0),
        run_backup.s(),
        name="run-backup-daily-at-midnight",
    )


@shared_task(bind=True, max_retries=3)
def run_sync_discovery_metadata(self, node_id):
    stats, exc = sync_discovery_metadata(node_id)
    
    if not stats and exc:
        logger.error(f"[DISCOVERY SYNC] No stats returned for node {node_id}. Retrying...")
        
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    
    return stats


@shared_task(bind=True, max_retries=3)
def run_sync_stations(self, node_id):
    stats, exc = sync_stations(node_id)
    
    if not stats and exc:
        logger.error(f"[STATION SYNC] No stats returned for node {node_id}. Retrying...")
        
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    
    return stats


@shared_task(bind=True, max_retries=3)
def run_sync_node_metadata(self, node_id):
    stats, exc = sync_discovery_metadata(node_id)
    
    if not stats and exc:
        logger.error(f"[DISCOVERY SYNC] No stats returned for node {node_id}. Retrying...")
        
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    
    stats, exc = sync_stations(node_id)
    if not stats and exc:
        logger.error(f"[STATION SYNC] No stats returned for node {node_id}. Retrying...")
        
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    
    return stats


@shared_task
def run_sync_all_nodes():
    """
    Trigger synchronization for all active nodes.
    Should be run periodically (e.g., every hour).
    """
    from .models import WIS2Node
    
    active_nodes = WIS2Node.objects.all()
    
    logger.info(f"Starting sync for {active_nodes.count()} nodes")
    
    for node in active_nodes:
        # Chain the tasks: first sync metadata, then stations
        run_sync_node_metadata.delay(node.id)
    
    logger.info("Sync tasks queued for all active nodes")


@shared_task
def run_cleanup_old_station_message_logs(days=90):
    return cleanup_old_station_message_logs(days=days)
