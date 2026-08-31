from datetime import datetime

from celery import shared_task
from celery.schedules import crontab
from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.core.management import call_command

from wis2watch.config.celery import app
from .alerts import check_hard_failures
from .cadence import learn_cadence_baselines, learn_station_activity_baselines
from .catalogue import sync_catalogues
from .daily_rollups import update_daily_rollups
from .digest import send_digest
from .node_datasets import sync_node_datasets
from .node_stations import sync_node_stations
from .oscar import sync_oscar_stations
from .probes import nodes_advertising_links, probe_node_links, probed_hour
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
        WIS2Node.objects.advertising_a_station_registry().values_list("id", flat=True)
    )

    logger.info("[STATION SYNC] queueing %s nodes", len(node_ids))

    for node_id in node_ids:
        run_sync_node_stations.delay(node_id)

    return node_ids


@shared_task
def run_sync_node_datasets(node_id):
    """Ask one centre what datasets it declares.

    Failures are diagnostic state rather than task failures: a centre that
    cannot be reached is recorded on its own sync log, and the next scheduled
    run asks again. Retrying here would only duplicate the schedule.
    """
    from .models import WIS2Node

    sync_log = sync_node_datasets(WIS2Node.objects.get(id=node_id))

    if sync_log is None:
        return None

    logger.info(
        "[DISCOVERY METADATA SYNC] %s: %s", sync_log.node.centre_id, sync_log.summary
    )

    return sync_log.id


@shared_task
def run_sync_all_node_datasets():
    """Ask every centre for its own records, so a new one needs no manual trigger.

    One task per centre rather than one run over all of them, for the reason
    the station fan-out gives: each is an HTTP fetch against a different
    centre, several of the region's are slow or unreachable from outside, and
    fanning out keeps one of those from holding up the rest of the region.

    Queued apart from the station registry even though both read the same
    host. The two endpoints fail independently -- a centre serving its records
    may serve no station registry at all -- and each run has to be able to
    state its own bound.
    """
    from .models import WIS2Node

    node_ids = list(
        WIS2Node.objects.advertising_discovery_metadata().values_list("id", flat=True)
    )

    logger.info("[DISCOVERY METADATA SYNC] queueing %s nodes", len(node_ids))

    for node_id in node_ids:
        run_sync_node_datasets.delay(node_id)

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
def run_update_daily_rollups():
    """Summarise the recent hourly rollups into station-days.

    Not chained to the hourly run, and not required to follow it. Both rebuild
    a trailing window rather than adding to one, and this window is taken from
    the hourly window's length, so a run that goes first reads slightly older
    hours and the next run corrects the day it wrote. Coupling them would only
    mean one failure took out both.
    """
    counts = update_daily_rollups()

    logger.info("[DAILY ROLLUPS] %s", counts.summary)

    return counts.summary


@shared_task
def run_learn_cadence_baselines():
    """Learn what each dataset's normal publishing rhythm is.

    Daily is ample and deliberately unhurried: a rhythm is learned from months
    of buckets and moves in weeks, so running it oftener would spend a scan of
    the region's history to arrive at the same number. A missed run costs
    nothing either -- the baselines already learned stand, and silence keeps
    being judged against them.
    """
    counts = learn_cadence_baselines()

    logger.info("[CADENCE] %s", counts.summary)

    return counts.summary


@shared_task
def run_learn_station_activity_baselines():
    """Learn how much of a day each station normally reports in.

    Daily, and unhurried for the same reasons the cadence run is: the figure is
    a percentile over ninety days of buckets and moves in weeks, so a more
    frequent run would spend a scan of the region's history arriving at the
    same number. A missed run costs nothing -- the baselines already learned
    stand, and the matrix keeps drawing against them.

    Runs after the daily rollups it reads, which is not a scheduling
    requirement so much as an observation: the rollups refresh every fifteen
    minutes, so whenever this lands, yesterday is already whole.
    """
    counts = learn_station_activity_baselines()

    logger.info("[STATION ACTIVITY] %s", counts.summary)

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


@app.task(base=Singleton, bind=True)
def run_probe_node_links(self, node_id, hour):
    """Ask one centre for a bounded sample of the files it advertised.

    ``hour`` is passed in as an ISO timestamp rather than worked out here, so
    that every node in a fan-out samples the same hour however long the queue
    takes to reach it -- otherwise a run that spilled over an hour boundary
    would leave some centres' hours checked twice and others not at all.

    A singleton on its own arguments, which is what actually holds the bound.
    The run reads how much of the hour's allowance is already spent and then
    spends the rest, so two of these for the same centre and hour -- a beat
    that overlapped, a queue drained twice -- would each read nothing spent and
    both spend the lot. Keyed on the node and the hour, so different centres
    and later hours are unaffected.

    Failures are not retried. The next hour asks again, and a centre whose
    server is down is precisely what the probe is meant to record rather than
    to work around.
    """
    from .models import WIS2Node

    counts = probe_node_links(
        WIS2Node.objects.get(id=node_id), hour=datetime.fromisoformat(hour)
    )

    return counts.summary


@shared_task
def run_probe_canonical_links():
    """Check that the files this hour's notifications advertised can be had.

    One task per centre rather than one run over the region: each probe is an
    HTTP request against a different centre, and the ones worth finding out
    about are exactly the ones that hang. Fanning out keeps a centre whose
    server never answers from holding up everybody else's checks.

    Only centres that advertised a link in the hour are queued, so a region of
    quiet nodes costs one query rather than a task apiece.

    A missed hour is not made up. The sample is of what was published then, and
    the point is a continuing picture of retrievability rather than a complete
    audit of every file.
    """
    hour = probed_hour()
    node_ids = list(nodes_advertising_links(hour))

    logger.info("[LINK PROBES] queueing %s nodes for %s", len(node_ids), hour)

    for node_id in node_ids:
        run_probe_node_links.delay(node_id, hour.isoformat())

    return node_ids


@app.task(base=Singleton)
def run_send_daily_digest():
    """Mail the diagnostician what the reports have found since yesterday.

    A singleton, because what makes the digest readable is that a finding is
    carried once: two runs overlapping would each read the findings before
    either had recorded them, and send the same list twice over two subjects
    that both claimed to be what changed.

    A missed run costs nothing but a day's delay. The digest is a difference
    against what has been reported rather than against yesterday, so the next
    run carries everything the missed one would have, and nothing twice.
    """
    return send_digest().summary


@app.task(base=Singleton)
def run_check_hard_failures():
    """Look for the ways this tool itself stops working.

    A singleton for the same reason as the digest, and a sharper one: the
    record of a failure is what keeps it to a single message, and two runs
    reading it before either had written would both find it unannounced.

    The beat is what the evidence is made of, which is why it is far finer
    than anything it judges. Every run is what opens and closes the drops a
    spell of unreliability is measured over, so running this half as often
    would not delay the alert -- it would coarsen the measure the alert is
    computed from, and a broker dropping for eight minutes at a time would be
    recorded as dropping for ten.

    Failures are recorded with when they began rather than when they were
    noticed, so a missed beat delays the message without misdating what it
    says.
    """
    return check_hard_failures().summary


@shared_task
def run_expire_raw_messages():
    """Drop raw messages past the forensic window.

    Whatever is about to go is rolled up first, so the region's history
    survives the messages it was taken from.
    """
    counts = expire_raw_messages()

    logger.info("[RETENTION] %s", counts.summary)

    return counts.summary
