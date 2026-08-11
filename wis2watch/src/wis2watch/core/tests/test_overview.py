"""The node overview, against a seeded database.

One screen answers the headline question for the region, so what it says has
to be right about every centre at once -- including the ones nothing has ever
been heard from, which are the whole point and the easiest to drop out of a
query.

The failures worth guarding against here are quiet ones: a volume that counts
the same notification twice because two vantage points saw it, a station
counted once per source that declared it, a centre missing from the table
because it has no rows to join to.
"""

from datetime import timedelta

from django.test import TestCase

from wis2watch.core.analysis import Staleness, node_overview
from wis2watch.core.models import (
    Dataset,
    HourlyRollup,
    MessageSource,
    NodeLastSeen,
    Station,
    StationSource,
    WIS2Node,
)
from wis2watch.core.tests.support import at


NOW = at("2026-08-11T12:00:00")


class OverviewTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )

    def node(self, centre_id, last_seen=None, **kwargs):
        kwargs.setdefault("name", centre_id.upper())
        node = WIS2Node.objects.create(centre_id=centre_id, **kwargs)

        if last_seen is not None:
            NodeLastSeen.objects.create(node=node, last_message_at=last_seen)

        return node

    def rollup(self, node, hour, count, source=None, dataset=None, station=None):
        return HourlyRollup.objects.create(
            hour=hour,
            source=source or self.global_broker,
            node=node,
            dataset=dataset,
            station=station,
            message_count=count,
        )

    def overview(self, **kwargs):
        kwargs.setdefault("now", NOW)
        return node_overview(**kwargs)

    def by_centre(self, **kwargs):
        return {row.centre_id: row for row in self.overview(**kwargs)}


class CoverageTests(OverviewTestCase):
    """Every monitored centre appears, whatever has or has not been heard."""

    def test_every_registered_centre_is_listed(self):
        self.node("ke-meteo", last_seen=NOW)
        self.node("dj-anm")

        self.assertEqual(
            sorted(row.centre_id for row in self.overview()), ["dj-anm", "ke-meteo"]
        )

    def test_a_centre_never_heard_from_is_listed_with_nothing_to_show(self):
        self.node("dj-anm")

        row = self.by_centre()["dj-anm"]

        self.assertIsNone(row.last_seen_at)
        self.assertIsNone(row.hours_since_last_seen)
        self.assertEqual(row.staleness, Staleness.NEVER_SEEN)
        self.assertEqual(row.recent_message_count, 0)

    def test_a_centre_carries_the_country_it_is_registered_under(self):
        self.node("ke-meteo", last_seen=NOW)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.country_code, "KE")
        self.assertEqual(row.country_name, "Kenya")


class LastSeenTests(OverviewTestCase):
    """How long a centre has been quiet, and what that is called."""

    def test_last_seen_is_reported_with_the_hours_since(self):
        self.node("ke-meteo", last_seen=at("2026-08-11T09:30:00"))

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.last_seen_at, at("2026-08-11T09:30:00"))
        self.assertEqual(row.hours_since_last_seen, 2.5)

    def test_a_centre_heard_from_within_the_threshold_is_active(self):
        self.node("ke-meteo", last_seen=NOW - timedelta(hours=23))

        self.assertEqual(self.by_centre()["ke-meteo"].staleness, Staleness.ACTIVE)

    def test_a_centre_quiet_for_longer_than_the_threshold_is_stale(self):
        self.node("ke-meteo", last_seen=NOW - timedelta(hours=25))

        self.assertEqual(self.by_centre()["ke-meteo"].staleness, Staleness.STALE)

    def test_the_threshold_is_an_argument(self):
        self.node("ke-meteo", last_seen=NOW - timedelta(hours=3))

        rows = self.by_centre(stale_after_hours=2)

        self.assertEqual(rows["ke-meteo"].staleness, Staleness.STALE)


