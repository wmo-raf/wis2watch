"""The statistics tab's findings, against a seeded database.

What is guarded here is the arithmetic a reader is going to trust without
being able to check it. Two station numbers are reported side by side and mean
different things; one of them deliberately leaves a population out of its own
denominator; and both have to agree, to the station, with the diagnostic page
that lists the same stations one at a time.

The windows are here too, because their bounds are the only thing on the tab a
client is forbidden to work out for itself. A laptop clock deciding where "the
last 24 hours" starts is how two readers screenshot the same node and get two
different charts.
"""

from datetime import timedelta

from django.test import SimpleTestCase, TestCase

from wis2watch.core.analysis import (
    Grain,
    StationStanding,
    UnknownWindow,
    Window,
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


class WindowTests(SimpleTestCase):
    """The four windows the API offers, and where each of them starts."""

    def test_the_default_window_is_the_last_day(self):
        self.assertEqual(Window.default().key, "24h")

    def test_every_window_is_reachable_by_its_key(self):
        for window in Window.available():
            with self.subTest(window=window.key):
                self.assertEqual(Window.resolve(window.key), window)

    def test_asking_for_nothing_is_asking_for_the_default(self):
        self.assertEqual(Window.resolve(None), Window.default())

    def test_a_window_nobody_offers_is_refused_with_the_ones_that_exist(self):
        """The refusal has to name the alternatives or it is a dead end."""
        with self.assertRaises(UnknownWindow) as refused:
            Window.resolve("6h")

        self.assertEqual(refused.exception.valid_keys, ["24h", "7d", "30d", "90d"])

    def test_a_free_range_is_not_a_window(self):
        """The unbounded question is refused by there being no way to ask it."""
        with self.assertRaises(UnknownWindow):
            Window.resolve("2024-01-01")

    def test_the_shortest_window_is_hourly_and_the_rest_are_daily(self):
        self.assertEqual(Window.resolve("24h").grain, Grain.HOUR)

        for key in ("7d", "30d", "90d"):
            with self.subTest(window=key):
                self.assertEqual(Window.resolve(key).grain, Grain.DAY)

    def test_the_last_day_ends_at_the_last_whole_hour(self):
        """Synoptic peaks land on round hours, and the hour in progress is a lie."""
        since, until = Window.resolve("24h").bounds(NOW)

        self.assertEqual(until, at("2026-08-11T12:00:00"))
        self.assertEqual(since, at("2026-08-10T12:00:00"))

    def test_a_daily_window_ends_with_the_day_in_progress(self):
        """A series whose newest bucket is yesterday hides today's outage."""
        since, until = Window.resolve("7d").bounds(NOW)

        self.assertEqual(until, at("2026-08-12T00:00:00"))
        self.assertEqual(since, at("2026-08-05T00:00:00"))


class SummaryTestCase(TestCase):
    """A node whose stations are in every standing there is."""

    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

    def declare(self, wigos_id, *, node=None, located=True):
        return declare_station(
            node or self.kenya, wigos_id, location=SOMEWHERE if located else None
        )

    def transmitted(self, wigos_id, *, hours_ago=1, node=None, located=True):
        return observe_station(
            node or self.kenya,
            wigos_id,
            last_seen=NOW - timedelta(hours=hours_ago),
            location=SOMEWHERE if located else None,
        )

    def summary(self, node=None, **kwargs):
        kwargs.setdefault("now", NOW)

        return node_statistics_summary(node or self.kenya, **kwargs)


class StandingCountTests(SummaryTestCase):
    """The headline figures, which are a count of what the station list holds."""

    def test_a_station_heard_from_lately_is_counted_transmitting(self):
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708", hours_ago=3)

        self.assertEqual(self.summary().now.transmitting, 1)

    def test_a_station_heard_from_long_ago_is_counted_as_having_stopped(self):
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708", hours_ago=30 * 24)

        counts = self.summary().now

        self.assertEqual(counts.gone_quiet, 1)
        self.assertEqual(counts.transmitting, 0)

    def test_a_declared_station_nothing_has_heard_from_is_counted_apart(self):
        """Stopped and never started are different findings about a centre."""
        self.declare("0-20000-0-63708")

        counts = self.summary().now

        self.assertEqual(counts.never_transmitted, 1)
        self.assertEqual(counts.gone_quiet, 0)

    def test_a_transmitting_station_nothing_declares_is_counted_apart(self):
        self.transmitted("0-20000-0-63999")

        self.assertEqual(self.summary().now.undeclared_transmitting, 1)

    def test_an_undeclared_station_stays_out_of_the_declared_count(self):
        """The denominator is what the registry claims, not what turned up."""
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708")
        self.transmitted("0-20000-0-63999")

        counts = self.summary().now

        self.assertEqual(counts.declared_station_count, 1)
        self.assertEqual(counts.undeclared_transmitting, 1)

    def test_a_station_declared_and_transmitting_is_counted_once(self):
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708")

        counts = self.summary().now

        self.assertEqual(counts.declared_station_count, 1)
        self.assertEqual(counts.transmitting, 1)

    def test_another_centres_stations_are_not_counted_here(self):
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        self.declare("0-20000-0-63708")
        self.declare("0-20000-0-60001", node=djibouti)

        self.assertEqual(self.summary().now.declared_station_count, 1)

    def test_another_centres_transmission_is_not_read_as_this_centres(self):
        """A station heard under another centre's topics has not been heard here."""
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708", node=djibouti, hours_ago=1)

        counts = self.summary().now

        self.assertEqual(counts.transmitting, 0)
        self.assertEqual(counts.never_transmitted, 1)

    def test_the_counts_agree_with_the_station_list_they_summarise(self):
        """The one failure that would make a reader disbelieve both pages."""
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708", hours_ago=2)
        self.declare("0-20000-0-63709")
        self.transmitted("0-20000-0-63709", hours_ago=200)
        self.declare("0-20000-0-63710")
        self.transmitted("0-20000-0-63999")

        counts = self.summary().now
        listed = node_detail_standings(self.kenya)

        self.assertEqual(counts.transmitting, listed[StationStanding.TRANSMITTING])
        self.assertEqual(counts.gone_quiet, listed[StationStanding.GONE_QUIET])
        self.assertEqual(
            counts.never_transmitted, listed[StationStanding.NEVER_TRANSMITTED]
        )
        self.assertEqual(
            counts.undeclared_transmitting, listed[StationStanding.UNDECLARED]
        )

    def test_a_station_with_no_coordinates_is_counted_as_unplottable(self):
        """A map of these stations is a subset, and has to be able to say so."""
        self.declare("0-20000-0-63708", located=True)
        self.declare("0-20000-0-63709", located=False)

        self.assertEqual(self.summary().now.unlocated_station_count, 1)


class SummaryShapeTests(SummaryTestCase):
    """What every response carries whatever the node has done."""

    def test_the_resolved_window_is_echoed_as_absolute_bounds(self):
        """The client labels its axes from server truth, never a laptop clock."""
        window = self.summary().window

        self.assertEqual(window.key, "24h")
        self.assertEqual(window.grain, Grain.HOUR)
        self.assertEqual(window.until, at("2026-08-11T12:00:00"))
        self.assertEqual(window.since, at("2026-08-10T12:00:00"))

    def test_the_windows_on_offer_are_published(self):
        """So the control renders what the server has, not four hard-coded buttons."""
        offered = [window.key for window in self.summary().windows]

        self.assertEqual(offered, ["24h", "7d", "30d", "90d"])

    def test_the_threshold_quiet_is_judged_by_is_stated(self):
        self.assertEqual(self.summary().stale_after_hours, 24)

    def test_the_vantage_the_volumes_come_from_is_named_and_answering(self):
        self.assertEqual(self.summary().vantage.source_type, "global_broker")
        self.assertTrue(self.summary().vantage.active)

    def test_a_region_with_no_global_broker_switched_on_says_so(self):
        """A wall of zeros is a configuration state, not a mystery."""
        MessageSource.objects.filter(pk=self.global_broker.pk).update(is_active=False)

        self.assertFalse(self.summary().vantage.active)

    def test_a_node_with_no_history_at_all_reports_zeros(self):
        """The empty tab is the finding; nulls would make the client invent it."""
        counts = self.summary().now

        self.assertEqual(counts.declared_station_count, 0)
        self.assertEqual(counts.transmitting, 0)
        self.assertEqual(counts.gone_quiet, 0)
        self.assertEqual(counts.never_transmitted, 0)
        self.assertEqual(counts.undeclared_transmitting, 0)
        self.assertEqual(counts.unlocated_station_count, 0)

    def test_the_summary_says_which_centre_it_is_about(self):
        summary = self.summary()

        self.assertEqual(summary.node_id, self.kenya.pk)
        self.assertEqual(summary.centre_id, "ke-meteo")

    def test_the_summary_says_when_it_was_computed(self):
        """No caching yet, so the age of a figure has to be readable off it."""
        self.assertEqual(self.summary().generated_at, NOW)


def node_detail_standings(node):
    """How the diagnostic page's own station list stands, counted by hand."""
    from wis2watch.core.analysis import node_detail

    counts = {standing: 0 for standing in StationStanding.RANK}

    for row in node_detail(node, now=NOW).stations:
        counts[row.standing] += 1

    return counts


class SeriesTestCase(SummaryTestCase):
    """A node whose hours can be seeded one rollup at a time."""

    def rollup(self, *, hours_ago, messages=1, station=None, source=None, node=None):
        """One hour of this centre's traffic, that many hours before NOW."""
        return published(
            node or self.kenya,
            source=source or self.global_broker,
            hour=NOW - timedelta(hours=hours_ago),
            messages=messages,
            station=station,
        )

    def hourly(self, **kwargs):
        return self.summary(**kwargs).now.hourly


class BucketAxisTests(SeriesTestCase):
    """Where the chart's columns fall, which no client is allowed to decide."""

    def test_the_axis_is_dense_over_the_whole_window(self):
        buckets = self.summary().buckets

        self.assertEqual(len(buckets), 24)

    def test_the_axis_starts_at_the_window_start_and_steps_by_the_hour(self):
        buckets = self.summary().buckets

        self.assertEqual(buckets[0].start, at("2026-08-10T12:00:00"))
        self.assertEqual(buckets[1].start, at("2026-08-10T13:00:00"))
        self.assertEqual(buckets[-1].start, at("2026-08-11T11:00:00"))

    def test_no_hourly_bucket_is_incomplete(self):
        """The hour in progress is left out rather than drawn as a collapse."""
        self.assertFalse(any(bucket.partial for bucket in self.summary().buckets))

    def test_the_fixed_block_carries_its_own_axis_of_whole_hours(self):
        """The window will move; the standing block's 24 hours will not."""
        buckets = self.summary().now.buckets

        self.assertEqual(len(buckets), 24)
        self.assertEqual(buckets[0].start, at("2026-08-10T12:00:00"))
        self.assertEqual(buckets[-1].start, at("2026-08-11T11:00:00"))

    def test_a_longer_window_moves_its_own_axis_and_not_the_fixed_one(self):
        """Why there are two axes at all, pinned before a control can move one.

        At the default window the two lists are identical, so nothing here
        would notice them being one list. Asking for a longer one is what
        makes the difference real: the window's buckets become days, and the
        fixed block goes on being the same 24 whole hours it was.
        """
        summary = self.summary(window=Window.resolve("7d"))

        self.assertEqual(len(summary.buckets), 7)
        self.assertEqual(summary.buckets[0].start, at("2026-08-05T00:00:00"))

        self.assertEqual(len(summary.now.buckets), 24)
        self.assertEqual(summary.now.buckets[0].start, at("2026-08-10T12:00:00"))
        self.assertEqual(len(summary.now.hourly), 24)

    def test_the_day_in_progress_is_the_one_bucket_marked_unfinished(self):
        """`partial` is "real but incomplete", and only the newest day is."""
        buckets = self.summary(window=Window.resolve("7d")).buckets

        self.assertTrue(buckets[-1].partial)
        self.assertFalse(any(bucket.partial for bucket in buckets[:-1]))


class HourlySeriesTests(SeriesTestCase):
    """The last 24 whole hours, as the bar per hour the tab opens on."""

    def test_every_hour_is_present_even_where_nothing_was_published(self):
        """A silent hour is the finding; a client filling gaps can get it wrong."""
        self.assertEqual(len(self.hourly()), 24)

    def test_the_series_is_positional_against_the_axis(self):
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=1, station=station, messages=5)

        series = self.hourly()

        self.assertEqual(series[-1].messages, 5)
        self.assertEqual(series[-2].messages, 0)

    def test_an_hour_counts_the_distinct_stations_heard_in_it(self):
        first = self.declare("0-20000-0-63708")
        second = self.declare("0-20000-0-63709")
        self.rollup(hours_ago=1, station=first, messages=3)
        self.rollup(hours_ago=1, station=second, messages=4)

        self.assertEqual(self.hourly()[-1].stations, 2)

    def test_a_station_heard_at_two_vantage_points_is_one_station(self):
        """DISTINCT absorbs what the Global Broker filter exists to prevent."""
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=1, station=station, messages=3)
        self.rollup(
            hours_ago=1, station=station, messages=3, source=origin_broker(self.kenya)
        )

        self.assertEqual(self.hourly()[-1].stations, 1)

    def test_a_station_heard_only_at_its_own_broker_still_counts(self):
        """Distinct-station counts are vantage-free, by #42's decision."""
        station = self.declare("0-20000-0-63708")
        self.rollup(
            hours_ago=1, station=station, messages=3, source=origin_broker(self.kenya)
        )

        self.assertEqual(self.hourly()[-1].stations, 1)

    def test_message_volume_is_counted_from_the_global_broker_only(self):
        """Summing the vantage points would report one message as many."""
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=1, station=station, messages=3)
        self.rollup(
            hours_ago=1, station=station, messages=3, source=origin_broker(self.kenya)
        )

        self.assertEqual(self.hourly()[-1].messages, 3)

    def test_messages_naming_no_station_are_counted_apart_and_included(self):
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=1, station=station, messages=3)
        self.rollup(hours_ago=1, station=None, messages=2)

        hour = self.hourly()[-1]

        self.assertEqual(hour.messages, 5)
        self.assertEqual(hour.unattributed_messages, 2)
        self.assertEqual(hour.stations, 1)

    def test_a_busy_hour_that_named_nobody_is_not_a_silent_hour(self):
        """The case that forced the unit: traffic with no station to plot."""
        self.rollup(hours_ago=1, station=None, messages=7)

        hour = self.hourly()[-1]

        self.assertEqual(hour.stations, 0)
        self.assertEqual(hour.unattributed_messages, 7)

    def test_an_hour_older_than_the_window_is_left_out(self):
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=25, station=station, messages=9)

        self.assertEqual(sum(hour.messages for hour in self.hourly()), 0)

    def test_the_hour_in_progress_is_left_out(self):
        """It is a part-counted hour, and drawing it is a collapse every hour."""
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=0, station=station, messages=9)

        self.assertEqual(sum(hour.messages for hour in self.hourly()), 0)

    def test_another_centres_traffic_is_not_counted_here(self):
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=1, station=station, messages=9, node=djibouti)

        self.assertEqual(sum(hour.messages for hour in self.hourly()), 0)

    def test_a_node_that_published_nothing_gets_a_real_zero_series(self):
        """An empty chart against a real axis, not an absent one."""
        series = self.hourly()

        self.assertEqual(len(series), 24)
        self.assertEqual(sum(hour.messages for hour in series), 0)
        self.assertEqual(sum(hour.stations for hour in series), 0)
        self.assertEqual(sum(hour.unattributed_messages for hour in series), 0)


