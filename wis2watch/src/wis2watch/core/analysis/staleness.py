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
    """How concerning a centre's quiet is."""

    ACTIVE = "active"
    STALE = "stale"
    NEVER_SEEN = "never_seen"

    CHOICES = [
        (NEVER_SEEN, _("Never seen")),
        (STALE, _("Stale")),
        (ACTIVE, _("Active")),
    ]

    LABELS = dict(CHOICES)


def default_stale_after_hours():
    """How long anything may be quiet before it is called stale."""
    return getattr(settings, "WIS2WATCH_STALE_AFTER_HOURS", DEFAULT_STALE_AFTER_HOURS)
