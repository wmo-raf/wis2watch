"""Asking whether the files the notifications advertise can actually be had.

This is the failure that every message-flow metric misses. A notification is
published perfectly, propagates to the Global Broker perfectly, and counts
towards every green number this tool holds -- and the file it points at cannot
be fetched. Nobody downstream gets the data, and nothing about the flow says
so. The only way to learn it is to ask for the file.

Asking is a headers-only request. No body is ever fetched and no hash is
checked, which is what keeps this inside the boundary the tool draws around
itself: notifications only, never the data. It also means one class of link
cannot be judged at all -- a server that refuses the method answers about
itself rather than about the file -- and that is recorded as its own outcome
rather than folded in with the failures, because it is this tool's limitation
and not the centre's.

Two things decide whether the finding is worth anything.

**The sample stays bounded.** This is the one job that makes requests of the
centres being monitored rather than of a broker. A tool that puts a centre's
web server under load to check on it is a fault report of its own. So a run
takes at most a configured number of links per centre per hour -- counted
against what has already been probed for that hour rather than against this
run, so that a job run twice does not knock twice as hard. Within the bound the
sample is spread across the centre's datasets, so a minute-by-minute feed
cannot be the only thing ever checked.

**The answers are told apart.** "Could not be fetched" sends nobody anywhere.
A 404 goes to whoever publishes the data, an expired certificate to whoever
runs the web server, a connection that never opens to whoever runs the network.
So the HTTP answers are read separately from the transport failures, and both
from the method refusal above.

The certificate is always verified, whatever a node's ``verify_ssl`` says. That
setting is about reading a node's registry, where a bad certificate is an
obstacle between this tool and the metadata it wants; here the certificate is
one of the things being tested, and turning verification off would suppress
exactly the finding the probe exists to make.

What is asked for is the centre's own advertised file, so the Global Caches'
republication of it is left out of the sample. A cached copy carries the
centre's node and its publication time but advertises the cache's copy of the
file, at the cache's address: probing that would measure a Global Cache's
retrievability and file the answer against the centre, which is the opposite of
the question. It would also point the whole region's probe volume at a handful
of cache hosts, outside any per-node bound.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic

import requests
from django.conf import settings
from django.utils import timezone as dj_timezone

from .models import LinkProbe, MessageSource, NotificationMessage, SyncLog
from .rollups import floor_to_hour

logger = logging.getLogger(__name__)

#: How many links may be asked for per centre per hour. Small on purpose: the
#: question is whether a centre's data is retrievable at all, and a handful of
#: files an hour answers that without the tool becoming traffic worth noticing.
DEFAULT_SAMPLE_SIZE = 5

#: How long a centre's server is given to answer, in seconds. A slow answer is
#: itself worth recording, so this is long enough that only a server which has
#: effectively stopped answering trips it.
DEFAULT_TIMEOUT_SECONDS = 15

#: What a centre's web server logs will show. A diagnostic tool asking for
#: files unannounced looks like a scraper; saying who it is costs nothing and
#: means an operator who wonders can find out.
USER_AGENT = "WIS2Watch/link-probe"

#: The statuses that mean something more specific than their class does.
#: ``405`` and ``501`` are the method refusal: the server is there and the file
#: may well be too, but it will not answer a request without a body.
STATUS_OUTCOMES = {
    401: LinkProbe.FORBIDDEN,
    403: LinkProbe.FORBIDDEN,
    404: LinkProbe.MISSING,
    405: LinkProbe.NOT_PROBEABLE,
    410: LinkProbe.MISSING,
    501: LinkProbe.NOT_PROBEABLE,
}

#: How a failure with no response behind it is read. Ordered, and read first
#: match wins, because requests' own exception hierarchy overlaps: an SSL
#: failure is a kind of connection error, and a connect timeout is both a
#: timeout and a connection error. Which one it is read as decides whether a
#: diagnostician is sent to the centre's certificate, its network or its
#: server, so the order is the finding.
TRANSPORT_OUTCOMES = (
    (
        (
            requests.exceptions.MissingSchema,
            requests.exceptions.InvalidSchema,
            requests.exceptions.InvalidURL,
            requests.exceptions.URLRequired,
        ),
        LinkProbe.BAD_URL,
    ),
    (requests.exceptions.SSLError, LinkProbe.TLS_ERROR),
    (requests.exceptions.Timeout, LinkProbe.TIMEOUT),
    (requests.exceptions.ConnectionError, LinkProbe.UNREACHABLE),
    # Whatever else requests raises, no response came back, which is what
    # unreachable says. Last, so it only ever catches what the named ones did
    # not.
    (requests.exceptions.RequestException, LinkProbe.UNREACHABLE),
)


@dataclass
class ProbeResult:
    """What one request came to.

    ``status_code`` is None where no response came back at all, which is the
    difference between a server that answered badly and one that never
    answered.
    """

    outcome: str
    status_code: int | None = None
    latency_ms: int | None = None
    error: str = ""


@dataclass
class ProbeCounts:
    """What a run came to.

    ``undetermined`` is kept apart from ``unretrievable`` deliberately. A
    server refusing a headers-only request has said nothing about the file, and
    counting that as a failure would report this tool's own limitation as the
    centre's.

    ``sampled`` and ``probed`` differ only when a run died partway, which is
    the whole reason both are kept: the sync log can then say how much of the
    sample it got through.
    """

    sampled: int = 0
    probed: int = 0
    retrievable: int = 0
    unretrievable: int = 0
    undetermined: int = 0

    def record(self, outcome):
        """Count one probe's outcome."""
        self.probed += 1

        if outcome == LinkProbe.RETRIEVABLE:
            self.retrievable += 1
        elif outcome == LinkProbe.NOT_PROBEABLE:
            self.undetermined += 1
        else:
            self.unretrievable += 1

    @property
    def summary(self):
        """What the run came to, in one line, for a log."""
        return (
            f"probed={self.probed} retrievable={self.retrievable} "
            f"unretrievable={self.unretrievable} undetermined={self.undetermined}"
        )

    def close(self, sync_log, status, error_message=""):
        """Close a sync log off with these counts.

        Only what the run did is recorded here -- how many links it took and
        how many it got through. What the answers were belongs on the probe
        rows: a run in which every file was missing did its job perfectly, and
        a sync log saying otherwise would confuse the tool's health with the
        region's.
        """
        sync_log.status = status
        sync_log.error_message = error_message
        sync_log.items_found = self.sampled
        sync_log.items_created = self.probed
        sync_log.completed_at = dj_timezone.now()
        sync_log.save()

        return sync_log


