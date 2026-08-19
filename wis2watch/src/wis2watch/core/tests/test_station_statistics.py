"""One centre's stations as rows, and what each of them has been heard doing.

What is guarded here is the join. The standing on a row and the standing
counted into the headline above it come from one derivation, and the test that
matters most is the one that says so -- a table showing 409 transmitting under
a headline saying 412 is the moment a reader stops believing either.

The rest is about vectors that are read positionally. A sparkline that is 23
long, or a presence vector indexed by an axis nothing else agrees with, is a
row drawn against the wrong hours -- and nothing on the page says so. Every
test below that counts a length is guarding that, not being pedantic.
"""

from datetime import timedelta

from django.test import TestCase

from wis2watch.core.analysis import (
    StationStanding,
    Window,
    node_station_statistics,
    node_statistics_summary,
)
from wis2watch.core.daily_rollups import backfill_daily_rollups
from wis2watch.core.models import MessageSource, WIS2Node

from .support import (
    SOMEWHERE,
    at,
    declare_station,
    observe_station,
    origin_broker,
    published,
)

NOW = at("2026-08-11T12:34:56")


class StationRowsTestCase(TestCase):
    """A centre whose stations can be put in any standing, and heard from."""

    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

    def declare(self, wigos_id, *, node=None, located=True, **kwargs):
        return declare_station(
            node or self.kenya,
            wigos_id,
            location=SOMEWHERE if located else None,
            **kwargs,
        )

    def transmitted(self, wigos_id, *, hours_ago=1, node=None, located=True):
        return observe_station(
            node or self.kenya,
            wigos_id,
            last_seen=NOW - timedelta(hours=hours_ago),
            location=SOMEWHERE if located else None,
        )

    def heard(self, wigos_id, *, hours_ago=1, node=None):
        """A station this centre both declares and has just been heard for."""
        station = self.declare(wigos_id, node=node)
        self.transmitted(wigos_id, hours_ago=hours_ago, node=node)

        return station

    def rollup(self, station, *, hours_ago=1, messages=1, source=None, node=None):
        return published(
            node or self.kenya,
            source=source or self.global_broker,
            hour=NOW - timedelta(hours=hours_ago),
            messages=messages,
            station=station,
        )

    def summarise(self):
        """Roll the seeded hours up into days, as the scheduled run does.

        Never written by hand, for the reason the summariser exists: a test
        that inserts its own daily row agrees with itself about how a day is
        counted, and the mistake worth catching is the two layers disagreeing.
        """
        backfill_daily_rollups(now=NOW)

    def rows(self, window=None, node=None):
        return node_station_statistics(
            node or self.kenya, window=window, now=NOW
        ).stations

    def week(self, **kwargs):
        return self.rows(window=Window.resolve("7d"), **kwargs)

    def only(self, **kwargs):
        rows = self.rows(**kwargs)

        self.assertEqual(len(rows), 1, rows)

        return rows[0]


