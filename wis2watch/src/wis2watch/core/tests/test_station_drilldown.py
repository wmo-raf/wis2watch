"""One station of one centre, opened.

The last step of the journey the statistics tab exists for: the reader has
found *which* station stopped, and now opens it. What is guarded here is
mostly node-scoping. A station transmits under whichever centres' topics it
transmits under, and every figure on this page has to be *this* centre's own
observation -- a drilldown that read the station's latest anywhere would
report a centre as publishing something it never sent, and the page has no way
to say so.

The other half is the 404. A station that exists in the database but is
neither declared nor observed under this node is not an empty drilldown, it is
a station that does not belong here; an empty page would read as "this centre
declares it and it has never transmitted", which is a different and much more
serious finding.
"""

from datetime import timedelta

from django.test import TestCase

from wis2watch.core.analysis import (
    StationStanding,
    UnknownStation,
    Window,
    node_station_detail,
    node_station_statistics,
)
from wis2watch.core.daily_rollups import backfill_daily_rollups
from wis2watch.core.models import Dataset, MessageSource, Station, WIS2Node

from .support import (
    SOMEWHERE,
    at,
    declare_station,
    observe_station,
    origin_broker,
    published,
)

NOW = at("2026-08-11T12:34:56")


class StationDrilldownTestCase(TestCase):
    """A centre, a station of it, and traffic that can be aimed anywhere."""

    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.station = self.heard("0-20000-0-63708")

    def declare(self, wigos_id, *, node=None, located=True, **kwargs):
        return declare_station(
            node or self.kenya,
            wigos_id,
            location=SOMEWHERE if located else None,
            **kwargs,
        )

    def transmitted(self, wigos_id, *, hours_ago=1, node=None):
        return observe_station(
            node or self.kenya, wigos_id, last_seen=NOW - timedelta(hours=hours_ago)
        )

    def heard(self, wigos_id, *, hours_ago=1, node=None):
        """A station this centre both declares and has just been heard for."""
        station = self.declare(wigos_id, node=node)
        self.transmitted(wigos_id, hours_ago=hours_ago, node=node)

        return station

    def dataset(self, identifier="urn:wmo:md:ke-meteo:synop", *, title=None):
        return Dataset.objects.create(
            node=self.kenya,
            identifier=identifier,
            title=title or identifier,
            wmo_data_policy="core",
            wmo_topic_hierarchy=f"origin/a/wis2/ke-meteo/{identifier}",
            raw_json={},
        )

    def rollup(
        self,
        *,
        station=None,
        hours_ago=1,
        days_ago=None,
        messages=1,
        source=None,
        node=None,
        dataset=None,
    ):
        """One hour of this centre's traffic, aimed at a station or at nobody.

        ``station`` is left out rather than defaulted to the drilldown's own,
        because half the seeding here is about traffic that names somebody
        else -- or nobody at all.
        """
        when = (
            NOW - timedelta(days=days_ago)
            if days_ago is not None
            else NOW - timedelta(hours=hours_ago)
        )

        return published(
            node or self.kenya,
            source=source or self.global_broker,
            hour=when,
            messages=messages,
            station=station,
            dataset=dataset,
        )

    def summarise(self):
        """Roll the seeded hours up into days, as the scheduled run does.

        Never written by hand, for the reason the summariser exists: a test
        that inserts its own daily row agrees with itself about how a day is
        counted, and the mistake worth catching is the two layers disagreeing.
        """
        backfill_daily_rollups(now=NOW)

    def detail(self, station=None, *, window=None, node=None):
        return node_station_detail(
            node or self.kenya,
            (station or self.station).pk,
            window=window,
            now=NOW,
        )

    def week(self, **kwargs):
        return self.detail(window=Window.resolve("7d"), **kwargs)


