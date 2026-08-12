from celery import shared_task
from celery.schedules import crontab
from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.core.management import call_command

from wis2watch.config.celery import app
from .catalogue import sync_catalogues
from .node_stations import sync_node_stations
from .oscar import sync_oscar_stations
from .propagation import evaluate_propagation
from .retention import expire_raw_messages
from .rollups import update_rollups

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


@shared_task
def run_sync_node_stations(node_id):
    """Ask one node what stations it declares.

    Failures are diagnostic state rather than task failures: a node that cannot
    be reached is recorded on its own sync log, and the next scheduled run asks
    again. Retrying here would only duplicate the schedule.
    """
    from .models import WIS2Node

    sync_log = sync_node_stations(WIS2Node.objects.get(id=node_id))

    if sync_log is None:
        return None

    logger.info("[STATION SYNC] %s: %s", sync_log.node.centre_id, sync_log.summary)

    return sync_log.id


@shared_task
def run_sync_all_node_stations():
    """Ask every node for its stations, so new ones need no manual trigger.

    One task per node rather than one run over all of them: each is an HTTP
    fetch against a different centre, and many African nodes are slow or
    unreachable from outside. Fanning out keeps one of those from holding up
    the rest of the region.
    """
    from .models import WIS2Node

    node_ids = list(
        WIS2Node.objects.exclude(stations_url="").values_list("id", flat=True)
    )

    logger.info("[STATION SYNC] queueing %s nodes", len(node_ids))

    for node_id in node_ids:
        run_sync_node_stations.delay(node_id)

    return node_ids


@shared_task
def run_sync_oscar_stations():
    """Ask OSCAR/Surface what each monitored country declares.

    Weekly is ample: OSCAR changes slowly, and a country's declared set moves in
    months rather than hours. Failures are diagnostic state rather than task
    failures -- a territory OSCAR could not be read for is recorded on the run's
    own sync log, and the next scheduled run asks again.
    """
    sync_log = sync_oscar_stations()

    logger.info("[OSCAR SYNC] %s", sync_log.summary)

    return sync_log.id


@shared_task
def run_update_rollups():
    """Bring the hourly rollups up to date with what has been stored.

    Counts are derived from stored rows, so this is safe to run as often as
    the schedule likes: it recomputes a trailing window rather than adding to
    what is already there.
    """
    counts = update_rollups()

    logger.info("[ROLLUPS] %s", counts.summary)

    return counts.summary


@shared_task
def run_evaluate_propagation():
    """Record what the centres published and the world never saw.

    Runs well inside the forensic window and re-examines a trailing one, so a
    missed run costs nothing: the gaps are found by the next. It has to run at
    all, though -- once the raw messages expire the evidence is gone, and the
    finding cannot be made again.
    """
    counts = evaluate_propagation()

    logger.info("[PROPAGATION] %s", counts.summary)

    return counts.summary


@shared_task
def run_expire_raw_messages():
    """Drop raw messages past the forensic window.

    Whatever is about to go is rolled up first, so the region's history
    survives the messages it was taken from.
    """
    counts = expire_raw_messages()

    logger.info("[RETENTION] %s", counts.summary)

    return counts.summary
