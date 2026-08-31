"""The daily digest: what changed, and nothing else.

The gap reports find the problems nobody was looking for, which is worth
nothing if nobody looks at the reports. This is the part that goes the other
way -- a scheduled run that reads the same nine reports the index does and
mails what is different from the last time somebody was told.

Only the difference. A digest that repeated yesterday's eight hundred silent
stations every morning would be filed unread within a week, and the first
genuinely new finding would arrive in a message nobody opens. So each finding
is remembered once it has been carried, and a finding already remembered is
passed over however long it stands.

What has gone is carried too, because a problem clearing is also a change, and
because the row this module keeps is the only thing that can still name a
finding once the report has stopped listing it.

Nothing is recorded as reported until it has actually been sent. The order --
compute, send, then record -- is what makes a failed send merely a digest
somebody did not get, rather than a set of findings quietly written off as
already mentioned.

A report can also stop listing a finding without the problem having gone
anywhere. Propagation gaps are withheld for a centre whose own broker cannot
currently be reached, and the unattributed share is worked out over a trailing
window a centre that goes quiet falls out of. Calling those cleared would be
wrong twice over: it announces a fix that never happened, and then announces
the finding again as new when it comes back. So a finding that has stopped
being listed is given a grace period before it is let go, and what a run found
is written down whether or not anything was worth sending -- the clock has to
keep running on the mornings there is no mail.

One of those absences no grace period can rescue, because it is not one that
ends: a propagation gap passes the horizon its evidence ends at, past which
nothing can settle it either way ever again. Waiting on it is pointless, and
calling it cleared is the first of the two harms above -- a centre that had
nothing new go missing for a fortnight is announced as a path somebody fixed,
which nobody did.

So a report is asked which of its findings have stopped being checkable
rather than stopped -- which only it can know -- and those are let go without
a word: nothing is announced, the row is dropped, and the same centre breaking
again is news again. Everything else a report stops listing keeps its grace
period, because every other absence is one that can end.

What that costs is that a centre really going quiet for the whole forensic
window is not announced either, which is why each report's own account of
what it has bounded is still carried into the mail beside its news.

The reports are read from the same registry the index and the routing read, so
a ninth report joins the digest by existing. What each one considers a finding,
and what identifies it, is the report's own to say: see ``describe_row`` in
:mod:`wis2watch.core.analysis.gaps`.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from .alerts import Downtime, downtime, span
from .analysis import GAP_REPORTS, Notice
from .mail import admin_url, digest_recipients, notify
from .models import HardFailure, OutgoingEmail, ReportedFinding

logger = logging.getLogger(__name__)

#: How many of a report's findings one email names. The report itself is
#: bounded by its filters rather than by truncation, but an email is read in a
#: window somebody is also doing their job in: past a couple of dozen lines a
#: list stops being read at all. What is left out is counted and linked to, so
#: nothing is silently dropped -- only deferred to the page that lays it out.
DEFAULT_DIGEST_SAMPLE_SIZE = 20

#: How long a finding may be absent from its report before it is called
#: cleared, in hours. Two daily digests, so that a report suppressed for an
#: afternoon -- a centre's own broker unreachable, a quiet week falling out of
#: the attribution window -- is not mistaken for a problem solved. What it
#: costs is that genuinely good news arrives a day or two late, which is the
#: least urgent thing the digest carries.
DEFAULT_FINDING_GRACE_HOURS = 48

#: How much of a day this tool may have spent unable to watch before the
#: digest says so, in minutes. About two per cent of a day, which on a healthy
#: installation -- where a reconnection is measured in seconds -- is well
#: outside the ordinary. Deliberately far below anything that would raise an
#: alert: the point of the line is the day nobody was interrupted about, where
#: the losses were each too small to be news and added up to an afternoon.
DEFAULT_BAD_DAY_MINUTES = 30


def digest_sample_size():
    """How many of a report's findings one email names."""
    return getattr(
        settings, "WIS2WATCH_DIGEST_SAMPLE_SIZE", DEFAULT_DIGEST_SAMPLE_SIZE
    )


def finding_grace_hours():
    """How long a finding may be missing before it counts as cleared."""
    return getattr(
        settings, "WIS2WATCH_FINDING_GRACE_HOURS", DEFAULT_FINDING_GRACE_HOURS
    )


