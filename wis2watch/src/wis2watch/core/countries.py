"""The monitored region.

WIS2Watch watches a configurable set of countries, defaulting to Africa. This
module is the single definition of that list and of how a WIS2 centre ID maps
onto it.

A WIS2 centre ID is a hyphenated token sequence whose first token is, by
convention, the ISO 3166 alpha-2 code of the publishing centre's country --
``ke-kmd``, ``ma-marocmeteo-global-monitor``. The convention is not universal:
global services carry prefixes such as ``data-metoffice-noaa-global-cache``
that are not country codes at all, which is why membership is resolved here
rather than assumed at the call site.
"""

from django.conf import settings
from django_countries import countries

#: The 54 African UN member states, as ISO 3166 alpha-2 codes.
AFRICA_COUNTRY_CODES = (
    "AO", "BF", "BI", "BJ", "BW", "CD", "CF", "CG", "CI", "CM",
    "CV", "DJ", "DZ", "EG", "ER", "ET", "GA", "GH", "GM", "GN",
    "GQ", "GW", "KE", "KM", "LR", "LS", "LY", "MA", "MG", "ML",
    "MR", "MU", "MW", "MZ", "NA", "NE", "NG", "RW", "SC", "SD",
    "SL", "SN", "SO", "SS", "ST", "SZ", "TD", "TG", "TN", "TZ",
    "UG", "ZA", "ZM", "ZW",
)


def get_monitored_country_codes():
    """The ISO 3166 alpha-2 codes of the countries currently monitored.

    Overridable through the ``WIS2WATCH_MONITORED_COUNTRIES`` setting; an
    unset or empty setting means the whole of Africa.
    """
    configured = getattr(settings, "WIS2WATCH_MONITORED_COUNTRIES", None)

    if not configured:
        return AFRICA_COUNTRY_CODES

    return tuple(code.strip().upper() for code in configured if code.strip())


def monitored_territory_codes():
    """The monitored region as OSCAR/Surface names it: ISO 3166 alpha-3 codes.

    OSCAR files a station under a territory rather than a country, and asks for
    that territory by its three-letter code. A configured code that names no
    country -- and so no territory OSCAR could answer for -- is left out rather
    than sent and rejected.
    """
    named = (countries.alpha3(code) for code in get_monitored_country_codes())

    return tuple(territory for territory in named if territory)


def centre_id_prefix(centre_id):
    """The first token of a centre ID, lowercased.

    Returns an empty string when the centre ID is missing or carries no
    token separator, since a bare token is not a well-formed centre ID.
    """
    if not centre_id:
        return ""

    prefix, separator, _ = centre_id.strip().lower().partition("-")

    return prefix if separator else ""


def is_monitored_centre_id(centre_id):
    """Whether a centre ID belongs to a monitored country."""
    return bool(monitored_country_code_for_centre_id(centre_id))


def monitored_country_code_for_centre_id(centre_id):
    """The monitored country a centre ID belongs to, or an empty string.

    Non-country prefixes and countries outside the monitored region both
    resolve to an empty string -- the caller is expected to leave the country
    unset rather than guess.
    """
    prefix = centre_id_prefix(centre_id).upper()

    return prefix if prefix in get_monitored_country_codes() else ""
