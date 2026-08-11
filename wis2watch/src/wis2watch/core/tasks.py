from celery import shared_task
from celery.schedules import crontab
from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.core.management import call_command

from wis2watch.config.celery import app
from .catalogue import sync_catalogues
from .cleanup import cleanup_old_notification_messages
from .sync import sync_stations

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


@shared_task
def run_sync_catalogues():
    """Rebuild the registry from the Global Discovery Catalogues.

    Failures are diagnostic state rather than task failures: an unreachable
    catalogue is recorded on its own sync log, so retrying here would only
    duplicate the schedule.
    """
    logs = sync_catalogues()

    for log in logs:
        logger.info("[CATALOGUE SYNC] %s: %s", log.catalogue.centre_id, log.summary)

    return [log.id for log in logs]


@shared_task(bind=True, max_retries=3)
def run_sync_stations(self, node_id):
    stats, exc = sync_stations(node_id)
    
    if not stats and exc:
        logger.error(f"[STATION SYNC] No stats returned for node {node_id}. Retrying...")
        
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    
    return stats


@shared_task
def run_sync_all_node_stations():
    """
    Trigger a station registry sync for every node.
    Should be run periodically (e.g., every hour).
    """
    from .models import WIS2Node

    nodes = WIS2Node.objects.all()

    logger.info(f"Starting station sync for {nodes.count()} nodes")

    for node in nodes:
        run_sync_stations.delay(node.id)

    logger.info("Station sync tasks queued for all nodes")


@shared_task
def run_cleanup_old_notification_messages(days=90):
    return cleanup_old_notification_messages(days=days)