class PopulationTests(StationRowsTestCase):
    """Which stations get a row at all, and in what order."""

    def test_a_declared_station_gets_a_row_even_though_nothing_heard_it(self):
        """The row that says a centre promised something and never sent it."""
        self.declare("0-20000-0-63708")

        self.assertEqual(self.only().standing, StationStanding.NEVER_TRANSMITTED)

    def test_a_transmitting_station_nothing_declares_gets_a_row(self):
        """Dropping it is exactly how a registration gap becomes invisible."""
        self.transmitted("0-20000-0-63999")

        self.assertEqual(self.only().standing, StationStanding.UNDECLARED)

    def test_a_station_declared_and_heard_from_gets_one_row(self):
        self.heard("0-20000-0-63708")

        self.assertEqual(self.only().wigos_id, "0-20000-0-63708")

    def test_a_centre_with_no_stations_has_no_rows(self):
        """A real 200 with an empty list: the empty table is the finding."""
        self.assertEqual(self.rows(), [])

    def test_another_centres_stations_are_not_this_centres_rows(self):
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        self.declare("0-20000-0-63708")
        self.declare("0-20000-0-60001", node=djibouti)

        self.assertEqual([row.wigos_id for row in self.rows()], ["0-20000-0-63708"])

    def test_another_centres_observation_is_not_read_as_this_centres(self):
        """A station may transmit under more than one centre's topics."""
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708", node=djibouti)

        row = self.only()

        self.assertEqual(row.standing, StationStanding.NEVER_TRANSMITTED)
        self.assertIsNone(row.last_heard)

    def test_the_rows_are_ordered_by_what_is_broken_first(self):
        """The default sort is a filter that hides nothing."""
        self.heard("0-20000-0-63704")
        self.heard("0-20000-0-63705", hours_ago=30 * 24)
        self.transmitted("0-20000-0-63706")
        self.declare("0-20000-0-63707")

        self.assertEqual(
            [row.standing for row in self.rows()],
            [
                StationStanding.NEVER_TRANSMITTED,
                StationStanding.GONE_QUIET,
                StationStanding.UNDECLARED,
                StationStanding.TRANSMITTING,
            ],
        )

    def test_the_longest_quiet_comes_first_among_stations_that_stopped(self):
        self.heard("0-20000-0-63704", hours_ago=30 * 24)
        self.heard("0-20000-0-63705", hours_ago=90 * 24)

        self.assertEqual(
            [row.wigos_id for row in self.rows()],
            ["0-20000-0-63705", "0-20000-0-63704"],
        )

    def test_the_standings_on_the_rows_are_the_headline_counts(self):
        """One derivation, or the table and the figures above it drift apart."""
        self.heard("0-20000-0-63704")
        self.heard("0-20000-0-63705", hours_ago=30 * 24)
        self.transmitted("0-20000-0-63706")
        self.declare("0-20000-0-63707")

        counts = node_statistics_summary(self.kenya, now=NOW).now
        standing = [row.standing for row in self.rows()]

        self.assertEqual(
            standing.count(StationStanding.TRANSMITTING), counts.transmitting
        )
        self.assertEqual(standing.count(StationStanding.GONE_QUIET), counts.gone_quiet)
        self.assertEqual(
            standing.count(StationStanding.NEVER_TRANSMITTED), counts.never_transmitted
        )
        self.assertEqual(
            standing.count(StationStanding.UNDECLARED), counts.undeclared_transmitting
        )


class IdentityTests(StationRowsTestCase):
    """What a row says about the station rather than about its traffic."""

    def test_a_row_carries_both_names_the_station_is_known_by(self):
        """The operator's own name is the one their staff will recognise."""
        self.declare("0-20000-0-63708", local_name="Dagoretti Corner")

        row = self.only()

        self.assertEqual(row.wigos_id, "0-20000-0-63708")
        self.assertEqual(row.local_name, "Dagoretti Corner")

    def test_a_row_carries_where_the_station_is(self):
        """The map is a projection of these rows, not a second endpoint."""
        row = (self.declare("0-20000-0-63708"), self.only())[1]

        self.assertEqual(row.latitude, SOMEWHERE.y)
        self.assertEqual(row.longitude, SOMEWHERE.x)

    def test_a_station_nothing_can_place_carries_no_coordinates(self):
        """Null rather than zero: the Gulf of Guinea is a real place."""
        self.declare("0-20000-0-63708", located=False)

        row = self.only()

        self.assertIsNone(row.latitude)
        self.assertIsNone(row.longitude)

    def test_a_row_says_when_this_centre_last_heard_the_station(self):
        self.heard("0-20000-0-63708", hours_ago=3)

        row = self.only()

        self.assertEqual(row.last_heard, NOW - timedelta(hours=3))
        self.assertAlmostEqual(row.hours_quiet, 3.0, places=3)

    def test_a_row_leaves_out_what_the_table_does_not_show(self):
        """CSV-export detail, and a declaration flag the standing already carries."""
        self.declare("0-20000-0-63708")

        row = self.only()

        absent_by_design = (
            "facility_type",
            "local_id",
            "declared_by_registry",
            "elevation",
        )

        for absent in absent_by_design:
            with self.subTest(field=absent):
                self.assertFalse(hasattr(row, absent))


