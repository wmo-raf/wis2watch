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

from wis2watch.core.analysis import (
    CachePickup,
    OriginReachability,
    OriginWatch,
    Silence,
    Staleness,
    node_overview,
)
from wis2watch.core.models import (
    CadenceBaseline,
    Dataset,
    HourlyRollup,
    MessageSource,
    NodeLastSeen,
    Station,
    StationSource,
    WIS2Node,
)
from wis2watch.core.tests.support import at, origin_api, origin_broker


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

    def test_a_retired_dataset_is_not_something_the_centre_still_claims(self):
        """The centre has said the record is not theirs, so the count it is
        judged by is not theirs either."""
        self.dataset("synop")
        self.dataset("kedehn", status=Dataset.INACTIVE)

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


class SilenceTests(OverviewTestCase):
    """Whether a centre's output has stopped, judged dataset by dataset.

    The last-seen column says when the centre last published anything, which
    is a different question: a centre publishing hourly synops while its daily
    climate summary has not appeared for a week is not stale, and is missing
    something.
    """

    def setUp(self):
        super().setUp()
        self.kenya = self.node("ke-meteo", last_seen=NOW)

    def dataset(self, name, *, expects=None, node=None):
        node = node or self.kenya

        return Dataset.objects.create(
            node=node,
            identifier=f"urn:wmo:md:{node.centre_id}:{name}",
            title=name,
            wmo_data_policy="core",
            wmo_topic_hierarchy=f"origin/a/wis2/{node.centre_id}/data/core/{name}",
            raw_json={},
            expected_interval_override_hours=expects,
        )

    def learned(self, dataset, interval_hours):
        return CadenceBaseline.objects.create(
            dataset=dataset,
            interval_hours=interval_hours,
            observations=20,
            learned_at=NOW - timedelta(days=1),
        )

    def last_published(self, dataset, hours_ago):
        return self.rollup(
            dataset.node, NOW - timedelta(hours=hours_ago), 1, dataset=dataset
        )

    def test_a_centre_with_a_dataset_past_its_expectation_is_silent(self):
        synop = self.dataset("synop")
        self.learned(synop, 6)
        self.last_published(synop, 30)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.silence, Silence.SILENT)
        self.assertEqual(row.silent_dataset_count, 1)

    def test_a_centre_whose_datasets_are_all_within_their_expectations_is_not(self):
        synop = self.dataset("synop")
        self.learned(synop, 6)
        self.last_published(synop, 1)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.silence, Silence.ON_SCHEDULE)
        self.assertEqual(row.silent_dataset_count, 0)
        self.assertEqual(row.judged_dataset_count, 1)

    def test_a_monthly_dataset_quiet_a_day_is_not_a_silent_centre(self):
        climate = self.dataset("climate", expects=24 * 30)
        self.last_published(climate, 24)

        self.assertEqual(self.by_centre()["ke-meteo"].silence, Silence.ON_SCHEDULE)

    def test_a_centre_with_nothing_that_can_be_judged_is_not_called_silent(self):
        self.last_published(self.dataset("synop"), 500)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.silence, Silence.UNKNOWN)
        self.assertEqual(row.judged_dataset_count, 0)

    def test_a_centre_with_no_datasets_at_all_is_not_called_silent(self):
        self.node("dj-anm")

        self.assertEqual(self.by_centre()["dj-anm"].silence, Silence.UNKNOWN)

    def test_each_centre_carries_its_own_datasets_verdict(self):
        djibouti = self.node("dj-anm", last_seen=NOW)
        overdue = self.dataset("synop")
        on_time = self.dataset("temp", node=djibouti)
        self.learned(overdue, 6)
        self.learned(on_time, 6)
        self.last_published(overdue, 30)
        self.last_published(on_time, 1)

        rows = self.by_centre()

        self.assertEqual(rows["ke-meteo"].silence, Silence.SILENT)
        self.assertEqual(rows["dj-anm"].silence, Silence.ON_SCHEDULE)


