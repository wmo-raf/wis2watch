"""The failures of this tool itself that cannot wait for the digest.

Everything else here is a finding about the region, and the digest carries
them in the morning because that is soon enough for a station that has been
silent for a year. These are different in kind: the Global Broker connection
not carrying the region's traffic, nothing at all being ingested, and the one
catalogue that writes the registry having stopped answering. While any of them
stands, every answer the tool gives about the region is an answer about its
own blindness -- centres look silent, propagation looks broken, and the
overview is quietly wrong rather than empty.

The last of the three is the one that gets quieter rather than louder, and it
is the reason a fourth kind exists at all. A registry that has stopped being
rebuilt does not empty any page: it freezes one, and every surface goes on
answering confidently about the region as it stood when the catalogue was last
reachable, while centres that onboard in the meantime are never created, never
subscribed to and never reported on. Nothing about that is visible to anybody
who has not already gone looking at a sync log.

They are also the ones that are unambiguous. There is no judgement to make
about whether a broker that is not delivering is a problem, which is why these
are alerts and the rest is a digest. A rules engine for anything subtler is
deliberately not here.

What is judged, and what took some finding out, is *when* a connection not
delivering is worth somebody's attention. The obvious answer -- it is down
right now, and has been for five minutes -- turns out to describe a broker
badly. A Global Broker that drops for eight minutes every quarter of an hour
passes that test sixty times a day and is announced sixty times, which is an
outage nobody reads about the second time; and the tool is meanwhile blind for
half of every hour, which is the thing actually worth saying and which no one
of those sixty messages says. So the connection is judged on how much of a
trailing window it has failed to carry rather than on whether it is carrying
now. A blackout is simply that measure at its maximum, and reaches the same
alert by the same route.

That leaves two records with two different jobs, and separating them is what
makes the rest work. Every drop opens and closes a ``GLOBAL_BROKER_LOST`` row
and is announced to nobody: those rows are the evidence, and they have to keep
being written whether or not anything is worth saying, because they are what
the window is measured over. The stretch in which they add up to the tool not
really watching is one ``GLOBAL_BROKER_UNRELIABLE`` row, and that is what is
announced. One spell, one message, however many drops it contains.

Three things keep an alert worth reading. It is announced once, however long
the failure lasts. It is not announced at all until it has passed the measure
that makes it more than a blip. And its clearing is announced too, since the
one thing worth knowing after "the region is unwatched" is that it is not any
more -- and, for a spell of unreliability, what the whole spell came to, which
is the number worth taking to whoever runs the broker.

The thresholds are a first guess. They are settings rather than constants
because the right numbers are the region's normal rhythms, and those are not
known until the tool has watched them for a while.

The checks are asymmetric on purpose. A broker's state is read from what the
supervisor recorded about its own connections, so it says nothing when the
supervisor itself is not running -- a process killed while connected leaves a
record saying it was connected. That case is exactly what the ingestion check
catches, which is why "nothing has arrived" is checked separately rather than
inferred from the connections looking healthy, and why that check is left fast
where the broker's has been deliberately slowed.

It is left fast and it is also silenced while the broker is already the news.
During a spell of unreliability the two checks are describing one event from
two sides, and the reader has the cause in front of them already. The
suppression is only ever of a second telling: a stall that begins when no
spell is standing is announced at once, ahead of anything the window could
say, and a stall that outlives the spell that silenced it is announced the
moment that spell clears. The broker coming back while traffic does not is the
most alarming thing this tool can report, and it is precisely the case the
ingestion check exists for.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .mail import admin_url, alert_recipients, notify
from .models import (
    GlobalDiscoveryCatalogue,
    HardFailure,
    MessageSource,
    NotificationMessage,
    OutgoingEmail,
    SyncLog,
)

logger = logging.getLogger(__name__)

#: How much of the trailing window the Global Broker connection may fail to
#: carry before it is announced, in minutes. Set well above any ordinary
#: reconnection, which is measured in seconds: what this is meant to catch is
#: a connection that is not really holding, not one that blinked.
DEFAULT_BROKER_UNRELIABLE_MINUTES = 45

#: How long the window it is measured over is, in minutes. This is the number
#: that decides what "unreliable" means, far more than the budget does: a
#: short window turns every bad hour into a spell of its own, and a long one
#: takes hours to notice a blackout. Two hours is long enough that a flapping
#: connection stays one spell rather than becoming a dozen.
DEFAULT_BROKER_UNRELIABLE_WINDOW_MINUTES = 120

#: How little of the window may be lost before the connection counts as
#: reliable again, in minutes. Deliberately far below the budget rather than
#: equal to it: a spell that cleared the moment the measure dipped under what
#: opened it would close and reopen all day, and announce itself each time.
DEFAULT_BROKER_RELIABLE_MINUTES = 10

#: How long nothing may be ingested from anywhere before it is announced, in
#: minutes. Longer than a reconnection and shorter than anything the window
#: can say, because this is the fast path: it is what notices a blackout while
#: the broker's own measure is still filling up, and what notices an ingest
#: process that has died leaving its connection records reading healthy.
DEFAULT_INGESTION_STALL_MINUTES = 15

#: How long the registry may go without being rebuilt before it is announced,
#: in hours. Measured in multiples of the sync's own six-hourly schedule
#: rather than in the minutes the connection checks use, because a single
#: missed run is a blip the next run fixes and nothing worth a message. Three
#: missed runs with a fourth due is a catalogue that is not coming back on its
#: own.
DEFAULT_CATALOGUE_STALE_HOURS = 24

# What a reader is to stop believing while one of these stands, in the
# sentence each message ends on. One per check rather than branches in the
# template, for the reason the list of checks itself is a list: a fifth kind of
# breakage should be one entry there rather than a fifth arm of an `if`
# somebody has to remember to extend. And it is the only part of the message a
# reader could not have worked out from the subject line, because the failures
# cost different things -- a broker gone empties the picture, a registry gone
# freezes it.

#: Nothing is arriving: what the region looks like is what this tool cannot
#: see. For the connection checks and for a stall.
BLIND = gettext_lazy(
    "While this stands, nothing WIS2Watch says about the region can be relied "
    "on: centres that are publishing normally will look silent, because "
    "nothing is reaching this tool to say otherwise."
)

#: Some of it is arriving. The harm is subtler and worth stating separately:
#: the numbers are all plausible and all short.
HALF_BLIND = gettext_lazy(
    "While this stands, WIS2Watch is only intermittently receiving the "
    "region's traffic: centres will look quieter than they are, and any "
    "propagation gap reported may be this tool's own."
)

#: Everything is arriving and the registry it is read against has stopped
#: moving. Nothing looks wrong, which is why this one has to say what it is
#: costing at more length than the others.
FROZEN = gettext_lazy(
    "While this stands, the registry is frozen at what that catalogue last "
    "said: a centre that onboards to WIS2 now is never created, never "
    "subscribed to, and never reported on. Unregistered centres are withheld "
    "meanwhile, because a centre publishing without a catalogue record cannot "
    "be told from one whose record this tool has not read."
)


@dataclass
class AlertCounts:
    """What a check came to."""

    opened: int = 0
    announced: int = 0
    cleared: int = 0
    standing: int = 0

    @property
    def summary(self):
        """What the run came to, in one line, for a log."""
        return (
            f"opened={self.opened} announced={self.announced} "
            f"cleared={self.cleared} standing={self.standing}"
        )


@dataclass(frozen=True)
class Symptom:
    """What one check found, whether or not anything is wrong.

    ``since`` is the failure's own beginning where that can be told -- the
    moment the last message arrived is the moment ingestion stopped, and the
    first drop in the window is when a spell of unreliability began. Where it
    cannot be, it is left empty and the moment a check first found the failure
    stands in. That is the most that can honestly be claimed of a broker whose
    record says only when it last came up.
    """

    failing: bool
    detail: str = ""
    since: datetime | None = None


@dataclass(frozen=True)
class Downtime:
    """How much of a stretch of time one kind of failure occupied.

    ``began`` is the start of the earliest spell touching the stretch, not
    clamped to it. A four-hour outage half an hour into a two-hour window
    began four hours ago, and saying it began when the window opened would
    under-report the one number a reader would take to whoever runs the
    broker.
    """

    minutes: float
    spells: int
    began: datetime | None

    @property
    def duration(self):
        """The same total, for anything that renders rather than compares."""
        return timedelta(minutes=self.minutes)


def broker_unreliable_minutes():
    """How much of the window may be lost before the connection is announced."""
    return getattr(
        settings,
        "WIS2WATCH_BROKER_UNRELIABLE_MINUTES",
        DEFAULT_BROKER_UNRELIABLE_MINUTES,
    )


def broker_unreliable_window_minutes():
    """How long the window the connection is judged over is."""
    return getattr(
        settings,
        "WIS2WATCH_BROKER_UNRELIABLE_WINDOW_MINUTES",
        DEFAULT_BROKER_UNRELIABLE_WINDOW_MINUTES,
    )


def broker_reliable_minutes():
    """How little of the window may be lost for the spell to be over."""
    return getattr(
        settings,
        "WIS2WATCH_BROKER_RELIABLE_MINUTES",
        DEFAULT_BROKER_RELIABLE_MINUTES,
    )


def ingestion_stall_minutes():
    """How long nothing may arrive before it is announced."""
    return getattr(
        settings,
        "WIS2WATCH_INGESTION_STALL_MINUTES",
        DEFAULT_INGESTION_STALL_MINUTES,
    )


def catalogue_stale_hours():
    """How long the registry may go unrebuilt before it is announced."""
    return getattr(
        settings, "WIS2WATCH_CATALOGUE_STALE_HOURS", DEFAULT_CATALOGUE_STALE_HOURS
    )


def catalogue_stale_minutes():
    """The same threshold in the minutes the announcing policies judge in."""
    return catalogue_stale_hours() * 60


def downtime(kind, *, start, end):
    """How much of a stretch of time one kind of failure stood for.

    Args:
        kind: which of :class:`~wis2watch.core.models.HardFailure`'s kinds to
            add up.
        start: the beginning of the stretch.
        end: the end of it, and what an unresolved spell is counted up to.

    Returns:
        Downtime: the total, how many spells contributed, and when the
        earliest of them began.

    Read out of the failure rows rather than kept as a counter of its own,
    because the rows are already the record and a counter would be a second
    account of the same thing that could disagree with it. Overlapping spells
    are not possible -- one open row per kind is a database constraint -- so
    the total is a plain sum rather than a union.
    """
    total = timedelta()
    spells = 0
    began = None

    for spell in HardFailure.objects.filter(kind=kind).overlapping(start, end):
        opened = max(spell.started_at, start)
        closed = min(spell.resolved_at or end, end)

        if closed <= opened:
            continue

        total += closed - opened
        spells += 1

        if began is None or spell.started_at < began:
            began = spell.started_at

    return Downtime(minutes=total.total_seconds() / 60, spells=spells, began=began)


def check_hard_failures(*, now=None):
    """Look for the ways this tool stops being able to answer anything.

    Args:
        now: the instant the failures are judged as of.

    Returns:
        AlertCounts: what was opened, announced and cleared.

    Safe to run on a beat and safe to miss: the state is the failure rows, and
    each run recomputes what is wrong now rather than advancing anything.

    The order the checks run in is load-bearing, which is why they are a
    sequence rather than a set. The drops have to be reconciled before the
    spell that is measured over them, or the window would be short by whatever
    is happening this minute; and the spell has to be reconciled before the
    stall it silences, or a spell clearing would take a beat longer to let the
    stall it was hiding be spoken about. The registry check depends on none of
    them and nothing depends on it, so it is last: what it reads is sync logs,
    which no check here writes.
    """
    now = now or dj_timezone.now()
    counts = AlertCounts()

    for check in HARD_FAILURE_CHECKS:
        _reconcile(check, check.look_for(now=now), now=now, counts=counts)

    logger.info("[ALERTS] %s", counts.summary)

    return counts


def _reconcile(check, symptom, *, now, counts):
    """Bring one kind of failure's record, and who knows about it, up to date."""
    standing = HardFailure.objects.standing(check.kind)

    if not symptom.failing:
        if standing is not None:
            _clear(standing, check=check, now=now, counts=counts)

        return

    if standing is None:
        standing = HardFailure.objects.create(
            kind=check.kind,
            detail=symptom.detail,
            started_at=min(symptom.since or now, now),
        )
        counts.opened += 1
    elif not check.frozen_detail and standing.detail != symptom.detail:
        standing.detail = symptom.detail
        standing.save(update_fields=["detail"])

    counts.standing += 1

    if standing.notified_at is None:
        _announce(standing, check=check, now=now, counts=counts)


