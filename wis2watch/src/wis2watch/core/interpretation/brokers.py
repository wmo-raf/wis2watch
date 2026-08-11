"""Broker URL parsing.

Discovery metadata advertises brokers as URLs, and what they look like in
practice varies: TLS and plain MQTT, WebSocket transports, ports left implicit,
and credentials embedded in the authority::

    mqtts://everyone:everyone@globalbroker.meteo.fr:8883
    mqtt://everyone:everyone@wis.dirmet.cg:1883
    mqtts://wis2.dwd.de
"""

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

#: The port each scheme implies when the URL leaves it out.
DEFAULT_PORTS = {
    "mqtt": 1883,
    "mqtts": 8883,
    "ws": 80,
    "wss": 443,
}

#: The schemes that carry TLS.
TLS_SCHEMES = frozenset({"mqtts", "wss"})


@dataclass(frozen=True)
class BrokerConnection:
    """Everything needed to connect to a broker."""

    host: str
    port: int
    use_tls: bool
    username: str = ""
    password: str = ""


def parse_broker_url(url):
    """A broker URL as connection details, or None when it is not one.

    A URL whose scheme is not an MQTT transport, that names no host, or whose
    port is not a number is not a broker we can connect to, and is reported as
    absent rather than guessed at.
    """
    if not url:
        return None

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()

    if scheme not in DEFAULT_PORTS:
        return None

    try:
        port = parts.port
    except ValueError:
        return None

    if not parts.hostname:
        return None

    return BrokerConnection(
        host=parts.hostname.lower(),
        port=port or DEFAULT_PORTS[scheme],
        use_tls=scheme in TLS_SCHEMES,
        username=unquote(parts.username or ""),
        password=unquote(parts.password or ""),
    )
