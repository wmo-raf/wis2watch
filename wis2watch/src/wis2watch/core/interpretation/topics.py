"""WIS2 topic parsing.

A WIS2 topic names the vantage point, the standard version, the publishing
centre and the data's place in the topic hierarchy::

    origin/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop
    cache/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop

Everything downstream -- which centre a message belongs to, whether a Global
Cache picked it up, whether the centre is one we monitor -- is read off that
structure, so it is parsed once, here.
"""

from dataclasses import dataclass

#: The tokens every WIS2 topic carries between the prefix and the centre ID.
STANDARD_SEGMENT = ("a", "wis2")

ORIGIN = "origin"
CACHE = "cache"

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


def sweep_topic(prefix=ORIGIN):
    """The topic filter covering every centre publishing under one prefix.

    The one filter in the project that names no centre. A centre absent from
    the registry cannot be subscribed to by name -- nothing knows it exists --
    so the only way to hear one is to ask for everything and decide afterwards
    which of it belongs to the monitored region. That is why the sweep this
    serves is bounded in time: for as long as it is carried, the connection is
    offered the whole world's traffic.
    """
    return "/".join(
        (prefix,)
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