def _announce(failure, *, check, now, counts):
    """Tell somebody, if this is a failure worth telling anybody about.

    Whether it is, and when, is the check's own to say: a drop is never worth
    it, a spell of unreliability is worth it the moment it is found because
    the window it was found over is itself the waiting, and a stall is worth
    it unless the reader is already holding the reason for it.

    Recorded as announced only when somebody was actually told -- an
    installation with no recipient configured yet has not been told, and gets
    the message when it has one.
    """
    if not check.announce_now(failure, now=now):
        return

    if not _send(failure, check=check, now=now, recovered=False):
        return

    failure.notified_at = now
    failure.save(update_fields=["notified_at"])

    counts.announced += 1

    logger.error(
        "[ALERTS] %s since %s: %s", failure.kind, failure.started_at, failure.detail
    )


def _clear(failure, *, check, now, counts):
    """Close a failure, and say so to whoever was told it had begun.

    A failure nobody was ever told about clears silently. Announcing the end
    of something that was never announced would be a message about nothing,
    and the drops -- which are never announced -- are most of what passes
    through here.
    """
    failure.resolved_at = now
    failure.save(update_fields=["resolved_at"])

    counts.cleared += 1

    if failure.notified_at is None:
        return

    _send(failure, check=check, now=now, recovered=True)

    logger.info("[ALERTS] %s cleared after %s", failure.kind, now - failure.started_at)


