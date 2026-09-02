"""WIS2 topic parsing.

A WIS2 topic names the vantage point, the standard version, the publishing
centre and the data's place in the topic hierarchy::

    origin/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop
    cache/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop

The level directly below the centre says what kind of thing is being announced.
``data`` is a publication; ``metadata`` is the centre announcing its own
catalogue record, which is not one::

    origin/a/wis2/ke-meteo/metadata

Below ``data`` the hierarchy is the data policy, then the earth-system
discipline, then the data category -- and the category is what says whether a
publication is an observation, in whichever discipline the centre filed it
under.

Everything downstream -- which centre a message belongs to, whether a Global
Cache picked it up, whether the centre is one we monitor -- is read off that
structure, so it is parsed once, here.
"""

from dataclasses import dataclass

#: The tokens every WIS2 topic carries between the prefix and the centre ID.
STANDARD_SEGMENT = ("a", "wis2")

ORIGIN = "origin"
CACHE = "cache"

#: The level below the centre on which it announces its WCMP2 discovery
#: metadata record. Every other level below a centre carries data.
METADATA = "metadata"

#: The level below the centre carrying a publication, under which the data
#: policy, the earth-system discipline and the data category follow in order.
DATA = "data"

#: Where the discipline and the category sit below ``data``, counted from the
#: top of the hierarchy: ``data/{policy}/{discipline}/{category}/...``.
DISCIPLINE_LEVEL = 2
CATEGORY_LEVEL = 3

#: The data categories that carry observations. Which discipline they sit
#: under is deliberately not part of it: a centre may file surface
#: observations under ``weather``, ``climate``, ``hydrology`` or any other
#: discipline, and four centres in the region file theirs under ``climate`` --
#: a rule pinned to ``weather`` would delete them from every count.
SURFACE_BASED_OBSERVATIONS = "surface-based-observations"
SPACE_BASED_OBSERVATIONS = "space-based-observations"
OBSERVATION_CATEGORIES = frozenset(
    {SURFACE_BASED_OBSERVATIONS, SPACE_BASED_OBSERVATIONS}
)

#: The MQTT wildcard matching a centre's whole hierarchy, however deep.
MULTI_LEVEL_WILDCARD = "#"

#: The MQTT wildcard matching exactly one topic level -- one centre, here.
SINGLE_LEVEL_WILDCARD = "+"


@dataclass(frozen=True)
class ParsedTopic:
    """A WIS2 topic, broken into the parts that carry meaning."""

    raw: str
    prefix: str
    centre_id: str
    hierarchy: tuple[str, ...]

    @property
    def is_origin(self):
        """Whether this is traffic as the centre published it."""
        return self.prefix == ORIGIN

    @property
    def is_cache(self):
        """Whether this is traffic republished by a Global Cache."""
        return self.prefix == CACHE

    @property
    def announces_catalogue_record(self):
        """Whether this topic carries a catalogue record rather than data."""
        return self.hierarchy[:1] == (METADATA,)

    @property
    def carries_data(self):
        """Whether this topic carries a publication rather than an announcement."""
        return self.hierarchy[:1] == (DATA,)

    @property
    def discipline(self):
        """The earth-system discipline this topic files its data under, or "".

        Empty for anything that is not a data topic, and for a data topic that
        stops above the discipline -- neither is a discipline this tool may
        name on a centre's behalf.
        """
        return self._data_level(DISCIPLINE_LEVEL)

    @property
    def data_category(self):
        """The data category this topic files its data under, or ""."""
        return self._data_level(CATEGORY_LEVEL)

    @property
    def is_observation(self):
        """Whether this topic carries observations, in any discipline."""
        return self.data_category in OBSERVATION_CATEGORIES

    def _data_level(self, level):
        """One named level of a data topic's hierarchy, or "" if it has none.

        The level is what gives a token its meaning, so a topic that stops
        short answers with nothing rather than with whatever token happens to
        sit last. That is what keeps a centre publishing on
        ``data/core/surface-based-observations`` -- a category where a
        discipline belongs -- from reading as an observation on the strength
        of the word alone.
        """
        if not self.carries_data or len(self.hierarchy) <= level:
            return ""

        return self.hierarchy[level]

    def as_origin(self):
        """The same topic as published at origin.

        A Global Cache mirrors a centre's topic under the ``cache/`` prefix, so
        the origin form is what identifies the dataset in both cases.
        """
        if self.is_origin:
            return self

        return ParsedTopic(
            raw="/".join((ORIGIN,) + STANDARD_SEGMENT + (self.centre_id,) + self.hierarchy),
            prefix=ORIGIN,
            centre_id=self.centre_id,
            hierarchy=self.hierarchy,
        )