class BelongingTests(StationDrilldownTestCase):
    """Which stations this centre's drilldown will open at all."""

    def test_a_station_this_centre_declares_opens(self):
        self.assertEqual(
            self.detail().station.wigos_id, "0-20000-0-63708"
        )

    def test_a_station_transmitting_that_nothing_declares_opens(self):
        """A registration gap is a finding, not a station to hide."""
        undeclared = self.transmitted("0-20000-0-63999")

        detail = self.detail(undeclared)

        self.assertEqual(detail.station.standing, StationStanding.UNDECLARED)

    def test_a_station_of_another_centre_is_not_found_here(self):
        """The cross-node view is a finding of its own, and it is not this."""
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        theirs = self.heard("0-20000-0-63125", node=djibouti)

        with self.assertRaises(UnknownStation):
            self.detail(theirs)

    def test_a_station_nothing_at_all_knows_is_not_found(self):
        nowhere = Station.objects.create(wigos_id="0-20000-0-00000")

        with self.assertRaises(UnknownStation):
            self.detail(nowhere)

    def test_an_id_naming_no_station_is_not_found(self):
        with self.assertRaises(UnknownStation):
            node_station_detail(self.kenya, 9999, now=NOW)

    def test_the_refusal_names_the_station_that_was_asked_for(self):
        """A refusal a caller cannot act on sends whoever reads it to the source."""
        nowhere = Station.objects.create(wigos_id="0-20000-0-00000")

        with self.assertRaises(UnknownStation) as refused:
            self.detail(nowhere)

        self.assertIn(str(nowhere.pk), str(refused.exception))


class IdentityTests(StationDrilldownTestCase):
    """What the page repeats rather than assuming from the table."""

    def test_the_standing_is_the_one_the_rows_carry(self):
        """One derivation, or the drilldown and the row it was opened from disagree."""
        self.transmitted("0-20000-0-63709", hours_ago=200)
        stale = self.declare("0-20000-0-63709")

        row = next(
            row
            for row in node_station_statistics(self.kenya, now=NOW).stations
            if row.station_id == stale.pk
        )

        self.assertEqual(self.detail(stale).station.standing, row.standing)
        self.assertEqual(self.detail(stale).station.last_heard, row.last_heard)
        self.assertEqual(self.detail(stale).station.hours_quiet, row.hours_quiet)

    def test_the_identity_carries_the_names_and_the_place(self):
        """A shareable link stands on its own or it is a link to a number."""
        station = self.detail().station

        self.assertEqual(station.station_id, self.station.pk)
        self.assertEqual(station.name, self.station.name)
        self.assertEqual(station.latitude, SOMEWHERE.y)
        self.assertEqual(station.longitude, SOMEWHERE.x)

    def test_the_operators_own_name_travels_where_the_registry_gave_one(self):
        declare_station(self.kenya, "0-20000-0-63740", local_name="Nyeri")
        theirs = Station.objects.get(wigos_id="0-20000-0-63740")

        self.assertEqual(self.detail(theirs).station.local_name, "Nyeri")

    def test_the_response_says_which_centre_and_when_it_was_read(self):
        detail = self.detail()

        self.assertEqual(detail.node_id, self.kenya.pk)
        self.assertEqual(detail.centre_id, "ke-meteo")
        self.assertEqual(detail.generated_at, NOW)
        self.assertEqual(detail.stale_after_hours, 24)

    def test_the_window_is_echoed_with_its_own_axis(self):
        detail = self.week()

        self.assertEqual(detail.window.key, "7d")
        self.assertEqual(detail.window.grain, "day")
        self.assertEqual(len(detail.buckets), 7)