def _send(failure, *, check, now, recovered):
    """Put one failure, beginning or ending, in front of whoever is watching.

    Both ends are the same message about the same thing, so they are written
    once and told apart by a flag: what a reader needs is the failure, when it
    began, what to stop believing while it stands, and -- for the second
    message -- that everything the tool said in between is worth reading
    again.

    The one thing the second message can say that the first cannot is what the
    whole spell came to, and only a check that knows how to add its own
    evidence up can say it. Composed here rather than held on the row, because
    it is not known until the spell is over and a row that carried it would
    have to be rewritten on every beat until then.
    """
    subject = failure.get_kind_display()

    if recovered:
        subject = _("%(failure)s -- recovered") % {"failure": subject}

    body = render_to_string(
        "wis2watchcore/email/hard_failure.txt",
        {
            "failure": failure,
            "now": now,
            "recovered": recovered,
            "consequence": check.consequence,
            "recovered_detail": (
                check.recovered_detail(failure)
                if recovered and check.recovered_detail
                else ""
            ),
            "overview_url": admin_url("node_overview"),
        },
    )

    return notify(
        subject,
        body,
        alert_recipients(),
        kind=OutgoingEmail.HARD_FAILURE,
        summary=failure.detail,
    )


# -- what the checks look for ---------------------------------------------


