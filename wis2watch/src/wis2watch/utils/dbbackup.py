"""Making ``dbbackup`` able to restore a TimescaleDB database.

django-dbbackup's PostgreSQL connector is written for a plain database, and a
hypertable is not one. ``pg_dump`` writes the chunks out as ordinary tables in
``_timescaledb_internal``, and writes the rows that say *which* tables those
are into the extension's own catalogue. Replaying either one against a live
extension is what TimescaleDB refuses to allow: the catalogue is guarded, and
a chunk arriving without its catalogue row is a table that merely resembles
part of a hypertable.

TimescaleDB's answer is to put the extension into a restoring state for the
duration -- ``timescaledb_pre_restore()`` before and ``timescaledb_post_restore()``
after -- which suspends the guards and the background workers. dbbackup has
nowhere to put those calls: ``restore_prefix`` and ``restore_suffix`` wrap the
*command line*, not statements, so there is no hook that runs SQL either side
of ``pg_restore``. Hence a connector rather than configuration.

Three consequences follow from that pair of calls, and each is why one of
upstream's defaults is turned off below:

``--single-transaction``
    ``pre_restore()`` has to be committed before ``pg_restore`` starts, so the
    restore cannot be the same transaction. Wrapping it in one is not a
    stricter version of this procedure; it is a different procedure that
    cannot work.

``--clean --if-exists``
    Emits ``DROP`` against extension-owned objects -- the very machinery
    ``pre_restore()`` has just suspended. The target is dropped and recreated
    here instead, which is both cleaner and what TimescaleDB documents.

extension creation
    ``timescaledb_pre_restore()`` is provided *by* the extension, so it cannot
    be called on a database that does not have it yet -- and a freshly created
    database has nothing. The extensions are therefore created before the
    dump is replayed, rather than by it. ``PgDumpGisConnector`` already does
    exactly this for PostGIS, for exactly this reason.

The restore drops the database it points at. Nothing here guards that; the
guard belongs where the argv is, and lives in the ``dbrestore`` command in
``wis2watch.core.management.commands``.
"""

import logging

from dbbackup.db.postgresql import PgDumpBinaryConnector, parse_postgres_settings

logger = logging.getLogger("dbbackup.command")

#: Applied to the dump. Prod's roles, grants and tablespaces do not exist on a
#: developer's laptop, and without these every one of them is an error to read
#: past on the way to finding out whether the restore actually worked.
DUMP_FLAGS = ("--no-owner", "--no-privileges", "--no-tablespaces")

#: The extensions the dump's objects are defined in terms of. ``timescaledb``
#: is here because ``pre_restore()`` needs it; ``postgis`` because a geometry
#: column cannot be created before the type exists.
REQUIRED_EXTENSIONS = ("timescaledb", "postgis")


class TimescaleConnector(PgDumpBinaryConnector):
    """``pg_dump``/``pg_restore`` with TimescaleDB's restore procedure around it.

    Restoring is destructive by design: the target database is dropped and
    recreated, because a hypertable cannot be replayed over its own remains.
    """

    psql_cmd = "psql"

    # See the module docstring. Each of these is incompatible with the
    # pre/post-restore procedure rather than merely unnecessary alongside it.
    single_transaction = False
    drop = False
    if_exists = False

    #: Inserted immediately after ``pg_restore``. The dump was taken with the
    #: matching flags; repeating them means a dump someone took by hand still
    #: restores onto a laptop that has none of prod's roles.
    pg_options = ["--no-owner", "--no-privileges"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Appended rather than substituted for upstream's `_create_dump`, so a
        # dbbackup upgrade that changes how the dump line is assembled does not
        # silently drop these.
        self.dump_suffix = " ".join((*DUMP_FLAGS, self.dump_suffix)).strip()

    def _psql(self, statement, *, dbname=None):
        """Run one statement, against the target database unless told otherwise.

        Args:
            statement: the SQL to run.
            dbname: the database to run it in, or None for the target. The
                drop and create are run against ``postgres``, since a database
                cannot be dropped by a session connected to it.
        """
        cmd_part, pg_env = parse_postgres_settings(self)

        if dbname is not None:
            cmd_part = f"{cmd_part.rsplit('/', 1)[0]}/{dbname}"

        # --tuples-only --no-align so that a statement asked for its value
        # yields the value alone. DDL prints nothing either way.
        cmd = (
            f"{self.psql_cmd} {cmd_part} --quiet --no-psqlrc --tuples-only --no-align "
            f'--set ON_ERROR_STOP=on -c "{statement}"'
        )
        return self.run_command(cmd, env={**self.restore_env, **pg_env})

    def _recreate_database(self):
        """Replace the target database with an empty one carrying the extensions.

        ``WITH (FORCE)`` because the developer stack keeps five services
        connected to this database, and a restore that fails on whichever one
        happened to be up is a coin toss rather than a procedure.
        """
        name = self.settings["NAME"]

        self._psql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', dbname="postgres")
        self._psql(f'CREATE DATABASE "{name}"', dbname="postgres")

        for extension in REQUIRED_EXTENSIONS:
            self._psql(f"CREATE EXTENSION IF NOT EXISTS {extension} CASCADE")

    def _restore_dump(self, dump):
        """Replay the dump into a database made ready to receive a hypertable."""
        # Django's own connection to the database being dropped would block the
        # drop. Nothing here needs the ORM.
        from django.db import connections

        connections.close_all()

        self._recreate_database()

        version = self._extension_version()
        logger.info("Restoring into timescaledb %s", version)

        self._psql("SELECT timescaledb_pre_restore()")
        try:
            return super()._restore_dump(dump)
        finally:
            # Unconditional: a database left in the restoring state has its
            # background workers stopped and its guards down, and a failed
            # restore is exactly when someone is least likely to notice.
            self._psql("SELECT timescaledb_post_restore()")

    def _extension_version(self):
        """The target's TimescaleDB version, for the log.

        A dump can only be replayed into the version it was taken from, and
        nothing in a custom-format dump records which that was. This cannot
        check the pairing, then -- it can only leave the target's half of it in
        the log, so that a restore that behaved strangely can be placed against
        the image that was running.
        """
        stdout, _ = self._psql(
            "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
        )
        return stdout.read().decode().strip() or "unknown"