def default_sample_size():
    """How many links may be asked for per centre per hour."""
    return getattr(
        settings, "WIS2WATCH_LINK_PROBE_SAMPLE_SIZE", DEFAULT_SAMPLE_SIZE
    )


def probe_timeout():
    """How long a centre's server is given to answer, in seconds."""
    return getattr(
        settings, "WIS2WATCH_LINK_PROBE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
    )


def probed_hour(now=None):
    """The UTC hour a run samples: the last one that is over.

    Never the hour in progress. The bound is a number of links per hour, so
    sampling a partial hour would draw the whole allowance against however many
    minutes of it had elapsed -- a run at five past would check a centre's
    first five minutes every time and never the rest of what it publishes.
    """
    return floor_to_hour(now or dj_timezone.now()) - timedelta(hours=1)


def outcome_for_status(status_code):
    """What an HTTP answer says about the file."""
    if 200 <= status_code < 300:
        return LinkProbe.RETRIEVABLE

    if status_code in STATUS_OUTCOMES:
        return STATUS_OUTCOMES[status_code]

    if status_code >= 500:
        return LinkProbe.SERVER_ERROR

    # Recorded as what it was rather than guessed at. The status is kept on the
    # row, so an answer nothing here anticipated is still investigable.
    return LinkProbe.UNEXPECTED_STATUS


def outcome_for_failure(exc):
    """What a request that never got a response says about the file."""
    for failures, outcome in TRANSPORT_OUTCOMES:
        if isinstance(exc, failures):
            return outcome

    return LinkProbe.UNREACHABLE


