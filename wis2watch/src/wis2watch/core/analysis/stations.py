"""One centre's stations, and what is known of each of them.

Its own module because two surfaces ask the same question of the same rows and
must not answer it twice. The node detail page lists the stations so a
diagnostician can name the one that stopped; the statistics tab counts them so
a reader can tell a node-wide outage from a station-by-station decay. A
dashboard reporting 412 transmitting over a list showing 409 is not a rounding
difference -- it is the moment a reader stops believing either page, and the
only defence against it is that there is one derivation.

What the derivation is: which stations belong to a centre at all, whether the
centre's own registry declares each one, and when this centre last heard from
it. Everything either surface says about a station's standing follows from
those three facts and the flat threshold in ``staleness``.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Exists, OuterRef, Subquery
from django.utils.translation import gettext_lazy as _

from ..models import Station, StationSource
from .silence import BEFORE_ANYTHING, hours_between
from .staleness import default_stale_after_hours


class StationStanding:
    """What is known of one of a centre's stations.

    Out of two facts -- whether the node's own registry declares the station,
    and when anything was last heard from it -- because each absence is a
    different finding. A declared station nothing has heard from is one that
    stopped or never started; one heard from long ago has stopped since; and a
    station transmitting that the registry declares nowhere is a registration
    gap, which dropping from the page because no declaration named it is
    exactly how a transmitting station becomes invisible.

    Quiet is judged against the same flat threshold the overview calls a centre
    stale by, for the reasons ``wis2watch.core.analysis.staleness`` gives.
    Without it every station ever heard from reads as working, and the one that
    stopped in March sits at the bottom of the page in green.
    """

    TRANSMITTING = "transmitting"
    GONE_QUIET = "gone_quiet"
    NEVER_TRANSMITTED = "never_transmitted"
    UNDECLARED = "undeclared"

    CHOICES = [
        (NEVER_TRANSMITTED, _("Declared, never heard from")),
        (GONE_QUIET, _("Gone quiet")),
        (UNDECLARED, _("Transmitting, not declared")),
        (TRANSMITTING, _("Transmitting")),
    ]

    LABELS = dict(CHOICES)

    #: What has stopped first, then what was never declared, then what is
    #: working: the order someone reads a station list in when they came here
    #: because something is missing.
    RANK = {NEVER_TRANSMITTED: 0, GONE_QUIET: 1, UNDECLARED: 2, TRANSMITTING: 3}

    #: The standings that mean nothing has been heard from the station lately.
    #: Named once because the page counts them and the rows report them, and
    #: those are decided in different places.
    SILENT = (NEVER_TRANSMITTED, GONE_QUIET)

    @classmethod
    def of(cls, *, declared, hours_quiet, stale_after):
        """What one station's declarations and last transmission amount to."""
        if hours_quiet is None:
            return cls.NEVER_TRANSMITTED

        if hours_quiet > stale_after:
            return cls.GONE_QUIET

        return cls.TRANSMITTING if declared else cls.UNDECLARED


@dataclass(frozen=True)
class NodeStationRow:
    """One station of a centre's, and when it last said anything."""

    station_id: int
    wigos_id: str
    name: str
    local_name: str
    local_id: str
    facility_type: str
    latitude: float | None
    longitude: float | None
    elevation: float | None
    declared_by_registry: bool
    last_transmitted: datetime | None
    hours_quiet: float | None
    standing: str

    @property
    def standing_label(self):
        """What this station's standing is called, for a table cell."""
        return StationStanding.LABELS.get(self.standing, self.standing)

    @property
    def display_name(self):
        """What to call the station, preferring the operator's own name.

        The name a node assigns is the one its staff will recognise, and is
        often the only one there is: a station created from observed traffic
        alone has no canonical name until OSCAR is read for it.
        """
        return self.local_name or self.name or self.wigos_id

    @property
    def is_located(self):
        """Whether this station can be put on a map at all.

        A station minted from observed traffic carries no coordinates until
        something declares it, so a map of a centre's stations is always a
        subset -- and the count of what it left out is what stops a reader
        assuming the residue is uninteresting.
        """
        return self.latitude is not None and self.longitude is not None


def node_stations(node, *, now, stale_after=None):
    """Every station this centre declares or has been heard transmitting for.

    Started from the stations rather than from the declarations, because a
    station is one station however many sources named it: listing the
    declarations would give a station its node declares and has transmitted
    two rows, and the whole point of the column is that those are two facts
    about one thing.

    OSCAR's declarations are not among them. OSCAR declares against a
    territory rather than a centre, so what it says belongs to the country's
    picture -- the declared-but-silent report -- and reading it here would put
    stations on a centre's page that the centre has never claimed and never
    transmitted.

    Args:
        node: the centre whose stations to read.
        now: the instant quiet is measured up to.
        stale_after: how many hours of quiet is too many, for a caller that
            has already asked. Left out, the flat threshold is read here.

    Returns:
        list[NodeStationRow]: the centre's stations, what has stopped first.
    """
    if stale_after is None:
        stale_after = default_stale_after_hours()

    declared = StationSource.objects.filter(
        station=OuterRef("pk"),
        source_type=StationSource.NODE_REGISTRY,
        node=node,
    )

    # The centre's own observation, not the station's latest anywhere. A
    # station may transmit under more than one centre's topics, and reading
    # another centre's observation here would report this one as publishing
    # something it never sent.
    observed = StationSource.objects.filter(
        station=OuterRef("pk"),
        source_type=StationSource.OBSERVED,
        node=node,
        last_seen__isnull=False,
    )

    stations = (
        Station.objects.filter(sources__node=node)
        .distinct()
        .annotate(
            declared_by_registry=Exists(declared),
            local_name=Subquery(declared.values("local_name")[:1]),
            local_id=Subquery(declared.values("local_id")[:1]),
            last_transmitted=Subquery(observed.values("last_seen")[:1]),
        )
    )

    rows = [
        _station_row(station, now=now, stale_after=stale_after) for station in stations
    ]

    return sorted(rows, key=_reading_order)


def _station_row(station, *, now, stale_after):
    """One station as a finding."""
    hours_quiet = hours_between(station.last_transmitted, now)
    location = station.location

    return NodeStationRow(
        station_id=station.pk,
        wigos_id=station.wigos_id,
        name=station.name,
        local_name=station.local_name or "",
        local_id=station.local_id or "",
        facility_type=station.get_facility_type_display(),
        latitude=location.y if location else None,
        longitude=location.x if location else None,
        elevation=location.z if location and location.hasz else None,
        declared_by_registry=station.declared_by_registry,
        last_transmitted=station.last_transmitted,
        hours_quiet=hours_quiet,
        standing=StationStanding.of(
            declared=station.declared_by_registry,
            hours_quiet=hours_quiet,
            stale_after=stale_after,
        ),
    )


def _reading_order(row):
    """What has stopped first, and among those the longest quiet."""
    return (
        StationStanding.RANK.get(row.standing, len(StationStanding.RANK)),
        row.last_transmitted or BEFORE_ANYTHING,
        row.wigos_id,
    )