def bad_day_minutes():
    """How much of a day may be lost before the digest mentions it."""
    return getattr(settings, "WIS2WATCH_BAD_DAY_MINUTES", DEFAULT_BAD_DAY_MINUTES)


@dataclass(frozen=True)
class BadDay:
    """A day this tool spent enough of unable to watch to be worth saying so.

    Not a finding about the region -- it is the region's diagnostician owning
    up -- which is why it rides along in the digest rather than being one of
    the reports. It never makes a digest worth sending on its own: a morning
    whose only news was that yesterday was patchy would be a daily email
    again, arriving by the side door the digest's own rule was built to shut.

    What it carries that the alerts cannot is the shape of a whole day. An
    alert is about one spell, and a day made of losses each too small to
    announce leaves no alert at all while still costing hours of watching.
    That day is exactly the one this exists to name.
    """

    headline: str
    noun: str
    lost: Downtime
    day: datetime

    @property
    def line(self):
        """The whole of it, as one sentence for a reader in a hurry."""
        return _("%(headline)s for %(lost)s, across %(count)d %(noun)s.") % {
            "headline": self.headline,
            "lost": span(self.lost.duration),
            "count": self.lost.spells,
            "noun": self.noun,
        }


@dataclass(frozen=True)
class ReportChanges:
    """One report's difference since somebody was last told about it.

    ``seen`` is every finding the report holds now, by key, including the ones
    already reported. It is what a run writes down to say these were still
    being found today, which is what the grace period is measured from.

    ``let_go`` is the findings that have stopped being checkable rather than
    stopped: the report has said it can no longer answer for them either way,
    so no grace period will bring them back and no clearing describes them.
    They are dropped from what is remembered and never mentioned, which is
    what leaves the same centre breaking again to be news again.

    ``bound`` is what the report holds and does not list, where it bounds
    anything. It is carried because it is the one thing that qualifies the
    news beside it: a centre that has gone quiet for the whole forensic window
    is one this report can no longer say anything about, and the mail should
    not read as though the region were clear.
    """

    slug: str
    title: str
    url: str
    seen: list[str]
    new: list[Notice]
    resolved: list[Notice]
    bound: str | None = None
    let_go: list[str] = field(default_factory=list)

    @property
    def found(self):
        """How many findings the report holds now."""
        return len(self.seen)

    @property
    def has_changes(self):
        """Whether this report has anything to tell anybody."""
        return bool(self.new or self.resolved)

    @property
    def new_sample(self):
        """The new findings the email names."""
        return self.new[: digest_sample_size()]

    @property
    def new_withheld(self):
        """How many new findings are left to the report itself to show."""
        return max(len(self.new) - digest_sample_size(), 0)

    @property
    def resolved_sample(self):
        """The cleared findings the email names."""
        return self.resolved[: digest_sample_size()]

    @property
    def resolved_withheld(self):
        """How many cleared findings are left unnamed."""
        return max(len(self.resolved) - digest_sample_size(), 0)


@dataclass(frozen=True)
class Digest:
    """What one run found across every report, and what of it is news.

    Every report is held, including the ones standing exactly as they were
    reported: what a run found is written down whether or not it was worth
    sending, and a report left out here would be one whose findings looked
    absent the next time somebody asked.
    """

    generated_at: datetime
    changes: list[ReportChanges]
    bad_days: list[BadDay] = field(default_factory=list)

    @property
    def changed(self):
        """The reports with something to tell anybody, which is what is sent."""
        return [change for change in self.changes if change.has_changes]

    @property
    def has_changes(self):
        """Whether there is anything worth anybody's morning.

        Asked of the reports alone. What the tool lost yesterday rides along
        with news and is never news itself -- a bad day is already an alert's
        to announce if it was bad enough to interrupt anybody, and a digest
        that sent because of one would put a daily email back in front of a
        reader who stopped wanting it.
        """
        return bool(self.changed)

    @property
    def new_count(self):
        """How many findings are new since the last digest."""
        return sum(len(change.new) for change in self.changes)

    @property
    def resolved_count(self):
        """How many findings have cleared since the last digest."""
        return sum(len(change.resolved) for change in self.changes)

    @property
    def subject(self):
        """What the message says before it is opened.

        Counts rather than the findings themselves: the subject is what
        somebody decides on in a list of unread mail, and the decision is
        whether anything has changed at all.
        """
        new = ngettext(
            "%(count)d new finding", "%(count)d new findings", self.new_count
        ) % {"count": self.new_count}

        if not self.resolved_count:
            return new

        cleared = _("%(count)d cleared") % {"count": self.resolved_count}

        if not self.new_count:
            return ngettext(
                "%(count)d finding cleared",
                "%(count)d findings cleared",
                self.resolved_count,
            ) % {"count": self.resolved_count}

        return f"{new}, {cleared}"

    @property
    def summary(self):
        """What the run came to, in one line, for a log."""
        return (
            f"new={self.new_count} cleared={self.resolved_count} "
            f"reports={len(self.changed)}"
        )


