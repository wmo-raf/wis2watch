"""The periodic look past the registry, at whatever else the region publishes.

The Global Broker connection carries one topic filter per centre in the
registry, which is what keeps it to the region rather than the world. The cost
of that is a blind spot shaped exactly like the thing this tool exists to
find: a centre publishing to WIS2 that no catalogue has indexed -- a country
part-way through onboarding, a centre whose metadata registration was never
completed -- cannot be subscribed to by name, because nothing knows its name.

So once in a while the connection asks for everything, briefly. Every centre
seen publishing that the registry has no record of, and whose centre ID begins
with the ISO 3166 code of a monitored country, is written down. The prefix is
the whole of the region test on purpose: it is the only question about an
unknown centre that can be answered without a registry, which is the position
a sweep is in by definition.

Bounded in time because of what the filter costs while it is carried: the
connection is offered the whole world's traffic, and the region is a small
fraction of it. Everything outside the region is dropped as it is stored --
see :mod:`wis2watch.ingest.store` -- so a sweep costs bandwidth for its window
rather than storage, but that is reason enough to keep the window short and
the interval long.

Traffic from a monitored centre arrives twice during the window, once on the
sweep filter and once on the centre's own. Per-source uniqueness on the
notification UUID absorbs the second copy, which is why the rollups are
derived from stored rows rather than counted on receipt.

A run is recorded as a sync log of its own, so "was the region swept, and what
did it turn up" is answered the same way as it is for the catalogue and OSCAR.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Exists, OuterRef
from django.utils import timezone as dj_timezone

from ..core.countries import monitored_country_code_for_centre_id
from ..core.interpretation import sweep_topic
from ..core.models import SyncLog, UnregisteredCentre, WIS2Node
from ..core.sync import CREATED, ERRORED, UPDATED, SyncCounts

logger = logging.getLogger(__name__)

#: How long between sweeps, in seconds. An unregistered centre is a slow
#: finding -- a country onboards over weeks -- so hourly is ample, and the
#: interval is what keeps the whole world's traffic a rare visitor.
DEFAULT_INTERVAL_SECONDS = 3600

#: How long one sweep carries the wildcard filter, in seconds. Long enough
#: that a centre publishing even a few times an hour is heard, short enough
#: that the connection is not asked for the world for any appreciable part of
#: the time.
DEFAULT_DURATION_SECONDS = 60


def sweep_interval_seconds():
    """How long between the start of one sweep and the start of the next."""
    return getattr(
        settings, "WIS2WATCH_SWEEP_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS
    )


def sweep_duration_seconds():
    """How long a sweep carries the wildcard filter."""
    return getattr(
        settings, "WIS2WATCH_SWEEP_DURATION_SECONDS", DEFAULT_DURATION_SECONDS
    )


def record_unregistered_centre(centre_id, topic, now):
    """Write down a centre publishing without a registry record.

    A row per centre, updated in place: what is worth reporting is that the
    centre exists and is still publishing, not how many messages a sweep
    happened to catch.

    A row is reopened when a centre is seen again, because the observation is
    what the finding rests on: a centre being heard from with no record behind
    it says the gap is open now, whatever a previous run concluded.
    """
    centre, created = UnregisteredCentre.objects.get_or_create(
        centre_id=centre_id,
        defaults={
            "country": monitored_country_code_for_centre_id(centre_id),
            "sample_topic": topic,
            "first_seen_at": now,
            "last_seen_at": now,
        },
    )

    if created:
        return CREATED

    centre.last_seen_at = now
    centre.sample_topic = topic
    centre.registered_at = None
    centre.save(
        update_fields=["last_seen_at", "sample_topic", "registered_at", "modified"]
    )

    return UPDATED


def close_centres_the_registry_has_caught_up_with(now):
    """Close the findings whose centre the registry now knows about.

    A centre registered since it was found may not publish again during any
    particular sweep, so waiting to hear from it would leave a closed finding
    standing indefinitely. The row is kept rather than deleted: that a centre
    was publishing before anyone registered it is worth being able to say
    afterwards.
    """
    registered = WIS2Node.objects.filter(centre_id=OuterRef("centre_id"))

    return (
        UnregisteredCentre.objects.unregistered()
        .filter(Exists(registered))
        .update(registered_at=now)
    )


class WildcardSweep:
    """The bounded wildcard subscription, and what it turned up.

    The state of a sweep is what filter the Global Broker connections should
    be carrying, so the supervisor asks this object rather than holding
    timers of its own: it starts one when the interval has elapsed, finishes
    it when the window is up, and says when that changed so the connections
    can be told.

    The clock is wall-clock rather than a count of loop ticks. A tick is not a
    unit of time -- a slow drain lengthens it -- and "a minute of traffic every
    hour" is what the window is meant to be.
    """

    def __init__(self, now=None):
        # The first sweep is one interval away rather than immediate. A
        # process that is restarting repeatedly would otherwise ask for the
        # world's traffic every time it came up, which is the one condition
        # under which that is least affordable.
        self._due_from = now or dj_timezone.now()
        self._started_at = None
        self._log = None
        self._counts = None
        self._seen = set()

    @property
    def is_running(self):
        """Whether a sweep window is open."""
        return self._started_at is not None

    def topics(self):
        """The topic filters a Global Broker connection carries for the sweep."""
        return (sweep_topic(),) if self.is_running else ()

    def service(self, now=None):
        """Start or finish the sweep if it is time to.

        Returns whether that changed what the connections should carry, so the
        caller can push the difference immediately rather than leave the
        filter on -- or off -- until the next registry refresh happens to come
        round.
        """
        now = now or dj_timezone.now()

        if self.is_running:
            if now - self._started_at < timedelta(seconds=sweep_duration_seconds()):
                return False

            self.finish(now)

            return True

        if now - self._due_from < timedelta(seconds=sweep_interval_seconds()):
            return False

        self._start(now)

        return True

    def observe(self, centres, now=None):
        """Record the unregistered centres a flush of traffic named.

        ``centres`` maps a centre ID to a topic it was seen publishing on, as
        the store reports them.

        Ignored outside a sweep window. The per-centre filters carry only
        centres the registry knows about, so there is nothing to hear then --
        and an observation recorded outside a run would belong to no sync log.

        A centre the database refuses is counted and stepped over: one bad row
        must not cost the rest of what a sweep found.
        """
        if not self.is_running or not centres:
            return

        now = now or dj_timezone.now()

        for centre_id, topic in sorted(centres.items()):
            self._seen.add(centre_id)

            try:
                self._counts.record(record_unregistered_centre(centre_id, topic, now))
            except Exception as exc:
                logger.warning("Could not record centre %s: %s", centre_id, exc)
                self._counts.record(ERRORED)

    def finish(self, now=None):
        """Close the window and the run's log. A no-op outside a sweep.

        The state is put back before anything is written, so a run whose log
        cannot be closed still stops asking for the world's traffic.
        """
        if not self.is_running:
            return

        now = now or dj_timezone.now()
        log, counts, seen = self._log, self._counts, self._seen

        self._started_at = None
        self._log = None
        self._counts = None
        self._seen = set()

        if log is None:
            return

        counts.found = len(seen)
        # A finding the registry has caught up with is one the report no
        # longer carries, which is what this run deleted.
        log.items_deleted = close_centres_the_registry_has_caught_up_with(now)
        counts.close(log, counts.status)

        logger.info(
            "Wildcard sweep finished: %s closed=%s", log.summary, log.items_deleted
        )

    def _start(self, now):
        """Open a window, and a log for whatever it finds.

        The log is opened failed and closed successful, as every sync here is,
        so a process that dies mid-sweep leaves a run that plainly did not
        finish rather than a silent gap in the record.
        """
        self._started_at = now
        self._due_from = now
        self._counts = SyncCounts()
        self._seen = set()
        self._log = SyncLog.objects.create(
            sync_type=SyncLog.WILDCARD_SWEEP,
            status=SyncLog.FAILED,
            started_at=now,
        )

        logger.info(
            "Wildcard sweep started for %ss on %s",
            sweep_duration_seconds(),
            sweep_topic(),
        )
