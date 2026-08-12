"""Whether a dataset has gone quiet, judged against what it normally does.

"Quiet" only means something next to an expectation. A centre that publishes
surface observations hourly and has said nothing for five hours has a problem;
a centre whose climate summary is monthly and has said nothing for five hours
is exactly where it should be. Both are reported by the same table, so the
threshold has to belong to the dataset rather than to the tool.

Two things can supply that expectation. A person may state one outright, which
is the answer for a dataset with too little history to learn from and for a
learned interval that is simply wrong; otherwise it is what the dataset's own
history said, which ``wis2watch.core.cadence`` derives on a schedule. The
stated one wins wherever it is set -- that is the whole point of being able to
set it.

Where there is neither, nothing is claimed. A dataset with no expectation is
reported as unjudged rather than assumed hourly, because a silence finding
nobody can trust is worse than no finding: it is what teaches a diagnostician
to stop reading the column.

Quiet is counted from the end of the hour a dataset last published in. The
history is hourly buckets, so the moment inside the bucket is unknown; taking
its end is the reading that cannot overstate how long the dataset has been
quiet, and overstating is what manufactures findings.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import F, Max
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext_lazy as _

from ..cadence import cadence_window_end, cadence_window_start
from ..models import Dataset, HourlyRollup


class Expectation:
    """Where a dataset's expected publication interval came from."""

    OVERRIDDEN = "overridden"
    LEARNED = "learned"
    UNKNOWN = "unknown"

    CHOICES = [
        (OVERRIDDEN, _("Set by hand")),
        (LEARNED, _("Learned from history")),
        (UNKNOWN, _("Nothing to expect yet")),
    ]

    LABELS = dict(CHOICES)


class Silence:
    """What a dataset's -- or a centre's -- quiet amounts to."""

    SILENT = "silent"
    ON_SCHEDULE = "on_schedule"
    UNKNOWN = "unknown"

    CHOICES = [
        (SILENT, _("Silent")),
        (ON_SCHEDULE, _("On schedule")),
        (UNKNOWN, _("Not judged")),
    ]

    LABELS = dict(CHOICES)

    #: Silent first, then what is fine, then what could not be judged: the
    #: order someone reads for what has broken.
    RANK = {SILENT: 0, ON_SCHEDULE: 1, UNKNOWN: 2}


@dataclass(frozen=True)
class DatasetSilenceRow:
    """One dataset's quiet, and what was expected of it."""

    dataset_id: int
    node_id: int
    centre_id: str
    title: str
    topic: str
    expected_interval_hours: float | None
    expectation: str
    observations: int | None
    last_active_hour: datetime | None
    hours_quiet: float
    is_silent: bool

    @property
    def silence(self):
        """What this dataset's quiet amounts to."""
        if self.expected_interval_hours is None:
            return Silence.UNKNOWN

        return Silence.SILENT if self.is_silent else Silence.ON_SCHEDULE

    @property
    def overdue_hours(self):
        """How far past its expectation the dataset is; negative if it is not."""
        if self.expected_interval_hours is None:
            return 0

        return self.hours_quiet - self.expected_interval_hours

    @property
    def silence_label(self):
        """What this dataset's silence is called, for a table cell."""
        return Silence.LABELS.get(self.silence, self.silence)

    @property
    def expectation_label(self):
        """Where the expectation came from, for a table cell."""
        return Expectation.LABELS.get(self.expectation, self.expectation)


@dataclass(frozen=True)
class NodeSilence:
    """What a centre's datasets add up to, for one line of the overview."""

    silence: str
    silent_dataset_count: int
    judged_dataset_count: int

    @classmethod
    def nothing_known(cls):
        """A centre with nothing that can be judged."""
        return cls(silence=Silence.UNKNOWN, silent_dataset_count=0, judged_dataset_count=0)

    @property
    def silence_label(self):
        """What the centre's silence is called, for a table cell."""
        return Silence.LABELS.get(self.silence, self.silence)


def dataset_silence(*, now=None, node=None, window_days=None):
    """Every live dataset, with how long it has been quiet and whether that is odd.

    Args:
        now: the instant quiet is measured up to.
        node: keep only this node's datasets, or all of them.
        window_days: how far back to look for the last hour each dataset
            published in.

    Returns:
        list[DatasetSilenceRow]: the silent first, furthest overdue before them.

    Only datasets the registry still calls active are judged. One the
    catalogue has dropped, or that has been marked inactive, is not something
    anyone is waiting to hear from, and reporting it silent would fill the
    finding with datasets nobody expects to publish.
    """
    now = now or dj_timezone.now()
    since = cadence_window_start(now, window_days)
    datasets = _live_datasets(node)
    latest = _last_active_hours(datasets, since=since, until=cadence_window_end(now))

    rows = [
        _row(dataset, last_active_hour=latest.get(dataset.pk), now=now, since=since)
        for dataset in datasets
    ]

    return sorted(rows, key=_reading_order)


