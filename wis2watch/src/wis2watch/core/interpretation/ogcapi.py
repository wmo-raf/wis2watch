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

#: What a collection says it holds in total, and what one page carried.
MATCHED = "numberMatched"
RETURNED = "numberReturned"

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

    Counted where the page does not say, because that is the number a reader
    is actually holding, and a server whose ``numberReturned`` disagrees with
    its own feature list is telling the reader about the list.
    """
    if not payload:
        return 0

    returned = payload.get(RETURNED)

    if isinstance(returned, int):
        return returned

    return len(payload.get("features") or [])


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
