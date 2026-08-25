"""The gap reports: the problems nobody was looking for.

Everything else this tool shows answers a question somebody already thought to
ask. The overview answers "is my region publishing", the node page answers
"what about this centre stopped". These answer questions nobody asked, which is
what makes the tool diagnostic rather than merely informative: a country whose
stations are declared to the world and have never once transmitted, a centre
publishing that no catalogue has indexed, data announced to a broker the rest
of the world never hears.

Six reports, because there are six ways the picture can be wrong that no
single view of one centre can show:

* what a country declares in OSCAR and has never been heard from;
* what is transmitting that no registry -- OSCAR's or a centre's own --
  declares;
* what a centre published that the Global Broker never carried;
* which centres publish with no catalogue record at all;
* whose own station registry has stopped answering, or never did;
* how much of each centre's traffic says nothing about which station it came
  from.

The last of those is the only one that is a finding about this tool's reach
rather than about the region alone, and it earns its place beside the others
for the reason all of them are here: nobody was looking. A registry that fails
every hourly run leaves a failed sync log every hour, and until something read
them the failure was visible only to whoever opened that centre's page already
suspecting it -- which for fifty-four countries is nobody. It is a pattern
over time rather than one bad run, so it is reported where the patterns are
rather than announced as a hard failure: one centre's dead registry costs one
of the three station pictures for one centre, and the tool's answers about
everywhere else stay good.

Every one of them is a list of named entities rather than a count. "Seventeen
stations are silent" is not a finding anybody can act on; a WIGOS identifier, a
territory and the centre that ought to know about it is a conversation someone
can have. So each row carries what it would take to open that conversation, and
the reports are bounded by filters rather than by truncation -- because a
report that quietly drops findings is worse than one that is long.

Two of those filters do most of the work, and both exist to keep the reports
readable rather than complete. OSCAR is notoriously stale, so only stations it
still reports as fully operational count as declared. And a centre no vantage
point of its own currently answers at has its propagation gaps withheld,
because they are indistinguishable from this tool having stopped listening.

A third withholds a whole report rather than rows of one, and does it for the
same reason. Every one of these compares what is arriving against what the
registry says should exist, so a registry that has stopped being rebuilt makes
one of them wrong across the board: a centre the catalogue has never indexed
and a centre whose record this tool has not read are the same centre from
here. That is a hard failure rather than a finding, it is announced as one,
and the report it invalidates says so and holds its rows until it clears.

One thing more is said and nothing withheld for it. Two of these reports read
what a centre's own registry declares, and a centre whose catalogue records
advertise no address for it has no registry to read: nothing has ever asked it.
Its stations stay listed -- traffic nothing accounts for is worth naming
whoever failed to declare it -- but the claim that the centre declares nothing
is not the reports' to make. So they say which centres nobody asked: on the row
where the row names a centre, and once above the table where it names a
territory instead.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Exists, F, Max, Min, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone as dj_timezone
from django.utils.formats import date_format
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django_countries.fields import Country

from ..models import (
    HardFailure,
    HourlyRollup,
    MessageSource,
    PropagationGap,
    StationSource,
    SyncLog,
    UnregisteredCentre,
    WIS2Node,
    evidence_horizon,
)
from ..rollups import window_start
from .reachability import OriginTransport
from .silence import hours_between

#: Over how many hours a centre's unattributed share is worked out. Longer than
#: the overview's volume window on purpose: a share needs enough messages
#: behind it to mean anything, and a centre publishing twice a day has none
#: over a day.
DEFAULT_ATTRIBUTION_WINDOW_HOURS = 168

#: Which report answers "is this share bad?" for a centre. Named here beside
#: the report rather than spelled again wherever a link to it is reversed: a
#: slug that lives in two places is a link that survives the rename of one.
UNATTRIBUTED_MESSAGES_SLUG = "unattributed-messages"

#: How long a centre's registry may fail every run before the report names it,
#: in hours. Against an hourly sync that is some fifty consecutive failures --
#: far past a host restarting overnight or a certificate renewed badly at
#: lunchtime, and still soon enough that a centre is told in the same week its
#: registry went. Deliberately not one of the alerting thresholds: this is not
#: a failure of the tool, and nobody is being interrupted for it.
DEFAULT_REGISTRY_UNANSWERED_HOURS = 48

#: How much of a run's error the digest will quote. Enough for a read timeout,
#: a refused connection or an HTTP status to be recognised, and not enough for
#: a registry answering with a page of HTML to fill the mail.
ERROR_EXCERPT_CHARS = 200


def default_attribution_window_hours():
    """Over how many hours the unattributed share is worked out."""
    return getattr(
        settings,
        "WIS2WATCH_ATTRIBUTION_WINDOW_HOURS",
        DEFAULT_ATTRIBUTION_WINDOW_HOURS,
    )


def default_registry_unanswered_hours():
    """How long a registry may fail every run before the report names it."""
    return getattr(
        settings,
        "WIS2WATCH_REGISTRY_UNANSWERED_HOURS",
        DEFAULT_REGISTRY_UNANSWERED_HOURS,
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


class DeclaringCentre:
    """What is known of the centre that ought to have declared this station.

    Three states rather than two flags, because each is a different errand and
    the row, the notice and the table cell were each working the pair out for
    themselves. A centre no catalogue has indexed cannot advertise a registry
    anywhere, so saying it advertises none says nothing about it -- that is the
    unregistered report's finding, not this one's.

    ``UNASKED`` is the one this distinction exists for. Nothing has ever asked
    such a centre what it declares, so a station under it is undeclared only as
    far as anything knows: the two African centres this was checked against
    turned out to run registries of 71 and 12 stations behind an address no
    record advertises.
    """

    UNREGISTERED = "unregistered"
    UNASKED = "unasked"
    ASKED = "asked"

    CHOICES = [
        (UNREGISTERED, _("Unregistered centre")),
        (UNASKED, _("Advertises no station registry")),
        (ASKED, _("Registry read")),
    ]

    LABELS = dict(CHOICES)

    @classmethod
    def of(cls, node):
        """Which of the three the centre behind an observation is."""
        if node is None:
            return cls.UNREGISTERED

        return cls.ASKED if node.advertises_station_registry else cls.UNASKED

    @classmethod
    def label(cls, value):
        """What that is called, for a cell or an email."""
        return cls.LABELS.get(value, value)


@dataclass(frozen=True)
class UndeclaredStationRow:
    """A station transmitting under a centre's topics that no registry declares.

    One row per centre that transmits for it rather than one per station: a
    station carried by two centres is two registration gaps, each of which is
    somebody's to close.

    Except where nothing asked, which is what ``declaring_centre`` carries: it
    decides whether the row is a finding about the centre at all, and every
    surface that renders the row asks it rather than working it out again.
    """

    station_id: int
    wigos_id: str
    name: str
    node_id: int | None
    centre_id: str
    declaring_centre: str
    last_transmitted: datetime | None
    hours_quiet: float | None

    @property
    def display_name(self):
        """What to call the station, falling back to what identifies it."""
        return self.name or self.wigos_id

    @property
    def declaring_centre_label(self):
        """What is known of the centre behind it, for a table cell."""
        return DeclaringCentre.label(self.declaring_centre)


@dataclass(frozen=True)
class PropagationGapRow:
    """One notification a centre published that the world never received.

    The transport that observed it travels with the row rather than being left
    to the reader, for the reason ``OriginTransport`` gives.
    """

    gap_id: int
    node_id: int
    centre_id: str
    node_name: str
    dataset_title: str
    notification_id: str
    topic: str
    origin_transport: str
    published_at: datetime
    observed_at_origin: datetime
    detected_at: datetime
    hours_missing: float | None

    @property
    def origin_transport_label(self):
        """What the transport that observed this is called, for a cell or an email."""
        return OriginTransport.label(self.origin_transport)


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


class RegistryStanding:
    """Whether a registry that is not answering ever did.

    Two states rather than one, because they send somebody to two different
    places. A registry that answered and stopped is a host that has moved or
    died, and the address this tool holds was right when it was learned: the
    errand is to find where the registry went, and the centre is the only one
    who knows. A registry that has never answered is an address that was
    wrong from the moment it was derived -- read off the host a centre serves
    its metadata from, which is not always the host its API answers on -- and
    the errand is to establish where the API is at all.

    Told apart by history alone, so a centre onboarded last week and a centre
    whose sync logs happen not to reach back far enough would read the same.
    Nothing prunes sync logs, so the second does not arise.
    """

    NEVER_ANSWERED = "never_answered"
    STOPPED = "stopped"

    CHOICES = [
        (NEVER_ANSWERED, _("Has never answered")),
        (STOPPED, _("Stopped answering")),
    ]

    LABELS = dict(CHOICES)

    @classmethod
    def of(cls, last_answered_at):
        """Which of the two a registry is, from when it last answered."""
        return cls.STOPPED if last_answered_at else cls.NEVER_ANSWERED

    @classmethod
    def label(cls, standing):
        """What that is called, for a table cell or an email."""
        return cls.LABELS.get(standing, standing)


@dataclass(frozen=True)
class UnansweredRegistryRow:
    """A centre's own station registry that has failed every run for days.

    The address is on the row because it is the finding. What has gone wrong
    is that this tool is asking somewhere nothing answers, and whether that is
    the centre's host or this tool's guess at it is exactly what an operator
    reads the address to decide.

    Beside it, what the last run said went wrong: a read timeout, a refused
    connection and a 404 are three different conversations, and a report that
    said only "failing" would send somebody to open the sync logs it was
    supposed to have read for them.
    """

    node_id: int
    centre_id: str
    name: str
    country_code: str
    country_name: str
    stations_url: str
    standing: str
    last_answered_at: datetime | None
    unanswered_since: datetime
    hours_unanswered: float
    last_error: str

    @property
    def standing_label(self):
        """Whether it ever answered, for a table cell."""
        return RegistryStanding.label(self.standing)


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


def stations_declared_but_silent_unasked_centres(*, now=None):
    """What this report cannot say about who declares a silent station.

    Args:
        now: unused; taken so that every caveat is asked in the same way.

    Returns:
        str | None: how many centres of the region nothing has been able to
        ask, or nothing where every one of them advertises a registry.

    The "declared by centre" column is blank two ways and cannot tell them
    apart. No centre's own registry names the station, which is a registration
    somebody has to correct; or the centre that would name it advertises no
    address at all, so nothing has ever asked it -- and the column is then
    reporting this tool's blind spot as a centre declaring nothing.

    Said rather than resolved, because a row here cannot be told which centre
    would have declared it. OSCAR files a declaration under a territory rather
    than under a centre, which is the whole reason this report is about
    countries; so the admission is the report's, once, above the table.

    Said only where there is a blank cell to qualify. A report every row of
    which names a declaring centre has nothing this could be about, and one
    with no rows at all is announcing that every declared station has been
    heard from -- a sentence about registries nobody read would read there as
    a reason to doubt that, which it is not.
    """
    unasked = WIS2Node.objects.advertising_no_station_registry().count()

    if not unasked or not _silent_declarations_naming_no_centre().exists():
        return None

    return ngettext(
        "%(count)d centre advertises no station registry, so nothing has ever "
        "asked it what it declares. A station only such a centre declares "
        "reads here as declared by nobody.",
        "%(count)d centres advertise no station registry, so nothing has ever "
        "asked them what they declare. A station only such a centre declares "
        "reads here as declared by nobody.",
        unasked,
    ) % {"count": unasked}


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

    Only centres some vantage point of their own answers at right now are
    reported on -- their broker, or the archive polled where that broker will
    not answer. A gap recorded while a centre could be heard and read after it
    went dark cannot be told from this tool having stopped listening, and
    sending somebody to a centre to ask about messages that may never have
    gone missing is how a diagnostic stops being believed.

    Each row says which of the two saw the notification, for the reason
    ``OriginTransport`` gives.

    And only gaps this tool can still stand behind. An open gap outlives the
    evidence that would close it: past the raw retention window the Global
    Broker rows that could settle it have been expired, so it can neither be
    closed nor checked again, and left here those rows would accumulate for
    ever. A report where last spring's gaps sit permanently above this
    morning's is one people stop opening, which is the failure the reports
    exist to prevent. What is left out that way is counted and said, by
    ``propagation_gaps_left_out`` below.
    """
    now = now or dj_timezone.now()

    return [_propagation_gap_row(gap, now=now) for gap in _reportable_gaps(now=now)]


