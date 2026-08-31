"""What every synchronisation run does the same way.

Every sync WIS2Watch runs -- the registry from a Global Discovery Catalogue,
the datasets and stations a centre's own endpoints declare -- reads an OGC API
Features collection page by page and writes what it can. Four things are
therefore said once, here, rather than once per sync:

- **How a collection is read.** Page after page, following the server's own
  ``next`` link while it advances, resuming from an offset of our own where it
  does not, retrying a page whose transport failed, and with a ceiling so that
  links which cycle cannot spin.
- **What became of the records.** The same four counts, and a run that stepped
  over a record it could not store succeeded only partly -- and says which
  records those were and what refused them, because the count on its own is a
  reader told that nine things are missing and given no way to find out which.
  That is what keeps two sync logs comparable when they are read side by side
  on a node's page.
- **Where a source places a station.** Every source that declares a station
  gives it a latitude, a longitude and sometimes an elevation, and the canonical
  location is one three-dimensional point whichever source supplied it.
- **What a source says about a dataset.** A catalogue's record and a centre's
  own are the same WCMP2 feature, so what either of them contributes to the
  canonical dataset is one mapping rather than one per sync -- two copies of
  it would drift, and the drift would read as the two sources disagreeing.

What differs between the syncs -- which URL, which credentials, how long to
wait -- stays with the sync that knows it.
"""

import logging
import time
from dataclasses import dataclass, field

import requests
from django.contrib.gis.geos import Point
from django.utils import timezone as dj_timezone

from .interpretation import (
    OFFSET,
    next_page_url,
    page_offset,
    records_matched,
    records_returned,
)
from .models import SyncLog, one_line

logger = logging.getLogger(__name__)

CREATED = "created"
UPDATED = "updated"
ERRORED = "errored"

#: A ceiling on paging, so a collection whose ``next`` links cycle cannot spin.
#: Sized for the collections a schedule reads, which are registries of a few
#: hundred records. A caller reading something that is legitimately longer --
#: a centre's archive of months of notifications -- says so rather than having
#: this raised for everybody, since the ceiling is only useful while it is
#: lower than anything a healthy source would return.
MAX_PAGES = 50

FETCH_TIMEOUT = 60

#: How many times one page is asked for before the read is given up.
#:
#: A page is a GET and asking again costs the source one more read of
#: something it is already serving, so this is set by what a blip looks like
#: rather than by what the source can bear. What it is set against is the
#: writing catalogue's four-megabyte page: eight seconds of transfer, over
#: which a refused connection or a body cut off partway was failing about half
#: its six-hourly runs, and one further attempt clears nearly all of that. A
#: source that has failed three times in half a minute is not blipping, and
#: going on asking would turn a sync into a load test.
FETCH_ATTEMPTS = 3

#: How long to wait after a failed attempt, in seconds, doubling each time. A
#: connection refused because the host is restarting is not helped by asking
#: again immediately, and the whole ladder still costs a run six seconds.
FETCH_BACKOFF_SECONDS = 2

#: What counts as the transport failing rather than the source answering.
#: These are the three the writing catalogue actually failed on -- a connection
#: refused or timed out, a connection closed without a reply, a body that
#: stopped partway -- and they have in common that the source said nothing.
#: An HTTP status is deliberately not here: a 404 or a 500 is an answer, and
#: asking a source to repeat an answer is not a retry but a hope.
TRANSPORT_FAULTS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

#: How many of a run's stepped-over records are recorded with their reasons.
#: A run that steps over a handful has a list of records somebody can go and
#: fix; a run that steps over a thousand has one fault, and the first fifty
#: reasons name it as well as the thousandth would. ``items_errored`` keeps
#: counting past this, so a run that stepped over more than it recorded says so
#: by the two numbers disagreeing rather than by quietly listing fewer.
MAX_STEPPED_OVER_RECORDED = 50

#: How much of one record's reason is kept, ellipsis included. Longer than the
#: excerpt a digest line quotes, because this is the copy everything else is
#: read from: what a page shows can be cut again, and what a log never kept
#: cannot be got back.
MAX_REASON_CHARS = 300


class PagingDidNotTerminate(Exception):
    """Raised when a collection never stops offering another page.

    A run that stopped at the ceiling has read part of a collection, and has no
    way to know how much of it. That is a failed run rather than a short one:
    reporting it as a success would leave a half-read registry indistinguishable
    from a centre that really has only these stations.
    """


class ReadKeptFailing(Exception):
    """Raised when every attempt at one page failed before the source answered.

    Named apart from whatever ``requests`` raised, because what a reader needs
    from the sync log is not that a connection was aborted -- it is that this
    source was asked three times over half a minute and said nothing each time.
    The last fault is quoted inside, since a refused connection, a read timeout
    and a body cut off partway are three different conversations.
    """


