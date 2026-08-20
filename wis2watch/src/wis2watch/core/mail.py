"""Reaching the diagnostician without their having to open the tool.

Everything else here reports to a screen somebody chose to look at. This is
the one path that goes the other way, and the whole of it is three questions:
who is told, how what is sent is addressed, and what record is left of having
said it. All three are answered once, so that the digest and the hard-failure
alerts cannot drift into having different ideas of who runs this installation
or of what an operator can go back and read.

Recipients are configuration rather than records. There is one operator of a
regional diagnostic tool, not a subscriber list, and an address that has to be
migrated to change is not one anybody will change. The cost of that is that
the addresses in force change silently, by redeploy, which is exactly why the
archive stores the ones each message actually went to rather than pointing at
the setting.

Nothing here decides whether a message is worth sending. That belongs with
whatever knows what it is about -- ``digest`` will not send an unchanged list,
``alerts`` will not send a blip -- and a module that silently declined to send
would be the hardest thing here to diagnose. What this module will not do is
send without writing down that it did.
"""

import logging
from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from wagtail.admin.utils import get_admin_base_url

from .models import OutgoingEmail

logger = logging.getLogger(__name__)

#: What every message this tool sends is announced by, so that an operator can
#: file them without reading them.
SUBJECT_PREFIX = "[WIS2Watch] "

#: What a row says while the backend is still being waited on. It is written
#: before the send rather than after, so that a worker killed mid-send leaves
#: behind the most that can honestly be claimed: the message was composed and
#: nothing came back to say it arrived.
INCOMPLETE = "The send did not complete"

#: A backend that neither raised nor sent anything. Both callers already read
#: a count of nought as "this did not go", so it is recorded as a failure
#: rather than given a state of its own.
NOTHING_SENT = "The mail backend reported 0 messages sent"


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


def notify(subject, body, recipients, *, kind, summary=""):
    """Send one message, or say why nobody got it, and record either way.

    Args:
        subject: what the message is about, unprefixed.
        body: the message itself, as plain text.
        recipients: the addresses to send it to.
        kind: which of the things this tool sends this is, as one of
            :class:`~wis2watch.core.models.OutgoingEmail`'s kinds. Passed
            rather than inferred: both subjects are composed at send time --
            one from counts, one through ``gettext`` -- so an archive that
            told them apart by reading them would sort mail by the reader's
            language.
        summary: what the message came to, in one line. Stored as the
            preview, because it cannot be recovered from the body: every
            digest opens with the same sentence saying what a digest is.

    Returns:
        int: how many messages were sent -- nought when nobody is configured
        to receive them.

    Failures are not swallowed. A digest counts as delivered by the fact that
    it was sent, and its findings are recorded as reported on the strength of
    that; a send that quietly failed would take those findings out of every
    future digest as well. The archive does not change that: a failed send is
    written down *and* raised, because a row is only read by somebody who
    already suspected, and a worker marked failed is seen by somebody who did
    not.
    """
    subject = f"{SUBJECT_PREFIX}{subject}"

    if not recipients:
        logger.warning(
            "Nobody is configured to receive '%s'; it has not been sent", subject
        )
        _record(
            kind=kind,
            subject=subject,
            summary=summary,
            body=body,
            recipients=[],
            status=OutgoingEmail.NO_RECIPIENTS,
        )
        return 0

    record = _record(
        kind=kind,
        subject=subject,
        summary=summary,
        body=body,
        recipients=list(recipients),
        status=OutgoingEmail.FAILED,
        error_message=INCOMPLETE,
    )

    try:
        sent = send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=recipients,
        )
    except Exception as error:
        _close(record, OutgoingEmail.FAILED, str(error) or type(error).__name__)
        raise

    if not sent:
        _close(record, OutgoingEmail.FAILED, NOTHING_SENT)
        return sent

    _close(record, OutgoingEmail.SENT, "")

    return sent


def _record(*, kind, subject, summary, body, recipients, status, error_message=""):
    """Write down one attempt, before anything can be known about how it ends.

    Deliberately outside any transaction of its own, and neither caller opens
    one: a record of having tried is worth nothing if it rolls back with the
    thing that went wrong.
    """
    return OutgoingEmail.objects.create(
        kind=kind,
        subject=subject,
        summary=summary,
        body=body,
        recipients=recipients,
        status=status,
        error_message=error_message,
    )


def _close(record, status, error_message):
    """Say how the attempt ended, now that it has."""
    record.status = status
    record.error_message = error_message
    record.save(update_fields=["status", "error_message"])


def admin_url(name, *args):
    """The full address of one admin page, for somebody reading their mail.

    A message that named a report without saying where to find it would make
    the reader search the tool for the page the tool had just been looking at.
    """
    return urljoin(
        get_admin_base_url() or "", reverse(name, args=args)
    )