def propagation_gaps_left_out(*, now=None):
    """What the propagation report holds and does not list, in a sentence.

    Args:
        now: the instant the horizon is worked out from.

    Returns:
        str | None: what was left out and why, or nothing where the report
        lists everything it holds.

    A bound that goes unsaid is truncation, and a report that quietly drops
    findings is worse than one that is long. It matters most where the report
    is otherwise empty: with nothing listed and nothing said, the empty state
    would announce that everything published has reached the Global Broker --
    the one thing this report does not know about the gaps past its horizon.

    Only the gaps the horizon left out. The centres whose own broker is
    unreachable are withheld for a different reason, which the report's own
    description gives; one sentence, one reason.
    """
    now = now or dj_timezone.now()
    left_out = _gaps_past_the_horizon(now=now).count()

    if not left_out:
        return None

    return ngettext(
        "%(count)d older gap is not listed. It was published before "
        "%(horizon)s, beyond which the Global Broker rows that would settle "
        "it have expired, so this tool can no longer check it either way.",
        "%(count)d older gaps are not listed. They were published before "
        "%(horizon)s, beyond which the Global Broker rows that would settle "
        "them have expired, so this tool can no longer check them either way.",
        left_out,
    ) % {
        "count": left_out,
        # Written the way every timestamp in the reports is written, because
        # this one is read against the publication times in the table beside
        # it.
        "horizon": date_format(
            dj_timezone.localtime(evidence_horizon(now)), "Y-m-d H:i"
        ),
    }