def _global_broker_symptom(*, now):
    """Whether anything is currently carrying the region's traffic to us.

    Args:
        now: unused; taken so that every check is asked in the same way.

    Read from what the supervisor recorded about its own connections, which
    means this can only ever speak about a supervisor that is running. A
    connection nothing has attempted yet says nothing either: a Global Broker
    the registry has just been given is not one that has failed.

    One Global Broker still connected is enough. The others are redundancy,
    and a redundant connection being down is not a region gone unwatched.

    Nothing here can say when the connection dropped, so nothing here tries.
    A source's ``last_connected_at`` is when it last came up, written on
    connection and left standing afterwards: a broker up for six hours and
    down for thirty seconds carries a stamp six hours old. So the drop begins
    when a check first found it, and the beat is what bounds how much later
    that is -- which is close enough, because nothing is announced off one of
    these and the window that is measured over them is two hours long.
    """
    watched = MessageSource.objects.connections().filter(
        source_type=MessageSource.GLOBAL_BROKER, is_active=True
    )
    attempted = [source for source in watched if source.is_reachable is not None]

    if not attempted or any(source.is_reachable for source in attempted):
        return Symptom(failing=False)

    return Symptom(
        failing=True,
        detail="; ".join(
            f"{source.name} ({source.host}:{source.port}): "
            f"{source.last_error or 'disconnected'}"
            f"{_last_connected(source)}"
            for source in attempted
        ),
    )


