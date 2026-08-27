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
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone as dj_timezone

from ..core.interpretation import (
    monitored_country_code_for_centre_id,
    sweep_topic,
)
from ..core.models import SyncLog, UnregisteredCentre, WIS2Node
from ..core.sync import CREATED, UPDATED, SteppedOver, SyncCounts

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


def _last_sweep_started_at():
    """When the last recorded sweep began, or None if none ever has.

    The schedule is read back from the runs rather than kept in the process,
    because the process is restarted -- by a deploy, by a crash, by a machine
    going away -- and an interval counted from startup would mean a process
    restarting oftener than the interval never sweeping at all. That failure
    is silent and permanent: the blind spot the sweep exists to close simply
    stays open.
    """
    return (
        SyncLog.objects.filter(sync_type=SyncLog.WILDCARD_SWEEP)
        .order_by("-started_at")
        .values_list("started_at", flat=True)
        .first()
    )


def _record_unregistered_centre(centre_id, topic, now):
    """Write down a centre publishing without a registry record.

    A row per centre, updated in place: what is worth reporting is that the
    centre exists and is still publishing, not how many messages a sweep
    happened to catch.

    A row is reopened when a centre is seen again, because the observation is
    what the finding rests on: a centre being heard from with no record behind
    it says the gap is open now, whatever a previous run concluded.

    Each centre is written in its own savepoint, so one the database refuses --
    a centre ID longer than the column, a topic longer than the sample -- is
    stepped over rather than left poisoning the transaction the rest of the
    sweep is writing in.
    """
    with transaction.atomic():
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


def _close_centres_the_registry_has_caught_up_with(now):
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
        # When this process came up, which is what the schedule falls back to
        # when nothing has ever swept. What the last run was is read from the
        # log instead, and only when the schedule is first consulted: the
        # supervisor builds this before it has any business querying.
        self._started_from = now or dj_timezone.now()
        self._due_from = None
        self._started_at = None
        self._log = None
        self._outcomes = {}

    @property
    def is_running(self):
        """Whether a sweep window is open."""
        return self._started_at is not None

    def topics(self):
        """The topic filters a Global Broker connection carries for the sweep."""
        return (sweep_topic(),) if self.is_running else ()

    def is_over(self, now=None):
        """Whether an open window has run its length.

        Asked separately from ``service`` so the caller can store what has
        just arrived before the window is closed: what a listener received in
        the last moments of a sweep is drained on the next pass, by which time
        a closed sweep would no longer be listening for what it named.
        """
        now = now or dj_timezone.now()

        return self.is_running and now - self._started_at >= timedelta(
            seconds=sweep_duration_seconds()
        )

    def service(self, now=None):
        """Start or finish the sweep if it is time to.

        Returns whether that changed what the connections should carry, so the
        caller can push the difference immediately rather than leave the
        filter on -- or off -- until the next registry refresh happens to come
        round.
        """
        now = now or dj_timezone.now()

        if self.is_running:
            if not self.is_over(now):
                return False

            self.finish(now)

            return True

        if now < self._due_at(now):
            return False

        self._start(now)

        return True

    def _due_at(self, now):
        """The moment the next sweep may open.

        Counted from the last recorded run, so a restart resumes the schedule
        rather than beginning it again -- the same rule as everything else
        this process does, which reads its state back rather than remembering
        it. The first sweep of a deployment that has never swept is an
        interval away rather than immediate, so that a process restarting
        repeatedly does not ask for the world's traffic each time it comes up.
        """
        if self._due_from is None:
            self._due_from = _last_sweep_started_at() or self._started_from

        return self._due_from + timedelta(seconds=sweep_interval_seconds())

    def observe(self, centres, now=None):
        """Record the unregistered centres a flush of traffic named.

        ``centres`` maps a centre ID to a topic it was seen publishing on, as
        the store reports them.

        Ignored outside a sweep window. The per-centre filters carry only
        centres the registry knows about, so there is nothing to hear then --
        and an observation recorded outside a run would belong to no sync log.

        A centre the database refuses is counted and stepped over: one bad row
        must not cost the rest of what a sweep found.

        A centre is written down every time it is heard, so that its last-seen
        keeps up, and counted once for the window however often that is. A
        centre publishing steadily is heard on every drain of a sweep, and a
        run reporting it as sixty updates would be describing the drain
        interval rather than the region.
        """
        if not self.is_running or not centres:
            return

        now = now or dj_timezone.now()

        for centre_id, topic in sorted(centres.items()):
            try:
                self._record_outcome(
                    centre_id, _record_unregistered_centre(centre_id, topic, now)
                )
            except Exception as exc:
                logger.warning("Could not record centre %s: %s", centre_id, exc)
                self._record_outcome(
                    centre_id, SteppedOver(item=centre_id, reason=str(exc))
                )

    def _record_outcome(self, centre_id, outcome):
        """Note what became of one centre, once for the window.

        A centre this window created stays created however often it is heard
        again, and one that could not be written stops counting as an error
        the moment a later flush gets it down.
        """
        if self._outcomes.get(centre_id) in (CREATED, UPDATED):
            return

        self._outcomes[centre_id] = outcome

    def finish(self, now=None):
        """Close the window and the run's log. A no-op outside a sweep.

        The state is put back before anything is written, so a run whose log
        cannot be closed still stops asking for the world's traffic.
        """
        if not self.is_running:
            return

        now = now or dj_timezone.now()
        log, outcomes = self._log, self._outcomes

        self._started_at = None
        self._log = None
        self._outcomes = {}

        counts = SyncCounts(found=len(outcomes))

        for outcome in outcomes.values():
            counts.record(outcome)

        # A finding the registry has caught up with is one the report no
        # longer carries, which is what this run deleted.
        log.items_deleted = _close_centres_the_registry_has_caught_up_with(now)
        counts.close(log, counts.status)

        logger.info(
            "Wildcard sweep finished: %s closed=%s", log.summary, log.items_deleted
        )

    def _start(self, now):
        """Open a window, and a log for whatever it finds.

        The log is written before the window opens, so a wildcard filter can
        never be carried without a record that it was: the log is both the
        outcome of this run and how the next process to come up knows when the
        last sweep was. A log that cannot be written leaves the window shut,
        and the next pass tries again.

        It is opened failed and closed successful, as every sync here is, so a
        process that dies mid-sweep leaves a run that plainly did not finish
        rather than a silent gap in the record.
        """
        self._log = SyncLog.objects.create(
            sync_type=SyncLog.WILDCARD_SWEEP,
            status=SyncLog.FAILED,
            started_at=now,
        )
        self._started_at = now
        self._due_from = now
        self._outcomes = {}

        logger.info(
            "Wildcard sweep started for %ss on %s",
            sweep_duration_seconds(),
            sweep_topic(),
        )