def propagation_gaps_unsettled(*, now=None):
    """The centres whose gaps left this report without ever being answered.

    Args:
        now: the instant the horizon is worked out from.

    Returns:
        set[str]: the keys of the findings this report can no longer settle
        either way -- centre IDs, as this report identifies a finding -- and
        an empty set where every centre that has left the report left it
        having been heard from.

    A centre leaves this report two ways that look identical from outside it.
    Its gaps close, which is a path that started working; or its gaps pass the
    horizon, which is this tool running out of evidence while the question
    stands. Whoever reads the report can tell them apart from the sentence
    beside it. Whatever reads it -- the digest -- cannot, and would otherwise
    announce the second as the first.

    The horizon is asked for by the same query that counts it in
    ``propagation_gaps_left_out``: the two disagreeing about what is past it
    would be this same mistake in a second place. Centres whose own vantage
    points have gone dark are absent here for the reason they are absent from
    that sentence too -- their gaps are withheld rather than unanswerable, and
    that is an absence that ends.

    What is asked on top of it is whether the centre has been heard from
    since. A gap the world turned out to carry is the one thing that settles
    anything here, and a centre with one of those later than its last
    unanswerable gap has been observed publishing to a path that works. So
    only a centre whose last word is a question nobody can ask any more is
    named -- otherwise a single gap left open one spring would silence every
    good word about that centre for the life of the installation.
    """
    now = now or dj_timezone.now()

    unanswered = dict(
        _gaps_past_the_horizon(now=now)
        .values_list("node__centre_id")
        .annotate(last=Max("published_at"))
    )

    if not unanswered:
        return set()

    carried = dict(
        PropagationGap.objects.filter(
            node__centre_id__in=unanswered, resolved_at__isnull=False
        )
        .values_list("node__centre_id")
        .annotate(last=Max("published_at"))
    )

    return {
        centre_id
        for centre_id, unanswered_at in unanswered.items()
        if centre_id not in carried or carried[centre_id] < unanswered_at
    }


