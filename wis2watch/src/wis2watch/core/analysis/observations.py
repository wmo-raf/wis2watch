"""What a centre's observation datasets amount to.

This installation is read on login to answer one question -- are observations
coming out of these centres -- so the centre verdict is anchored here rather
than on the centre's traffic as a whole (ADR-0017). A centre whose synops died
three days ago and whose aerodrome reports are flowing has stopped doing the
thing this tool is watching for, and every fold below says so.

**Only the datasets a centre still publishes, and only the observations among
them.** Which of them is an observation is the topic hierarchy's answer and
not this module's (ADR-0016): the classification is read through the dataset,
so this fold and the kind badge on the node detail page can never come to
disagree about one row.

**Folded from the silence rows rather than queried again.** The quiet of every
live dataset in the region is already worked out one backwards walk each off
the rollups' ``(dataset, -hour)`` index, and the most recent hour a centre's
observations published in is the latest of those walks. Asking the database a
second question, per node, would be a second derivation of one fact -- and the
staleness column and the silence column beside it would then be free to
disagree about whether a centre published this morning.

**A centre with no observation datasets is absent**, the way a centre with no
datasets at all was absent before it. It is not quiet and it is not stale:
there is nothing it was expected to publish, and reporting one of those states
would put a fault on a centre that has committed none. What that absence is
read as is the caller's business -- the overview names it as a state of its
own.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone as dj_timezone

from .silence import Silence, dataset_silence


@dataclass(frozen=True)
class NodeObservations:
    """One centre's observation traffic, as the verdict surfaces read it.

    Both of the judgements the verdict is folded from ride here, because both
    are answered by the same pass over the same rows: how long it is since any
    observation of this centre's arrived, and whether the ones with an
    expectation are keeping to it.

    ``dataset_count`` is what separates "quiet" from "nothing to be quiet
    about". A centre with no observation datasets carries a last hour of
    ``None`` exactly as a centre whose observations have never published does,
    and the two are not the same finding.
    """

    #: How many observation datasets the centre still publishes.
    dataset_count: int
    #: The most recent hour any of them was seen publishing in, or None.
    last_active_hour: datetime | None
    #: What those datasets' own cadences say, worst-of: one past its
    #: expectation is a centre worth looking at whatever the rest are doing.
    silence: str
    #: How many were past it, out of how many could be judged at all. Both
    #: are counts of observation datasets and neither is a count of the
    #: centre's datasets -- the difference is what the sentence under the
    #: verdict has to spell out for a reader comparing it against the
    #: dataset column.
    silent_dataset_count: int
    judged_dataset_count: int

    @classmethod
    def none_declared(cls):
        """A centre that declares no observations at all.

        What stands in for a centre the fold never mentioned, so that a caller
        reads one shape whatever the region holds rather than testing for a
        missing key before every field.
        """
        return cls(
            dataset_count=0,
            last_active_hour=None,
            silence=Silence.UNKNOWN,
            silent_dataset_count=0,
            judged_dataset_count=0,
        )

    @property
    def declares_observations(self):
        """Whether the centre publishes anything this tool is watching for."""
        return self.dataset_count > 0


def observations_by_node(*, now=None):
    """Each centre's observation datasets, folded into one line.

    Args:
        now: the instant quiet is measured up to.

    Returns:
        dict[int, NodeObservations]: keyed by node id, for every centre that
        still publishes an observation dataset. A centre publishing none is
        absent rather than reported quiet.
    """
    now = now or dj_timezone.now()
    by_node = defaultdict(list)

    for row in dataset_silence(now=now):
        if row.is_observation:
            by_node[row.node_id].append(row)

    return {node_id: _folded(rows) for node_id, rows in by_node.items()}


def _folded(rows):
    """One centre's observation datasets as a single line.

    A centre is as concerning as its worst observation dataset -- one past its
    expectation is a centre worth looking at, whatever the rest is doing --
    which is what the counts beside the verdict are for.
    """
    silent = sum(row.is_silent for row in rows)
    judged = sum(row.expected_interval_hours is not None for row in rows)
    hours = [row.last_active_hour for row in rows if row.last_active_hour]

    return NodeObservations(
        dataset_count=len(rows),
        last_active_hour=max(hours, default=None),
        silence=(
            Silence.SILENT if silent
            else Silence.ON_SCHEDULE if judged
            else Silence.UNKNOWN
        ),
        silent_dataset_count=silent,
        judged_dataset_count=judged,
    )