def digest_changes(now=None):
    """What every report has to say that nobody has been told yet.

    Args:
        now: the instant the reports are asked about.

    Returns:
        Digest: every report, with what is new and what has been gone long
        enough to call cleared. Which of them is worth sending is the
        digest's own ``changed``.

    Reads only. What a run found is written down by ``record_seen``, and what
    was told to somebody by ``record_digest``.
    """
    now = now or dj_timezone.now()

    return Digest(
        generated_at=now,
        changes=[_changes_for(report, now=now) for report in GAP_REPORTS],
        bad_days=_bad_days(now=now),
    )


def send_digest(now=None):
    """Mail the diagnostician what has changed, and remember having done it.

    Args:
        now: the instant the reports are asked about.

    Returns:
        Digest: what was found, whether or not it was sent.

    Nothing is sent when nothing has changed. An empty message every morning
    trains its reader exactly as a repetitive one does, and the tool being
    alive is the overview's answer to give rather than the digest's.

    Nothing is recorded when nothing was sent, including when nobody is
    configured to receive it: a finding is remembered as reported because
    somebody was told, so an installation that has not named a recipient yet
    gets the whole picture in its first real digest rather than an empty one.
    """
    now = now or dj_timezone.now()
    digest = digest_changes(now=now)

    # Written down before anything else, and on every run: a finding still
    # being found is what keeps its grace period from running out, and a
    # morning with no mail is exactly when that would otherwise go unsaid.
    record_seen(digest, now=now)

    # Likewise on every run, and for the same reason in reverse: letting go of
    # a finding nothing can check any more is not news, so it cannot be made
    # to wait on there being any.
    record_let_go(digest)

    if not digest.has_changes:
        logger.info("[DIGEST] nothing has changed; nothing sent")
        return digest

    body = render_to_string(
        "wis2watchcore/email/daily_digest.txt",
        {"digest": digest, "gap_reports_url": admin_url("gap_reports")},
    )

    if not notify(
        digest.subject,
        body,
        digest_recipients(),
        kind=OutgoingEmail.DAILY_DIGEST,
        summary=digest.summary,
    ):
        return digest

    record_digest(digest, now=now)

    logger.info("[DIGEST] %s", digest.summary)

    return digest


def record_seen(digest, *, now):
    """Note that the findings already reported are still being found.

    This is the grace period's clock. A finding whose report has stopped
    listing it is kept until it has been missing long enough to mean
    something, and "long enough" is measured from the last run that saw it --
    so every run has to say so, including the ones that send nothing.

    Only findings already reported have rows to touch. A new one is written
    down when it is sent, by ``record_digest``.
    """
    for change in digest.changes:
        ReportedFinding.objects.filter(
            report_slug=change.slug, key__in=change.seen
        ).update(last_seen_at=now)


def record_let_go(digest):
    """Forget the findings their reports can no longer answer for.

    Quietly, and whether or not anything was sent. A finding here has not
    cleared and has not gone: the report has run out of the evidence that
    would settle it, so it will never be listed again and nothing will ever
    say what became of it. Keeping the row would leave a grace period running
    against an absence that does not end, which is how a fortnight of silence
    at a centre comes to be announced as a recovery.

    Dropping it is what makes the same centre breaking again arrive as news
    rather than as a finding somebody was told about a season ago. Nothing is
    lost by it: the rows here are a record of what has been said, not evidence
    of anything, and the reports are derived afresh each time they are asked.
    """
    for change in digest.changes:
        if not change.let_go:
            continue

        ReportedFinding.objects.filter(
            report_slug=change.slug, key__in=change.let_go
        ).delete()


