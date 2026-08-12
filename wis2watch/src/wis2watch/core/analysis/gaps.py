"""The gap reports: the problems nobody was looking for.

Everything else this tool shows answers a question somebody already thought to
ask. The overview answers "is my region publishing", the node page answers
"what about this centre stopped". These answer questions nobody asked, which is
what makes the tool diagnostic rather than merely informative: a country whose
stations are declared to the world and have never once transmitted, a centre
publishing that no catalogue has indexed, data announced to a broker the rest
of the world never hears.

Five reports, because there are five ways the picture can be wrong that no
single view of one centre can show:

* what a country declares in OSCAR and has never been heard from;
* what is transmitting that no registry -- OSCAR's or a centre's own --
  declares;
* what a centre published that the Global Broker never carried;
* which centres publish with no catalogue record at all;
* how much of each centre's traffic says nothing about which station it came
  from.

Every one of them is a list of named entities rather than a count. "Seventeen
stations are silent" is not a finding anybody can act on; a WIGOS identifier, a
territory and the centre that ought to know about it is a conversation someone
can have. So each row carries what it would take to open that conversation, and
the reports are bounded by filters rather than by truncation -- because a
report that quietly drops findings is worse than one that is long.

Two of those filters do most of the work, and both exist to keep the reports
readable rather than complete. OSCAR is notoriously stale, so only stations it
still reports as fully operational count as declared. And a centre whose own
broker this tool cannot currently reach has its propagation gaps withheld,
because they are indistinguishable from this tool having stopped listening.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db.models import Exists, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext_lazy as _
from django_countries.fields import Country

from ..models import (
    HourlyRollup,
    MessageSource,
    PropagationGap,
    StationSource,
    UnregisteredCentre,
)
from ..rollups import window_start
from .silence import hours_between

#: Over how many hours a centre's unattributed share is worked out. Longer than
#: the overview's volume window on purpose: a share needs enough messages
#: behind it to mean anything, and a centre publishing twice a day has none
#: over a day.
DEFAULT_ATTRIBUTION_WINDOW_HOURS = 168


def default_attribution_window_hours():
    """Over how many hours the unattributed share is worked out."""
    return getattr(
        settings,
        "WIS2WATCH_ATTRIBUTION_WINDOW_HOURS",
        DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    )


@dataclass(frozen=True)
class SilentStationRow:
    """A station a country declares to the world and nothing has ever heard.

    The territory is what makes the row actionable: OSCAR files a station under
    a territory rather than a centre, so that is who the declaration belongs
    to. Beside it, whether any centre's own registry declares the same station,
    because the two are different conversations -- a station its centre knows
    about and never transmits for is an ingestion to fix, and one only OSCAR
    has ever heard of is a registration to correct.
    """

    station_id: int
    wigos_id: str
    name: str
    territory: str
    wmo_region: str
    facility_type: str
    registry_centre_id: str

    @property
    def display_name(self):
        """What to call the station, falling back to what identifies it."""
        return self.name or self.wigos_id


@dataclass(frozen=True)
class UndeclaredStationRow:
    """A station transmitting under a centre's topics that no registry declares.

    One row per centre that transmits for it rather than one per station: a
    station carried by two centres is two registration gaps, each of which is
    somebody's to close.
    """

    station_id: int
    wigos_id: str
    name: str
    node_id: int | None
    centre_id: str
    last_transmitted: datetime | None
    hours_quiet: float | None

    @property
    def display_name(self):
        """What to call the station, falling back to what identifies it."""
        return self.name or self.wigos_id


@dataclass(frozen=True)
class PropagationGapRow:
    """One notification a centre published that the world never received."""

    gap_id: int
    node_id: int
    centre_id: str
    node_name: str
    dataset_title: str
    notification_id: str
    topic: str
    published_at: datetime
    observed_at_origin: datetime
    detected_at: datetime
    hours_missing: float | None


@dataclass(frozen=True)
class UnregisteredCentreRow:
    """A centre of the region publishing that no catalogue has indexed."""

    centre_id: str
    country_code: str
    country_name: str
    sample_topic: str
    first_seen_at: datetime
    last_seen_at: datetime
    hours_unregistered: float | None


@dataclass(frozen=True)
class UnattributedRateRow:
    """How much of one centre's traffic names no station.

    A share rather than a count, because centres publish at wildly different
    volumes and the finding is about the habit rather than the amount. What it
    is not is a fault on its own: whole data categories -- gridded products,
    satellite imagery, warnings -- have no station to name, so a high share is
    a question to ask the centre, not an answer.
    """

    node_id: int
    centre_id: str
    name: str
    country_code: str
    country_name: str
    message_count: int
    unattributed_count: int

    @property
    def attributed_count(self):
        """How many of the centre's messages named the station they came from."""
        return self.message_count - self.unattributed_count

    @property
    def rate(self):
        """The share of the centre's messages naming no station, from 0 to 1."""
        if not self.message_count:
            return 0

        return self.unattributed_count / self.message_count

    @property
    def percent(self):
        """The same share as a percentage, for a table cell."""
        return self.rate * 100


