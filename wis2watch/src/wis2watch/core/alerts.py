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
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext as _

from .mail import admin_url, alert_recipients, notify
from .models import HardFailure, MessageSource, NotificationMessage

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

    ``since`` is the failure's own beginning where that can be told -- when
    the broker was last connected, when a message last arrived. Where it
    cannot be, it is left empty and the moment the check first noticed stands
    in, which is the most that can honestly be claimed about a tool that has
    never worked since it was deployed.
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

    for kind, symptom in (
        (HardFailure.GLOBAL_BROKER_LOST, _global_broker_symptom(now=now)),
        (HardFailure.INGESTION_STALLED, _ingestion_symptom(now=now)),
    ):
        _reconcile(kind, symptom, now=now, counts=counts)

    logger.info("[ALERTS] %s", counts.summary)

    return counts


def _reconcile(kind, symptom, *, now, counts):
    """Bring one kind of failure's record, and who knows about it, up to date."""
    standing = HardFailure.objects.open().filter(kind=kind).first()

    if not symptom.failing:
        if standing is not None:
            _clear(standing, now=now, counts=counts)

        return

    if standing is None:
        standing = HardFailure.objects.create(
            kind=kind,
            detail=symptom.detail,
            started_at=min(symptom.since or now, now),
        )
        counts.opened += 1
    elif standing.detail != symptom.detail:
        standing.detail = symptom.detail
        standing.save(update_fields=["detail"])

    counts.standing += 1

    if standing.notified_at is None:
        _announce(standing, now=now, counts=counts)


def _announce(failure, *, now, counts):
    """Tell somebody, once the failure has lasted long enough to be worth it.

    Announced only when it has lasted past its threshold, and recorded as
    announced only when somebody was actually told -- an installation with no
    recipient configured yet has not been told, and gets the message when it
    has one.
    """
    if now - failure.started_at < timedelta(minutes=_threshold_minutes(failure.kind)):
        return

    subject = failure.get_kind_display()
    body = render_to_string(
        "wis2watchcore/email/hard_failure.txt",
        {
            "failure": failure,
            "now": now,
            "recovered": False,
            "overview_url": admin_url("node_overview"),
        },
    )

    if not notify(subject, body, alert_recipients()):
        return

    failure.notified_at = now
    failure.save(update_fields=["notified_at"])

    counts.announced += 1

    logger.error("[ALERTS] %s since %s: %s", failure.kind, failure.started_at, failure.detail)


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

    subject = _("%(failure)s -- recovered") % {"failure": failure.get_kind_display()}
    body = render_to_string(
        "wis2watchcore/email/hard_failure.txt",
        {
            "failure": failure,
            "now": now,
            "recovered": True,
            "overview_url": admin_url("node_overview"),
        },
    )

    notify(subject, body, alert_recipients())

    logger.info("[ALERTS] %s cleared after %s", failure.kind, now - failure.started_at)


def _threshold_minutes(kind):
    """How long a failure of this kind must last before it is announced."""
    if kind == HardFailure.GLOBAL_BROKER_LOST:
        return broker_outage_minutes()

    return ingestion_stall_minutes()


def _global_broker_symptom(*, now):
    """Whether anything is currently carrying the region's traffic to us.

    Read from what the supervisor recorded about its own connections, which
    means this can only ever speak about a supervisor that is running. A
    connection nothing has attempted yet says nothing either: a Global Broker
    the registry has just been given is not one that has failed.

    One Global Broker still connected is enough. The others are redundancy,
    and a redundant connection being down is not a region gone unwatched.
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
            for source in attempted
        ),
        # The most recent connection any of them held. What is being timed is
        # the region going unwatched, which began when the last of them
        # dropped rather than when the first did.
        since=max(
            (
                source.last_connected_at
                for source in attempted
                if source.last_connected_at
            ),
            default=None,
        ),
    )


def _ingestion_symptom(*, now):
    """Whether anything at all has arrived lately, from any vantage point.

    Measured on when a message was stored rather than on the time it carries.
    A Global Cache republishing an hour-old notification is ingestion working;
    a centre whose clock is days out is not a stall. The publication time is
    the publisher's claim, and this is a question about this tool.

    Any source counts. One centre's origin broker still delivering is not a
    healthy region, but it is not a stall either, and this alert exists for
    the case where nothing is arriving at all.
    """
    arrived = (
        NotificationMessage.objects.order_by("-received_datetime")
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
