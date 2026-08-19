"""The statistics endpoints, as an admin page's island reaches them.

Two things are guarded here that the analysis tests cannot see. One is who may
ask: everything these endpoints return is what an admin page shows, and until
now any account with a password could read it. The other is the shape that
crosses the wire -- the dataclass is the contract, so a field renamed on the
Python side is a chart that silently stops drawing, and the assertions below
are what makes that a failing test rather than a blank panel.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as dj_timezone

from wis2watch.core.models import MessageSource, WIS2Node
from wis2watch.core.tests.support import declare_station, observe_station, published


def admin_reader(username="diagnostician"):
    """Someone who may open the admin, and nothing more.

    Not a superuser: a superuser passes every permission check there is, so a
    test that granted one would pass whether or not the check exists.
    """
    user = get_user_model().objects.create_user(username, password="s3cret")
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="wagtailadmin", codename="access_admin"
        )
    )

    return user


class StatisticsEndpointTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.client.force_login(admin_reader())

    def url(self, node=None):
        return reverse("node_statistics_summary", args=[(node or self.kenya).pk])

    def summary(self, node=None):
        response = self.client.get(self.url(node))

        self.assertEqual(response.status_code, 200)

        return response.json()

    def declare(self, wigos_id):
        return declare_station(self.kenya, wigos_id)

    def transmitted(self, wigos_id, *, hours_ago=1):
        # Measured back from the real clock: the view has no ``now`` seam and
        # should not grow one, so a fixed instant here would drift into "gone
        # quiet" the day after it was written.
        return observe_station(
            self.kenya, wigos_id, last_seen=dj_timezone.now() - timedelta(hours=hours_ago)
        )


class AccessTests(StatisticsEndpointTestCase):
    """Who may read what only an admin page was ever meant to show."""

    def test_a_reader_who_is_not_signed_in_is_refused(self):
        self.client.logout()

        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_a_signed_in_account_with_no_admin_access_is_refused(self):
        """Having a password is not having been shown the monitoring."""
        self.client.force_login(
            get_user_model().objects.create_user("outsider", password="s3cret")
        )

        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_the_nodes_api_refuses_the_same_account(self):
        """It has been readable by any logged-in account until now."""
        self.client.force_login(
            get_user_model().objects.create_user("outsider", password="s3cret")
        )

        self.assertEqual(self.client.get(reverse("nodes_api")).status_code, 403)

    def test_the_nodes_api_still_answers_an_admin_reader(self):
        self.assertEqual(self.client.get(reverse("nodes_api")).status_code, 200)


class SummaryResponseTests(StatisticsEndpointTestCase):
    """The shape the island binds to."""

    def test_the_standing_counts_cross_the_wire(self):
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708")
        self.transmitted("0-20000-0-63999")

        counts = self.summary()["now"]

        self.assertEqual(counts["declared_station_count"], 1)
        self.assertEqual(counts["undeclared_transmitting"], 1)
        self.assertEqual(
            set(counts),
            {
                "transmitting",
                "gone_quiet",
                "never_transmitted",
                "undeclared_transmitting",
                "declared_station_count",
                "unlocated_station_count",
                "buckets",
                "hourly",
            },
        )

    def test_the_resolved_window_travels_as_utc_instants(self):
        window = self.summary()["window"]

        self.assertEqual(window["key"], "24h")
        self.assertEqual(window["grain"], "hour")
        self.assertTrue(window["since"].endswith("Z"), window["since"])
        self.assertTrue(window["until"].endswith("Z"), window["until"])

    def test_the_windows_on_offer_travel_with_their_labels(self):
        offered = self.summary()["windows"]

        self.assertEqual(
            [window["key"] for window in offered], ["24h", "7d", "30d", "90d"]
        )
        self.assertEqual(offered[0]["label"], "Last 24 hours")

    def test_the_response_says_when_it_was_computed_and_what_quiet_means(self):
        summary = self.summary()

        self.assertTrue(summary["generated_at"].endswith("Z"))
        self.assertEqual(summary["stale_after_hours"], 24)

    def test_the_vantage_the_volumes_come_from_is_named(self):
        self.assertEqual(
            self.summary()["vantage"], {"source_type": "global_broker", "active": True}
        )

    def test_a_node_with_no_history_is_answered_with_zeros(self):
        """Not a 404 and not a null block: the empty tab is the finding."""
        counts = self.summary()["now"]

        self.assertEqual(counts["declared_station_count"], 0)
        self.assertEqual(counts["transmitting"], 0)

    def test_a_node_that_does_not_exist_is_not_found(self):
        missing = reverse("node_statistics_summary", args=[9999])

        self.assertEqual(self.client.get(missing).status_code, 404)

    def test_the_endpoint_logs_what_it_cost(self):
        """No caching yet, so the timings have to exist the day someone measures."""
        with self.assertLogs("wis2watch.api.views", level="DEBUG") as logged:
            self.summary()

        self.assertTrue(
            any("24h" in line for line in logged.output),
            logged.output,
        )


class HourlySeriesResponseTests(StatisticsEndpointTestCase):
    """The axis and the series the tab's first chart is drawn from."""

    def rollup(self, *, hours_ago=1, messages=1, station=None):
        """One hour of Global Broker traffic for this centre.

        Measured back from the real clock for the same reason the observations
        above are: the view has no ``now`` seam, so a fixed instant here would
        fall out of the window the day after it was written.
        """
        return published(
            self.kenya,
            source=self.global_broker,
            hour=dj_timezone.now() - timedelta(hours=hours_ago),
            messages=messages,
            station=station,
        )

    def test_the_bucket_axis_travels_once_at_the_top(self):
        """Not repeated per series, and never worked out on the client."""
        summary = self.summary()

        self.assertEqual(len(summary["buckets"]), 24)
        self.assertEqual(set(summary["buckets"][0]), {"start", "partial"})
        self.assertTrue(summary["buckets"][0]["start"].endswith("Z"))
        self.assertFalse(any(bucket["partial"] for bucket in summary["buckets"]))

    def test_the_fixed_block_carries_the_hours_its_series_are_drawn_on(self):
        now = self.summary()["now"]

        self.assertEqual(len(now["buckets"]), 24)
        self.assertEqual(now["buckets"], self.summary()["buckets"])

    def test_the_hourly_series_is_dense_and_crosses_the_wire_whole(self):
        series = self.summary()["now"]["hourly"]

        self.assertEqual(len(series), 24)
        self.assertEqual(
            set(series[0]), {"messages", "unattributed_messages", "stations"}
        )

    def test_an_hour_of_traffic_is_counted_into_its_own_bucket(self):
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=1, messages=4, station=station)

        series = self.summary()["now"]["hourly"]

        self.assertEqual(series[-1], {
            "messages": 4,
            "unattributed_messages": 0,
            "stations": 1,
        })

    def test_an_hour_that_named_no_station_reaches_the_client_as_such(self):
        """The mark the chart draws instead of a bar depends on this pair."""
        self.rollup(hours_ago=1, messages=6, station=None)

        series = self.summary()["now"]["hourly"]

        self.assertEqual(series[-1]["unattributed_messages"], 6)
        self.assertEqual(series[-1]["stations"], 0)


class MountPointTests(TestCase):
    """What the page hands the island before it can ask anything."""

    def setUp(self):
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.client.force_login(admin_reader())

    def test_the_statistics_tab_reverses_the_summary_url_for_the_island(self):
        """A path assembled in JavaScript is a path nobody can rename here."""
        response = self.client.get(reverse("node_statistics", args=[self.kenya.pk]))
        summary_url = reverse("node_statistics_summary", args=[self.kenya.pk])

        self.assertContains(response, f'data-summary-url="{summary_url}"')
