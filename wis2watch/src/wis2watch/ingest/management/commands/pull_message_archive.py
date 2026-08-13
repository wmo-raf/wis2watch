"""Ask one centre for the notifications it published, and count them.

This is how a centre's own account of itself is recovered after the fact: for a
node whose broker nothing outside can reach, and for a stretch this tool was
not listening through -- its own downtime, or the weeks before it was installed.

The rollups are rebuilt here rather than left to the schedule. The scheduled
run recomputes the last two days, so a pull reaching further back would sit in
the raw messages until the expiry job dropped them a fortnight later, and the
history would materialise as a side effect of that -- correct by coincidence
rather than by design.

Beware what a deep pull means for propagation findings. Notifications recovered
from a centre's archive across hours the Global Broker was never listened to
will look, to anything comparing the two, like a Global Broker that failed to
carry them. That is why the propagation evaluation judges only the hours it can
prove it was listening through, and why this command can be pointed at any
depth without manufacturing accusations.
"""

from django.core.management.base import BaseCommand, CommandError

from wis2watch.core.models import MessageSource, WIS2Node
from wis2watch.core.rollups import rollup_hours
from wis2watch.ingest.archive import poll_message_archive, trailing_window

#: How deep a pull goes when nobody says. A day is the overlap a daily run
#: wants: long enough to absorb a publisher's stamp skew, short enough to be
#: the thing somebody reaches for without thinking about it.
DEFAULT_HOURS = 24


class Command(BaseCommand):
    help = "Pull one centre's own archive of the notifications it published"

    def add_arguments(self, parser):
        parser.add_argument(
            "centre_id",
            help="The WIS2 centre ID of the node to ask, e.g. sc-seychelles-met",
        )
        parser.add_argument(
            "--hours",
            type=int,
            default=DEFAULT_HOURS,
            help=(
                "How many hours back to ask for. The window is asked for "
                "outright each time rather than resumed, so overlapping an "
                "earlier pull costs nothing"
            ),
        )

    def handle(self, *args, **options):
        source = self.archive_of(options["centre_id"])
        hours = self.depth(options["hours"])

        since, until = trailing_window(hours)

        sync_log = poll_message_archive(source, since=since, until=until)

        style = self.style.ERROR if sync_log.error_message else self.style.SUCCESS

        self.stdout.write(
            style(f"{source.node.centre_id} message archive: {sync_log.summary}")
        )

        if sync_log.error_message:
            self.stdout.write(self.style.ERROR(f"  {sync_log.error_message}"))

        self.rebuild_rollups(since, until)

    def rebuild_rollups(self, since, until):
        """Count the hours that were just pulled.

        Run whatever the poll came to. A run that failed part way through still
        stored the pages it read before it failed, and those hours are as much
        in need of counting as a whole run's.
        """
        counts = rollup_hours(since=since, until=until)

        self.stdout.write(
            f"Rolled up {since:%Y-%m-%d %H:%M} to {until:%Y-%m-%d %H:%M} UTC: "
            f"{counts.summary}"
        )

    def archive_of(self, centre_id):
        """The vantage point on this centre's archive, or a refusal.

        Both refusals are the operator's to act on rather than something to
        guess past: a centre nothing has registered may be a typo, and one with
        no archive registered needs an address nobody here can invent -- where a
        centre serves its notifications is a wis2box convention rather than
        anything WCMP2 advertises, and the admin is where it is corrected.
        """
        node = WIS2Node.objects.filter(centre_id=centre_id.strip().lower()).first()

        if node is None:
            raise CommandError(f"No node is registered under the centre ID {centre_id}")

        source = node.message_sources.filter(
            source_type=MessageSource.ORIGIN_API
        ).first()

        if source is None:
            raise CommandError(
                f"{node.centre_id} has no message archive registered. Add one "
                "against the node under Message Sources, with the address it "
                "serves its own notifications at."
            )

        return source

    def depth(self, hours):
        """How far back to ask, or a refusal.

        A window of nothing would report a successful run that read an archive
        and found it empty, which is a different finding entirely.
        """
        if hours < 1:
            raise CommandError("A pull covers at least one hour")

        return hours
