"""``dbrestore``, with the drop it performs made impossible to do by accident.

The TimescaleDB connector restores by dropping the target database and
recreating it, because a hypertable cannot be replayed over its own remains.
That makes ``dbrestore`` a one-command way to destroy whichever database the
environment happens to point at -- and the environment on a production host
points at production. Upstream's confirmation prompt is not enough on its own:
``--noinput`` exists precisely to skip it, and a restore is the kind of thing
that ends up in a script.

So the destruction has to be named on the command line. A flag nobody types by
muscle memory, and which reads, in a shell history or a runbook, as a decision
that was made rather than a default that was taken.

This shadows ``dbbackup``'s own command. Django registers commands by walking
INSTALLED_APPS in reverse, so a duplicate name goes to the application listed
*earliest* -- which is why ``dbbackup`` sits below the project's apps there.
Reordering the two silently reinstates upstream's version, and with it the
unguarded drop, which is the sort of change that looks like tidying.
"""

from dbbackup.management.commands.dbrestore import Command as DbRestoreCommand
from django.core.management.base import CommandError

#: Long, unabbreviated, and a full sentence about the consequence. Typing it is
#: meant to be the moment someone checks which database they are pointed at.
CONFIRM_FLAG = "--i-know-this-drops-the-database"


class Command(DbRestoreCommand):
    help = (
        "Restore a database backup. Drops and recreates the target database, "
        f"so it requires {CONFIRM_FLAG}."
    )

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            CONFIRM_FLAG,
            action="store_true",
            dest="confirmed_drop",
            help="Required. Confirms that the target database is to be destroyed.",
        )

    def handle(self, *args, **options):
        if not options.get("confirmed_drop"):
            # The database is named because the whole failure mode this guards
            # against is being sure you were pointed somewhere else.
            from django.db import connections

            name = connections["default"].settings_dict["NAME"]
            raise CommandError(
                f"This would DROP the database '{name}' and rebuild it from a "
                f"backup. Every row currently in it would be lost.\n\n"
                f"If that is what you want, pass {CONFIRM_FLAG}."
            )

        return super().handle(*args, **options)
