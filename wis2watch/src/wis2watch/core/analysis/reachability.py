"""What is known about a centre's own broker, from outside.

Its own module because two surfaces read it: the overview asks it of every
centre at once, off annotations across the whole region, and a centre's page
asks it of one broker record. The states and the rule that decides between
them are written once here, so that the table and the page can never disagree
about the same broker -- which is the kind of contradiction that costs a
diagnostic tool its credibility on the row that mattered.
"""

from django.utils.translation import gettext_lazy as _


class OriginReachability:
    """What is known about a centre's own broker, from outside.

    Four states, because two of them are absences that mean different things.
    A broker nothing has attempted yet is not a broker that failed, and a
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
