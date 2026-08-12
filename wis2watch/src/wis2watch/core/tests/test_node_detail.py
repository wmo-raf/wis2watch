"""One centre's page, against a seeded database.

The overview says which centre to look at; this says what about it stopped. So
what is being guarded here is that the page separates things the overview
deliberately runs together -- which dataset went quiet rather than the centre,
which station stopped rather than the node, whether the missing data is a
failing sync or a broker nothing can reach.

The quiet failures are the ones worth the seeding: a station counted twice
because two sources declared it, another centre's transmission read as this
one's, a station transmitting that no registry declares dropped from the list
because the query started from the declarations.
"""

from datetime import timedelta

from django.contrib.gis.geos import Point
from django.test import TestCase

from wis2watch.core.analysis import (
    Expectation,
    OriginReachability,
    Silence,
    StationStanding,
    SyncScope,
    node_detail,
)
from wis2watch.core.models import (
    CadenceBaseline,
    Dataset,
    GlobalDiscoveryCatalogue,
    HourlyRollup,
    MessageSource,
    NodeLastSeen,
    Station,
    StationSource,
    SyncLog,
    WIS2Node,
)
from wis2watch.core.tests.support import at, origin_broker


NOW = at("2026-08-11T12:00:00")


class NodeDetailTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = self.node("ke-meteo")

    def node(self, centre_id, last_seen=None):
        node = WIS2Node.objects.create(centre_id=centre_id, name=centre_id.upper())

        if last_seen is not None:
            NodeLastSeen.objects.create(node=node, last_message_at=last_seen)

        return node

    def catalogue(self, centre_id="int-wmo-global-discovery", is_writer=False):
        return GlobalDiscoveryCatalogue.objects.create(
            centre_id=centre_id,
            name=centre_id,
            base_url=f"https://{centre_id}.example.int",
            is_writer=is_writer,
        )

    def dataset(self, name="synop", *, node=None, expects=None, status=Dataset.ACTIVE):
        node = node or self.kenya

        return Dataset.objects.create(
            node=node,
            identifier=f"urn:wmo:md:{node.centre_id}:{name}",
            title=name,
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy=f"origin/a/wis2/{node.centre_id}/data/core/{name}",
            raw_json={},
            status=status,
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
        return HourlyRollup.objects.create(
            hour=NOW - timedelta(hours=hours_ago),
            source=self.global_broker,
            node=dataset.node,
            dataset=dataset,
            message_count=1,
        )

    def declare(self, wigos_id, *, node=None, local_name="", local_id=""):
        """The node's own registry saying it operates a station."""
        station, _ = Station.objects.get_or_create(wigos_id=wigos_id)

        StationSource.objects.create(
            station=station,
            source_type=StationSource.NODE_REGISTRY,
            node=self.kenya if node is None else node,
            local_name=local_name,
            local_id=local_id,
        )

        return station

    def transmitted(self, wigos_id, *, hours_ago=1, node=None):
        """A station heard transmitting under a centre's topics."""
        station, _ = Station.objects.get_or_create(wigos_id=wigos_id)

        StationSource.objects.create(
            station=station,
            source_type=StationSource.OBSERVED,
            node=self.kenya if node is None else node,
            last_seen=NOW - timedelta(hours=hours_ago),
        )

        return station

    def detail(self, node=None, **kwargs):
        kwargs.setdefault("now", NOW)

        return node_detail(node or self.kenya, **kwargs)


class HeadlineTests(NodeDetailTestCase):
    """What the page says about the centre as a whole."""

    def test_the_centre_carries_when_it_was_last_heard_from(self):
        node = self.node("dj-anm", last_seen=at("2026-08-11T09:30:00"))

        detail = self.detail(node)

        self.assertEqual(detail.last_seen_at, at("2026-08-11T09:30:00"))
        self.assertEqual(detail.hours_since_last_seen, 2.5)

    def test_a_centre_never_heard_from_says_so_rather_than_nothing(self):
        detail = self.detail()

        self.assertIsNone(detail.last_seen_at)
        self.assertIsNone(detail.hours_since_last_seen)


class DatasetTests(NodeDetailTestCase):
    """Which part of a centre's output stopped, rather than that something did."""

    def by_title(self, **kwargs):
        return {row.title: row for row in self.detail(**kwargs).datasets}

    def test_a_dataset_carries_when_it_last_published_and_what_is_expected(self):
        synop = self.dataset("synop")
        self.learned(synop, 6)
        self.last_published(synop, 30)

        row = self.by_title()["synop"]

        self.assertEqual(row.last_active_hour, NOW - timedelta(hours=30))
        self.assertEqual(row.quiet.expected_interval_hours, 6)
        self.assertEqual(row.quiet.expectation, Expectation.LEARNED)
        self.assertEqual(row.quiet.silence, Silence.SILENT)

    def test_a_dataset_carries_what_the_catalogue_says_of_it(self):
        """The registry's own description, beside the judgement of it."""
        self.dataset("synop")

        row = self.by_title()["synop"]

        self.assertEqual(row.identifier, "urn:wmo:md:ke-meteo:synop")
        self.assertEqual(row.topic, "origin/a/wis2/ke-meteo/data/core/synop")
        self.assertEqual(row.policy, Dataset.CORE)

    def test_a_stated_expectation_is_the_one_the_page_judges_against(self):
        climate = self.dataset("climate", expects=24 * 30)
        self.learned(climate, 6)
        self.last_published(climate, 24)

        row = self.by_title()["climate"]

        self.assertEqual(row.quiet.expected_interval_hours, 24 * 30)
        self.assertEqual(row.quiet.expectation, Expectation.OVERRIDDEN)
        self.assertEqual(row.quiet.silence, Silence.ON_SCHEDULE)

    def test_a_dataset_with_nothing_to_expect_of_it_is_not_called_silent(self):
        self.last_published(self.dataset("synop"), 500)

        row = self.by_title()["synop"]

        self.assertIsNone(row.quiet.expected_interval_hours)
        self.assertEqual(row.quiet.silence, Silence.UNKNOWN)

    def test_another_centres_datasets_are_not_this_centres(self):
        djibouti = self.node("dj-anm")
        self.dataset("synop")
        self.dataset("temp", node=djibouti)

        self.assertEqual(sorted(self.by_title()), ["synop"])

    def test_a_dataset_the_catalogue_no_longer_lists_is_shown_apart_from_the_rest(self):
        """Retired datasets stay on the page, and no silence is claimed of them."""
        self.dataset("synop")
        withdrawn = self.dataset("withdrawn", status=Dataset.DELETED)
        self.last_published(withdrawn, 300)

        detail = self.detail()

        self.assertEqual([row.title for row in detail.datasets], ["synop"])

        retired = detail.retired_datasets[0]

        self.assertEqual(retired.title, "withdrawn")
        self.assertIsNone(retired.quiet)
        self.assertEqual(retired.last_active_hour, NOW - timedelta(hours=300))


class StationTests(NodeDetailTestCase):
    """The centre's stations, and which of them have stopped."""

    def by_wigos_id(self, **kwargs):
        return {row.wigos_id: row for row in self.detail(**kwargs).stations}

    def test_a_declared_station_carries_when_it_last_transmitted(self):
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708", hours_ago=3)

        row = self.by_wigos_id()["0-20000-0-63708"]

        self.assertEqual(row.last_transmitted, NOW - timedelta(hours=3))
        self.assertEqual(row.standing, StationStanding.TRANSMITTING)

    def test_a_station_heard_from_long_ago_is_not_called_transmitting(self):
        """Green for a station last heard from in March is how one is missed."""
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708", hours_ago=30 * 24)

        row = self.by_wigos_id()["0-20000-0-63708"]

        self.assertEqual(row.standing, StationStanding.GONE_QUIET)
        self.assertEqual(row.hours_quiet, 30 * 24)

    def test_a_declared_station_nothing_has_heard_from_is_named(self):
        self.declare("0-20000-0-63708")

        row = self.by_wigos_id()["0-20000-0-63708"]

        self.assertIsNone(row.last_transmitted)
        self.assertEqual(row.standing, StationStanding.NEVER_TRANSMITTED)

    def test_a_station_transmitting_that_the_registry_declares_nowhere_is_named(self):
        """A transmitting station is never invisible, declared or not."""
        self.transmitted("0-20000-0-63999")

        row = self.by_wigos_id()["0-20000-0-63999"]

        self.assertEqual(row.standing, StationStanding.UNDECLARED)
        self.assertFalse(row.declared_by_registry)

    def test_a_station_declared_and_observed_is_one_row(self):
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708")

        self.assertEqual(len(self.detail().stations), 1)

    def test_the_names_the_node_assigns_are_kept_beside_the_canonical_record(self):
        station = self.declare(
            "0-20000-0-63708", local_name="Dagoretti Corner", local_id="63741"
        )
        Station.objects.filter(pk=station.pk).update(name="NAIROBI DAGORETTI")

        row = self.by_wigos_id()["0-20000-0-63708"]

        self.assertEqual(row.name, "NAIROBI DAGORETTI")
        self.assertEqual(row.local_name, "Dagoretti Corner")
        self.assertEqual(row.local_id, "63741")

    def test_another_centres_transmission_is_not_read_as_this_centres(self):
        djibouti = self.node("dj-anm")
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63708", node=djibouti, hours_ago=1)

        row = self.by_wigos_id()["0-20000-0-63708"]

        self.assertIsNone(row.last_transmitted)
        self.assertEqual(row.standing, StationStanding.NEVER_TRANSMITTED)

    def test_another_centres_stations_are_not_this_centres(self):
        djibouti = self.node("dj-anm")
        self.declare("0-20000-0-63708")
        self.declare("0-20000-0-60001", node=djibouti)

        self.assertEqual(sorted(self.by_wigos_id()), ["0-20000-0-63708"])

    def test_a_station_only_oscar_declares_is_not_the_nodes(self):
        """OSCAR declares against a territory, and says nothing of this node."""
        station, _ = Station.objects.get_or_create(wigos_id="0-20000-0-63888")
        StationSource.objects.create(
            station=station, source_type=StationSource.OSCAR, node=None
        )

        self.assertEqual(self.detail().stations, [])

    def test_a_station_carries_where_it_is_and_what_kind_it_is(self):
        """A silent station is followed up by whoever can reach the site."""
        station = self.declare("0-20000-0-63708")
        Station.objects.filter(pk=station.pk).update(
            facility_type="landFixed",
            location=Point(36.75, -1.30, 1798.0, srid=4326),
        )

        row = self.by_wigos_id()["0-20000-0-63708"]

        self.assertEqual(row.facility_type, "Land Fixed")
        self.assertEqual((row.longitude, row.latitude), (36.75, -1.30))
        self.assertEqual(row.elevation, 1798.0)

    def test_the_silent_come_first_and_among_them_the_longest_quiet(self):
        self.declare("0-20000-0-00001")
        self.transmitted("0-20000-0-00001", hours_ago=1)
        self.declare("0-20000-0-00002")
        self.transmitted("0-20000-0-00002", hours_ago=40)
        self.declare("0-20000-0-00003")

        self.assertEqual(
            [row.wigos_id for row in self.detail().stations],
            ["0-20000-0-00003", "0-20000-0-00002", "0-20000-0-00001"],
        )

    def test_what_the_export_covers_is_counted_apart_from_what_is_listed(self):
        """The export is the registry's declarations; the list is more than them."""
        self.declare("0-20000-0-63708")
        self.transmitted("0-20000-0-63999")

        detail = self.detail()

        self.assertEqual(len(detail.stations), 2)
        self.assertEqual(detail.declared_station_count, 1)


class SyncRunTests(NodeDetailTestCase):
    """Whether a missing dataset is a failing sync."""

    def sync_run(
        self,
        *,
        node=None,
        status=SyncLog.SUCCESS,
        hours_ago=1,
        error="",
        sync_type=SyncLog.NODE_STATIONS,
    ):
        return SyncLog.objects.create(
            node=self.kenya if node is None else node,
            sync_type=sync_type,
            status=status,
            started_at=NOW - timedelta(hours=hours_ago),
            error_message=error,
        )

    def test_the_most_recent_runs_come_first_with_what_they_said(self):
        self.sync_run(hours_ago=5)
        failed = self.sync_run(
            hours_ago=1, status=SyncLog.FAILED, error="Connection refused"
        )

        runs = self.detail().sync_runs

        self.assertEqual(runs[0].run_id, failed.pk)
        self.assertEqual(runs[0].error_message, "Connection refused")
        self.assertEqual(runs[0].scope, SyncScope.CENTRE)

    def test_only_a_bounded_number_of_runs_is_read(self):
        for hours_ago in range(1, 8):
            self.sync_run(hours_ago=hours_ago)

        self.assertEqual(len(self.detail(runs_per_type=3).sync_runs), 3)

    def test_a_frequent_kind_of_run_cannot_bury_a_rare_one(self):
        """Probes are sampled hourly; the station registry is read daily."""
        for hours_ago in range(1, 8):
            self.sync_run(hours_ago=hours_ago, sync_type=SyncLog.LINK_PROBES)

        stations = self.sync_run(hours_ago=20, status=SyncLog.FAILED)

        runs = self.detail(runs_per_type=3).sync_runs

        self.assertIn(stations.pk, [run.run_id for run in runs])
        self.assertEqual(runs[-1].run_id, stations.pk)

    def test_another_centres_runs_are_not_this_centres(self):
        djibouti = self.node("dj-anm")
        self.sync_run()
        self.sync_run(node=djibouti)

        self.assertEqual(len(self.detail().sync_runs), 1)

    def test_a_centre_nothing_has_synced_reports_no_runs(self):
        self.assertEqual(self.detail().sync_runs, [])

    def test_the_run_that_creates_the_centres_datasets_is_on_the_page(self):
        """A catalogue sync is recorded against the catalogue, not the node.

        It is the run that populates every centre's datasets, topics and
        broker address, so leaving it out would answer "why are this centre's
        datasets missing" with a table that structurally cannot contain the
        answer.
        """
        run = SyncLog.objects.create(
            catalogue=self.catalogue(is_writer=True),
            sync_type=SyncLog.CATALOGUE,
            status=SyncLog.FAILED,
            started_at=NOW - timedelta(hours=2),
            error_message="The catalogue could not be read",
        )

        runs = {row.run_id: row for row in self.detail().sync_runs}

        self.assertIn(run.pk, runs)
        self.assertEqual(runs[run.pk].scope, SyncScope.REGISTRY)

    def test_a_catalogue_that_writes_nothing_is_not_read_as_the_registrys(self):
        """A read-only catalogue's runs say nothing about what was registered."""
        SyncLog.objects.create(
            catalogue=self.catalogue(centre_id="fr-meteofrance-global-discovery"),
            sync_type=SyncLog.CATALOGUE,
            status=SyncLog.SUCCESS,
            started_at=NOW - timedelta(hours=2),
        )

        self.assertEqual(self.detail().sync_runs, [])


class OriginBrokerTests(NodeDetailTestCase):
    """Whether the missing data is a broker nothing outside can reach."""

    def test_a_broker_that_answers_is_reachable_and_says_where_it_is(self):
        broker = origin_broker(
            self.kenya, is_reachable=True, last_connected_at=NOW - timedelta(minutes=5)
        )

        origin = self.detail().origin

        self.assertEqual(origin.reachability, OriginReachability.REACHABLE)
        self.assertEqual(origin.address, f"{broker.host}:{broker.port}")
        self.assertEqual(origin.last_connected_at, NOW - timedelta(minutes=5))

    def test_a_broker_that_does_not_answer_says_what_went_wrong(self):
        origin_broker(self.kenya, is_reachable=False, last_error="Connection timed out")

        origin = self.detail().origin

        self.assertEqual(origin.reachability, OriginReachability.UNREACHABLE)
        self.assertEqual(origin.last_error, "Connection timed out")

    def test_a_broker_nothing_has_tried_yet_is_not_called_unreachable(self):
        origin_broker(self.kenya)

        self.assertEqual(
            self.detail().origin.reachability, OriginReachability.NOT_ATTEMPTED
        )

    def test_a_centre_advertising_no_broker_of_its_own_says_so(self):
        origin = self.detail().origin

        self.assertEqual(origin.reachability, OriginReachability.NOT_ADVERTISED)
        self.assertEqual(origin.address, "")

    def test_the_global_broker_is_not_mistaken_for_the_centres_own(self):
        self.global_broker.node = self.kenya
        self.global_broker.is_reachable = True
        self.global_broker.save()

        self.assertEqual(
            self.detail().origin.reachability, OriginReachability.NOT_ADVERTISED
        )