class NowBlockTests(StationDrilldownTestCase):
    """The hours this station was heard in, whatever window is chosen."""

    def test_the_fixed_block_is_twenty_four_hours_at_every_window(self):
        for key in ("24h", "7d", "90d"):
            with self.subTest(window=key):
                detail = self.detail(window=Window.resolve(key))

                self.assertEqual(len(detail.now.buckets), 24)
                self.assertEqual(len(detail.now.hourly), 24)

    def test_an_hour_of_this_stations_traffic_lands_in_its_own_bucket(self):
        self.rollup(station=self.station, hours_ago=1, messages=4)

        self.assertEqual(self.detail().now.hourly[-1].messages, 4)

    def test_another_stations_traffic_is_not_this_stations(self):
        other = self.heard("0-20000-0-63709")
        self.rollup(station=other, hours_ago=1, messages=9)

        self.assertEqual(self.detail().now.hourly[-1].messages, 0)

    def test_another_centres_traffic_for_the_same_station_is_not_counted(self):
        """Everything on this page is this centre's own observation."""
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        observe_station(djibouti, "0-20000-0-63708", last_seen=NOW)
        self.rollup(station=self.station, hours_ago=1, messages=7, node=djibouti)

        self.assertEqual(self.detail().now.hourly[-1].messages, 0)

    def test_volume_is_counted_from_the_global_broker_alone(self):
        """One publication observed at two vantage points is one publication."""
        self.rollup(station=self.station, hours_ago=1, messages=3)
        self.rollup(
            station=self.station,
            hours_ago=1,
            messages=3,
            source=origin_broker(self.kenya),
        )

        self.assertEqual(self.detail().now.hourly[-1].messages, 3)

    def test_an_hour_whose_traffic_named_nobody_is_marked_as_such(self):
        """Not a silent hour: the centre published, and named no station in it."""
        self.rollup(station=None, hours_ago=1, messages=6)

        hour = self.detail().now.hourly[-1]

        self.assertEqual(hour.messages, 0)
        self.assertTrue(hour.station_less)

    def test_an_hour_that_named_somebody_is_not_station_less(self):
        other = self.heard("0-20000-0-63709")
        self.rollup(station=other, hours_ago=1, messages=1)
        self.rollup(station=None, hours_ago=1, messages=6)

        self.assertFalse(self.detail().now.hourly[-1].station_less)

    def test_a_silent_hour_is_not_station_less_either(self):
        """Nothing published at all is silence, which is a different mark."""
        self.assertFalse(self.detail().now.hourly[-1].station_less)


class WindowStatsTests(StationDrilldownTestCase):
    """What this station did over the window the reader chose."""

    def test_there_is_no_daily_series_at_the_default_window(self):
        """One day is one cell, and a heatmap of one cell is not a heatmap."""
        self.assertIsNone(self.detail().window_stats.daily)

    def test_a_daily_window_carries_a_dense_series_on_its_own_axis(self):
        detail = self.week()

        self.assertEqual(len(detail.window_stats.daily), len(detail.buckets))

    def test_a_day_carries_how_much_of_it_this_station_was_heard_in(self):
        self.rollup(station=self.station, days_ago=2, messages=5)
        self.summarise()

        day = self.week().window_stats.daily[-3]

        self.assertEqual(day.active_hours, 1)
        self.assertEqual(day.messages, 5)

    def test_a_day_nothing_was_heard_in_is_a_zero_rather_than_a_gap(self):
        """The vector is positional, and a silent day is the finding."""
        detail = self.week()

        self.assertEqual([day.messages for day in detail.window_stats.daily], [0] * 7)
        self.assertEqual(
            [day.active_hours for day in detail.window_stats.daily], [0] * 7
        )

    def test_a_day_whose_traffic_named_nobody_is_marked_as_such(self):
        self.rollup(station=None, days_ago=2, messages=6)
        self.summarise()

        self.assertTrue(self.week().window_stats.daily[-3].station_less)

    def test_a_day_that_named_somebody_is_not_station_less(self):
        other = self.heard("0-20000-0-63709")
        self.rollup(station=other, days_ago=2, messages=1)
        self.rollup(station=None, days_ago=2, messages=6)
        self.summarise()

        self.assertFalse(self.week().window_stats.daily[-3].station_less)

    def test_the_window_total_is_this_stations_messages_over_the_window(self):
        self.rollup(station=self.station, days_ago=2, messages=5)
        self.rollup(station=self.station, days_ago=3, messages=2)
        self.summarise()

        self.assertEqual(self.week().window_stats.messages_total, 7)

    def test_traffic_older_than_the_window_is_left_out_of_the_total(self):
        self.rollup(station=self.station, days_ago=40, messages=99)
        self.summarise()

        self.assertEqual(self.week().window_stats.messages_total, 0)

    def test_the_buckets_this_station_was_heard_in_are_counted_vantage_free(self):
        """A station is one station however many vantage points heard it."""
        self.rollup(station=self.station, days_ago=2, messages=5)
        self.rollup(
            station=self.station,
            days_ago=2,
            messages=5,
            source=origin_broker(self.kenya),
        )
        self.summarise()

        self.assertEqual(self.week().window_stats.active_buckets, 1)

    def test_a_station_with_no_traffic_at_all_reads_as_zeros(self):
        """The commonest drilldown on a centre in trouble, and it has to draw."""
        stats = self.detail().window_stats

        self.assertEqual(stats.messages_total, 0)
        self.assertEqual(stats.active_buckets, 0)
        self.assertEqual(stats.datasets, [])


