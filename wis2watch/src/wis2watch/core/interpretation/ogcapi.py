"""Paging an OGC API Features collection.

Both of the endpoints WIS2Watch reads over HTTP -- a Global Discovery
Catalogue's discovery metadata and a node's own station registry -- are OGC API
Features collections, and both page the same way: each response links to the
next one until there is none. The rule is written once here so that the two
syncs cannot drift into reading it differently.

The link is followed as given rather than rebuilt from an offset we compute,
since it already carries whatever query the server needs to resume.
"""

#: The link relation naming the next page of a collection.
NEXT = "next"


def next_page_url(payload):
    """Where the next page of a collection lives, or None on the last page."""
    if not payload:
        return None

    for link in payload.get("links") or []:
        if link.get("rel") == NEXT and link.get("href"):
            return link["href"]

    return None