class WindowSeriesTestCase(SeriesTestCase):
    """A node whose days can be seeded an hour at a time.

    The daily rows are derived by the summariser rather than written by hand,
    for the reason the summariser exists: a test that inserts its own
    ``DailyStationRollup`` is a test that agrees with itself about how a day
    is counted, and the mistake worth catching is the two layers disagreeing.
    """

    def summarise(self):
        """Roll the seeded hours up into days, as the scheduled run does."""
        backfill_daily_rollups(now=NOW)

    def week(self, **kwargs):
        return self.summary(window=Window.resolve("7d"), **kwargs)


class WindowCoverageTests(WindowSeriesTestCase):
    """The moving figure, and the gap between it and the standing one."""

    def test_a_station_heard_once_in_the_window_is_covered_by_it(self):
        """The finding the tab exists for: reported, then stopped."""
        stopped = self.declare("0-20000-0-63708")
        self.declare("0-20000-0-63709")
        self.rollup(hours_ago=5 * 24, station=stopped, messages=3)
        self.summarise()

        stats = self.week().window_stats

        self.assertEqual(stats.reported_station_count, 1)
        self.assertEqual(stats.declared_station_count, 2)

    def test_the_window_coverage_can_exceed_what_is_transmitting_now(self):
        """The 66 stations that reported this month and have since stopped."""
        stopped = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=5 * 24, station=stopped, messages=3)
        self.summarise()

        summary = self.week()

        self.assertEqual(summary.window_stats.reported_station_count, 1)
        self.assertEqual(summary.now.transmitting, 0)

    def test_a_station_heard_twice_in_the_window_is_one_station(self):
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=5 * 24, station=station, messages=3)
        self.rollup(hours_ago=2 * 24, station=station, messages=3)
        self.summarise()

        self.assertEqual(self.week().window_stats.reported_station_count, 1)

    def test_a_station_heard_only_before_the_window_is_not_covered_by_it(self):
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=20 * 24, station=station, messages=3)
        self.summarise()

        self.assertEqual(self.week().window_stats.reported_station_count, 0)

    def test_the_default_window_counts_coverage_from_the_hourly_rollups(self):
        """No daily rows exist at all, and the 24h figure still answers."""
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=3, station=station, messages=3)

        self.assertEqual(self.summary().window_stats.reported_station_count, 1)

    def test_message_volume_over_the_window_is_global_broker_only(self):
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=2 * 24, station=station, messages=4)
        self.rollup(
            hours_ago=2 * 24,
            station=station,
            messages=4,
            source=origin_broker(self.kenya),
        )
        self.rollup(hours_ago=2 * 24, station=None, messages=2)
        self.summarise()

        stats = self.week().window_stats

        self.assertEqual(stats.messages_total, 6)
        self.assertEqual(stats.unattributed_messages_total, 2)

    def test_a_node_with_no_history_reports_zero_coverage(self):
        stats = self.week().window_stats

        self.assertEqual(stats.reported_station_count, 0)
        self.assertEqual(stats.messages_total, 0)
        self.assertEqual(stats.unattributed_messages_total, 0)