class SparklineTests(StationRowsTestCase):
    """The 24-hour shape beside each row, which does not move with the window."""

    def test_the_sparkline_is_twenty_four_whole_hours_long(self):
        self.heard("0-20000-0-63708")

        self.assertEqual(len(self.only().sparkline), 24)

    def test_an_hour_of_traffic_lands_in_its_own_bucket(self):
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)

        self.assertEqual(self.only().sparkline[-1], 4)
        self.assertEqual(sum(self.only().sparkline), 4)

    def test_a_station_nothing_was_heard_from_draws_on_the_baseline(self):
        """All zeros rather than an absent vector: a dead cohort is the finding."""
        self.declare("0-20000-0-63708")

        self.assertEqual(self.only().sparkline, [0] * 24)

    def test_the_sparkline_does_not_move_with_the_window(self):
        """It is the fixed 24 hours whatever span the reader chose."""
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)
        self.summarise()

        self.assertEqual(len(self.week()[0].sparkline), 24)
        self.assertEqual(self.week()[0].sparkline, self.only().sparkline)

    def test_the_sparkline_counts_the_worlds_view_of_the_centre(self):
        """One publication observed at two vantage points is one publication."""
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)
        self.rollup(station, hours_ago=1, messages=4, source=origin_broker(self.kenya))

        self.assertEqual(sum(self.only().sparkline), 4)

    def test_one_stations_traffic_is_not_drawn_on_another_stations_row(self):
        loud = self.heard("0-20000-0-63704")
        self.heard("0-20000-0-63705")
        self.rollup(loud, hours_ago=1, messages=9)

        drawn = {row.wigos_id: sum(row.sparkline) for row in self.rows()}

        self.assertEqual(drawn, {"0-20000-0-63704": 9, "0-20000-0-63705": 0})

    def test_traffic_older_than_the_day_is_not_in_the_sparkline(self):
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=30, messages=9)

        self.assertEqual(sum(self.only().sparkline), 0)


class WindowVolumeTests(StationRowsTestCase):
    """The magnitude column beside the sparkline, which does move."""

    def test_messages_in_window_counts_the_hours_of_the_default_window(self):
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)
        self.rollup(station, hours_ago=2, messages=6)

        self.assertEqual(self.only().messages_in_window, 10)

    def test_messages_in_window_counts_the_worlds_view_of_the_centre(self):
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)
        self.rollup(station, hours_ago=1, messages=4, source=origin_broker(self.kenya))

        self.assertEqual(self.only().messages_in_window, 4)

    def test_messages_in_window_over_a_week_reads_the_days(self):
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)
        self.rollup(station, hours_ago=3 * 24, messages=6)
        self.summarise()

        self.assertEqual(self.week()[0].messages_in_window, 10)

    def test_traffic_outside_the_window_is_not_counted_into_it(self):
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=30, messages=9)
        self.summarise()

        self.assertEqual(self.only().messages_in_window, 0)
        self.assertEqual(self.week()[0].messages_in_window, 9)

    def test_a_station_nothing_was_heard_from_carries_a_real_zero(self):
        self.declare("0-20000-0-63708")

        self.assertEqual(self.only().messages_in_window, 0)


