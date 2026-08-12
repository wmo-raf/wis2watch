"""Reaching the diagnostician without their having to open the tool.

Everything else here reports to a screen somebody chose to look at. This is
the one path that goes the other way, and the whole of it is two questions:
who is told, and how what is sent is addressed. Both are answered once, so
that the digest and the hard-failure alerts cannot drift into having different
ideas of who runs this installation.

Recipients are configuration rather than records. There is one operator of a
regional diagnostic tool, not a subscriber list, and an address that has to be
migrated to change is not one anybody will change.

Nothing here decides whether a message is worth sending. That belongs with
whatever knows what it is about -- ``digest`` will not send an unchanged list,
``alerts`` will not send a blip -- and a module that silently declined to send
would be the hardest thing here to diagnose.
"""

import logging
from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from wagtail.admin.utils import get_admin_base_url

logger = logging.getLogger(__name__)

#: What every message this tool sends is announced by, so that an operator can
#: file them without reading them.
SUBJECT_PREFIX = "[WIS2Watch] "


def digest_recipients():
    """Who receives the daily digest of what changed."""
    return list(getattr(settings, "WIS2WATCH_DIGEST_RECIPIENTS", None) or [])


def alert_recipients():
    """Who receives an immediate alert about the tool itself failing.

    Falls back to the digest's recipients, because the same person reads both
    in the ordinary case, and an installation that had configured the digest
    and never heard about the alerts would be one that thought it was watched.
    Set separately where the urgent address is not the one that reads mail in
    the morning.
    """
    return (
        list(getattr(settings, "WIS2WATCH_ALERT_RECIPIENTS", None) or [])
        or digest_recipients()
    )


def notify(subject, body, recipients):
    """Send one message, or say why nobody got it.

    Args:
        subject: what the message is about, unprefixed.
        body: the message itself, as plain text.
        recipients: the addresses to send it to.

    Returns:
        int: how many messages were sent -- nought when nobody is configured
        to receive them.

    Failures are not swallowed. A digest counts as delivered by the fact that
    it was sent, and its findings are recorded as reported on the strength of
    that; a send that quietly failed would take those findings out of every
    future digest as well.
    """
    if not recipients:
        logger.warning(
            "Nobody is configured to receive '%s'; it has not been sent", subject
        )
        return 0

    return send_mail(
        subject=f"{SUBJECT_PREFIX}{subject}",
        message=body,
        from_email=None,
        recipient_list=recipients,
    )


def admin_url(name, *args):
    """The full address of one admin page, for somebody reading their mail.

    A message that named a report without saying where to find it would make
    the reader search the tool for the page the tool had just been looking at.
    """
    return urljoin(
        get_admin_base_url() or "", reverse(name, args=args)
    )
