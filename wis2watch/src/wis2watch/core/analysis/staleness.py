"""How long anything may be quiet before it is worth looking at.

One flat threshold, deliberately, and its own module because two different
surfaces judge two different things by it: the overview's staleness column,
and whether a station a centre transmits for has stopped.

Flat is the right shape for both. Whether a dataset's quiet is a fault is a
question about that dataset's own rhythm, and ``wis2watch.core.analysis.
silence`` answers it; this one is cruder on purpose -- it puts what is worth
looking at first, and something quiet for a month is worth looking at whatever
its rhythm was.

Stations get no learned cadence of their own. A station transmits as often as
the datasets it feeds publish, and there are tens of thousands of them, so
learning a baseline each would be a great deal of machinery for a judgement
this threshold already makes well enough: heard from lately, or not.
"""

from django.conf import settings
from django.utils.translation import gettext_lazy as _

#: How long anything may be quiet before it is called stale.
DEFAULT_STALE_AFTER_HOURS = 24


class Staleness:
    """How concerning a centre's quiet is.

    Four states rather than three, because a centre can have nothing to be
    quiet about. What the overview measures is observation traffic (ADR-0017),
    and a centre that declares no observation datasets at all has published
    nothing this installation was waiting for -- reporting it as never heard
    from, or as gone quiet, would put a fault on a centre that has committed
    none, and would do it on the one row nobody could act on. ``CachePickup``
    carries a third state for exactly this reason and this is the same move.

    ``NO_OBSERVATIONS`` is a statement about the centre's catalogue and not
    about its traffic: such a centre may be publishing warnings by the hour.
    Which is why it is a state here rather than an absence -- an empty cell
    reads as missing data, and this is an answer.
    """

    ACTIVE = "active"
    STALE = "stale"
    NEVER_SEEN = "never_seen"
    NO_OBSERVATIONS = "no_observations"

    CHOICES = [
        (NEVER_SEEN, _("Never seen")),
        (STALE, _("Stale")),
        (NO_OBSERVATIONS, _("No observations")),
        (ACTIVE, _("Active")),
    ]

    LABELS = dict(CHOICES)

    #: Where a state sorts. Derived from ``CHOICES`` rather than written out
    #: again, for the reason ``NodeStanding.RANK`` is: two spellings of one
    #: order is one of them being wrong later.
    #:
    #: It is also the order ``NodeStanding`` puts these states in -- what has
    #: stopped, then what there was nothing to judge, then what is fine -- and
    #: it has to be. The overview sorts by this and the all-centres table
    #: sorts by the standing, over one region; two orderings that disagree
    #: about which centre to look at first are one of them being wrong.
    RANK = {state: rank for rank, (state, _label) in enumerate(CHOICES)}


def default_stale_after_hours():
    """How long anything may be quiet before it is called stale."""
    return getattr(settings, "WIS2WATCH_STALE_AFTER_HOURS", DEFAULT_STALE_AFTER_HOURS)
