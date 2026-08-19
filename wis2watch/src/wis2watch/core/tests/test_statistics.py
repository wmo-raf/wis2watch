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

from django.contrib.gis.geos import Point
from django.test import SimpleTestCase, TestCase

from wis2watch.core.analysis import (
    Grain,
    StationStanding,
    UnknownWindow,
    Window,
    node_statistics_summary,
)
from wis2watch.core.models import (
    MessageSource,
    Station,
    StationSource,
    WIS2Node,
)

from .support import at

NOW = at("2026-08-11T12:34:56")

#: Somewhere to put a station that has one. Where it is does not matter; that
#: it has coordinates at all is the only thing counted here.
NAIROBI = Point(36.75, -1.30, 1798.0, srid=4326)


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

    def test_a_window_covers_exactly_as_many_buckets_as_it_is_named_for(self):
        for key, buckets in (("24h", 24), ("7d", 7), ("30d", 30), ("90d", 90)):
            with self.subTest(window=key):
                self.assertEqual(Window.resolve(key).bucket_count, buckets)


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
        """The node's own registry saying it operates a station."""
        station, _ = Station.objects.get_or_create(
            wigos_id=wigos_id,
            defaults={"location": NAIROBI if located else None},
        )

        StationSource.objects.create(
            station=station,
            source_type=StationSource.NODE_REGISTRY,
            node=self.kenya if node is None else node,
        )

        return station

    def transmitted(self, wigos_id, *, hours_ago=1, node=None, located=True):
        """A station heard transmitting under a centre's topics."""
        station, _ = Station.objects.get_or_create(
            wigos_id=wigos_id,
            defaults={"location": NAIROBI if located else None},
        )

        StationSource.objects.create(
            station=station,
            source_type=StationSource.OBSERVED,
            node=self.kenya if node is None else node,
            last_seen=NOW - timedelta(hours=hours_ago),
        )

        return station

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
