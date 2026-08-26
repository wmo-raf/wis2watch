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


class AllNodesEndpointTests(StatisticsEndpointTestCase):
    """The region in one response, for the panel on the admin home.

    The envelope is what is guarded here rather than the findings, which
    ``core.tests.test_node_statistics`` covers against a seeded database. What
    this can see and those cannot is the shape that crosses the wire: the
    vocabularies the client words its cells from, and the link each row is
    reached by. Both are Python's on purpose, and a field renamed on this side
    is a panel that silently draws keys instead of words.
    """

    def url(self):
        return reverse("nodes_statistics")

    def region(self):
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)

        return response.json()

    def test_a_reader_who_is_not_signed_in_is_refused(self):
        self.client.logout()

        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_a_signed_in_account_with_no_admin_access_is_refused(self):
        self.client.force_login(
            get_user_model().objects.create_user("outsider", password="s3cret")
        )

        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_the_envelope_carries_the_frame_the_rows_are_read_against(self):
        found = self.region()

        self.assertEqual(
            set(found),
            {
                "generated_at",
                "stale_after_hours",
                "window",
                "hours",
                "rows",
                "vocabularies",
            },
        )
        self.assertEqual(len(found["hours"]), 24)
        self.assertEqual(found["window"]["key"], "24h")

    def test_every_registered_centre_is_a_row(self):
        WIS2Node.objects.create(centre_id="bj-meteobenin", name="Benin")

        found = self.region()

        self.assertEqual(
            {row["centre_id"] for row in found["rows"]},
            {"ke-meteo", "bj-meteobenin"},
        )

    def test_a_row_carries_keys_and_the_link_it_is_reached_by(self):
        row = self.region()["rows"][0]

        self.assertEqual(
            set(row),
            {
                "node_id",
                "centre_id",
                "country_name",
                # Two verdicts, because two tables ask different questions of
                # one row. Both travel always: one request serves both, and
                # neither table can be computed from rows the other never saw.
                "transmission",
                "standing",
                "last_seen_at",
                "hours_quiet",
                "messages_in_window",
                "sparkline",
                "origin_watch",
                "cache_pickup",
                "silence",
                # Drawn by the detailed table only, and carried always for the
                # same reason the second verdict is.
                "dataset_count",
                "station_count",
                "origin_broker_reachability",
                "origin_last_error",
                "silent_dataset_count",
                "judged_dataset_count",
                "node_url",
            },
        )
        # Reversed on this side rather than assembled in a bundle that is
        # built ahead of time and committed, which is ADR-0001's rule for
        # anything reversible and is no different for travelling in JSON.
        #
        # The *statistics* tab, not the diagnostic one: going back in time is
        # that tab's job, and it is the question both tables leave a reader
        # with.
        self.assertEqual(
            row["node_url"], reverse("node_statistics", args=[self.kenya.pk])
        )

    def test_a_centre_whose_own_broker_does_not_answer_carries_why(self):
        """What the overview page used to print under its Origin badge.

        It is a tooltip on that badge now rather than a second line, and it
        arrives whole -- the page cut the error at sixty characters.
        """
        MessageSource.objects.create(
            name="ke-meteo origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.kenya,
            centre_id="ke-meteo",
            host="wis.ke-meteo.example.int",
            is_reachable=False,
            last_error="Connection timed out after 30 seconds of waiting",
        )

        row = self.region()["rows"][0]

        self.assertEqual(row["origin_broker_reachability"], "unreachable")
        self.assertEqual(
            row["origin_last_error"],
            "Connection timed out after 30 seconds of waiting",
        )

    def test_a_centre_advertising_no_broker_is_not_called_unreachable(self):
        """A fault that has not been observed is not a fault."""
        row = self.region()["rows"][0]

        self.assertEqual(row["origin_broker_reachability"], "not_advertised")

    def test_the_two_verdicts_agree_about_what_is_worst(self):
        """The glance verdict is a coarsening of the full one, not a rival.

        Its three faults are the three worst ranks of the full standing under
        the same three names, and `transmitting` is exactly the ranks below
        them. That is what lets one server order serve both tables -- and what
        stops the two surfaces disagreeing about which centre to look at first.
        """
        found = self.region()
        ranks = {
            field: {entry["key"]: rank for rank, entry in enumerate(entries)}
            for field, entries in found["vocabularies"].items()
        }

        for row in found["rows"]:
            transmitting = row["transmission"] == "transmitting"

            if transmitting:
                # Every plumbing fault, and the clean bill, sit below the three
                # the glance verdict knows about.
                self.assertGreaterEqual(ranks["standing"][row["standing"]], 3)
            else:
                self.assertEqual(row["transmission"], row["standing"])

    def test_the_words_are_the_servers_and_travel_once(self):
        vocabularies = self.region()["vocabularies"]

        self.assertEqual(
            set(vocabularies),
            {
                "transmission",
                "standing",
                "origin_watch",
                "cache_pickup",
                "silence",
                # Not a column of its own -- it is what the origin badge says
                # under itself on the detailed page.
                "origin_broker_reachability",
            },
        )
        self.assertEqual(
            [entry["key"] for entry in vocabularies["transmission"]],
            ["never_seen", "stale", "silent", "transmitting"],
        )
        # Worst first, which is the order the rows arrive in and the order a
        # filter control offers. A client that sorted these itself would be a
        # second opinion about which standing is the concerning one.
        self.assertEqual(
            [entry["key"] for entry in vocabularies["standing"]],
            [
                "never_seen",
                "stale",
                "silent",
                "not_cached",
                "no_broker",
                "archive_only",
                "healthy",
            ],
        )
        self.assertEqual(vocabularies["standing"][0]["label"], "Never heard from")

    def test_every_standing_a_row_can_carry_is_in_the_vocabulary(self):
        """A filter offering a standing no row has is fine; the reverse is not.

        A row spelled in a word the envelope never sent is a cell the client
        can only draw as a raw key, and a filter that cannot select it.
        """
        found = self.region()
        offered = {entry["key"] for entry in found["vocabularies"]["standing"]}

        self.assertLessEqual(
            {row["standing"] for row in found["rows"]}, offered
        )


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
                "advertises_station_registry",
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

    def test_the_statistics_tab_reverses_the_stations_url_for_the_island(self):
        """The rows are a second request, so they are a second reversed path."""
        response = self.client.get(reverse("node_statistics", args=[self.kenya.pk]))
        stations_url = reverse("node_statistics_stations", args=[self.kenya.pk])

        self.assertContains(response, f'data-stations-url="{stations_url}"')