def unregistered_centres(*, now=None):
    """Centres of the region publishing with no discovery catalogue record.

    Args:
        now: the instant each centre's time unregistered is measured up to.

    Returns:
        list[UnregisteredCentreRow]: longest unregistered first, and nothing
        at all while the registry these are missing from is frozen.

    What the wildcard sweep found by listening past the registry. A centre here
    is publishing to WIS2 while being invisible to everything that reads a
    catalogue -- including, but for the sweep, this tool.

    Which makes the whole report a statement about the registry, and worth
    nothing while the registry has stopped being rebuilt: the centres it names
    then are the ones the writing catalogue has not been asked about, not the
    ones no catalogue knows. Withheld rather than qualified, because a list of
    named centres with a caveat above it is a list somebody acts on.
    """
    now = now or dj_timezone.now()

    return [
        _unregistered_centre_row(centre, now=now)
        for centre in _reportable_unregistered_centres()
    ]


def unregistered_centres_withheld(*, now=None):
    """What the unregistered report is holding back, in a sentence.

    Args:
        now: unused; taken so that every bound is asked in the same way.

    Returns:
        str | None: what is withheld and why, or nothing when the registry is
        current and the report is listing everything it holds.

    Said even when nothing is being held. An empty report with nothing beside
    it announces that every publishing centre in the region has a catalogue
    record, which is the one thing this report cannot know while the catalogue
    it would know it from is unreachable.
    """
    if not _registry_is_frozen():
        return None

    withheld = _open_unregistered_centres().count()

    return ngettext(
        "%(count)d centre is not listed. The catalogue that writes the "
        "registry is not syncing, so a centre publishing with no record "
        "cannot be told from one whose record this tool has not read.",
        "%(count)d centres are not listed. The catalogue that writes the "
        "registry is not syncing, so a centre publishing with no record "
        "cannot be told from one whose record this tool has not read.",
        withheld,
    ) % {"count": withheld}


def unregistered_centres_unsettled(*, now=None):
    """The centres this report has stopped being able to answer for.

    Args:
        now: unused; taken so that every report is asked in the same way.

    Returns:
        set[str]: the centre IDs withheld while the registry is frozen, and an
        empty set whenever it is current.

    The same admission the sentence above makes to a reader, made to the
    digest. A centre leaving this report ordinarily means the registry caught
    up with it, which is news worth sending; a centre leaving it because the
    registry stopped being rebuilt is not, and the grace period cannot tell
    them apart -- a writer unreachable for a week outlasts any grace, and
    every centre the sweep had found would be mailed out as registered.

    Let go rather than held, so nothing is announced and the row is dropped.
    A centre still unregistered once the catalogue answers again is found by
    the next sweep and is news again then, which is the right time to say it:
    it has survived the registry catching up.
    """
    if not _registry_is_frozen():
        return set()

    return set(_open_unregistered_centres().values_list("centre_id", flat=True))


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


def registries_not_answering(*, now=None, unanswered_hours=None):
    """Centres whose own station registry has failed every run for days.

    Args:
        now: the instant each registry's silence is measured up to.
        unanswered_hours: how long a registry must have been failing before it
            is named.

    Returns:
        list[UnansweredRegistryRow]: longest unanswered first, and among
        equals by centre ID.

    One of the three station pictures this tool compares is what a centre's
    own registry declares, and it is the only one asked for directly. Each
    hourly attempt leaves a sync log whether it worked or not, so a registry
    that has failed since March has left some thousands of failed runs and
    said nothing to anybody -- the failure is legible only to somebody who
    opens that centre's page already suspecting it.

    What is reported is the pattern rather than the run. A centre whose host
    restarted overnight fails twice and is not a finding; a centre nothing has
    got an answer out of for days has a registry this tool cannot read, and
    everything downstream is quietly answering about that centre with two
    pictures instead of three.

    A centre nobody asked is not here. It has no address to fail against, no
    sync log and no registry to be silent -- which is the distinction
    ``advertises_station_registry`` exists to keep, and naming it here would
    report a centre for a failure that never happened.
    """
    now = now or dj_timezone.now()
    hours = (
        default_registry_unanswered_hours()
        if unanswered_hours is None
        else unanswered_hours
    )

    unanswered = list(_registries_not_answering(now=now, hours=hours))
    errors = _last_registry_errors(node.pk for node in unanswered)

    return [
        _unanswered_registry_row(node, error=errors.get(node.pk, ""), now=now)
        for node in unanswered
    ]


