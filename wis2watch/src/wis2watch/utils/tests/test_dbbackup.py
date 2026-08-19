"""Tests for the TimescaleDB backup connector.

These assert on the command lines the connector builds, and run no database
commands at all. That is the point rather than a shortcut: the connector
overrides a *private* method of django-dbbackup, whose command builder has
changed shape between releases, and the failure this guards against is an
upgrade quietly reinstating a flag that TimescaleDB cannot restore under.

A round trip against a real hypertable would exercise something these cannot,
but it would also be the slowest test in the suite, and it would not fail on
the change these are here to catch.
"""

import io

from django.test import SimpleTestCase

from ..dbbackup import TimescaleConnector


class RecordingConnector(TimescaleConnector):
    """A connector that records its commands instead of running them."""

    def __init__(self, *args, fail_restore=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.commands = []
        self.fail_restore = fail_restore

    def run_command(self, command, stdin=None, env=None):
        self.commands.append(command)

        if self.fail_restore and command.startswith("pg_restore"):
            raise RuntimeError("pg_restore failed")

        return io.BytesIO(b"2.29.1\n"), io.BytesIO(b"")

    @property
    def restore_commands(self):
        """Just the statements, for asserting on the order they ran in."""
        return [c for c in self.commands if not c.startswith("pg_restore")]


class DumpCommandTests(SimpleTestCase):
    """What the dump is taken with."""

    def dump_command(self):
        connector = RecordingConnector()
        connector.create_dump()
        return connector.commands[0]

    def test_dumps_in_the_format_pg_restore_reads(self):
        self.assertIn("--format=custom", self.dump_command())

    def test_drops_what_a_laptop_cannot_reproduce(self):
        """Prod's roles, grants and tablespaces exist on no developer machine."""
        command = self.dump_command()

        self.assertIn("--no-owner", command)
        self.assertIn("--no-privileges", command)
        self.assertIn("--no-tablespaces", command)

    def test_does_not_exclude_the_extensions(self):
        """`-e plpgsql` once lived in the settings, and made restores impossible.

        It excluded TimescaleDB's catalogue from the dump while still dumping
        the chunk tables that catalogue describes -- so the chunks arrived
        naming a schema that nothing had created.
        """
        self.assertNotIn(" -e ", self.dump_command())
        self.assertNotIn("--extension", self.dump_command())


class RestoreCommandTests(SimpleTestCase):
    """What the restore does, and in what order."""

    def restore(self, **kwargs):
        connector = RecordingConnector(**kwargs)
        connector.restore_dump(io.BytesIO(b""))
        return connector

    def test_puts_the_extension_into_the_restoring_state(self):
        commands = self.restore().commands

        pre = next(i for i, c in enumerate(commands) if "timescaledb_pre_restore" in c)
        restore = next(i for i, c in enumerate(commands) if c.startswith("pg_restore"))
        post = next(i for i, c in enumerate(commands) if "timescaledb_post_restore" in c)

        self.assertLess(pre, restore)
        self.assertLess(restore, post)

    def test_leaves_the_extension_out_of_it_when_the_restore_fails(self):
        """A database left restoring has its guards down and its workers stopped.

        Which is the state nobody checks for, on the occasion nobody expected.
        """
        connector = RecordingConnector(fail_restore=True)

        with self.assertRaises(RuntimeError):
            connector.restore_dump(io.BytesIO(b""))

        self.assertTrue(
            any("timescaledb_post_restore" in c for c in connector.commands)
        )

    def test_replaces_the_database_rather_than_cleaning_it(self):
        """--clean would DROP the very objects pre_restore has just suspended."""
        commands = self.restore().commands
        restore = next(c for c in commands if c.startswith("pg_restore"))

        self.assertNotIn("--clean", restore)
        self.assertNotIn("--if-exists", restore)
        self.assertTrue(any("DROP DATABASE" in c for c in commands))
        self.assertTrue(any("CREATE DATABASE" in c for c in commands))

    def test_does_not_wrap_the_restore_in_a_transaction(self):
        """pre_restore has to be committed before pg_restore starts."""
        restore = next(c for c in self.restore().commands if c.startswith("pg_restore"))

        self.assertNotIn("--single-transaction", restore)

    def test_creates_the_extensions_before_it_needs_them(self):
        """pre_restore is provided by the extension, so it cannot come first."""
        commands = self.restore().commands

        timescale = next(i for i, c in enumerate(commands) if "EXTENSION IF NOT EXISTS timescaledb" in c)
        postgis = next(i for i, c in enumerate(commands) if "EXTENSION IF NOT EXISTS postgis" in c)
        pre = next(i for i, c in enumerate(commands) if "timescaledb_pre_restore" in c)

        self.assertLess(timescale, pre)
        self.assertLess(postgis, pre)

    def test_drops_the_database_from_outside_itself(self):
        """A session connected to a database cannot drop it."""
        drop = next(c for c in self.restore().commands if "DROP DATABASE" in c)

        self.assertIn("/postgres", drop)
        # The developer stack keeps five services connected; without FORCE the
        # drop succeeds or fails depending on which of them happened to be up.
        self.assertIn("WITH (FORCE)", drop)

    def test_restores_without_prod_ownership(self):
        restore = next(c for c in self.restore().commands if c.startswith("pg_restore"))

        self.assertIn("--no-owner", restore)
        self.assertIn("--no-privileges", restore)