class OrderingTests(OverviewTestCase):
    """The most concerning centres come first."""

    def setUp(self):
        super().setUp()
        self.node("ke-meteo", last_seen=NOW - timedelta(hours=1))
        self.node("ug-unma", last_seen=NOW - timedelta(days=9))
        self.node("dj-anm")

    def centres(self, **kwargs):
        return [row.centre_id for row in self.overview(**kwargs)]

    def test_the_longest_silent_come_first_and_the_never_heard_from_before_them(self):
        self.assertEqual(self.centres(), ["dj-anm", "ug-unma", "ke-meteo"])

    def test_the_table_can_be_ordered_by_centre_instead(self):
        self.assertEqual(self.centres(order="centre"), ["dj-anm", "ke-meteo", "ug-unma"])


class FilterTests(OverviewTestCase):
    """Narrowing the table to what is worth looking at."""

    def setUp(self):
        super().setUp()
        self.node("ke-meteo", last_seen=NOW - timedelta(hours=1))
        self.node("ug-unma", last_seen=NOW - timedelta(days=9))
        self.node("dj-anm")

    def centres(self, **kwargs):
        return [row.centre_id for row in self.overview(**kwargs)]

    def test_the_table_can_be_narrowed_to_the_stale(self):
        self.assertEqual(self.centres(staleness=Staleness.STALE), ["ug-unma"])

    def test_the_table_can_be_narrowed_to_those_never_heard_from(self):
        self.assertEqual(self.centres(staleness=Staleness.NEVER_SEEN), ["dj-anm"])

    def test_the_table_can_be_narrowed_to_the_active(self):
        self.assertEqual(self.centres(staleness=Staleness.ACTIVE), ["ke-meteo"])

    def test_an_unknown_filter_narrows_to_nothing_rather_than_everything(self):
        self.assertEqual(self.centres(staleness="whatever"), [])


class VolumeTests(OverviewTestCase):
    """Recent volume, read from the rollups rather than the time series."""

    def setUp(self):
        super().setUp()
        self.kenya = self.node("ke-meteo", last_seen=NOW)

    def test_volume_is_the_messages_rolled_up_over_the_window(self):
        self.rollup(self.kenya, NOW - timedelta(hours=1), 12)
        self.rollup(self.kenya, NOW - timedelta(hours=5), 30)

        self.assertEqual(self.by_centre()["ke-meteo"].recent_message_count, 42)

    def test_volume_ignores_hours_outside_the_window(self):
        self.rollup(self.kenya, NOW - timedelta(hours=1), 12)
        self.rollup(self.kenya, NOW - timedelta(hours=30), 500)

        self.assertEqual(self.by_centre()["ke-meteo"].recent_message_count, 12)

    def test_the_window_is_an_argument(self):
        self.rollup(self.kenya, NOW - timedelta(hours=1), 12)
        self.rollup(self.kenya, NOW - timedelta(hours=5), 30)

        rows = self.by_centre(volume_hours=2)

        self.assertEqual(rows["ke-meteo"].recent_message_count, 12)

    def test_the_window_is_the_same_length_whatever_minute_it_is_asked_at(self):
        """Counted in whole buckets, so "24h" is not 25h at half past."""
        for hours_ago in range(26):
            self.rollup(self.kenya, at("2026-08-11T12:00:00") - timedelta(hours=hours_ago), 1)

        on_the_hour = self.by_centre(now=at("2026-08-11T12:00:00"), volume_hours=24)
        half_past = self.by_centre(now=at("2026-08-11T12:30:00"), volume_hours=24)

        self.assertEqual(on_the_hour["ke-meteo"].recent_message_count, 24)
        self.assertEqual(half_past["ke-meteo"].recent_message_count, 24)

    def test_the_hour_in_progress_is_counted(self):
        self.rollup(self.kenya, at("2026-08-11T12:00:00"), 3)

        rows = self.by_centre(now=at("2026-08-11T12:45:00"), volume_hours=1)

        self.assertEqual(rows["ke-meteo"].recent_message_count, 3)

    def test_volume_belongs_to_the_centre_that_published_it(self):
        djibouti = self.node("dj-anm", last_seen=NOW)
        self.rollup(self.kenya, NOW - timedelta(hours=1), 12)
        self.rollup(djibouti, NOW - timedelta(hours=1), 7)

        rows = self.by_centre()

        self.assertEqual(rows["ke-meteo"].recent_message_count, 12)
        self.assertEqual(rows["dj-anm"].recent_message_count, 7)

    def test_a_notification_seen_at_two_vantage_points_is_not_counted_twice(self):
        """The origin broker sees the same traffic; the world's view is the answer."""
        origin = MessageSource.objects.create(
            name="ke-meteo origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.kenya,
            host="wis.meteo.example.int",
        )
        self.rollup(self.kenya, NOW - timedelta(hours=1), 12)
        self.rollup(self.kenya, NOW - timedelta(hours=1), 12, source=origin)

        self.assertEqual(self.by_centre()["ke-meteo"].recent_message_count, 12)

    def test_volume_sums_the_grains_within_an_hour(self):
        synop = Dataset.objects.create(
            node=self.kenya,
            identifier="urn:wmo:md:ke-meteo:synop",
            title="Synop",
            wmo_data_policy="core",
            wmo_topic_hierarchy="origin/a/wis2/ke-meteo/synop",
            raw_json={},
        )
        self.rollup(self.kenya, NOW - timedelta(hours=1), 12, dataset=synop)
        self.rollup(self.kenya, NOW - timedelta(hours=1), 3)

        self.assertEqual(self.by_centre()["ke-meteo"].recent_message_count, 15)