def stations_declared_but_silent(*, now=None):
    """Stations OSCAR calls operational that have never once transmitted.

    Args:
        now: unused; taken so that every report is asked for the same way.

    Returns:
        list[SilentStationRow]: by territory, then by station name.

    Never transmitted, rather than not lately: a station that stopped in March
    is the centre page's finding, and naming it here as well would report one
    fault twice under two names. What this is for is the country whose declared
    network is not connected to WIS2 at all.
    """
    return [_silent_station_row(declaration) for declaration in _silent_declarations()]


def stations_transmitting_undeclared(*, now=None):
    """Stations being heard from that no registry admits to.

    Args:
        now: the instant the quiet beside each station is measured up to.

    Returns:
        list[UndeclaredStationRow]: by centre, then by identifier.

    Declared anywhere is declared: a station another centre's registry names is
    not a registration gap, whoever is transmitting for it. What is left is
    traffic attributed to stations no official or local declaration has ever
    mentioned, which is the registration nobody has noticed is missing.
    """
    now = now or dj_timezone.now()

    return [
        _undeclared_station_row(observation, now=now)
        for observation in _undeclared_observations()
    ]


def propagation_gaps(*, now=None):
    """Notifications published at origin that the Global Broker never carried.

    Args:
        now: the instant each gap's age is measured up to.

    Returns:
        list[PropagationGapRow]: newest publication first.

    Only centres whose own broker answers right now are reported on. A gap
    recorded while a broker was up and read after it went dark cannot be told
    from this tool having stopped listening, and sending somebody to a centre
    to ask about messages that may never have gone missing is how a diagnostic
    stops being believed.
    """
    now = now or dj_timezone.now()

    return [_propagation_gap_row(gap, now=now) for gap in _reportable_gaps()]


def unregistered_centres(*, now=None):
    """Centres of the region publishing with no discovery catalogue record.

    Args:
        now: the instant each centre's time unregistered is measured up to.

    Returns:
        list[UnregisteredCentreRow]: longest unregistered first.

    What the wildcard sweep found by listening past the registry. A centre here
    is publishing to WIS2 while being invisible to everything that reads a
    catalogue -- including, but for the sweep, this tool.
    """
    now = now or dj_timezone.now()

    return [
        _unregistered_centre_row(centre, now=now)
        for centre in _open_unregistered_centres()
    ]


def unattributed_rates(*, now=None, window_hours=None):
    """Each publishing centre's share of messages naming no station.

    Args:
        now: the instant the window ends with the hour of.
        window_hours: how many hourly buckets of traffic the share is worked
            out over.

    Returns:
        list[UnattributedRateRow]: worst share first, and among equals the
        centre publishing most.

    Every centre heard publishing in the window, not only the ones with a share
    to answer for: this is a rate report, and a rate is only readable against
    the centres that are doing it right. A centre that published nothing is
    absent rather than reported at nought per cent, which would read as exactly
    that -- doing it right -- when what it has actually done is go quiet.

    Counted from the Global Broker alone. The same publication is observed at
    the centre's own broker and again on every cache that carried it, so
    counting every vantage point would report one message as several and shrink
    every share by however many caches happened to pick the centre up.
    """
    now = now or dj_timezone.now()
    hours = (
        default_attribution_window_hours() if window_hours is None else window_hours
    )

    rows = [
        _unattributed_rate_row(counted)
        for counted in _traffic_by_centre(since=window_start(now, hours))
    ]

    return sorted(rows, key=lambda row: (-row.rate, -row.message_count, row.centre_id))