def _last_connected(source):
    """When a broker was last seen to come up, for a reader to judge by.

    Reported rather than timed against. It says how long this connection had
    been working, which is worth knowing about a broker that has just started
    refusing, and nothing at all about when it stopped.
    """
    if not source.last_connected_at:
        return " (never connected)"

    return f" (last connected {source.last_connected_at:%Y-%m-%d %H:%M} UTC)"


def _global_broker_unreliable_symptom(*, now):
    """Whether the Global Broker connection has really been carrying the region.

    Args:
        now: the instant the window ends at.

    The measure is how many minutes of the trailing window the connection
    failed to carry, added up out of the drops recorded beneath it. A
    connection down solidly and one down half of every quarter of an hour
    reach the same number by different routes, which is the point: to this
    tool they are the same failure, because in both the region goes half
    unwatched and everything said about it is unsafe.

    Opening and clearing are deliberately not the same number. A spell that
    ended the moment the measure fell back under what opened it would spend a
    flapping afternoon closing and reopening, and announce itself on each --
    which is the failure mode this whole check exists to end. So once a spell
    stands it stands until the window is nearly clean, and the distance
    between the two numbers is what makes it one spell rather than twelve.
    """
    window = timedelta(minutes=broker_unreliable_window_minutes())
    lost = downtime(HardFailure.GLOBAL_BROKER_LOST, start=now - window, end=now)

    standing = HardFailure.objects.standing(HardFailure.GLOBAL_BROKER_UNRELIABLE)

    if standing:
        failing = lost.minutes > broker_reliable_minutes()
    else:
        failing = lost.minutes >= broker_unreliable_minutes()

    if not failing:
        return Symptom(failing=False)

    return Symptom(
        failing=True,
        detail=(
            f"{_watched_brokers()}: unreachable for {round(lost.minutes)} of the "
            f"last {round(window.total_seconds() / 60)} minutes, "
            f"across {lost.spells} {_drops(lost.spells)}"
        ),
        since=lost.began,
    )


def _watched_brokers():
    """The Global Brokers a spell of unreliability is about, by name.

    Named from the registry rather than from the drops, because a drop records
    that the connection was not carrying rather than which broker was asked --
    and because a spell is about the connection this installation relies on,
    which is what the registry says it is.
    """
    names = list(
        MessageSource.objects.connections()
        .filter(source_type=MessageSource.GLOBAL_BROKER, is_active=True)
        .values_list("name", flat=True)
    )

    return "; ".join(names) or "Global Broker"


def _drops(count):
    """One word or the other, for a sentence that has to read as one."""
    return "drop" if count == 1 else "drops"