class CountTests(OverviewTestCase):
    """What each centre claims to publish, and from where."""

    def setUp(self):
        super().setUp()
        self.kenya = self.node("ke-meteo", last_seen=NOW)

    def dataset(self, identifier, status=Dataset.ACTIVE):
        return Dataset.objects.create(
            node=self.kenya,
            identifier=identifier,
            title=identifier,
            wmo_data_policy="core",
            wmo_topic_hierarchy=f"origin/a/wis2/ke-meteo/{identifier}",
            raw_json={},
            status=status,
        )

    def declare(self, wigos_id, source_type=StationSource.NODE_REGISTRY, node=None):
        station, _ = Station.objects.get_or_create(wigos_id=wigos_id)

        return StationSource.objects.create(
            station=station,
            source_type=source_type,
            node=self.kenya if node is None else node,
        )

    def test_the_datasets_a_centre_claims_are_counted(self):
        self.dataset("synop")
        self.dataset("temp")

        self.assertEqual(self.by_centre()["ke-meteo"].dataset_count, 2)

    def test_a_deleted_dataset_is_not_something_the_centre_still_claims(self):
        self.dataset("synop")
        self.dataset("gone", status=Dataset.DELETED)

        self.assertEqual(self.by_centre()["ke-meteo"].dataset_count, 1)

    def test_the_stations_a_centre_is_associated_with_are_counted(self):
        self.declare("0-20000-0-63708")
        self.declare("0-20000-0-63710")

        self.assertEqual(self.by_centre()["ke-meteo"].station_count, 2)

    def test_a_station_declared_by_two_sources_is_one_station(self):
        self.declare("0-20000-0-63708")
        self.declare("0-20000-0-63708", source_type=StationSource.OBSERVED)

        self.assertEqual(self.by_centre()["ke-meteo"].station_count, 1)

    def test_another_centres_stations_are_not_counted(self):
        djibouti = self.node("dj-anm", last_seen=NOW)
        self.declare("0-20000-0-63708")
        self.declare("0-20000-0-60001", node=djibouti)

        rows = self.by_centre()

        self.assertEqual(rows["ke-meteo"].station_count, 1)
        self.assertEqual(rows["dj-anm"].station_count, 1)

    def test_a_centre_with_nothing_registered_counts_zero_rather_than_none(self):
        row = self.by_centre()["ke-meteo"]

        self.assertEqual((row.dataset_count, row.station_count), (0, 0))