def _silent_declarations():
    """OSCAR's operational declarations that no observation answers.

    The observed declaration is what says a station has ever transmitted, and
    it is asked for by station rather than by node: a station heard under any
    centre's topics has been heard.
    """
    observed = StationSource.objects.filter(
        station=OuterRef("station_id"), source_type=StationSource.OBSERVED
    )

    # Ordered so that a station two centres declare names the same one of them
    # every time the report is read. Which centre it names decides whether the
    # row is somebody's ingestion to fix or somebody's registration to correct,
    # and an answer that changes between two readings of the same page is not
    # one anybody can act on.
    by_a_centre = StationSource.objects.filter(
        station=OuterRef("station_id"),
        source_type=StationSource.NODE_REGISTRY,
        node__isnull=False,
    ).order_by("node__centre_id")

    return (
        StationSource.objects.declared_in_oscar()
        .filter(~Exists(observed))
        .annotate(registry_centre_id=Subquery(by_a_centre.values("node__centre_id")[:1]))
        .order_by("station__territory", "station__name", "station__wigos_id")
    )


def _silent_station_row(declaration):
    """One OSCAR declaration nothing has answered, as a finding."""
    station = declaration.station

    return SilentStationRow(
        station_id=station.pk,
        wigos_id=station.wigos_id,
        name=station.name,
        territory=station.territory,
        wmo_region=station.wmo_region,
        facility_type=station.get_facility_type_display(),
        registry_centre_id=declaration.registry_centre_id or "",
    )


def _undeclared_observations():
    """Observations of stations that neither OSCAR nor any centre declares.

    Centres with no catalogue record sort first, since their observations carry
    no centre at all: a station transmitting under a centre nothing has heard
    of is the least accounted-for traffic there is, and letting it settle to
    the bottom of the report is how it stays that way.
    """
    declared = StationSource.objects.filter(
        station=OuterRef("station_id"),
        source_type__in=(StationSource.OSCAR, StationSource.NODE_REGISTRY),
    )

    return (
        StationSource.objects.filter(source_type=StationSource.OBSERVED)
        .filter(~Exists(declared))
        .select_related("station", "node")
        .order_by(F("node__centre_id").asc(nulls_first=True), "station__wigos_id")
    )


def _undeclared_station_row(observation, *, now):
    """One observation of an undeclared station, as a finding."""
    station = observation.station

    return UndeclaredStationRow(
        station_id=station.pk,
        wigos_id=station.wigos_id,
        name=station.name,
        node_id=observation.node_id,
        centre_id=observation.node.centre_id if observation.node_id else "",
        last_transmitted=observation.last_seen,
        hours_quiet=hours_between(observation.last_seen, now),
    )


def _reportable_gaps():
    """The open gaps of centres whose own broker still answers."""
    return (
        PropagationGap.objects.open()
        .filter(node__in=MessageSource.objects.watched_origins().values("node_id"))
        .select_related("node", "dataset")
    )


def _propagation_gap_row(gap, *, now):
    """One open gap as a finding."""
    return PropagationGapRow(
        gap_id=gap.pk,
        node_id=gap.node_id,
        centre_id=gap.node.centre_id,
        node_name=gap.node.name,
        dataset_title=gap.dataset.title if gap.dataset_id else "",
        notification_id=gap.notification_id,
        topic=gap.topic,
        published_at=gap.published_at,
        observed_at_origin=gap.observed_at_origin,
        detected_at=gap.detected_at,
        hours_missing=hours_between(gap.published_at, now),
    )


def _open_unregistered_centres():
    """The centres the registry has still not caught up with."""
    return UnregisteredCentre.objects.unregistered().order_by(
        "first_seen_at", "centre_id"
    )


def _unregistered_centre_row(centre, *, now):
    """One unregistered centre as a finding."""
    return UnregisteredCentreRow(
        centre_id=centre.centre_id,
        country_code=centre.country.code if centre.country else "",
        country_name=centre.country.name if centre.country else "",
        sample_topic=centre.sample_topic,
        first_seen_at=centre.first_seen_at,
        last_seen_at=centre.last_seen_at,
        hours_unregistered=hours_between(centre.first_seen_at, now),
    )


def _traffic_by_centre(*, since):
    """What each centre published in the window, and how much of it named nothing.

    Both counts come off the same grouped pass over the rollups, because they
    are a share of one another: derived separately they could be drawn over
    slightly different sets of buckets, and a share of two numbers that do not
    belong together is worse than no share at all.

    A centre whose messages all name a station has no unattributed buckets to
    sum, which is a null rather than a nought -- so it is coalesced, not left
    to make the arithmetic below disappear.

    The sums are named apart from the column they are drawn from, because a
    bucket's own count and a centre's total are different numbers and the ORM
    will not let one alias stand for both.
    """
    return (
        HourlyRollup.objects.filter(
            hour__gte=since, source__source_type=MessageSource.GLOBAL_BROKER
        )
        .values("node_id", "node__centre_id", "node__name", "node__country")
        .annotate(
            messages=Sum("message_count"),
            unattributed=Coalesce(
                Sum("message_count", filter=Q(station__isnull=True)), 0
            ),
        )
    )