class ActiveBucketTests(StationRowsTestCase):
    """How much of the window a station was heard in, from every vantage point."""

    def test_the_hours_a_station_reported_in_are_counted_at_the_short_window(self):
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)
        self.rollup(station, hours_ago=2, messages=6)

        self.assertEqual(self.only().active_buckets, 2)

    def test_the_days_a_station_reported_in_are_counted_over_a_week(self):
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)
        self.rollup(station, hours_ago=2, messages=6)
        self.rollup(station, hours_ago=3 * 24, messages=6)
        self.summarise()

        self.assertEqual(self.week()[0].active_buckets, 2)

    def test_a_station_heard_only_at_its_own_broker_still_reported(self):
        """Vantage-free: a station is one station however many heard it."""
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4, source=origin_broker(self.kenya))

        row = self.only()

        self.assertEqual(row.active_buckets, 1)
        self.assertEqual(row.messages_in_window, 0)

    def test_one_hour_heard_at_two_vantage_points_is_one_hour(self):
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)
        self.rollup(station, hours_ago=1, messages=4, source=origin_broker(self.kenya))

        self.assertEqual(self.only().active_buckets, 1)


class PresenceTests(StationRowsTestCase):
    """The vector the availability matrix will be drawn from, shipped early."""

    def buckets(self, window=None):
        return node_station_statistics(self.kenya, window=window, now=NOW).buckets

    def test_the_presence_vector_is_indexed_by_the_windows_own_axis(self):
        """A row against an axis nothing else agrees with is a row drawn wrong."""
        self.heard("0-20000-0-63708")

        self.assertEqual(len(self.only().presence), len(self.buckets()))

    def test_the_presence_vector_spans_a_longer_window_too(self):
        self.heard("0-20000-0-63708")
        self.summarise()

        week = Window.resolve("7d")

        self.assertEqual(len(self.week()[0].presence), len(self.buckets(week)))
        self.assertEqual(len(self.week()[0].presence), 7)

    def test_presence_carries_messages_at_hourly_grain(self):
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)

        self.assertEqual(self.only().presence[-1], 4)

    def test_presence_carries_the_hours_of_the_day_at_daily_grain(self):
        """How much of the day, not how loudly -- a cell is availability."""
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=40)
        self.rollup(station, hours_ago=2, messages=6)
        self.summarise()

        self.assertEqual(self.week()[0].presence[-1], 2)

    def test_a_day_heard_at_two_vantage_points_is_not_counted_twice(self):
        """Summing the vantage points would report 48 hours in a day."""
        station = self.heard("0-20000-0-63708")
        self.rollup(station, hours_ago=1, messages=4)
        self.rollup(station, hours_ago=1, messages=4, source=origin_broker(self.kenya))
        self.summarise()

        self.assertEqual(self.week()[0].presence[-1], 1)

    def test_a_station_nothing_was_heard_from_carries_a_dense_zero_vector(self):
        self.declare("0-20000-0-63708")
        self.summarise()

        self.assertEqual(self.only().presence, [0] * 24)
        self.assertEqual(self.week()[0].presence, [0] * 7)


class RowFrameTests(StationRowsTestCase):
    """What the payload carries around the rows themselves."""

    def test_the_response_echoes_the_window_it_was_read_over(self):
        """The client labels its axes from server truth, never a laptop clock."""
        window = node_station_statistics(self.kenya, now=NOW).window

        self.assertEqual(window.key, "24h")
        self.assertEqual(window.grain, "hour")
        self.assertEqual(window.until, at("2026-08-11T12:00:00"))

    def test_the_bucket_axis_travels_once_rather_than_on_every_row(self):
        rows = node_station_statistics(self.kenya, window=Window.resolve("7d"), now=NOW)

        self.assertEqual(len(rows.buckets), 7)
        self.assertTrue(rows.buckets[-1].partial)

    def test_the_response_says_which_centre_and_when_it_was_computed(self):
        rows = node_station_statistics(self.kenya, now=NOW)

        self.assertEqual(rows.centre_id, "ke-meteo")
        self.assertEqual(rows.node_id, self.kenya.pk)
        self.assertEqual(rows.generated_at, NOW)
        self.assertEqual(rows.stale_after_hours, 24)
