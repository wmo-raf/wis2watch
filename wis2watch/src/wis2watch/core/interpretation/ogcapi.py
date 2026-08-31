"""Paging an OGC API Features collection.

Both of the endpoints WIS2Watch reads over HTTP -- a Global Discovery
Catalogue's discovery metadata and a node's own station registry -- are OGC API
Features collections, and both page the same way: each response links to the
next one until there is none.

The link is followed as given rather than rebuilt from an offset we compute,
since it already carries whatever query the server needs to resume. That is
the rule, and it holds only while the link really does resume where the page
ended. One of the three Global Discovery Catalogues serves the same next link
on every page it has -- ``limit=1&offset=1``, on page one and on the last page
alike -- so a reader following it as given never reaches the end of a
collection of 560 records.

So three things are read off a page besides the link: how many records the
collection says match, how many the page carried, and where the link says it
resumes. Together they are what tells a reader that it has the collection
whole, and what tells it that a link is offering it a page it has already
read. What to do about that is the sync's, not this module's: read here,
judged there.
"""

from urllib.parse import parse_qs, urlsplit

#: The link relation naming the next page of a collection.
NEXT = "next"

#: What a collection says it holds in total, what one page claims to have
#: carried, and the list that actually answers for it.
MATCHED = "numberMatched"
RETURNED = "numberReturned"
FEATURES = "features"

#: The query parameter a paging link resumes at.
OFFSET = "offset"


def next_page_url(payload):
    """Where the next page of a collection lives, or None on the last page."""
    if not payload:
        return None

    for link in payload.get("links") or []:
        if link.get("rel") == NEXT and link.get("href"):
            return link["href"]

    return None


def records_matched(payload):
    """How many records the collection says match, or None if it does not say.

    The one number that tells a short read from a whole one. A server that
    omits it, or answers something that is not a count, has said nothing --
    and a reader that guessed would be inventing the evidence it is about to
    act on.
    """
    if not payload:
        return None

    matched = payload.get(MATCHED)

    return matched if isinstance(matched, int) else None


def records_returned(payload):
    """How many records one page carried.

    The feature list is what answers, not the claim beside it. What this count
    is used for is the offset a resumed read asks from, and the servers it is
    used against are exactly the ones whose paging metadata is stale -- so a
    server able to move that offset by saying a number could make a read skip
    records nobody would ever know were missing.

    ``numberReturned`` answers only for a payload carrying no list at all,
    where it is the sole thing said and there is nothing to check it against.
    """
    if not payload:
        return 0

    if FEATURES in payload:
        return len(payload.get(FEATURES) or [])

    returned = payload.get(RETURNED)

    return returned if isinstance(returned, int) else 0


def page_offset(url):
    """Where a paging link says it resumes, or None if it does not say.

    A link with no offset is not one that resumes at nought: it may be paging
    by a cursor this knows nothing about, and reading it as zero would call
    every such link a repeat of the first page.
    """
    if not url:
        return None

    offsets = parse_qs(urlsplit(url).query).get(OFFSET) or []

    for offset in offsets:
        if offset.isdigit():
            return int(offset)

    return None