class OriginWatchTests(OverviewTestCase):
    """Which of a centre's own transports is carrying its view of itself.

    A large share of these brokers are unreachable, which is a finding about
    the centre rather than a fault to hide: read next to the volume the Global
    Broker saw, "not reachable, and yet publishing" is the row that starts an
    investigation. What the archive changes is that such a centre can still be
    watched -- which the column has to say without letting "we can see them
    another way" stand in for a broker that answers.
    """

    def test_a_centre_whose_broker_answers_is_watched_at_its_broker(self):
        origin_broker(self.node("ke-meteo"), is_reachable=True)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.origin_watch, OriginWatch.AT_BROKER)
        self.assertTrue(row.is_watched_at_broker)
        self.assertEqual(row.origin_last_error, "")

    def test_a_centre_watched_only_through_its_archive_says_the_broker_is_down(self):
        """The fallback is not a green tick.

        The centre is being watched and its broker is not answering, and the
        row has to say both: the first is what entitles this tool to judge its
        propagation, and the second is the WIS2 obligation it is failing.
        """
        node = self.node("ke-meteo")
        origin_broker(node, is_reachable=False, last_error="Connection timed out")
        origin_api(node, is_reachable=True)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.origin_watch, OriginWatch.AT_ARCHIVE)
        self.assertFalse(row.is_watched_at_broker)
        self.assertEqual(
            row.origin_broker_reachability, OriginReachability.UNREACHABLE
        )
        self.assertEqual(row.origin_last_error, "Connection timed out")

    def test_a_centre_watched_at_an_archive_it_never_advertised_a_broker_for(self):
        """The fallback does not assert a broker that was never advertised.

        The archive is polled for these centres too -- a centre with no broker
        on the record has never been heard from at all -- so the state has to
        be true of them, and what its broker is doing is the line beneath.
        """
        origin_api(self.node("ke-meteo"), is_reachable=True)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.origin_watch, OriginWatch.AT_ARCHIVE)
        self.assertEqual(
            row.origin_broker_reachability, OriginReachability.NOT_ADVERTISED
        )

    def test_a_broker_that_answers_is_what_the_row_reports_watching_at(self):
        """The two transports are one witness, and the broker is the one owed."""
        node = self.node("ke-meteo")
        origin_broker(node, is_reachable=True)
        origin_api(node, is_reachable=True)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.origin_watch, OriginWatch.AT_BROKER)

    def test_a_centre_neither_transport_answers_for_is_not_watched(self):
        node = self.node("ke-meteo")
        origin_broker(node, is_reachable=False, last_error="Connection timed out")
        origin_api(node, is_reachable=False)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.origin_watch, OriginWatch.UNWATCHED)
        self.assertEqual(row.origin_last_error, "Connection timed out")

    def test_a_broker_nothing_has_tried_yet_is_not_read_as_watching(self):
        origin_broker(self.node("ke-meteo"))

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.origin_watch, OriginWatch.UNWATCHED)
        self.assertEqual(
            row.origin_broker_reachability, OriginReachability.NOT_ATTEMPTED
        )

    def test_an_archive_nothing_has_polled_yet_is_not_read_as_watching(self):
        node = self.node("ke-meteo")
        origin_broker(node, is_reachable=False)
        origin_api(node)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.origin_watch, OriginWatch.UNWATCHED)

    def test_a_vantage_point_switched_off_is_not_read_as_watching(self):
        """What is not being asked any more carries an answer that has gone stale.

        The same rule the propagation evaluation applies, because a row
        claiming a centre is watched while the evaluation refuses to judge it
        is the contradiction that costs the table its credibility.
        """
        origin_broker(self.node("ke-meteo"), is_reachable=True, is_active=False)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.origin_watch, OriginWatch.UNWATCHED)

    def test_a_centre_with_no_vantage_point_at_all_says_so(self):
        self.node("ke-meteo")

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.origin_watch, OriginWatch.UNWATCHED)
        self.assertEqual(
            row.origin_broker_reachability, OriginReachability.NOT_ADVERTISED
        )
        self.assertEqual(row.origin_last_error, "")

    def test_the_global_broker_is_not_mistaken_for_a_centres_own(self):
        """The world's vantage point says nothing about the centre's broker."""
        node = self.node("ke-meteo")
        self.global_broker.node = node
        self.global_broker.is_reachable = True
        self.global_broker.save()

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.origin_watch, OriginWatch.UNWATCHED)
        self.assertEqual(
            row.origin_broker_reachability, OriginReachability.NOT_ADVERTISED
        )

    def test_each_centre_carries_its_own_state(self):
        origin_broker(self.node("ke-meteo"), is_reachable=True)
        origin_broker(self.node("dj-anm"), is_reachable=False)

        rows = self.by_centre()

        self.assertEqual(rows["ke-meteo"].origin_watch, OriginWatch.AT_BROKER)
        self.assertEqual(rows["dj-anm"].origin_watch, OriginWatch.UNWATCHED)