def subscription_topic(centre_id, prefix=ORIGIN):
    """The topic filter covering everything one centre publishes, or None.

    Subscribing per centre rather than under a single ``#`` is what keeps a
    Global Broker connection to the monitored region: the broker carries the
    whole world, and a wildcard would ingest all of it.
    """
    if not centre_id or not centre_id.strip():
        return None

    return "/".join(
        (prefix,)
        + STANDARD_SEGMENT
        + (centre_id.strip().lower(), MULTI_LEVEL_WILDCARD)
    )


def sweep_topic():
    """The topic filter covering every centre publishing at origin.

    The one filter in the project that names no centre. A centre absent from
    the registry cannot be subscribed to by name -- nothing knows it exists --
    so the only way to hear one is to ask for everything and decide afterwards
    which of it belongs to the monitored region. That is why the sweep this
    serves is bounded in time: for as long as it is carried, the connection is
    offered the whole world's traffic.

    Origin only. A Global Cache mirrors what a centre published at origin, so
    sweeping the cache prefix as well would double the traffic to hear the
    same centres.
    """
    return "/".join(
        (ORIGIN,)
        + STANDARD_SEGMENT
        + (SINGLE_LEVEL_WILDCARD, MULTI_LEVEL_WILDCARD)
    )


def parse_topic(topic):
    """A WIS2 topic broken into its parts, or None when it is not one.

    The prefix and centre ID are lowercased, since they are matched against
    stored values; the hierarchy is left exactly as published.
    """
    if not topic:
        return None

    tokens = topic.strip().split("/")

    if len(tokens) < 4:
        return None

    prefix, first, second, centre_id = tokens[:4]

    if (first.lower(), second.lower()) != STANDARD_SEGMENT:
        return None

    if not prefix or not centre_id:
        return None

    return ParsedTopic(
        raw=topic.strip(),
        prefix=prefix.lower(),
        centre_id=centre_id.lower(),
        hierarchy=tuple(tokens[4:]),
    )


def announces_catalogue_record(topic, data_id=""):
    """Whether a notification announces a discovery metadata record.

    A centre announces its own WCMP2 record on ``.../{centre}/metadata``, and
    every Global Cache republishes that announcement under its own prefix. It
    is a notification like any other and nothing about its payload says it is
    not a publication -- what says so is where it was published.

    The topic answers it wherever one was observed, and answers it alone: a
    message that went out on a data topic is a data publication whatever else
    it says about itself.

    A centre's own message archive returns notifications with no topic at all,
    and there the data identifier is read instead. It spells the same path the
    topic would have -- ``{centre}/metadata/{record}`` -- which is what lets the
    one announcement be recognised from either vantage point rather than only
    from the broker.
    """
    if topic and topic.strip():
        parsed = parse_topic(topic)

        return parsed is not None and parsed.announces_catalogue_record

    return (data_id or "").split("/")[1:2] == [METADATA]


def is_observation_topic(topic):
    """Whether a topic carries observations, in any earth-system discipline.

    The whole classification, and the only place it is decided. A topic this
    tool cannot read is not an observation rather than an error: an
    unparseable topic is a catalogue mistake at the centre, and this tool
    exists to report those rather than to stop on them.
    """
    parsed = parse_topic(topic)

    return parsed is not None and parsed.is_observation