def probe_link(url):
    """Ask for a file's headers, and read what comes back.

    Redirects are followed: a canonical link that redirects to the file still
    resolves to it, and requests would otherwise report the redirect itself as
    the answer.

    The failure is timed as well as the success. How long a centre's server
    took to refuse is worth as much as how long it took to answer -- a refusal
    after fifteen seconds and one after fifteen milliseconds are different
    machines in different trouble.
    """
    started = monotonic()

    try:
        response = requests.head(
            url,
            timeout=probe_timeout(),
            allow_redirects=True,
            verify=True,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.exceptions.RequestException as exc:
        return ProbeResult(
            outcome=outcome_for_failure(exc),
            latency_ms=_elapsed_ms(started),
            error=str(exc),
        )

    return ProbeResult(
        outcome=outcome_for_status(response.status_code),
        status_code=response.status_code,
        latency_ms=_elapsed_ms(started),
    )


def _elapsed_ms(started):
    """How long has passed since a monotonic reading, in whole milliseconds."""
    return round((monotonic() - started) * 1000)


def files_advertised_in(hour):
    """Every notification advertising a file the centres themselves published,
    in one UTC hour.

    What both the fan-out and the sampling start from, so that "a file this
    tool may ask a centre for" is decided once. Two exclusions make it that
    rather than simply an hour of traffic:

    A Global Cache's republication is left out. It carries the centre's node
    and its publication time, but the link on it is the cache's copy at the
    cache's address, and an answer about that filed against the centre would
    be a finding about the wrong machine.

    Traffic belonging to no registered centre is left out too. The bound is per
    node, and what a sweep turns up has no node to bound it against.
    """
    return (
        NotificationMessage.objects.filter(
            node__isnull=False,
            time__gte=hour,
            time__lt=hour + timedelta(hours=1),
        )
        .exclude(source__source_type=MessageSource.GLOBAL_CACHE)
        .exclude(canonical_link="")
    )


def sample_links(node, *, hour, limit):
    """The links to ask this centre for, at most ``limit`` of them.

    One link per dataset, so that a centre publishing a minute-by-minute feed
    alongside a daily summary does not spend its whole allowance on the feed
    and leave everything else unchecked. Within that, the most recently
    published first: a file advertised twenty minutes ago is the one whose
    absence anybody would want to hear about soonest.

    Everything on a topic no dataset claims counts as one bucket and takes one
    slot between them, rather than a slot each. Unknown-topic traffic is worth
    probing -- nobody else is watching it -- but a centre publishing an hour of
    it on topics the registry has never heard of would otherwise crowd out
    every dataset the registry does know.

    Links already probed for this node and hour are left out, so that a run
    which follows a short one tops the sample up rather than asking again for
    what has been asked for.
    """
    if limit <= 0:
        return []

    already_asked = set(
        LinkProbe.objects.filter(node=node, hour=hour).values_list("url", flat=True)
    )

    latest_per_dataset = (
        files_advertised_in(hour)
        .filter(node=node)
        .order_by("dataset_id", "-time")
        .distinct("dataset_id")
        .values("dataset_id", "notification_id", "canonical_link", "time")
    )

    chosen = []

    for row in sorted(latest_per_dataset, key=lambda row: row["time"], reverse=True):
        if row["canonical_link"] in already_asked:
            continue

        already_asked.add(row["canonical_link"])
        chosen.append(row)

        if len(chosen) == limit:
            break

    return chosen


def probe_node_links(node, *, hour=None, now=None, sample_size=None, probe=None):
    """Ask one centre for a bounded sample of the files it advertised.

    The bound is counted against what has already been probed for this node and
    hour rather than against this run, so a job that runs twice -- retried,
    or scheduled tighter than it should be -- does not knock on the centre's
    server twice as often.

    Each answer is written down as it arrives rather than at the end, so a run
    that dies partway keeps what it learned, and the hour's allowance already
    reflects what it spent. Each carries the moment its own request was made,
    which for a centre whose server hangs is minutes away from the moment the
    run started and is what an operator would correlate their logs against.

    The run is recorded as a sync log whether or not it found anything to ask
    for, so that a centre's page can say the probes ran rather than leaving
    "no findings" and "never checked" looking the same.

    ``probe`` is how a link is asked for, defaulting to the network.
    """
    now = now or dj_timezone.now()
    hour = probed_hour(now=now) if hour is None else hour
    bound = default_sample_size() if sample_size is None else sample_size
    probe = probe or probe_link

    sync_log = SyncLog.objects.create(
        node=node,
        sync_type=SyncLog.LINK_PROBES,
        status=SyncLog.FAILED,
        started_at=now,
    )
    counts = ProbeCounts()
    already_spent = LinkProbe.objects.filter(node=node, hour=hour).count()

    try:
        sampled = sample_links(node, hour=hour, limit=bound - already_spent)
        counts.sampled = len(sampled)

        for row in sampled:
            result = probe(row["canonical_link"])

            LinkProbe.objects.create(
                node=node,
                dataset_id=row["dataset_id"],
                notification_id=row["notification_id"],
                url=row["canonical_link"],
                outcome=result.outcome,
                status_code=result.status_code,
                latency_ms=result.latency_ms,
                error=result.error,
                hour=hour,
                probed_at=dj_timezone.now(),
            )

            counts.record(result.outcome)
    except Exception as exc:
        logger.error("Link probes failed for %s: %s", node.centre_id, exc)
        counts.close(sync_log, SyncLog.FAILED, str(exc))

        raise

    counts.close(sync_log, SyncLog.SUCCESS)

    logger.info("Probed %s links for %s: %s", counts.probed, node.centre_id,
                counts.summary)

    return counts


def nodes_advertising_links(hour):
    """The centres with a file to ask for in this hour.

    Read once by the fan-out rather than discovered by each node's own run, so
    that a region of quiet centres does not queue a task apiece to find nothing.
    """
    return files_advertised_in(hour).values_list("node_id", flat=True).distinct()