class DailySeriesTests(WindowSeriesTestCase):
    """The dense daily series, which carries the node-wide-outage signal."""

    def daily(self, **kwargs):
        return self.week(**kwargs).window_stats.daily

    def test_the_default_window_has_no_daily_series_at_all(self):
        """A one-cell chart is not a series; the panel says so instead."""
        self.assertIsNone(self.summary().window_stats.daily)

    def test_the_series_is_dense_over_the_whole_window(self):
        self.assertEqual(len(self.daily()), 7)

    def test_the_series_is_positional_against_the_window_axis(self):
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=2 * 24, station=station, messages=5)
        self.summarise()

        series = self.daily()

        self.assertEqual(series[-3].messages, 5)
        self.assertEqual(series[-2].messages, 0)
        self.assertEqual(len(series), len(self.week().buckets))

    def test_a_day_counts_the_distinct_stations_heard_in_it(self):
        first = self.declare("0-20000-0-63708")
        second = self.declare("0-20000-0-63709")
        self.rollup(hours_ago=2 * 24, station=first, messages=3)
        self.rollup(hours_ago=2 * 24 + 3, station=second, messages=3)
        self.summarise()

        self.assertEqual(self.daily()[-3].stations, 2)

    def test_a_station_heard_at_two_vantage_points_is_one_station(self):
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=2 * 24, station=station, messages=3)
        self.rollup(
            hours_ago=2 * 24, station=station, messages=3, source=origin_broker(self.kenya)
        )
        self.summarise()

        self.assertEqual(self.daily()[-3].stations, 1)

    def test_messages_naming_no_station_are_counted_apart_and_included(self):
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=2 * 24, station=station, messages=3)
        self.rollup(hours_ago=2 * 24, station=None, messages=2)
        self.summarise()

        day = self.daily()[-3]

        self.assertEqual(day.messages, 5)
        self.assertEqual(day.unattributed_messages, 2)
        self.assertEqual(day.stations, 1)

    def test_the_ratio_is_computed_on_the_server(self):
        first = self.declare("0-20000-0-63708")
        second = self.declare("0-20000-0-63709")
        self.rollup(hours_ago=2 * 24, station=first, messages=7)
        self.rollup(hours_ago=2 * 24, station=second, messages=3)
        self.summarise()

        self.assertEqual(self.daily()[-3].messages_per_active_station, 5)

    def test_a_day_with_no_stations_has_no_ratio_rather_than_a_zero(self):
        """Dividing by nothing is not zero messages per station."""
        self.rollup(hours_ago=2 * 24, station=None, messages=9)
        self.summarise()

        day = self.daily()[-3]

        self.assertEqual(day.stations, 0)
        self.assertIsNone(day.messages_per_active_station)

    def test_another_centres_traffic_is_not_counted_here(self):
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=2 * 24, station=station, messages=9, node=djibouti)
        self.summarise()

        self.assertEqual(sum(day.messages for day in self.daily()), 0)

    def test_a_node_that_published_nothing_gets_a_real_zero_series(self):
        series = self.daily()

        self.assertEqual(len(series), 7)
        self.assertEqual(sum(day.messages for day in series), 0)
        self.assertEqual(sum(day.stations for day in series), 0)

    def test_the_newest_bucket_is_the_day_in_progress(self):
        """A series whose newest bucket is yesterday hides today's outage."""
        buckets = self.week().buckets

        self.assertEqual(buckets[-1].start, at("2026-08-11T00:00:00"))
        self.assertTrue(buckets[-1].partial)

    def test_the_day_in_progress_bites_the_station_count(self):
        """The mark has nothing to mark unless today's bar is really short.

        At 12:34 UTC the stations that report in the afternoon have not
        reported yet, so today is short in *stations* and not only in message
        hours -- which is the thing the daily series plots.
        """
        morning = self.declare("0-20000-0-63708")
        afternoon = self.declare("0-20000-0-63709")
        self.rollup(hours_ago=24 + 4, station=morning, messages=3)
        self.rollup(hours_ago=24, station=afternoon, messages=3)
        self.rollup(hours_ago=4, station=morning, messages=3)
        self.summarise()

        series = self.daily()

        self.assertEqual(series[-2].stations, 2)
        self.assertEqual(series[-1].stations, 1)

    def test_the_series_reads_the_daily_rollups_and_not_the_hours(self):
        """7d and longer are served by the table built for station questions."""
        station = self.declare("0-20000-0-63708")
        self.rollup(hours_ago=2 * 24, station=station, messages=5)

        self.assertEqual(sum(day.messages for day in self.daily()), 0)

        self.summarise()

        self.assertEqual(sum(day.messages for day in self.daily()), 5)
