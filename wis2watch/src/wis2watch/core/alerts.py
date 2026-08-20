"""The two failures of this tool itself that cannot wait for the digest.

Everything else here is a finding about the region, and the digest carries
them in the morning because that is soon enough for a station that has been
silent for a year. These two are different in kind: the Global Broker
connection lost, and nothing at all being ingested. While either stands, every
answer the tool gives about the region is an answer about its own blindness --
centres look silent, propagation looks broken, and the overview is quietly
wrong rather than empty.

They are also the two that are unambiguous. There is no judgement to make
about whether a broker connection being down is a problem, which is why these
are alerts and the rest is a digest. A rules engine for anything subtler is
deliberately not here.

Three things keep an alert worth reading. It is announced once, however long
the failure lasts, because an outage repeated every minute is an outage
nobody reads about the second time. It is not announced at all until it has
lasted past a threshold, so that a broker reconnecting after a blip costs
nobody their morning. And its clearing is announced too, since the one thing
worth knowing after "the region is unwatched" is that it is not any more.

The thresholds are a first guess. They are settings rather than constants
because the right numbers are the region's normal rhythms, and those are not
known until the tool has watched them for a while.

The two checks are asymmetric on purpose. A broker's state is read from what
the supervisor recorded about its own connections, so it says nothing when the
supervisor itself is not running -- a process killed while connected leaves a
record saying it was connected. That case is exactly what the ingestion check
catches, which is why "nothing has arrived" is checked separately rather than
inferred from the connections looking healthy.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext as _

from .mail import admin_url, alert_recipients, notify
from .models import HardFailure, MessageSource, NotificationMessage, OutgoingEmail

logger = logging.getLogger(__name__)

#: How long the Global Broker connection may be down before it is announced,
#: in minutes. Long enough that an ordinary reconnection is not news.
DEFAULT_BROKER_OUTAGE_MINUTES = 5

#: How long nothing may be ingested from anywhere before it is announced, in
#: minutes. Longer than the broker's, because this is a statement about a
#: whole region's traffic rather than about one connection, and the region is
#: entitled to a quiet quarter of an hour.
DEFAULT_INGESTION_STALL_MINUTES = 15


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
    moment the last message arrived is the moment ingestion stopped. Where it
    cannot be, it is left empty and the moment a check first found the failure
    stands in. That is the most that can honestly be claimed of a broker whose
    record says only when it last came up, and it is what keeps a blip from
    being announced as an outage.
    """

    failing: bool
    detail: str = ""
    since: datetime | None = None


def broker_outage_minutes():
    """How long the Global Broker may be unreachable before it is announced."""
    return getattr(
        settings, "WIS2WATCH_BROKER_OUTAGE_MINUTES", DEFAULT_BROKER_OUTAGE_MINUTES
    )


def ingestion_stall_minutes():
    """How long nothing may arrive before it is announced."""
    return getattr(
        settings,
        "WIS2WATCH_INGESTION_STALL_MINUTES",
        DEFAULT_INGESTION_STALL_MINUTES,
    )


def check_hard_failures(*, now=None):
    """Look for the two ways this tool stops being able to answer anything.

    Args:
        now: the instant the failures are judged as of.

    Returns:
        AlertCounts: what was opened, announced and cleared.

    Safe to run on a beat and safe to miss: the state is the failure rows, and
    each run recomputes what is wrong now rather than advancing anything.
    """
    now = now or dj_timezone.now()
    counts = AlertCounts()

    for check in HARD_FAILURE_CHECKS:
        _reconcile(check, check.look_for(now=now), now=now, counts=counts)

    logger.info("[ALERTS] %s", counts.summary)

    return counts


def _reconcile(check, symptom, *, now, counts):
    """Bring one kind of failure's record, and who knows about it, up to date."""
    standing = HardFailure.objects.open().filter(kind=check.kind).first()

    if not symptom.failing:
        if standing is not None:
            _clear(standing, now=now, counts=counts)

        return

    if standing is None:
        standing = HardFailure.objects.create(
            kind=check.kind,
            detail=symptom.detail,
            started_at=min(symptom.since or now, now),
        )
        counts.opened += 1
    elif standing.detail != symptom.detail:
        standing.detail = symptom.detail
        standing.save(update_fields=["detail"])

    counts.standing += 1

    if standing.notified_at is None:
        _announce(standing, check=check, now=now, counts=counts)


def _announce(failure, *, check, now, counts):
    """Tell somebody, once the failure has lasted long enough to be worth it.

    Announced only when it has lasted past its threshold, and recorded as
    announced only when somebody was actually told -- an installation with no
    recipient configured yet has not been told, and gets the message when it
    has one.
    """
    if now - failure.started_at < timedelta(minutes=check.threshold_minutes()):
        return

    if not _send(failure, now=now, recovered=False):
        return

    failure.notified_at = now
    failure.save(update_fields=["notified_at"])

    counts.announced += 1

    logger.error(
        "[ALERTS] %s since %s: %s", failure.kind, failure.started_at, failure.detail
    )


def _clear(failure, *, now, counts):
    """Close a failure, and say so to whoever was told it had begun.

    A failure nobody was ever told about clears silently. Announcing the end
    of an outage that was never announced would be a message about nothing,
    and blips are exactly what the threshold exists to keep out of the mail.
    """
    failure.resolved_at = now
    failure.save(update_fields=["resolved_at"])

    counts.cleared += 1

    if failure.notified_at is None:
        return

    _send(failure, now=now, recovered=True)

    logger.info("[ALERTS] %s cleared after %s", failure.kind, now - failure.started_at)


def _send(failure, *, now, recovered):
    """Put one failure, beginning or ending, in front of whoever is watching.

    Both ends of an outage are the same message about the same thing, so they
    are written once and told apart by a flag: what a reader needs is the
    failure, when it began, and -- for the second message -- that everything
    the tool said in between is worth reading again.
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
    down for thirty seconds carries a stamp six hours old, and timing the
    outage from it would announce every blip on the next beat -- which is
    exactly what the threshold exists to prevent. So the outage begins when a
    check first found it, and the beat is what bounds how much later that is.
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

    Unlike the broker check, this one can date its own failure: the moment the
    last message arrived is the moment ingestion stopped. An installation that
    has never ingested anything has no such moment, and is timed from when it
    was first looked at instead.
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


@dataclass(frozen=True)
class HardFailureCheck:
    """One way this tool stops working: how it is looked for, and how long it
    is given before anybody is told.

    The two are held as a list for the same reason the gap reports are: a
    third kind of breakage should be one entry rather than an edit to a
    branch in the announcing, another in the checking, and a third somebody
    forgets. What is left hard-wired is the pair of choices on the model,
    which is what the rows are read back by.
    """

    kind: str
    look_for: Callable[..., Symptom]
    threshold_minutes: Callable[[], int]


#: The failures worth interrupting somebody for, in the order they are checked.
HARD_FAILURE_CHECKS = (
    HardFailureCheck(
        kind=HardFailure.GLOBAL_BROKER_LOST,
        look_for=_global_broker_symptom,
        threshold_minutes=broker_outage_minutes,
    ),
    HardFailureCheck(
        kind=HardFailure.INGESTION_STALLED,
        look_for=_ingestion_symptom,
        threshold_minutes=ingestion_stall_minutes,
    ),
)