def span(delta):
    """A length of time as somebody would say it, to the minute.

    Minutes alone stop being read somewhere around three figures, and a spell
    of unreliability is very often measured in half-days. Shared with the
    digest, which reports the same totals over a day rather than a spell and
    must not word them differently.
    """
    minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)

    if not hours:
        return f"{minutes}m"

    return f"{hours}h{minutes:02d}m"


def _unreliable_spell_summary(failure):
    """What a whole spell of unreliability came to, once it is over.

    This is the number worth having: not that the connection is working again,
    which the subject line already said, but how much of the stretch it was
    not working for and how many separate drops that took. It is the sentence
    somebody would quote to whoever runs the broker, and it cannot be composed
    until the spell has ended.
    """
    ended = failure.resolved_at or dj_timezone.now()
    lost = downtime(HardFailure.GLOBAL_BROKER_LOST, start=failure.started_at, end=ended)

    return _(
        "Over %(spell)s, the connection was unreachable for %(lost)s "
        "across %(spells)d %(drops)s."
    ) % {
        "spell": span(ended - failure.started_at),
        "lost": span(lost.duration),
        "spells": lost.spells,
        "drops": _drops(lost.spells),
    }


def _ingestion_symptom(*, now):
    """Whether anything at all has arrived lately, from any vantage point.

    Measured on when a message was stored rather than on the time it carries.
    A Global Cache republishing an hour-old notification is ingestion working;
    a centre whose clock is days out is not a stall. The publication time is
    the publisher's claim, and this is a question about this tool.

    Any connection counts. One centre's origin broker still delivering is not
    a healthy region, but it is not a stall either, and this alert exists for
    the case where nothing is arriving at all.

    What a poller wrote does not count, and that exclusion is the whole
    integrity of this alert. It is the only thing standing between the
    single-replica ingest process dying and nobody noticing, and it works by
    reading a clock that only that process winds. A poller runs elsewhere, on
    a schedule of its own, and its rows would hold the clock up indefinitely
    while nothing at all was being listened to -- an alert silently disabled
    by another feature working correctly.

    Unlike the broker's checks, this one can date its own failure: the moment
    the last message arrived is the moment ingestion stopped. An installation
    that has never ingested anything has no such moment, and is timed from
    when it was first looked at instead.
    """
    arrived = (
        NotificationMessage.objects.exclude(
            source__source_type=MessageSource.ORIGIN_API
        )
        .order_by("-received_datetime")
        .values_list("received_datetime", flat=True)
        .first()
    )

    if arrived and now - arrived < timedelta(minutes=ingestion_stall_minutes()):
        return Symptom(failing=False)

    if arrived is None:
        return Symptom(
            failing=True,
            detail="No notification message has ever been ingested",
        )

    return Symptom(
        failing=True,
        detail=f"Nothing has been ingested since {arrived:%Y-%m-%d %H:%M} UTC",
        since=arrived,
    )


def _writer_catalogue_symptom(*, now):
    """Whether the registry is still being rebuilt from a catalogue.

    Args:
        now: the instant the registry's age is measured up to.

    Exactly one catalogue creates registry records; the others are fetched
    read-only so that their divergence from it is reportable. So this is a
    question about one catalogue, and a reading catalogue that goes dark is
    deliberately not asked about here -- the tool still answers without it.

    An installation with no writer designated is not one whose writer has
    stopped answering, and gets the silence a Global Broker nothing has been
    given gets. A writer that has never synced is a different thing and is
    reported, timed from when it was first looked at: a catalogue that has sat
    in the registry for a day without one run against it means the sync is not
    running, which freezes the registry exactly as an unreachable host does.

    That case is also the only one the announcing threshold does any work on.
    Every other route here has already outlived it by the time it is found --
    the failure is dated from a sync that stopped happening, so it is old the
    moment it is opened -- and a writer nothing has run against yet is dated
    from now, which is what gives a fresh installation its day of grace.

    What is measured is when the registry was last actually rebuilt, not when
    a run last succeeded. A catalogue answering 200 with nothing in it passes
    every check this tool makes -- the fetch worked, the run is green, the
    stamp is fresh -- and stops the registry growing just as effectively as a
    refused connection. So the clock is read off the last run that brought
    records back, and a run that brought none holds nothing up.
    """
    writer = GlobalDiscoveryCatalogue.objects.filter(
        is_writer=True, is_active=True
    ).first()

    if writer is None:
        return Symptom(failing=False)

    rebuilt = _registry_last_rebuilt(writer)

    if rebuilt and now - rebuilt < timedelta(hours=catalogue_stale_hours()):
        return Symptom(failing=False)

    return Symptom(
        failing=True,
        detail=f"{writer.name} ({writer.centre_id}): {_frozen_since(rebuilt)}, "
        f"{_why_the_registry_is_frozen(writer)}",
        since=rebuilt,
    )