def registries_not_answering_centre_ids(*, now=None):
    """Which centres the not-answering report currently names.

    Args:
        now: the instant each registry's silence is measured up to.

    Returns:
        set[str]: their centre IDs, empty where every registry is answering.

    The finding without the prose around it, for the one caller that acts on
    it rather than showing it. The catalogue sync asks this before it will
    correct a stored address: a registry this report is naming is one whose
    address is demonstrably dead, which is what entitles a sync that otherwise
    never writes over an address to write over that one.

    Asked here rather than worked out again there, so that the address a sync
    corrects and the address a page reports as dead can never be a different
    set of centres -- which would be a tool quietly editing the registry on
    evidence it was not showing anybody.
    """
    now = now or dj_timezone.now()

    return set(
        _registries_not_answering(
            now=now, hours=default_registry_unanswered_hours()
        ).values_list("centre_id", flat=True)
    )


def registries_not_answering_caveat(*, now=None):
    """What this report cannot say when nothing at all is answering.

    Args:
        now: unused; taken so that every caveat is asked in the same way.

    Returns:
        str | None: that every registry is failing at once and what that
        probably means, or nothing whenever any of them is answering.

    A handful of the region's registries failing is the region. Every one of
    them failing at once is very much more likely to be here -- an outbound
    route lost, a proxy retired, a certificate store gone stale -- and the
    report cannot tell the two apart from the sync logs, because they leave
    identical ones.

    Said rather than withheld, unlike the registry-frozen case above. These
    rows stay true either way: the tool really is failing to read those
    registries, and that is worth showing whoever can go and look. What would
    be wrong is letting somebody take thirty of them to thirty centres, and a
    sentence prevents that where withholding the lot would also hide the only
    evidence of the fault.

    Nothing is said of a single centre failing on its own. One registry down
    is one registry down whichever way it is counted, and "every registry is
    failing" over a set of one is a coincidence dressed as a pattern.
    """
    asked = _registries_asked()
    failing = _registries_failing_now(asked).count()

    if failing < 2 or failing < asked.count():
        return None

    return gettext(
        "None of the %(count)d centres this tool asks is answering. That is "
        "more likely to be a fault here -- an outbound route, a proxy, a "
        "certificate store -- than every registry in the region failing at "
        "once, so check that one of these addresses answers from this host "
        "before taking any of them to a centre."
    ) % {"count": failing}


def _registries_asked():
    """Every centre whose own registry has actually been asked, with what came back.

    Three instants off one pass over the sync logs, all of them filtered to
    the station sync: a centre's message archive answering this morning says
    nothing whatever about its registry, and an unfiltered ``Max`` would read
    it as though it did.

    ``first_asked_at`` is what times a registry that has never answered. There
    is no last-answered instant to count from, and timing it from the first
    failure would say the same thing -- the two are one run apart -- while
    reading as though something had once been different.

    The set this starts from is the set the hourly beat queues, both of them
    ``advertising_a_station_registry``. That is what entitles the sentence
    above to say "the centres this tool asks" and mean it: a centre in here is
    one something is still going to every hour, so its newest run is as recent
    as the schedule, and a stale answer can only mean the schedule itself has
    stopped -- in which case nothing is being asked and there is nothing for
    that sentence to be about.
    """
    asked = Q(sync_logs__sync_type=SyncLog.NODE_STATIONS)
    answered = asked & ~Q(sync_logs__status=SyncLog.FAILED)

    return (
        WIS2Node.objects.advertising_a_station_registry()
        .annotate(
            first_asked_at=Min("sync_logs__started_at", filter=asked),
            last_run_at=Max("sync_logs__started_at", filter=asked),
            last_answered_at=Max("sync_logs__started_at", filter=answered),
        )
        .filter(last_run_at__isnull=False)
    )


def _registries_not_answering(*, now, hours):
    """The centres whose registry has failed every run since long enough ago.

    "Every run since" needs no counting. The newest run being later than the
    newest run that answered is exactly the statement that nothing since that
    answer got one, whatever number of runs sits in between -- so a week in
    which the schedule itself was down cannot be read as a week of failures.

    A partial run answered. What the report is about is whether this tool can
    reach the registry at all; a run that read it and stepped over a record it
    could not store reached it, and is the node page's finding rather than
    this one's.
    """
    return (
        _registries_failing_now(_registries_asked())
        .annotate(unanswered_since=Coalesce("last_answered_at", "first_asked_at"))
        .filter(unanswered_since__lte=now - timedelta(hours=hours))
        .order_by("unanswered_since", "centre_id")
    )


def _registries_failing_now(registries):
    """Those of them whose newest run is one that failed.

    The one place "nothing has answered since" is spelled, because the report
    and the sentence above it both ask it and an answer they disagreed about
    would be a page saying every registry is failing over a table of the ones
    that are not.

    Written as two arms rather than as the negation of "the newest run
    answered", which would be the same statement in SQL only for a registry
    that has answered at some point: there is no instant to compare against
    for one that never has, and a comparison with nothing is neither true nor
    false, so the negation would quietly drop exactly the registries this
    report was built to find.
    """
    return registries.filter(
        Q(last_answered_at__isnull=True) | Q(last_run_at__gt=F("last_answered_at"))
    )