def fetch_pages(
    url,
    params=None,
    verify=True,
    timeout=FETCH_TIMEOUT,
    read_from="",
    max_pages=MAX_PAGES,
    attempts=FETCH_ATTEMPTS,
):
    """Every page of an OGC API Features collection, exactly as returned.

    Paging follows the server's own ``next`` link rather than an offset we
    compute, since that link already carries whatever query it needs to resume.
    Only the first request supplies parameters -- which is also why the link
    has to carry the query forward: a filtered read that resumed without its
    filter would page on through the whole collection believing it was still
    reading the window it asked for.

    It follows that link only while it advances. See :func:`_next_page` for the
    catalogue that made that a rule rather than an assumption, and for what
    this does instead.

    ``read_from`` names what is being read, for the failures raised when the
    source will not answer and when the collection never stops offering another
    page. ``max_pages`` is how many it is given to stop, and is worth raising
    only where the collection really is longer than a registry. ``attempts`` is
    how many times a page whose transport failed is asked for, and is worth
    lowering to one where the schedule is itself the retry.
    """
    resume_from, query = url, params
    read = 0

    for _ in range(max_pages):
        payload = _fetch_page(
            url,
            params,
            verify=verify,
            timeout=timeout,
            read_from=read_from,
            attempts=attempts,
        )

        yield payload

        read += records_returned(payload)
        url, params = _next_page(
            payload, resume_from=resume_from, query=query, read=read
        )

        if not url:
            return

    raise PagingDidNotTerminate(
        f"stopped paging {read_from} after {max_pages} pages; "
        "its next links do not terminate"
    )


def _fetch_page(url, params, *, verify, timeout, read_from, attempts):
    """One page, asked for again while it is the transport that is failing.

    A page is a GET, so asking again is safe in the way retrying a write never
    is. What makes it worth doing is what one blip costs: a catalogue sync is
    one four-megabyte transfer every six hours, a connection closed anywhere
    inside it failed the whole run, and the registry then stood unwritten until
    the next one -- which is how a source that answers most of the time came to
    fail half of its runs.

    Only the transport is retried. A source that answered with a status has
    said something, and asking it to say it again is a hope rather than a
    retry; the same goes for a body that arrived whole and was not JSON.

    ``attempts`` is the caller's, because what one lost run costs is the
    caller's. A read on an hourly schedule, or one whose window overlaps the
    last five, has a retry already and does not need a second one against
    fifty-four hosts of which many hang until the timeout.
    """
    # Clamped, so that a page is asked for at least once whatever a caller
    # passes: a loop that never ran would return no payload at all, and a
    # generator yielding None is a failure nothing downstream would recognise.
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"Accept": "application/json"},
                verify=verify,
            )
            response.raise_for_status()

            return response.json()
        except TRANSPORT_FAULTS as fault:
            if attempt >= attempts:
                raise ReadKeptFailing(
                    f"{read_from or url} did not answer in {attempts} "
                    f"attempts; the last of them: {fault}"
                ) from fault

            logger.warning(
                "Attempt %s of %s reading %s failed, waiting to ask again: %s",
                attempt,
                attempts,
                read_from or url,
                fault,
            )

            time.sleep(FETCH_BACKOFF_SECONDS * 2 ** (attempt - 1))


def _next_page(payload, *, resume_from, query, read):
    """Where the read goes after this page, and with what query.

    The server's own link, while it advances. A link that says it resumes at an
    offset behind what has already been read is not a next page: it is this
    page again, or one before it. That is not a hypothetical -- one of the
    three Global Discovery Catalogues serves ``limit=1&offset=1`` as its next
    link on every page it has, so a reader following it as given walks the
    second record of the collection forever and reaches the ceiling instead of
    the end. Its registry had never once been read through.

    A link naming no offset at all is followed as given. It may be paging by
    something this knows nothing about, and refusing a cursor for not being an
    offset would break every server that pages properly by one. So is a link
    that jumps *ahead* of what has been read: that is the server's own
    statement about where its next page begins, and second-guessing it is how
    a reader comes to re-read or skip.

    **No link at all ends the read**, however short it was. A server offering
    none has said that is all of it, and taking over its paging on the
    strength of a count it also published would be calling one half of its
    answer wrong on the authority of the other. This only takes over from a
    server that has contradicted itself.

    Where it has, the read resumes from an offset of its own -- the original
    URL and the original query, which is what keeps a filtered read inside its
    filter, plus how much has been read. That count is records held rather than
    where the server had got to, and the difference can only run one way: a
    link that jumped ahead leaves the count behind the server's position, never
    in front of it. So a resume can re-read, which is idempotent, and cannot
    skip, which would not be.

    It is only worth resuming while the collection says there is more to come
    and while pages keep coming back with records in them, which is what
    ``_more_to_read`` decides.
    """
    following = next_page_url(payload)

    if not following:
        return None, None

    offset = page_offset(following)

    if offset is None or offset >= read:
        return following, None

    if not _more_to_read(payload, read=read):
        return None, None

    return resume_from, {**(query or {}), OFFSET: read}


