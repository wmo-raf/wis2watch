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
quiet, and overstating is what manufactures findings. It does mean a dataset
must be a bucket past its expectation before it is called silent, because the
intervals it is judged against are measured bucket to bucket. That hour is
deliberate: it is the price of not reporting a dataset late for being at the
wrong end of the hour it published in.

How far back this looks is not windowed. A dataset expected once a month is
exactly the one a trailing window would fail: bound the search at ninety days
and a yearly dataset can never be found quiet, because its absence never
exceeds what was looked at. So the last hour a dataset published in is asked
of the whole history, which the index on the rollups makes one backwards walk
per dataset, and a dataset never seen publishing at all is quiet only as far
back as this tool can show it was listening -- absence before that is not
evidence, it is the tool not having existed yet. Which is why that floor is
read off the vantage points it listens to live: a centre's own archive can be
pulled months deep, and those are hours it holds one centre's word for rather
than hours it was hearing the region.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from django.db.models import F, Min, OuterRef, Subquery
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext_lazy as _

from ..models import Dataset, HourlyRollup, MessageSource
from ..rollups import floor_to_hour

#: Sorts before any real last-seen time, so whatever nothing has ever been
#: heard from heads a table and still tie-breaks by name among its own kind.
BEFORE_ANYTHING = datetime.min.replace(tzinfo=timezone.utc)


def hours_between(last_seen_at, now):
    """How many hours something has been quiet, or None if it never spoke."""
    if last_seen_at is None:
        return None

    return (now - last_seen_at).total_seconds() / 3600


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

    @classmethod
    def label(cls, silence):
        """What a silence is called, for a table cell.

        A classmethod because a dataset's silence, a centre's and the
        overview's are the same three states read off three different rows.
        """
        return cls.LABELS.get(silence, silence)


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
        return Silence.label(self.silence)

    @property
    def expectation_label(self):
        """Where the expectation came from, for a table cell.

        Shown beside the interval rather than instead of it, because a person
        deciding whether to correct a baseline needs to know whether they are
        looking at their own answer or the tool's.
        """
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
        return cls(
            silence=Silence.UNKNOWN,
            silent_dataset_count=0,
            judged_dataset_count=0,
        )

    @property
    def silence_label(self):
        """What the centre's silence is called, for a table cell."""
        return Silence.label(self.silence)


def dataset_silence(*, now=None, node=None):
    """Every live dataset, with how long it has been quiet and whether that is odd.

    Args:
        now: the instant quiet is measured up to.
        node: keep only this node's datasets, or all of them.

    Returns:
        list[DatasetSilenceRow]: the silent first, furthest overdue before them.

    Only datasets the registry still calls active are judged. One the
    catalogue has dropped, or that has been marked inactive, is not something
    anyone is waiting to hear from, and reporting it silent would fill the
    finding with datasets nobody expects to publish.
    """
    now = now or dj_timezone.now()
    records_from = _records_begin(now)

    rows = [
        _row(dataset, now=now, records_from=records_from)
        for dataset in _live_datasets(node, now=now)
    ]

    return sorted(rows, key=_reading_order)


def silence_by_node(*, now=None):
    """Each centre's datasets, reduced to the one line the overview shows.

    Returns:
        dict[int, NodeSilence]: keyed by node id, for every centre that has a
        live dataset at all. A centre with none is absent rather than
        reported quiet: there is nothing it was expected to publish.
    """
    counts = {}

    for row in dataset_silence(now=now):
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


def _live_datasets(node, *, now):
    """The datasets anyone is waiting to hear from, with what is known of them.

    The learned interval is annotated rather than followed as a relation: a
    dataset with too little history has no baseline at all, and a left join
    says so as a null instead of raising when it is read.
    """
    datasets = Dataset.objects.filter(status=Dataset.ACTIVE).select_related("node")

    if node is not None:
        datasets = datasets.filter(node=node)

    return list(
        with_last_active_hour(datasets, now=now).annotate(
            learned_interval_hours=F("cadence_baseline__interval_hours"),
            learned_from=F("cadence_baseline__observations"),
        )
    )