class WindowParameterTests(StatisticsEndpointTestCase):
    """The one input this endpoint takes, and the only spellings of it."""

    def test_the_default_window_is_the_last_day(self):
        self.assertEqual(self.summary()["window"]["key"], "24h")

    def test_every_offered_window_can_be_asked_for(self):
        for key, grain in (("24h", "hour"), ("7d", "day"), ("30d", "day"), ("90d", "day")):
            with self.subTest(window=key):
                response = self.client.get(self.url(), {"window": key})

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["window"]["key"], key)
                self.assertEqual(response.json()["window"]["grain"], grain)

    def test_a_window_nobody_offers_is_refused_with_the_ones_that_exist(self):
        """A refusal a client cannot act on sends a reader into the source."""
        response = self.client.get(self.url(), {"window": "6h"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["valid_windows"], ["24h", "7d", "30d", "90d"])

    def test_a_free_range_is_not_a_window(self):
        """The unbounded question is refused by there being no way to ask it."""
        response = self.client.get(
            self.url(), {"since": "2024-01-01", "until": "2026-01-01"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["window"]["key"], "24h")

    def test_an_empty_window_parameter_is_the_default_rather_than_a_refusal(self):
        """A client that built a querystring is not a reader asking for '6h'."""
        response = self.client.get(self.url(), {"window": ""})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["window"]["key"], "24h")

    def test_the_endpoint_logs_the_window_it_was_asked_for(self):
        with self.assertLogs("wis2watch.api.views", level="DEBUG") as logged:
            self.client.get(self.url(), {"window": "30d"})

        self.assertTrue(any("30d" in line for line in logged.output), logged.output)

    def test_the_longest_window_logs_what_it_cost(self):
        """90d is where the hour-of-day query is longest, so it is timed too."""
        with self.assertLogs("wis2watch.api.views", level="DEBUG") as logged:
            self.client.get(self.url(), {"window": "90d"})

        self.assertTrue(
            any("90d" in line and "took=" in line for line in logged.output),
            logged.output,
        )


class WindowStatsResponseTests(StatisticsEndpointTestCase):
    """The moving block the control binds to."""

    def summary_over(self, window):
        response = self.client.get(self.url(), {"window": window})

        self.assertEqual(response.status_code, 200)

        return response.json()

    def test_the_moving_figures_cross_the_wire_in_their_own_block(self):
        stats = self.summary()["window_stats"]

        self.assertEqual(
            set(stats),
            {
                "reported_station_count",
                "declared_station_count",
                "messages_total",
                "unattributed_messages_total",
                "daily",
                "hour_of_day",
            },
        )

    def test_the_window_coverage_is_reported_against_the_declared_population(self):
        self.declare("0-20000-0-63708")
        self.declare("0-20000-0-63709")

        stats = self.summary()["window_stats"]

        self.assertEqual(stats["declared_station_count"], 2)
        self.assertEqual(stats["reported_station_count"], 0)

    def test_there_is_no_daily_series_at_the_default_window(self):
        self.assertIsNone(self.summary()["window_stats"]["daily"])

    def test_a_daily_window_carries_a_dense_series_against_its_own_axis(self):
        summary = self.summary_over("7d")
        daily = summary["window_stats"]["daily"]

        self.assertEqual(len(daily), 7)
        self.assertEqual(len(daily), len(summary["buckets"]))
        self.assertEqual(
            set(daily[0]),
            {
                "messages",
                "unattributed_messages",
                "stations",
                "messages_per_active_station",
            },
        )

    def test_the_newest_daily_bucket_is_the_day_in_progress(self):
        buckets = self.summary_over("7d")["buckets"]

        self.assertTrue(buckets[-1]["partial"])
        self.assertFalse(any(bucket["partial"] for bucket in buckets[:-1]))

    def test_a_day_nothing_reported_in_carries_no_ratio(self):
        daily = self.summary_over("7d")["window_stats"]["daily"]

        self.assertIsNone(daily[0]["messages_per_active_station"])

    def test_there_is_no_hour_of_day_profile_at_the_default_window(self):
        """Over one day it would be the hourly chart in another unit."""
        self.assertIsNone(self.summary()["window_stats"]["hour_of_day"])

    def test_a_daily_window_carries_twenty_four_hour_of_day_counts(self):
        profile = self.summary_over("7d")["window_stats"]["hour_of_day"]

        self.assertEqual(len(profile), 24)
        self.assertEqual(profile, [0] * 24)

    def test_the_hour_of_day_profile_is_message_volume_on_the_utc_clock(self):
        """The one figure on this tab that crosses the wire as raw volume."""
        # Measured back from the real clock, as everything here is: the view
        # has no ``now`` seam, so the hour this lands on is read off the same
        # instant rather than written down.
        when = dj_timezone.now() - timedelta(days=2)
        published(self.kenya, source=self.global_broker, hour=when, messages=5)

        profile = self.summary_over("7d")["window_stats"]["hour_of_day"]

        self.assertEqual(profile[when.hour], 5)
        self.assertEqual(sum(profile), 5)

    def test_the_standing_block_does_not_move_with_the_window(self):
        """The control moves everything on the tab except these figures."""
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708")

        self.assertEqual(self.summary()["now"], self.summary_over("90d")["now"])


class StationRowsEndpointTestCase(StatisticsEndpointTestCase):
    """The rows the table and, later, the matrix are drawn from."""

    def stations_url(self, node=None):
        return reverse("node_statistics_stations", args=[(node or self.kenya).pk])

    def payload(self, node=None, **params):
        response = self.client.get(self.stations_url(node), params)

        self.assertEqual(response.status_code, 200)

        return response.json()

    def rows(self, **params):
        return self.payload(**params)["stations"]


class StationRowsAccessTests(StationRowsEndpointTestCase):
    """The same door as everything else on this tab."""

    def test_a_reader_who_is_not_signed_in_is_refused(self):
        self.client.logout()

        self.assertEqual(self.client.get(self.stations_url()).status_code, 403)

    def test_a_signed_in_account_with_no_admin_access_is_refused(self):
        self.client.force_login(
            get_user_model().objects.create_user("outsider", password="s3cret")
        )

        self.assertEqual(self.client.get(self.stations_url()).status_code, 403)


class StationRowsResponseTests(StationRowsEndpointTestCase):
    """The shape the table binds to."""

    def test_a_row_carries_exactly_what_the_contract_says_it_does(self):
        """A field renamed here is a column that silently stops drawing."""
        self.declare("0-20000-0-63708")

        self.assertEqual(
            set(self.rows()[0]),
            {
                "station_id",
                "wigos_id",
                "name",
                "local_name",
                "standing",
                "last_heard",
                "hours_quiet",
                "latitude",
                "longitude",
                "sparkline",
                "messages_in_window",
                "active_buckets",
                "presence",
                # Added with the station baseline in #112, and missing from
                # this set until #118 ran the whole suite: the row grew a
                # field and the contract that is supposed to catch exactly
                # that was not brought with it.
                "baseline_hours",
            },
        )

    def test_the_row_leaves_out_what_the_table_does_not_show(self):
        self.declare("0-20000-0-63708")

        absent_by_design = (
            "facility_type",
            "local_id",
            "declared_by_registry",
            "elevation",
        )

        for absent in absent_by_design:
            with self.subTest(field=absent):
                self.assertNotIn(absent, self.rows()[0])

    def test_a_declared_station_never_heard_from_carries_zeros_not_nulls(self):
        """The commonest row on a centre in trouble, and it has to draw."""
        self.declare("0-20000-0-63708")

        row = self.rows()[0]

        self.assertEqual(row["standing"], "never_transmitted")
        self.assertIsNone(row["last_heard"])
        self.assertIsNone(row["hours_quiet"])
        self.assertEqual(row["sparkline"], [0] * 24)
        self.assertEqual(row["presence"], [0] * 24)
        self.assertEqual(row["messages_in_window"], 0)
        self.assertEqual(row["active_buckets"], 0)

    def test_a_row_says_when_this_centre_last_heard_the_station(self):
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708", hours_ago=2)

        row = self.rows()[0]

        self.assertEqual(row["standing"], "transmitting")
        self.assertTrue(row["last_heard"].endswith("Z"), row["last_heard"])

    def test_the_rows_arrive_with_what_is_broken_at_the_top(self):
        self.declare("0-20000-0-63708")
        self.declare("0-20000-0-63709")
        self.transmitted("0-20000-0-63709")

        self.assertEqual(
            [row["standing"] for row in self.rows()],
            ["never_transmitted", "transmitting"],
        )

    def test_every_row_travels_with_no_way_to_ask_for_fewer(self):
        """Server paging is the trap: a stripe only shows on the page you are on."""
        for number in range(5):
            self.declare(f"0-20000-0-6370{number}")

        self.assertEqual(len(self.rows()), 5)
        self.assertEqual(len(self.rows(page=1, page_size=2)), 5)
        self.assertNotIn("next", self.payload())

    def test_a_centre_with_no_stations_is_answered_with_an_empty_list(self):
        """Not a 404 and not a null: the empty table is the finding."""
        self.assertEqual(self.payload()["stations"], [])

    def test_a_centre_that_does_not_exist_is_not_found(self):
        missing = reverse("node_statistics_stations", args=[9999])

        self.assertEqual(self.client.get(missing).status_code, 404)

    def test_the_bucket_axis_travels_once_rather_than_on_every_row(self):
        payload = self.payload()

        self.assertEqual(len(payload["buckets"]), 24)
        self.assertEqual(set(payload["buckets"][0]), {"start", "partial"})

    def test_the_response_echoes_the_window_it_was_read_over(self):
        window = self.payload()["window"]

        self.assertEqual(window["key"], "24h")
        self.assertTrue(window["since"].endswith("Z"), window["since"])

    def test_the_standings_here_are_the_ones_the_headline_counts(self):
        """The table and the figures above it are one derivation or neither."""
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708")
        self.transmitted("0-20000-0-63999")

        standing = [row["standing"] for row in self.rows()]
        counts = self.summary()["now"]

        self.assertEqual(standing.count("transmitting"), counts["transmitting"])
        self.assertEqual(
            standing.count("undeclared"), counts["undeclared_transmitting"]
        )

    def test_the_endpoint_logs_what_it_cost(self):
        """90d over a whole population is the request to watch."""
        with self.assertLogs("wis2watch.api.views", level="DEBUG") as logged:
            self.payload(window="90d")

        self.assertTrue(
            any("90d" in line and "took=" in line for line in logged.output),
            logged.output,
        )


class StationRowsWindowTests(StationRowsEndpointTestCase):
    """The one input, spelled the same way as everywhere else on the tab."""

    def test_the_window_moves_the_axis_the_presence_vector_is_read_against(self):
        self.declare("0-20000-0-63708")

        payload = self.payload(window="7d")

        self.assertEqual(payload["window"]["grain"], "day")
        self.assertEqual(len(payload["buckets"]), 7)
        self.assertEqual(payload["stations"][0]["presence"], [0] * 7)

    def test_the_sparkline_does_not_move_with_the_window(self):
        """It is the fixed 24 hours, which is what makes the column comparable."""
        self.declare("0-20000-0-63708")

        self.assertEqual(len(self.rows(window="90d")[0]["sparkline"]), 24)

    def test_a_window_nobody_offers_is_refused_with_the_ones_that_exist(self):
        response = self.client.get(self.stations_url(), {"window": "6h"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["valid_windows"], ["24h", "7d", "30d", "90d"])

    def test_an_empty_window_parameter_is_the_default_rather_than_a_refusal(self):
        self.assertEqual(self.payload(window="")["window"]["key"], "24h")


class StationDrilldownEndpointTestCase(StatisticsEndpointTestCase):
    """One station of one centre, as the drilldown reaches it."""

    def drilldown_url(self, station, node=None):
        return reverse(
            "node_statistics_station",
            args=[(node or self.kenya).pk, getattr(station, "pk", station)],
        )

    def payload(self, station, **params):
        response = self.client.get(self.drilldown_url(station), params)

        self.assertEqual(response.status_code, 200)

        return response.json()

    def heard(self, wigos_id="0-20000-0-63708", *, hours_ago=1):
        """A station this centre both declares and has just been heard for."""
        station = self.declare(wigos_id)
        self.transmitted(wigos_id, hours_ago=hours_ago)

        return station


class StationDrilldownAccessTests(StationDrilldownEndpointTestCase):
    """The same door as everything else on this tab."""

    def test_a_reader_who_is_not_signed_in_is_refused(self):
        station = self.heard()
        self.client.logout()

        self.assertEqual(
            self.client.get(self.drilldown_url(station)).status_code, 403
        )

    def test_a_signed_in_account_with_no_admin_access_is_refused(self):
        station = self.heard()
        self.client.force_login(
            get_user_model().objects.create_user("outsider", password="s3cret")
        )

        self.assertEqual(
            self.client.get(self.drilldown_url(station)).status_code, 403
        )


class StationDrilldownScopeTests(StationDrilldownEndpointTestCase):
    """Which stations this centre's drilldown will open at all."""

    def test_a_station_this_centre_has_heard_from_opens(self):
        station = self.heard()

        self.assertEqual(
            self.payload(station)["station"]["wigos_id"], "0-20000-0-63708"
        )

    def test_a_station_of_another_centre_is_not_found_here(self):
        """An empty page here would read as 'declared, and never heard from'."""
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        theirs = declare_station(djibouti, "0-20000-0-63125")

        self.assertEqual(
            self.client.get(self.drilldown_url(theirs)).status_code, 404
        )

    def test_an_id_naming_no_station_is_not_found(self):
        self.assertEqual(self.client.get(self.drilldown_url(9999)).status_code, 404)

    def test_a_centre_that_does_not_exist_is_not_found(self):
        station = self.heard()
        missing = reverse("node_statistics_station", args=[9999, station.pk])

        self.assertEqual(self.client.get(missing).status_code, 404)


class StationDrilldownResponseTests(StationDrilldownEndpointTestCase):
    """The shape the drilldown binds to."""

    def test_the_response_carries_exactly_what_the_contract_says_it_does(self):
        payload = self.payload(self.heard())

        self.assertEqual(
            set(payload),
            {
                "node_id",
                "centre_id",
                "generated_at",
                "stale_after_hours",
                "window",
                "buckets",
                "station",
                "now",
                "window_stats",
            },
        )

    def test_the_identity_and_the_standing_are_repeated_rather_than_assumed(self):
        """The link stands on its own or it is a link to a number."""
        station = self.heard()

        identity = self.payload(station)["station"]

        self.assertEqual(
            set(identity),
            {
                "station_id",
                "wigos_id",
                "name",
                "local_name",
                "standing",
                "last_heard",
                "hours_quiet",
                "latitude",
                "longitude",
            },
        )
        self.assertEqual(identity["station_id"], station.pk)
        self.assertEqual(identity["standing"], "transmitting")

    def test_the_fixed_block_is_kept_apart_from_the_moving_one(self):
        payload = self.payload(self.heard())

        self.assertEqual(set(payload["now"]), {"buckets", "hourly"})
        self.assertEqual(
            set(payload["window_stats"]),
            {"messages_total", "active_buckets", "daily", "datasets"},
        )

    def test_the_fixed_block_is_twenty_four_hours_at_every_window(self):
        station = self.heard()

        for key in ("24h", "90d"):
            with self.subTest(window=key):
                now = self.payload(station, window=key)["now"]

                self.assertEqual(len(now["buckets"]), 24)
                self.assertEqual(len(now["hourly"]), 24)
                self.assertEqual(
                    set(now["hourly"][0]), {"messages", "station_less"}
                )

    def test_the_window_moves_the_axis_the_heatmap_is_read_against(self):
        payload = self.payload(self.heard(), window="7d")

        self.assertEqual(payload["window"]["grain"], "day")
        self.assertEqual(len(payload["buckets"]), 7)
        self.assertEqual(len(payload["window_stats"]["daily"]), 7)
        self.assertEqual(
            set(payload["window_stats"]["daily"][0]),
            {"messages", "active_hours", "station_less"},
        )

    def test_there_is_no_daily_series_at_the_default_window(self):
        self.assertIsNone(self.payload(self.heard())["window_stats"]["daily"])

    def test_a_station_with_no_traffic_at_all_is_answered_with_zeros(self):
        """A real zero chart, rather than a panel with nothing in it."""
        stats = self.payload(self.declare("0-20000-0-63709"))["window_stats"]

        self.assertEqual(stats["messages_total"], 0)
        self.assertEqual(stats["active_buckets"], 0)
        self.assertEqual(stats["datasets"], [])

    def test_the_dataset_breakdown_crosses_the_wire_whole(self):
        station = self.heard()
        published(
            self.kenya,
            source=self.global_broker,
            hour=dj_timezone.now() - timedelta(hours=1),
            messages=3,
            station=station,
        )

        breakdown = self.payload(station)["window_stats"]["datasets"]

        self.assertEqual(
            set(breakdown[0]),
            {"id", "identifier", "title", "messages", "last_heard"},
        )
        self.assertEqual(breakdown[0]["messages"], 3)

    def test_a_window_nobody_offers_is_refused_with_the_ones_that_exist(self):
        response = self.client.get(
            self.drilldown_url(self.heard()), {"window": "6h"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["valid_windows"], ["24h", "7d", "30d", "90d"])

    def test_the_endpoint_logs_what_it_cost(self):
        with self.assertLogs("wis2watch.api.views", level="DEBUG") as logged:
            self.payload(self.heard(), window="90d")

        self.assertTrue(
            any("90d" in line and "took=" in line for line in logged.output),
            logged.output,
        )


class StationDrilldownMountPointTests(MountPointTests):
    """What the page hands the island before it can open one station."""

    def test_the_island_is_given_no_second_path_for_the_drilldown(self):
        """It adds an id to the stations URL rather than assembling a path."""
        response = self.client.get(reverse("node_statistics", args=[self.kenya.pk]))

        self.assertNotContains(response, "data-station-url")

    def test_the_drilldown_url_is_the_stations_url_plus_the_id(self):
        stations_url = reverse("node_statistics_stations", args=[self.kenya.pk])
        drilldown = reverse("node_statistics_station", args=[self.kenya.pk, 12])

        self.assertEqual(drilldown, f"{stations_url}12/")