def record_digest(digest, *, now):
    """Remember what has now been reported, and forget what has gone.

    A finding that has cleared has its row deleted rather than marked, so that
    the same problem returning is reported again. Nothing here is evidence of
    anything -- the reports are derived from the observations each time they
    are asked -- so there is nothing to preserve.
    """
    for change in digest.changed:
        ReportedFinding.objects.filter(
            report_slug=change.slug,
            key__in=[notice.key for notice in change.resolved],
        ).delete()

        ReportedFinding.objects.bulk_create(
            [
                ReportedFinding(
                    report_slug=change.slug,
                    key=notice.key,
                    summary=notice.summary,
                    reported_at=now,
                    last_seen_at=now,
                )
                for notice in change.new
            ],
            batch_size=1000,
            # A run that overlapped another -- a schedule fired twice, a run
            # by hand -- must not fail on the finding both of them found.
            ignore_conflicts=True,
        )


def _bad_days(*, now):
    """What yesterday cost this tool, where that came to enough to say.

    Args:
        now: the instant the digest is being composed at.

    Returns:
        list[BadDay]: a line per kind of loss that crossed the mark, and an
        empty list on a day worth nobody's attention -- which is most days,
        and is what makes the lines worth reading when they appear.

    The two kinds are asked separately and neither is inferred from the other.
    During a spell of flapping they are two views of one cause and both lines
    appear; but a day when the broker was faultless and ingestion stalled
    anyway is the one this has to be able to show, and a single line that
    blamed the stall on the connection would show it as a clean day with an
    unexplained footnote. The modules that find them are careful never to
    assert one causes the other, and neither is this.

    The day is the last whole UTC day, not the last twenty-four hours. The
    digest is read as a statement about yesterday, and a rolling window would
    put part of this morning into it and quietly move the boundary every time
    the schedule slipped a minute.
    """
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=1)
    mark = bad_day_minutes()

    days = []

    lost = downtime(HardFailure.GLOBAL_BROKER_LOST, start=start, end=end)

    if lost.minutes >= mark:
        days.append(
            BadDay(
                headline=_("The Global Broker connection was unreachable"),
                noun=ngettext("drop", "drops", lost.spells),
                lost=lost,
                day=start,
            )
        )

    stalled = downtime(HardFailure.INGESTION_STALLED, start=start, end=end)

    if stalled.minutes >= mark:
        days.append(
            BadDay(
                headline=_("Nothing was being ingested"),
                noun=ngettext("spell", "spells", stalled.spells),
                lost=stalled,
                day=start,
            )
        )

    return days


def _changes_for(report, *, now):
    """One report's findings, against the ones already reported from it."""
    found = _notices(report, now=now)
    reported = {
        finding.key: finding
        for finding in ReportedFinding.objects.filter(report_slug=report.slug)
    }
    unsettled = report.find_unsettled(now=now)
    gone_by = now - timedelta(hours=finding_grace_hours())

    # A finding the report still lists is a finding, whatever else the report
    # can no longer answer for. Only what has left it is anybody's to let go.
    gone = {key: finding for key, finding in reported.items() if key not in found}

    return ReportChanges(
        slug=report.slug,
        title=report.title,
        url=admin_url("gap_report", report.slug),
        seen=list(found),
        new=[notice for key, notice in found.items() if key not in reported],
        resolved=[
            Notice(key=key, summary=finding.summary)
            for key, finding in gone.items()
            if key not in unsettled and finding.last_seen_at <= gone_by
        ],
        bound=report.describe_bound(now=now),
        let_go=[key for key in gone if key in unsettled],
    )


def _notices(report, *, now):
    """What one report currently finds, by what identifies each finding.

    Rows that are not findings are dropped, and rows naming the same finding
    collapse onto one another: the first the report listed is kept, and the
    reports list what is worst or most recent first.
    """
    notices = {}

    for row in report.find_rows(now=now):
        notice = report.describe_row(row)

        if notice is not None:
            notices.setdefault(notice.key, notice)

    return notices
