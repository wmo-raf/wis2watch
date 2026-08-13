"""Reading a centre's own archive of the notifications it published.

A wis2box serves the notifications it has published as an OGC API Features
collection, and each feature in it *is* a WIS2 Notification Message -- the same
JSON the broker carries, envelope and all. So there is nothing to normalise
here: what the page offers is handed to :func:`parse_notification` as it
stands, which is what lets one store path serve both vantage points on a
centre.

What the page does not carry is a topic. On the broker, the topic is what says
which centre published, whether the message is the centre's own publication or
a Global Cache's copy of it, and which dataset it belongs to. None of that is
in the archive, and none of it is invented here: the centre is known because
the poller chose the address, and everything else is read off the message or
left absent.
"""


def archived_notifications(payload):
    """The notification messages a page of a centre's archive carries.

    Returned as captured. Whether one of them can be identified in time --
    and so stored at all -- is :func:`parse_notification`'s question, asked
    once for both vantage points rather than answered again here.
    """
    if not payload:
        return []

    return payload.get("features") or []