def with_last_active_hour(datasets, *, now):
    """Each dataset beside the most recent hour it was seen publishing in.

    Public because a dataset nobody is waiting to hear from still has a last
    publication, and a page that lists one has the same question to answer as
    this module does -- asked the same way, off the same index, rather than
    spelled a second time somewhere the two could drift apart.
    """
    return datasets.annotate(last_active_hour=Subquery(_last_active_hour(now)))


def _last_active_hour(now):
    """The most recent hour the outer dataset was seen publishing in.

    One row per dataset, ordered off the index on the rollups, so asking it of
    every dataset in the region is a backwards walk each rather than a scan of
    the whole table.

    Every vantage point counts. Whether the world received what a centre
    published is the propagation report's question; a centre heard only at its
    own broker is publishing, and calling it silent here would report the same
    fault twice under two names, one of them wrong.

    The hour in progress counts and nothing beyond it does. Buckets ahead of
    the instant being judged only exist when an older moment is being asked
    about, and letting them answer would have a dataset judged quiet against
    publications that had not happened yet.
    """
    return (
        HourlyRollup.objects.filter(
            dataset=OuterRef("pk"),
            message_count__gt=0,
            hour__lt=floor_to_hour(now) + timedelta(hours=1),
        )
        .order_by("-hour")
        .values("hour")[:1]
    )


def _records_begin(now):
    """The earliest hour this tool can show it was listening to the region.

    What a dataset never seen publishing is measured against. Absence before
    this is not evidence of silence -- there was nothing here to have seen it
    -- and treating it as evidence would greet every new deployment with a
    region-wide outage that never happened.

    Read off the vantage points this tool listens to live, and off those only.
    A centre's own message archive can be pulled months deep, which is what it
    is for; counting those hours here would move the floor for the whole
    region back to a stretch nobody was watching, and every dataset never seen
    publishing -- at every other centre, whose archive was never asked --
    would be reported quiet for months that only one centre has any record of.
    A pull of one centre's history is evidence about that centre, and the hours
    it reaches back through are not hours this tool was hearing anyone else.

    The centre that was pulled loses nothing by it. Its datasets that did
    publish are judged on the hours their own messages landed in, archive and
    all; this floor is only ever reached for a dataset never seen at all, where
    measuring from when the tool arrived understates the quiet rather than
    inventing it.
    """
    earliest = (
        HourlyRollup.objects.exclude(source__source_type=MessageSource.ORIGIN_API)
        .aggregate(earliest=Min("hour"))["earliest"]
    )

    return earliest or now


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


def _hours_quiet(last_active_hour, *, now, records_from):
    """How long a dataset has been quiet, as far as the buckets can say.

    Counted from the end of the hour it last published in, because that is the
    latest instant the bucket admits a message at and therefore the smallest
    quiet the evidence supports. Negative never means anything -- a dataset
    that published in the hour in progress is not quiet at all -- so it floors
    at nothing.

    A dataset never seen publishing is quiet for at least as long as this tool
    has held records. A lower bound rather than an answer, and the honest one:
    it is what keeps a dataset expected less often than the records go back
    from being called silent on no evidence at all.
    """
    quiet_since = (
        records_from if last_active_hour is None
        else last_active_hour + timedelta(hours=1)
    )

    return max((now - quiet_since).total_seconds() / 3600, 0)


def _row(dataset, *, now, records_from):
    """One dataset as a finding."""
    expected, expectation, observations = _expectation(dataset)
    last_active_hour = dataset.last_active_hour
    hours_quiet = _hours_quiet(
        last_active_hour, now=now, records_from=records_from
    )

    return DatasetSilenceRow(
        dataset_id=dataset.pk,
        node_id=dataset.node_id,
        centre_id=dataset.node.centre_id,
        title=dataset.display_title,
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
