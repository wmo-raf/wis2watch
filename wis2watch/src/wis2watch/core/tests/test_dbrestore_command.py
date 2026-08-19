"""Tests for the two things ``dbrestore`` refuses to do.

Neither test lets the restore itself run. What is being checked is the pair of
guards in front of it, and a restore that got as far as `super().handle()`
would drop the database the test suite is running in.
"""

from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ..management.commands.dbrestore import Command

CONFIRM = "--i-know-this-drops-the-database"

#: Shaped like a `pg_stat_activity` row, because that is what the real query
#: returns and the message is built out of the columns.
A_SESSION = (4242, "172.23.0.5", "idle")


class ConfirmationFlagTests(TestCase):
    """The flag that has to be typed out."""

    def test_refuses_without_it(self):
        with self.assertRaises(CommandError) as caught:
            call_command("dbrestore")

        self.assertIn(CONFIRM, str(caught.exception))

    def test_names_the_database_it_would_have_dropped(self):
        """The failure this guards against is being sure you were elsewhere."""
        with self.assertRaises(CommandError) as caught:
            call_command("dbrestore")

        from django.db import connections

        self.assertIn(connections["default"].settings_dict["NAME"], str(caught.exception))


class OtherSessionsTests(TestCase):
    """The guard against restoring underneath a running stack."""

    def test_refuses_while_anything_else_is_connected(self):
        with patch.object(Command, "other_sessions", return_value=[A_SESSION]):
            with self.assertRaises(CommandError) as caught:
                call_command("dbrestore", CONFIRM)

        message = str(caught.exception)
        self.assertIn("4242", message)
        self.assertIn("172.23.0.5", message)

    def test_says_how_to_get_the_database_to_itself(self):
        """A refusal nobody can act on is one people learn to work around."""
        with patch.object(Command, "other_sessions", return_value=[A_SESSION]):
            with self.assertRaises(CommandError) as caught:
                call_command("dbrestore", CONFIRM)

        self.assertIn("docker compose stop", str(caught.exception))

    def test_restores_when_it_has_the_database_to_itself(self):
        with patch.object(Command, "other_sessions", return_value=[]):
            with patch(
                "dbbackup.management.commands.dbrestore.Command.handle",
                return_value=None,
            ) as upstream:
                call_command("dbrestore", CONFIRM)

        self.assertTrue(upstream.called)

    def test_counts_the_extension_workers_out(self):
        """TimescaleDB's own workers are always connected, and pre_restore
        stops them. Counting them would mean the guard never passes."""
        sessions = Command().other_sessions()

        self.assertNotIn(
            "TimescaleDB",
            " ".join(str(column) for row in sessions for column in row),
        )