def _last_registry_errors(node_ids):
    """What the newest failed run said went wrong, per centre.

    Asked of the reported centres together rather than per row, so that a page
    of findings is one query rather than one each. A failed run that never got
    as far as recording why -- a worker killed mid-fetch -- carries nothing,
    and the row says nothing rather than inventing a cause.
    """
    return dict(
        SyncLog.objects.filter(
            node_id__in=list(node_ids),
            sync_type=SyncLog.NODE_STATIONS,
            status=SyncLog.FAILED,
        )
        .order_by("node_id", "-started_at")
        .distinct("node_id")
        .values_list("node_id", "error_message")
    )


def _unanswered_registry_row(node, *, error, now):
    """One unreadable registry as a finding."""
    return UnansweredRegistryRow(
        node_id=node.pk,
        centre_id=node.centre_id,
        name=node.name,
        country_code=node.country.code if node.country else "",
        country_name=node.country.name if node.country else "",
        stations_url=node.stations_url,
        standing=RegistryStanding.of(node.last_answered_at),
        last_answered_at=node.last_answered_at,
        unanswered_since=node.unanswered_since,
        hours_unanswered=hours_between(node.unanswered_since, now),
        last_error=_error_excerpt(error),
    )


def _error_excerpt(message):
    """An error as much of one line of it as a reader needs.

    Registries fail in prose. A refused connection is a phrase; a proxy
    answering with an HTML error page is a screenful, and a report or an email
    that quoted it whole would bury the twenty findings around it.
    """
    excerpt = " ".join(message.split())

    if len(excerpt) <= ERROR_EXCERPT_CHARS:
        return excerpt

    return excerpt[: ERROR_EXCERPT_CHARS - 1].rstrip() + "\u2026"


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


def _silent_declarations_naming_no_centre():
    """The silent declarations whose "declared by centre" column is blank.

    What the caveat above the table is about. Asked of the same query the
    report is built from, so the sentence cannot appear over a table where
    every row names a centre.
    """
    return _silent_declarations().filter(registry_centre_id__isnull=True)


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
        declaring_centre=DeclaringCentre.of(observation.node),
        last_transmitted=observation.last_seen,
        hours_quiet=hours_between(observation.last_seen, now),
    )


def _reportable_gaps(*, now=None):
    """The open gaps of centres whose own broker still answers.

    Bounded at the horizon its evidence ends at, so that the report is as long
    as the forensic window rather than as long as the installation has been
    running.
    """
    return (
        _gaps_at_watched_centres()
        .within_evidence(now)
        .select_related("node", "dataset", "origin_source")
    )


def _gaps_past_the_horizon(*, now=None):
    """The open gaps the report holds and cannot stand behind any more."""
    return _gaps_at_watched_centres().beyond_evidence(now)


def _gaps_at_watched_centres():
    """Every open gap the report would be entitled to list at all."""
    return PropagationGap.objects.open().filter(
        node__in=MessageSource.objects.watched_origins().values("node_id")
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
        origin_transport=_transport_of(gap),
        published_at=gap.published_at,
        observed_at_origin=gap.observed_at_origin,
        detected_at=gap.detected_at,
        hours_missing=hours_between(gap.published_at, now),
    )


def _transport_of(gap):
    """Which of the centre's own transports observed this notification.

    A vantage point may have been replaced by a later catalogue sync or
    deleted by hand, which unmakes the note of how the notification was seen
    without unmaking the observation. The gap is still a finding; what it can
    no longer say is which transport carried it.
    """
    if gap.origin_source_id is None:
        return OriginTransport.UNRECORDED

    return OriginTransport.of(gap.origin_source.source_type)


def _open_unregistered_centres():
    """The centres the registry has still not caught up with."""
    return UnregisteredCentre.objects.unregistered().order_by(
        "first_seen_at", "centre_id"
    )


def _reportable_unregistered_centres():
    """The same, where the registry is current enough for them to mean anything."""
    if _registry_is_frozen():
        return UnregisteredCentre.objects.none()

    return _open_unregistered_centres()


def _registry_is_frozen():
    """Whether the catalogue that writes the registry has stopped answering.

    Read off the open hard failure rather than recomputed here, so that one
    answer serves the alert and the report and they cannot come apart: a
    report that worked it out for itself would need the threshold too, and
    two copies of a threshold meant to be revised is one revised copy and one
    forgotten one.

    Which is the row rather than the message, deliberately. A failure is
    announced once it has outlasted the announcing threshold and to whoever is
    configured to hear it, and neither of those has any bearing on whether
    this report can stand behind what it lists. An installation with nobody to
    mail still withholds, and says why on the page.
    """
    return bool(HardFailure.objects.standing(HardFailure.CATALOGUE_WRITER_STALE))


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
class Notice:
    """One finding as something other than a table row would put it.

    A report row is columns, and columns need a heading and a page. A notice
    is the same finding in a sentence, and what identifies it -- which is what
    it takes to tell somebody about it in an email, and to know tomorrow that
    they have already been told.

    The key is the finding's identity rather than the row's. Two rows that
    name the same problem carry the same key and are one notice: a centre
    whose propagation has broken produces a row per notification, and a digest
    that reported each of them would report one fault hundreds of times, then
    hundreds more tomorrow.

    Summaries are not translated. They are mostly identifiers, they are read
    by whoever runs the installation rather than published, and the framing
    around them in the email is translated in the usual way.
    """

    key: str
    summary: str