def _more_to_read(payload, *, read):
    """Whether the collection says there is more of it than has been read.

    Says, rather than is guessed at. A collection that reports no count has
    told the reader nothing, and a read that resumed on a guess would be
    inventing the evidence it acts on -- so it stops where the server's own
    links stopped advancing, which is what it did before any of this.

    The empty-page arm is what stops a server that answers an offset it does
    not understand: asked to resume, it returns the first page again, and a
    page that came back with nothing at all ends the read there rather than
    being asked once per remaining page. A server that goes on returning the
    *same* records is bounded instead by the count climbing a page at a time
    until it reaches what the collection says it holds -- which costs the
    reader a handful of repeated records, applied twice and stored once.
    """
    matched = records_matched(payload)

    if matched is None or not records_returned(payload):
        return False

    return read < matched


def declared_position(declared):
    """Where a source places a station, or None if it places it nowhere.

    Elevation stands at zero where the source gives none: the canonical location
    is three-dimensional, and a station's position is worth keeping even when its
    height is not stated.
    """
    if declared.latitude is None or declared.longitude is None:
        return None

    return Point(
        declared.longitude,
        declared.latitude,
        declared.elevation if declared.elevation is not None else 0,
        srid=4326,
    )


def declared_dataset_fields(declared):
    """What a source says about a dataset, under the canonical record's names.

    Written once because two sources describe a dataset in the identical WCMP2
    feature -- a catalogue's copy of what a centre registered, and the centre's
    own records -- so the mapping onto the canonical row is the same mapping.
    Kept apart from either sync, in the way a station's declared position is,
    because two copies of it would drift and the drift would show up as the
    two sources disagreeing about a dataset neither had read differently.

    What each sync does with these is its own: the catalogue writes them over
    the record it owns, and a centre fills in only what nothing else has. The
    fields no source supplies -- ``last_synced``, ``status`` -- are named by
    the sync that has something to say about them, and are deliberately not
    here.
    """
    return {
        "title": declared.title,
        "wmo_data_policy": declared.data_policy,
        "wmo_topic_hierarchy": declared.topic,
        "self_link": declared.canonical_link,
        "raw_json": declared.raw,
        "metadata_created": declared.metadata_created,
        "metadata_updated": declared.metadata_updated,
    }


@dataclass(frozen=True)
class SteppedOver:
    """One record a run read and could not store, and what refused it.

    What a sync reports for such a record, in place of a bare ``ERRORED``. The
    count on its own is what let a unique constraint drop a centre's largest
    observation feed for four days: a run reporting that it errored on nine of
    sixty-three names neither the nine nor the constraint, and the only trace
    of either was a line in a worker's output nobody reads.

    ``item`` is what the record is called at its source -- a dataset
    identifier, a WIGOS identifier, a centre ID -- because the source is where
    whoever reads this has to go next.
    """

    item: str
    reason: str

    def as_recorded(self):
        """This one as a sync log keeps it: one line of it, and not a page."""
        return {
            "item": str(self.item),
            "reason": one_line(self.reason, MAX_REASON_CHARS),
        }


@dataclass
class SyncCounts:
    """What became of the records a run read.

    ``found`` is what the source offered that the run had any business with;
    the rest is what became of it.
    """

    found: int = 0
    created: int = 0
    updated: int = 0
    errored: int = 0
    stepped_over: list[SteppedOver] = field(default_factory=list)

    def record(self, outcome):
        """Count one record's outcome.

        ``CREATED`` and ``UPDATED`` each name the field they count into. A
        record the run stepped over comes back as a ``SteppedOver``, which
        counts as an error and keeps its reason with it; a bare ``ERRORED`` is
        still counted, for a caller with nothing to say about which record it
        was.
        """
        if isinstance(outcome, SteppedOver):
            self.step_over(outcome)

            outcome = ERRORED

        setattr(self, outcome, getattr(self, outcome) + 1)

    def step_over(self, record):
        """Keep one record's reason, without counting it.

        Apart from ``record`` for the one caller that counts in bulk: a poll
        that stores a page at a time is told how many of it the store refused
        and separately which they were, and adding the count per record would
        be counting the page twice. The ceiling is here rather than at either
        call site, so that a run cannot keep more reasons by arriving at them
        one way rather than the other.
        """
        if len(self.stepped_over) < MAX_STEPPED_OVER_RECORDED:
            self.stepped_over.append(record)

    @property
    def status(self):
        """A run that stepped over records succeeded only partly."""
        return SyncLog.PARTIAL if self.errored else SyncLog.SUCCESS

    def close(self, sync_log, status, error_message=""):
        """Close a sync log off with these counts."""
        sync_log.status = status
        sync_log.error_message = error_message
        sync_log.items_found = self.found
        sync_log.items_created = self.created
        sync_log.items_updated = self.updated
        sync_log.items_errored = self.errored
        sync_log.stepped_over = [record.as_recorded() for record in self.stepped_over]
        sync_log.completed_at = dj_timezone.now()
        sync_log.save()

        return sync_log