def _unattributed_rate_row(counted):
    """One centre's counted traffic as a finding."""
    country = Country(counted["node__country"] or "")

    return UnattributedRateRow(
        node_id=counted["node_id"],
        centre_id=counted["node__centre_id"],
        name=counted["node__name"],
        country_code=country.code,
        country_name=country.name,
        message_count=counted["messages"],
        unattributed_count=counted["unattributed"],
    )


def _centres_naming_no_station(*, now):
    """How many centres left at least one message unattributed in the window.

    The rate report's headline, and deliberately not the number of rows it
    lists: it lists every publishing centre, including the ones with nothing to
    answer for, and an index promising a hundred findings that turn out to be a
    hundred centres doing it right is how the index stops being read.
    """
    return (
        HourlyRollup.objects.filter(
            hour__gte=window_start(now, default_attribution_window_hours()),
            source__source_type=MessageSource.GLOBAL_BROKER,
            station__isnull=True,
            message_count__gt=0,
        )
        .values("node_id")
        .distinct()
        .count()
    )


@dataclass(frozen=True)
class GapReport:
    """One report: what it finds, and how to ask for it.

    The five are held as a list rather than as five hard-wired pages so that
    the index, the routing and the report itself all read from one place. A
    report that exists but is not on the index is a finding nobody sees, which
    is the failure this whole module exists to prevent.

    The two callables are named as the verbs they are. A template renders any
    attribute it is given and calls it if it can, so a field called ``count``
    would run a query from the middle of a page that only meant to print a
    number.
    """

    slug: str
    title: str
    description: str
    find_rows: Callable[..., list]
    count_rows: Callable[..., int]


@dataclass(frozen=True)
class GapReportSummary:
    """One line of the index: a report and how much it has found."""

    slug: str
    title: str
    description: str
    count: int


#: The five reports, in the order the index shows them: what is declared and
#: missing, what is arriving and unaccounted for, then the three about the
#: centres themselves.
GAP_REPORTS = (
    GapReport(
        slug="declared-but-silent",
        title=_("Declared but silent stations"),
        description=_(
            "Stations OSCAR/Surface reports as operational that have never "
            "been heard transmitting."
        ),
        find_rows=stations_declared_but_silent,
        count_rows=lambda *, now=None: _silent_declarations().count(),
    ),
    GapReport(
        slug="transmitting-undeclared",
        title=_("Transmitting but undeclared stations"),
        description=_(
            "Stations heard transmitting that neither OSCAR/Surface nor any "
            "centre's own registry declares."
        ),
        find_rows=stations_transmitting_undeclared,
        count_rows=lambda *, now=None: _undeclared_observations().count(),
    ),
    GapReport(
        slug="propagation-gaps",
        title=_("Propagation gaps"),
        description=_(
            "Notifications seen at a centre's own broker that the Global "
            "Broker has never carried. Centres whose own broker cannot "
            "currently be reached are left out."
        ),
        find_rows=propagation_gaps,
        count_rows=lambda *, now=None: _reportable_gaps().count(),
    ),
    GapReport(
        slug="unregistered-centres",
        title=_("Unregistered centres"),
        description=_(
            "Centres of the monitored region publishing with no discovery "
            "catalogue record."
        ),
        find_rows=unregistered_centres,
        count_rows=lambda *, now=None: _open_unregistered_centres().count(),
    ),
    GapReport(
        slug="unattributed-messages",
        title=_("Unattributed messages"),
        description=_(
            "How much of each centre's traffic carries no WIGOS station "
            "identifier. The count is the centres leaving any message "
            "unattributed; the report lists every centre publishing."
        ),
        find_rows=unattributed_rates,
        count_rows=lambda *, now=None: _centres_naming_no_station(
            now=now or dj_timezone.now()
        ),
    ),
)


def gap_report(slug):
    """The report of that name, or None if nothing is called that."""
    return next((report for report in GAP_REPORTS if report.slug == slug), None)


def gap_report_summaries(*, now=None):
    """Every report with how much it has found, for the index.

    Counted rather than listed: the index exists to say which report is worth
    opening, and building five reports in full to show five numbers would make
    the cheapest page in the tool the most expensive.
    """
    now = now or dj_timezone.now()

    return [
        GapReportSummary(
            slug=report.slug,
            title=report.title,
            description=report.description,
            count=report.count_rows(now=now),
        )
        for report in GAP_REPORTS
    ]