def _silent_station_notice(row):
    """A declared station nothing has ever heard, in a sentence."""
    known_to = f", declared by {row.registry_centre_id}" if row.registry_centre_id else ""

    return Notice(
        key=row.wigos_id,
        summary=(
            f"{row.display_name} ({row.wigos_id}) in {row.territory or 'no territory'} "
            f"is declared operational and has never transmitted{known_to}"
        ),
    )


def _undeclared_station_notice(row):
    """A station transmitting that nothing declares, in a sentence.

    Two sentences, because a centre nobody asked is a different errand from a
    centre that answered. Mailing the first as a registration gap sends
    somebody to a centre to ask about a station its own registry may well
    declare, which is how a digest stops being read.
    """
    centre = row.centre_id or "an unregistered centre"

    if row.declaring_centre == DeclaringCentre.UNASKED:
        nobody_declares = (
            ", which advertises no station registry and has never been asked "
            "what it declares; OSCAR/Surface does not declare it either"
        )
    else:
        nobody_declares = (
            " and neither OSCAR/Surface nor any centre's registry declares it"
        )

    return Notice(
        key=f"{row.centre_id}:{row.wigos_id}",
        summary=f"{row.wigos_id} is transmitting under {centre}{nobody_declares}",
    )


def _propagation_gap_notice(row):
    """A centre whose publications are not reaching the world, in a sentence.

    Keyed on the centre rather than on the notification. What has gone wrong
    is the path, and it is the path somebody is sent to look at; the
    notification named here is the evidence to open the conversation with, and
    the report holds however many others there are.

    The transport is named after the finding rather than inside it, because it
    is not part of what went wrong: it is how this tool knows.
    """
    return Notice(
        key=row.centre_id,
        summary=(
            f"{row.centre_id} published {row.notification_id} on {row.topic} "
            f"and the Global Broker has not carried it. "
            f"Seen at: {row.origin_transport_label}"
        ),
    )


def _unregistered_centre_notice(row):
    """A centre publishing that no catalogue knows, in a sentence."""
    country = f" ({row.country_name})" if row.country_name else ""

    return Notice(
        key=row.centre_id,
        summary=(
            f"{row.centre_id}{country} is publishing with no discovery "
            f"catalogue record"
        ),
    )


def _unattributed_rate_notice(row):
    """A centre naming no station for some of its traffic, in a sentence.

    Nothing at all for a centre that attributed everything it published. This
    report lists every publishing centre so that the share can be read against
    the ones doing it right, and a digest that announced those as findings
    would announce the whole region the first time it ran.
    """
    if not row.unattributed_count:
        return None

    return Notice(
        key=row.centre_id,
        summary=(
            f"{row.centre_id} left {row.unattributed_count} of "
            f"{row.message_count} messages ({row.percent:.0f}%) naming no station"
        ),
    )


def _unanswered_registry_notice(row):
    """A registry this tool cannot read, in a sentence.

    Two sentences, because a registry that stopped and a registry that never
    started are two errands. The first names when it last worked, which is
    what dates the change at the centre; the second says plainly that this
    tool has never had an answer out of the address, which is as much a
    question about the address as about the centre.

    The error is quoted because it is the whole of the diagnosis a reader can
    make without opening anything: a refused connection is a host that is
    gone, a read timeout is one that is there and not serving, and a 404 is an
    address off by a path.
    """
    if row.standing == RegistryStanding.NEVER_ANSWERED:
        silence = "has never answered"
    else:
        silence = (
            f"has not answered since "
            f"{date_format(row.last_answered_at, 'DATETIME_FORMAT')}"
        )

    because = f": {row.last_error}" if row.last_error else ""

    return Notice(
        key=row.centre_id,
        summary=(
            f"{row.centre_id}'s station registry at {row.stations_url} "
            f"{silence}{because}"
        ),
    )


def _bounds_nothing(*, now=None):
    """What a report bounded by its filters rather than by truncation says.

    Nothing at all: everything it found is on the page, a page at a time.
    """
    return None


def _caveats_nothing(*, now=None):
    """What a report whose every column means one thing says.

    Nothing at all: each of its cells is a fact it can stand behind, so there
    is nothing to qualify above the table.
    """
    return None


def _leaves_nothing_unsettled(*, now=None):
    """Which of a report's findings have stopped being checkable at all.

    None of them, for every report whose findings it can still answer for.
    A report that stops listing something is saying the thing has gone, and
    only a report whose evidence expires under it -- the propagation report --
    can mean anything else by it.
    """
    return set()