def silence_by_node(*, now=None, window_days=None):
    """Each centre's datasets, reduced to the one line the overview shows.

    Returns:
        dict[int, NodeSilence]: keyed by node id, for every centre that has a
        live dataset at all. A centre with none is absent rather than
        reported quiet: there is nothing it was expected to publish.
    """
    counts = {}

    for row in dataset_silence(now=now, window_days=window_days):
        silent, judged = counts.get(row.node_id, (0, 0))
        counts[row.node_id] = (
            silent + row.is_silent,
            judged + (row.expected_interval_hours is not None),
        )

    return {
        node_id: NodeSilence(
            # A centre is as concerning as its worst dataset. One dataset past
            # its expectation is a centre worth looking at, whatever the rest
            # of its output is doing -- which is what the counts beside it are
            # for.
            silence=(
                Silence.SILENT if silent
                else Silence.ON_SCHEDULE if judged
                else Silence.UNKNOWN
            ),
            silent_dataset_count=silent,
            judged_dataset_count=judged,
        )
        for node_id, (silent, judged) in counts.items()
    }


def _live_datasets(node):
    """The datasets anyone is waiting to hear from, with what to expect of them.

    The learned interval is annotated rather than followed as a relation: a
    dataset with too little history has no baseline at all, and a left join
    says so as a null instead of raising when it is read.
    """
    datasets = Dataset.objects.filter(status=Dataset.ACTIVE).select_related("node")

    if node is not None:
        datasets = datasets.filter(node=node)

    return list(
        datasets.annotate(
            learned_interval_hours=F("cadence_baseline__interval_hours"),
            learned_from=F("cadence_baseline__observations"),
        )
    )


def _last_active_hours(datasets, *, since, until):
    """The most recent hour each dataset was seen publishing in.

    Every vantage point counts. Whether the world received what a centre
    published is the propagation report's question; a centre heard only at its
    own broker is publishing, and calling it silent here would report the same
    fault twice under two names, one of them wrong.
    """
    counted = (
        HourlyRollup.objects.filter(
            dataset__in=datasets,
            message_count__gt=0,
            hour__gte=since,
            hour__lt=until,
        )
        .values("dataset_id")
        .annotate(latest=Max("hour"))
    )

    return {row["dataset_id"]: row["latest"] for row in counted}


def _expectation(dataset):
    """What to expect of a dataset, and where that came from.

    A stated interval wins over a learned one wherever it is set. That is what
    it is for: the learned value is an inference from history, and a person
    who knows the dataset knows things its history cannot show -- that it is
    new, that its history is an artefact of an outage, that it is meant to
    publish daily whatever it has managed so far.
    """
    if dataset.expected_interval_override_hours is not None:
        return dataset.expected_interval_override_hours, Expectation.OVERRIDDEN, None

    if dataset.learned_interval_hours is not None:
        return (
            dataset.learned_interval_hours,
            Expectation.LEARNED,
            dataset.learned_from,
        )

    return None, Expectation.UNKNOWN, None


def _hours_quiet(last_active_hour, *, now, since):
    """How long a dataset has been quiet, as far as the buckets can say.

    Counted from the end of the hour it last published in, because that is the
    latest instant the bucket admits a message at and therefore the smallest
    quiet the evidence supports. Negative never means anything -- a dataset
    that published in the hour in progress is not quiet at all -- so it floors
    at nothing.

    A dataset absent from the whole window is quiet for at least as long as
    the window itself. That is a lower bound rather than an answer, and it is
    the one that keeps a dataset expected less often than the window is long
    from being called silent on no evidence.
    """
    if last_active_hour is None:
        return (now - since).total_seconds() / 3600

    quiet_since = last_active_hour + timedelta(hours=1)

    return max((now - quiet_since).total_seconds() / 3600, 0)


def _row(dataset, *, last_active_hour, now, since):
    """One dataset as a finding."""
    expected, expectation, observations = _expectation(dataset)
    hours_quiet = _hours_quiet(last_active_hour, now=now, since=since)

    return DatasetSilenceRow(
        dataset_id=dataset.pk,
        node_id=dataset.node_id,
        centre_id=dataset.node.centre_id,
        title=dataset.title,
        topic=dataset.wmo_topic_hierarchy,
        expected_interval_hours=expected,
        expectation=expectation,
        observations=observations,
        last_active_hour=last_active_hour,
        hours_quiet=hours_quiet,
        is_silent=expected is not None and hours_quiet > expected,
    )


def _reading_order(row):
    """The silent first, and among them the furthest past what was expected."""
    return (
        Silence.RANK.get(row.silence, len(Silence.RANK)),
        -row.overdue_hours,
        row.centre_id,
        row.title,
    )