class DatasetBreakdownTests(StationDrilldownTestCase):
    """Which of the centre's datasets this station publishes under."""

    def test_the_datasets_are_ordered_by_what_this_station_sent_most_of(self):
        synop = self.dataset("urn:wmo:md:ke-meteo:synop", title="Surface synoptic")
        temp = self.dataset("urn:wmo:md:ke-meteo:temp", title="Upper air")
        self.rollup(station=self.station, hours_ago=1, messages=2, dataset=synop)
        self.rollup(station=self.station, hours_ago=2, messages=9, dataset=temp)

        breakdown = self.detail().window_stats.datasets

        self.assertEqual(
            [entry.title for entry in breakdown], ["Upper air", "Surface synoptic"]
        )
        self.assertEqual([entry.messages for entry in breakdown], [9, 2])

    def test_a_dataset_carries_its_identifier_and_its_own_last_heard(self):
        synop = self.dataset("urn:wmo:md:ke-meteo:synop", title="Surface synoptic")
        self.rollup(station=self.station, hours_ago=3, messages=2, dataset=synop)
        self.rollup(station=self.station, hours_ago=1, messages=2, dataset=synop)

        entry = self.detail().window_stats.datasets[0]

        self.assertEqual(entry.id, synop.pk)
        self.assertEqual(entry.identifier, "urn:wmo:md:ke-meteo:synop")
        self.assertEqual(entry.last_heard, at("2026-08-11T11:00:00"))

    def test_traffic_on_a_topic_no_dataset_claims_keeps_its_own_entry(self):
        """Dropping it would leave a breakdown that does not add up to the total."""
        self.rollup(station=self.station, hours_ago=1, messages=4, dataset=None)

        entry = self.detail().window_stats.datasets[0]

        self.assertIsNone(entry.id)
        self.assertEqual(entry.messages, 4)

    def test_another_stations_traffic_is_not_in_this_breakdown(self):
        synop = self.dataset()
        other = self.heard("0-20000-0-63709")
        self.rollup(station=other, hours_ago=1, messages=9, dataset=synop)

        self.assertEqual(self.detail().window_stats.datasets, [])

    def test_the_breakdown_is_read_over_the_window(self):
        synop = self.dataset()
        self.rollup(station=self.station, days_ago=40, messages=9, dataset=synop)
        self.rollup(station=self.station, days_ago=2, messages=3, dataset=synop)
        self.summarise()

        self.assertEqual(self.week().window_stats.datasets[0].messages, 3)

    def test_the_breakdown_is_counted_from_the_global_broker_alone(self):
        synop = self.dataset()
        self.rollup(station=self.station, hours_ago=1, messages=3, dataset=synop)
        self.rollup(
            station=self.station,
            hours_ago=1,
            messages=3,
            dataset=synop,
            source=origin_broker(self.kenya),
        )

        self.assertEqual(self.detail().window_stats.datasets[0].messages, 3)

    def test_the_breakdown_adds_up_to_the_window_total(self):
        synop = self.dataset("urn:wmo:md:ke-meteo:synop")
        self.rollup(station=self.station, hours_ago=1, messages=4, dataset=synop)
        self.rollup(station=self.station, hours_ago=2, messages=5, dataset=None)

        stats = self.detail().window_stats

        self.assertEqual(
            sum(entry.messages for entry in stats.datasets), stats.messages_total
        )
