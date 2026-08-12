"""The daily digest: what changed, and nothing else.

The gap reports find the problems nobody was looking for, which is worth
nothing if nobody looks at the reports. This is the part that goes the other
way -- a scheduled run that reads the same five reports the index does and
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

The reports are read from the same registry the index and the routing read, so
a sixth report joins the digest by existing. What each one considers a finding,
and what identifies it, is the report's own to say: see ``describe_row`` in
:mod:`wis2watch.core.analysis.gaps`.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from django.template.loader import render_to_string
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from .analysis import GAP_REPORTS, Notice
from .mail import admin_url, digest_recipients, notify
from .models import ReportedFinding

logger = logging.getLogger(__name__)

#: How many of a report's findings one email names. The report itself is
#: bounded by its filters rather than by truncation, but an email is read in a
#: window somebody is also doing their job in: past a couple of dozen lines a
#: list stops being read at all. What is left out is counted and linked to, so
#: nothing is silently dropped -- only deferred to the page that lays it out.
DIGEST_SAMPLE_SIZE = 20


@dataclass(frozen=True)
class ReportChanges:
    """One report's difference since somebody was last told about it."""

    slug: str
    title: str
    url: str
    found: int
    new: list[Notice]
    resolved: list[Notice]

    @property
    def has_changes(self):
        """Whether this report has anything to tell anybody."""
        return bool(self.new or self.resolved)

    @property
    def new_sample(self):
        """The new findings the email names."""
        return self.new[:DIGEST_SAMPLE_SIZE]

    @property
    def new_withheld(self):
        """How many new findings are left to the report itself to show."""
        return max(len(self.new) - DIGEST_SAMPLE_SIZE, 0)

    @property
    def resolved_sample(self):
        """The cleared findings the email names."""
        return self.resolved[:DIGEST_SAMPLE_SIZE]

    @property
    def resolved_withheld(self):
        """How many cleared findings are left unnamed."""
        return max(len(self.resolved) - DIGEST_SAMPLE_SIZE, 0)


@dataclass(frozen=True)
class Digest:
    """What one run found to have changed across every report."""

    generated_at: datetime
    changes: list[ReportChanges]

    @property
    def has_changes(self):
        """Whether there is anything worth anybody's morning."""
        return bool(self.changes)

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
            f"reports={len(self.changes)}"
        )


def digest_changes(now=None):
    """What every report has to say that nobody has been told yet.

    Args:
        now: the instant the reports are asked about.

    Returns:
        Digest: the reports with something new or newly gone, and nothing
        else. A report standing exactly as it was reported is absent rather
        than present and empty, because the email is a list of what changed.

    Reads only. What was found is recorded once it has been sent, by
    ``record_digest``.
    """
    now = now or dj_timezone.now()

    changes = [_changes_for(report, now=now) for report in GAP_REPORTS]

    return Digest(
        generated_at=now,
        changes=[change for change in changes if change.has_changes],
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

    if not digest.has_changes:
        logger.info("[DIGEST] nothing has changed; nothing sent")
        return digest

    body = render_to_string(
        "wis2watchcore/email/daily_digest.txt",
        {"digest": digest, "gap_reports_url": admin_url("gap_reports")},
    )

    if not notify(digest.subject, body, digest_recipients()):
        return digest

    record_digest(digest, now=now)

    logger.info("[DIGEST] %s", digest.summary)

    return digest


def record_digest(digest, *, now):
    """Remember what has now been reported, and forget what has gone.

    A finding that has cleared has its row deleted rather than marked, so that
    the same problem returning is reported again. Nothing here is evidence of
    anything -- the reports are derived from the observations each time they
    are asked -- so there is nothing to preserve.
    """
    for change in digest.changes:
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
                )
                for notice in change.new
            ],
            batch_size=1000,
            # A run that overlapped another -- a schedule fired twice, a run
            # by hand -- must not fail on the finding both of them found.
            ignore_conflicts=True,
        )


def _changes_for(report, *, now):
    """One report's findings, against the ones already reported from it."""
    found = _notices(report, now=now)
    reported = {
        finding.key: finding
        for finding in ReportedFinding.objects.filter(report_slug=report.slug)
    }

    return ReportChanges(
        slug=report.slug,
        title=report.title,
        url=admin_url("gap_report", report.slug),
        found=len(found),
        new=[notice for key, notice in found.items() if key not in reported],
        resolved=[
            Notice(key=key, summary=finding.summary)
            for key, finding in reported.items()
            if key not in found
        ],
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