def _registry_last_rebuilt(writer):
    """When a run of this catalogue last brought registry records back.

    Three kinds of run bring nothing back and none of them moves this clock: a
    failed one, whatever it read on the way; an empty one, by answering with
    no records for the region; and one every record of which was stepped over,
    which reaches the registry no better than the other two. What is left is
    the whole of what "the registry is current" can honestly mean.

    A reading catalogue's runs count here, and have to. They store nothing --
    only the writer writes -- but they are the record of that catalogue
    answering, which is what a newly promoted writer needs to be judged on
    rather than being announced stale for having been a reader last week.

    Timed from when the run finished rather than when it started, because
    what the stamp claims is that the registry was current at that moment.
    """
    last = (
        _runs_against(writer)
        .filter(items_found__gt=F("items_errored"))
        .exclude(status=SyncLog.FAILED)
        .first()
    )

    if last is None:
        return None

    return last.completed_at or last.started_at


def _runs_against(catalogue):
    """Every run of the catalogue sync against one catalogue, newest first.

    Both questions below are asked of this same set on the same beat -- when
    records last came back, and what the most recent run did -- and they are
    two questions rather than one because their answers are usually different
    rows. What they must not differ on is which runs count, which is what this
    is for: a station sync or a sweep logged against the same catalogue is not
    evidence about the registry.
    """
    return SyncLog.objects.filter(
        catalogue=catalogue, sync_type=SyncLog.CATALOGUE
    ).order_by("-started_at")


def _frozen_since(rebuilt):
    """How long the registry has been standing still, for a reader to date it by."""
    if rebuilt is None:
        return "the registry has never been built from it"

    return f"no records read since {rebuilt:%Y-%m-%d %H:%M} UTC"


def _why_the_registry_is_frozen(writer):
    """Which of the ways a catalogue stops feeding the registry this one is.

    Four of them leave the registry in the same state and want four different
    people. A run that failed is a catalogue or a network to chase. A run that
    answered with nothing is a catalogue that has lost the region's records. A
    run that stepped over everything it read is this tool's own problem, in
    interpretation or in the shape of what it was sent. And no run at all
    since is a scheduler that has stopped -- the one nothing else here would
    ever say, because a sync that does not run leaves no failing log to read.
    """
    latest = _runs_against(writer).first()

    if latest is None:
        return "no sync has ever run against it"

    ran_at = f"{latest.started_at:%Y-%m-%d %H:%M} UTC"

    if latest.status == SyncLog.FAILED:
        return f"the run at {ran_at} failed: {_one_line(latest.error_message)}"

    if not latest.items_found:
        return f"the run at {ran_at} returned no records for the monitored region"

    if latest.items_errored >= latest.items_found:
        return f"the run at {ran_at} stepped over all {latest.items_found} it read"

    return "no sync has run since"


def _one_line(message):
    """An exception's text as a sentence a subject-adjacent line can hold."""
    return " ".join(message.split()) or "no reason recorded"


# -- when a check is worth somebody's attention ---------------------------


