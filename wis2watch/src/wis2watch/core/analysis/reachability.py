"""What is known about the transports a centre offers on its own account.

Its own module because several surfaces read it: the overview asks it of every
centre at once, off annotations across the whole region, a centre's page asks
it of that centre's own vantage points, and the propagation report names the
one an individual finding came through. The states and the rules that decide
between them are written once here, so that the table, the page and the report
can never disagree about the same centre -- which is the kind of contradiction
that costs a diagnostic tool its credibility on the row that mattered.

Three vocabularies, because they answer three questions. What one vantage
point last reported is ``OriginReachability``. Which transport is carrying a
centre's view of itself, if either is, is ``OriginWatch``. Which one a
particular observation arrived through is ``OriginTransport``.
"""

from django.utils.translation import gettext_lazy as _

from ..models import MessageSource


class OriginReachability:
    """What one of a centre's own vantage points last reported, from outside.

    Four states, because two of them are absences that mean different things.
    A vantage point nothing has attempted yet is not one that failed, and a
    centre whose catalogue record advertises no broker at all is neither --
    calling any of these "unreachable" would report a fault that has not been
    observed.
    """

    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    NOT_ATTEMPTED = "not_attempted"
    NOT_ADVERTISED = "not_advertised"

    CHOICES = [
        (UNREACHABLE, _("Not reachable")),
        (NOT_ATTEMPTED, _("Not attempted yet")),
        (NOT_ADVERTISED, _("No broker advertised")),
        (REACHABLE, _("Reachable")),
    ]

    LABELS = dict(CHOICES)

    @classmethod
    def of(cls, is_reachable, *, advertised=True):
        """What a broker's stored reachability amounts to.

        Read from the one field and from whether there is a broker at all,
        because both absences are spelled as the absence of a value: no broker
        record, and a broker record nothing has dialled yet.
        """
        if not advertised:
            return cls.NOT_ADVERTISED

        if is_reachable is None:
            return cls.NOT_ATTEMPTED

        return cls.REACHABLE if is_reachable else cls.UNREACHABLE

    @classmethod
    def label(cls, reachability):
        """What a reachability is called, for a table cell or a page."""
        return cls.LABELS.get(reachability, reachability)


class OriginWatch:
    """Which transport is carrying a centre's own view of itself, if either.

    Three states, because a centre offers two transports on its own account
    and the difference between them is a finding rather than plumbing. Its
    broker is what WIS2 obliges it to run; its archive is an HTTP endpoint
    polled precisely when that broker will not answer. Both entitle this tool
    to judge the centre's propagation, and only one of them says the centre is
    doing what it is required to do.

    So the fallback is a state of its own rather than a second kind of green.
    Collapsing the two into one "watched" would launder a broker nobody
    outside can dial into a healthy row, and reporting only the broker would
    call a centre this tool is watching over HTTPS unwatched -- an operator
    reading "origin unreachable" beside propagation gaps piling up for the
    same centre would reasonably conclude the tool was broken.

    Watched means answering *and* still being asked. Reachability is only ever
    what the last attempt recorded, so a vantage point switched off in the
    admin carries an answer that has since gone stale -- and it is the same
    rule ``MessageSource.objects.watched_origins`` applies, because a table
    calling a centre watched while the evaluation refuses to judge it is the
    contradiction that costs both of them their credibility.
    """

    AT_BROKER = "watched_at_broker"
    AT_ARCHIVE = "watched_at_archive"
    UNWATCHED = "unwatched"

    # "No broker answering" rather than "broker not answering": a centre may
    # reach this state having advertised no broker at all, and the archive is
    # polled for exactly those centres too. What is true of all of them is
    # that nothing is answering at a broker; which flavour of not answering it
    # is belongs beside the state rather than inside it.
    CHOICES = [
        (UNWATCHED, _("Not watched")),
        (AT_ARCHIVE, _("Archive only, no broker answering")),
        (AT_BROKER, _("Watched at broker")),
    ]

    LABELS = dict(CHOICES)

    #: The states in which some vantage point of the centre's own is
    #: answering, and the centre may therefore be judged on what it published.
    WATCHED = (AT_BROKER, AT_ARCHIVE)

    @classmethod
    def of(cls, *, broker, archive):
        """Which transport is carrying the centre, given what each is doing.

        Both arguments are whether that vantage point is watching now --
        answering and still being asked. The broker is preferred where both
        are, because the two are the same witness seen twice and the broker is
        the one the centre is obliged to run.
        """
        if broker:
            return cls.AT_BROKER

        if archive:
            return cls.AT_ARCHIVE

        return cls.UNWATCHED

    @classmethod
    def label(cls, watch):
        """What a watch state is called, for a table cell or a page."""
        return cls.LABELS.get(watch, watch)


class OriginTransport:
    """Which of a centre's own transports an observation arrived through.

    Named on the finding rather than left to the reader, because a propagation
    gap is taken to a national met service by email and "we saw you publish
    this and the world did not" invites the obvious question of how we saw it.
    A centre whose broker is dark is being watched through its archive, and a
    finding that does not say so reads as evidence from a broker the centre
    knows nobody can reach. Everywhere that names a transport says it by
    reference to this, so the report, the page and the email cannot describe
    the same observation three ways.

    Two ways of naming no transport, because they are opposite findings. The
    vantage point a gap was found through may since have been deleted, which
    is not "no transport": something observed the notification and what has
    been lost is only the note of which. A centre that offers neither
    transport has nothing observing it at all, and telling an operator that
    its vantage point is no longer recorded would send them looking for one
    that never existed.

    The two named transports take the source types they stand for rather than
    values of their own. They are one vocabulary read two ways -- what a row
    is stored as, and what a centre is told -- and a second set of constants
    would be a mapping to keep in step for no gain.
    """

    BROKER = MessageSource.ORIGIN_BROKER
    ARCHIVE = MessageSource.ORIGIN_API
    UNRECORDED = "unrecorded"
    NONE = "none"

    CHOICES = [
        (BROKER, _("The centre's own broker")),
        (ARCHIVE, _("The centre's message archive")),
        (UNRECORDED, _("No longer recorded")),
        (NONE, _("None")),
    ]

    LABELS = dict(CHOICES)

    @classmethod
    def of(cls, source_type):
        """Which transport observed something, from the source type it was seen at.

        Anything that is not one of the centre's own transports is unrecorded
        rather than named. An observation is only ever made at a vantage
        point, so a source type from outside that set -- or none at all, where
        the row it named has since been deleted -- is one this tool can no
        longer explain, and inventing a transport for it would be the one
        thing naming the transport exists to prevent.
        """
        if source_type in (cls.BROKER, cls.ARCHIVE):
            return source_type

        return cls.UNRECORDED

    @classmethod
    def label(cls, transport):
        """What a transport is called, for a table cell or an email."""
        return cls.LABELS.get(transport, transport)