class CachePickupTests(OverviewTestCase):
    """Whether a centre's core data is reaching the Global Caches.

    The last link in the chain from a centre to the world, and the one the
    origin volume column cannot speak to: a centre whose notifications reach
    the Global Broker and are never cached is publishing into a world that
    cannot retrieve what it published.

    Only core data is cached, so a centre publishing nothing else has nothing
    to be missing -- which is the distinction these tests are mostly about.
    """

    def setUp(self):
        super().setUp()
        self.cache = MessageSource.objects.create(
            name="Global Cache via Global Broker",
            source_type=MessageSource.GLOBAL_CACHE,
            carried_by=self.global_broker,
            host="globalbroker.example.int",
        )
        self.kenya = self.node("ke-meteo", last_seen=NOW)

    def dataset(self, name, policy=Dataset.CORE):
        return Dataset.objects.create(
            node=self.kenya,
            identifier=f"urn:wmo:md:ke-meteo:{name}",
            title=name,
            wmo_data_policy=policy,
            wmo_topic_hierarchy=f"origin/a/wis2/ke-meteo/data/{policy}/{name}",
            raw_json={},
        )

    def published(self, dataset, count=1, hours_ago=1, source=None):
        return self.rollup(
            self.kenya,
            NOW - timedelta(hours=hours_ago),
            count,
            dataset=dataset,
            source=source,
        )

    def test_core_data_the_caches_republished_is_picked_up(self):
        synop = self.dataset("synop")
        self.published(synop, count=12)
        self.published(synop, count=12, source=self.cache)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.cache_pickup, CachePickup.PICKED_UP)
        self.assertEqual(row.cache_message_count, 12)

    def test_core_data_no_cache_carried_is_not_picked_up(self):
        self.published(self.dataset("synop"), count=12)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.cache_pickup, CachePickup.NOT_PICKED_UP)
        self.assertEqual(row.cache_message_count, 0)

    def test_a_centre_publishing_only_recommended_data_has_nothing_to_cache(self):
        """The Global Caches carry core data; the rest is not theirs to hold."""
        self.published(self.dataset("taf", policy=Dataset.RECOMMENDED), count=12)

        self.assertEqual(
            self.by_centre()["ke-meteo"].cache_pickup, CachePickup.NOTHING_TO_CACHE
        )

    def test_a_centre_that_has_published_nothing_is_not_reported_as_missing(self):
        self.assertEqual(
            self.by_centre()["ke-meteo"].cache_pickup, CachePickup.NOTHING_TO_CACHE
        )

    def test_cache_pickup_is_judged_over_the_same_window_as_the_volume(self):
        synop = self.dataset("synop")
        self.published(synop, count=12, hours_ago=1)
        self.published(synop, count=12, hours_ago=30, source=self.cache)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.cache_pickup, CachePickup.NOT_PICKED_UP)
        self.assertEqual(row.cache_message_count, 0)

    def test_what_a_cache_republished_does_not_inflate_the_origin_volume(self):
        synop = self.dataset("synop")
        self.published(synop, count=12)
        self.published(synop, count=12, source=self.cache)

        self.assertEqual(self.by_centre()["ke-meteo"].recent_message_count, 12)

    def test_each_centre_carries_its_own_pickup(self):
        djibouti = self.node("dj-anm", last_seen=NOW)
        synop = self.dataset("synop")
        self.published(synop, count=12)
        self.published(synop, count=12, source=self.cache)
        HourlyRollup.objects.create(
            hour=NOW - timedelta(hours=1),
            source=self.global_broker,
            node=djibouti,
            dataset=Dataset.objects.create(
                node=djibouti,
                identifier="urn:wmo:md:dj-anm:synop",
                title="synop",
                wmo_data_policy=Dataset.CORE,
                wmo_topic_hierarchy="origin/a/wis2/dj-anm/data/core/synop",
                raw_json={},
            ),
            message_count=7,
        )

        rows = self.by_centre()

        self.assertEqual(rows["ke-meteo"].cache_pickup, CachePickup.PICKED_UP)
        self.assertEqual(rows["dj-anm"].cache_pickup, CachePickup.NOT_PICKED_UP)
