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

The same reasoning covers the second guard here. The restore empties the
database before it replays the dump, and every other service goes on holding
its connection through that -- so a celery beat tick or an ingest write lands
in the empty database, takes the primary key the dump is about to bring, and
the restore fails building an index an hour after the row that broke it was
written. The connection is the thing that can be checked, so it is what gets
checked.

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

#: Sessions on the target database that are neither this command's own nor the
#: extension's. ``backend_type`` excludes TimescaleDB's background workers,
#: which are always there and which ``pre_restore()`` stops anyway.
OTHER_SESSIONS = """
    SELECT pid, coalesce(host(client_addr), 'local socket'), state
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND backend_type = 'client backend'
      AND pid <> pg_backend_pid()
    ORDER BY pid
"""


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

        self.refuse_if_anything_else_is_connected()

        return super().handle(*args, **options)

    def other_sessions(self):
        """Every session on the target database except this command's own."""
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(OTHER_SESSIONS)
            return cursor.fetchall()

    def refuse_if_anything_else_is_connected(self):
        """Stop unless this command has the database to itself.

        Dropping the database does not need this -- ``WITH (FORCE)`` closes
        whatever is connected. Surviving the next ten minutes does: the
        services reconnect immediately, to a database that is now empty and
        accepting writes, and go on writing into it for the whole length of
        the restore. What that costs is a duplicate key, discovered at the end
        while the indexes are built, naming a row that arrived long before.
        """
        sessions = self.other_sessions()

        if not sessions:
            return

        listed = "\n".join(
            f"  pid {pid}, from {address} ({state})" for pid, address, state in sessions
        )

        raise CommandError(
            f"{len(sessions)} other session(s) are connected to this database:\n\n"
            f"{listed}\n\n"
            f"They would keep writing into the restored database while it is "
            f"being restored, and the restore would fail on their rows. Stop "
            f"them first -- including the web container, which is why this is "
            f"run in a container of its own:\n\n"
            f"  docker compose stop wis2watch wis2watch_celery_worker \\\n"
            f"                      wis2watch_celery_beat wis2watch_ingest \\\n"
            f"                      wis2watch_web_proxy\n"
            f"  docker compose run --rm --no-deps wis2watch manage dbrestore {CONFIRM_FLAG}\n"
            f"  docker compose start"
        )