@dataclass(frozen=True)
class GapReport:
    """One report: what it finds, and how to ask for it.

    The six are held as a list rather than as six hard-wired pages so that
    the index, the routing, the report itself and the digest all read from one
    place. A report that exists but is not on the index is a finding nobody
    sees, which is the failure this whole module exists to prevent -- and one
    that exists but is not in the digest is a finding nobody sees until they
    next open the tool.

    The callables are named as the verbs they are. A template renders any
    attribute it is given and calls it if it can, so a field called ``count``
    would run a query from the middle of a page that only meant to print a
    number.

    ``describe_row`` answers with nothing where a row is not a finding at all.
    The rate report needs that: it lists every publishing centre so that a
    share can be read against the ones doing it right, and most of them have
    nothing to answer for.

    ``describe_bound`` is how a report that lists less than it holds says so,
    and most of them have nothing to say: they are bounded by filters rather
    than by truncation, so everything they found is on the page. Only the
    propagation report bounds anything, and it is held here rather than in its
    template so that a sixth report that has to truncate says so in the same
    place and the same way.

    ``describe_caveat`` is the other thing a report can have to say for
    itself, and is not the same thing. A bound is about which findings are on
    the page; a caveat is about what a column of the findings that *are* on
    the page can be read to mean. So it stays off the index, which exists to
    decide whether a report is worth opening -- a count that is right is worth
    opening whatever its columns can and cannot distinguish.

    ``find_unsettled`` is the same admission made to the digest rather than to
    a reader. A finding leaving a report ordinarily means the problem has
    gone, and where a report has stopped being able to answer for one instead,
    this is where it says which -- so that what nobody can check any more is
    let go rather than announced as fixed.
    """

    slug: str
    title: str
    description: str
    find_rows: Callable[..., list]
    count_rows: Callable[..., int]
    describe_row: Callable[..., Notice | None]
    describe_bound: Callable[..., str | None] = _bounds_nothing
    describe_caveat: Callable[..., str | None] = _caveats_nothing
    find_unsettled: Callable[..., set[str]] = _leaves_nothing_unsettled


@dataclass(frozen=True)
class GapReportSummary:
    """One line of the index: a report and how much it has found.

    The bound travels with the count rather than waiting on the page behind
    it. A count that leaves something out is exactly what decides whether the
    report is worth opening, and the index is where that decision is made.
    """

    slug: str
    title: str
    description: str
    count: int
    bound: str | None = None


#: The six reports, in the order the index shows them: what is declared and
#: missing, what is arriving and unaccounted for, then the four about the
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
        describe_row=_silent_station_notice,
        describe_caveat=stations_declared_but_silent_unasked_centres,
    ),
    GapReport(
        slug="transmitting-undeclared",
        title=_("Transmitting but undeclared stations"),
        description=_(
            "Stations heard transmitting that neither OSCAR/Surface nor any "
            "centre's own registry declares. A centre advertising no registry "
            "has never been asked, so its stations are traffic nothing "
            "accounts for rather than registrations known to be missing."
        ),
        find_rows=stations_transmitting_undeclared,
        count_rows=lambda *, now=None: _undeclared_observations().count(),
        describe_row=_undeclared_station_notice,
    ),
    GapReport(
        slug="propagation-gaps",
        title=_("Propagation gaps"),
        description=_(
            "Notifications seen at a centre's own vantage point that the "
            "Global Broker has never carried, each saying which of the "
            "centre's transports saw it. Centres no vantage point of their "
            "own currently answers at are left out."
        ),
        find_rows=propagation_gaps,
        count_rows=lambda *, now=None: _reportable_gaps(now=now).count(),
        describe_row=_propagation_gap_notice,
        describe_bound=propagation_gaps_left_out,
        find_unsettled=propagation_gaps_unsettled,
    ),
    GapReport(
        slug="unregistered-centres",
        title=_("Unregistered centres"),
        description=_(
            "Centres of the monitored region publishing with no discovery "
            "catalogue record."
        ),
        find_rows=unregistered_centres,
        count_rows=lambda *, now=None: _reportable_unregistered_centres().count(),
        describe_row=_unregistered_centre_notice,
        describe_bound=unregistered_centres_withheld,
        find_unsettled=unregistered_centres_unsettled,
    ),
    GapReport(
        slug="registries-not-answering",
        title=_("Registries that are not answering"),
        description=_(
            "Centres whose own station registry has failed every run for days "
            "on end, saying which have never answered at all and which "
            "answered once and stopped. A centre advertising no registry is "
            "not here: nothing has ever asked it."
        ),
        find_rows=registries_not_answering,
        count_rows=lambda *, now=None: _registries_not_answering(
            now=now or dj_timezone.now(),
            hours=default_registry_unanswered_hours(),
        ).count(),
        describe_row=_unanswered_registry_notice,
        describe_caveat=registries_not_answering_caveat,
    ),
    GapReport(
        slug=UNATTRIBUTED_MESSAGES_SLUG,
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
        describe_row=_unattributed_rate_notice,
    ),
)


def gap_report(slug):
    """The report of that name, or None if nothing is called that."""
    return next((report for report in GAP_REPORTS if report.slug == slug), None)


def gap_report_summaries(*, now=None):
    """Every report with how much it has found, for the index.

    Counted rather than listed: the index exists to say which report is worth
    opening, and building six reports in full to show six numbers would make
    the cheapest page in the tool the most expensive.
    """
    now = now or dj_timezone.now()

    return [
        GapReportSummary(
            slug=report.slug,
            title=report.title,
            description=report.description,
            count=report.count_rows(now=now),
            bound=report.describe_bound(now=now),
        )
        for report in GAP_REPORTS
    ]