def never(failure, *, now):
    """A failure worth recording and never worth a message.

    The drops are this. Each one is evidence the window is measured over, and
    on a connection that is behaving badly there are dozens a day; the story
    they add up to is told by the spell above them, once.
    """
    return False


def immediately(failure, *, now):
    """A failure worth a message on the beat it is found.

    For a check whose looking is already the waiting. A spell of unreliability
    cannot be found until two hours of evidence say so, and holding it back
    further would be waiting twice.
    """
    return True


def after(threshold_minutes):
    """Announce once the failure has outlasted a threshold.

    Args:
        threshold_minutes: a callable giving the threshold, so that the
            setting is read at the moment of judging rather than at import.
    """

    def announce(failure, *, now):
        return now - failure.started_at >= timedelta(minutes=threshold_minutes())

    return announce


def unless_the_broker_is_already_the_news(announce):
    """Hold a message back while its likeliest cause is already standing.

    Args:
        announce: the policy that applies when nothing is suppressing it.

    Wraps rather than replaces, so that the check underneath keeps its own
    threshold and gets it back the moment the suppression lifts. That lifting
    matters as much as the suppressing: a stall that outlives the spell which
    silenced it is a broker that came back while traffic did not, and there is
    nothing this tool can say that is more worth saying.

    Only ever a second telling is withheld. A stall beginning when no spell is
    standing goes out at once -- which is the fast path a blackout is caught
    on, well before the window has enough evidence to call the connection
    unreliable at all.
    """

    def announce_unless_suppressed(failure, *, now):
        if HardFailure.objects.standing(HardFailure.GLOBAL_BROKER_UNRELIABLE):
            return False

        return announce(failure, now=now)

    return announce_unless_suppressed


@dataclass(frozen=True)
class HardFailureCheck:
    """One way this tool stops working: how it is looked for, whether anybody
    is told, and what is said when it is over.

    Held as a list for the same reason the gap reports are: another kind of
    breakage should be one entry rather than an edit to a branch in the
    announcing, another in the checking, and a third somebody forgets. What is
    left hard-wired is the set of choices on the model, which is what the rows
    are read back by.

    ``consequence`` is what the failure costs a reader, and it is the reason
    the message is worth sending at all: the subject line says what broke, and
    this says what to stop believing until it is mended.

    ``frozen_detail`` is for a check whose detail quotes a moving measure. The
    reconciliation rewrites a standing failure's detail whenever it changes,
    which for a spell of unreliability would be a database write every beat
    for hours, and would leave the row describing its last minute rather than
    the reason anybody was told about it. Frozen, the detail says what was
    true when the message went out, which is what the row is a record of.
    """

    kind: str
    look_for: Callable[..., Symptom]
    announce_now: Callable[..., bool]
    consequence: str
    frozen_detail: bool = False
    recovered_detail: Callable[[HardFailure], str] | None = None


#: The failures this tool watches for, in the order they are checked. The
#: order is part of the behaviour: see ``check_hard_failures``.
HARD_FAILURE_CHECKS = (
    HardFailureCheck(
        kind=HardFailure.GLOBAL_BROKER_LOST,
        look_for=_global_broker_symptom,
        announce_now=never,
        consequence=BLIND,
    ),
    HardFailureCheck(
        kind=HardFailure.GLOBAL_BROKER_UNRELIABLE,
        look_for=_global_broker_unreliable_symptom,
        announce_now=immediately,
        consequence=HALF_BLIND,
        frozen_detail=True,
        recovered_detail=_unreliable_spell_summary,
    ),
    HardFailureCheck(
        kind=HardFailure.INGESTION_STALLED,
        look_for=_ingestion_symptom,
        announce_now=unless_the_broker_is_already_the_news(
            after(ingestion_stall_minutes)
        ),
        consequence=BLIND,
    ),
    HardFailureCheck(
        kind=HardFailure.CATALOGUE_WRITER_STALE,
        look_for=_writer_catalogue_symptom,
        announce_now=after(catalogue_stale_minutes),
        consequence=FROZEN,
    ),
)
