"""Seeding the WIS2 Global Services a fresh install starts from.

What is asserted here is the seeding rule -- create a service only where its
centre ID is absent, never touch a row that exists -- and the two invariants
that rule has to keep on its own: exactly one writing catalogue, exactly one
active Global Broker. Deletion is the interesting case in both, because a
deleted row is indistinguishable from one never seen, so it comes back; what
must not come back with it is authority the operator has since moved
elsewhere.

The first-sync kick is keyed on `SyncLog`, so it is asserted the same way: not
"did the seed create something" but "has a sync of this type ever landed".
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from wis2watch.core.global_services import (
    GLOBAL_BROKERS,
    GLOBAL_DISCOVERY_CATALOGUES,
    adopt_unnamed_global_brokers,
    pending_first_syncs,
    seed_global_services,
)
from wis2watch.core.models import (
    GlobalDiscoveryCatalogue,
    MessageSource,
    SyncLog,
)

ECCC = "ca-eccc-msc-global-discovery-catalogue"
DWD = "de-dwd-global-discovery-catalogue"
CMA = "cn-cma-global-discovery-catalogue"

METEO_FRANCE = "fr-meteofrance-global-broker"
INMET = "br-inmet-global-broker"


class TheListTests(TestCase):
    """The constant itself, before anything writes it to the database."""

    def test_the_three_catalogues_are_declared(self):
        self.assertEqual(
            {catalogue.centre_id for catalogue in GLOBAL_DISCOVERY_CATALOGUES},
            {ECCC, DWD, CMA},
        )

    def test_exactly_one_catalogue_is_declared_the_writer(self):
        writers = [c.centre_id for c in GLOBAL_DISCOVERY_CATALOGUES if c.is_writer]

        self.assertEqual(writers, [ECCC])

    def test_a_catalogue_base_url_stops_short_of_the_collection_path(self):
        for catalogue in GLOBAL_DISCOVERY_CATALOGUES:
            with self.subTest(catalogue.centre_id):
                self.assertNotIn("collections", catalogue.base_url)

    def test_the_four_global_brokers_are_declared(self):
        self.assertEqual(len(GLOBAL_BROKERS), 4)

    def test_exactly_one_global_broker_is_declared_active(self):
        active = [broker.centre_id for broker in GLOBAL_BROKERS if broker.is_active]

        self.assertEqual(active, [METEO_FRANCE])


class FreshInstallTests(TestCase):
    """An empty database, seeded once."""

    def setUp(self):
        self.report = seed_global_services()

    def test_every_catalogue_is_created(self):
        self.assertEqual(GlobalDiscoveryCatalogue.objects.count(), 3)

    def test_every_global_broker_is_created(self):
        self.assertEqual(
            MessageSource.objects.filter(
                source_type=MessageSource.GLOBAL_BROKER
            ).count(),
            4,
        )

    def test_one_catalogue_writes_and_the_others_are_read_only(self):
        writers = GlobalDiscoveryCatalogue.objects.filter(is_writer=True)

        self.assertEqual([c.centre_id for c in writers], [ECCC])

    def test_one_global_broker_is_active_and_the_others_are_not(self):
        active = MessageSource.objects.filter(
            source_type=MessageSource.GLOBAL_BROKER, is_active=True
        )

        self.assertEqual([source.centre_id for source in active], [METEO_FRANCE])

    def test_the_active_broker_is_dialable_as_declared(self):
        source = MessageSource.objects.get(centre_id=METEO_FRANCE)

        self.assertEqual(source.host, "globalbroker.meteo.fr")
        self.assertEqual(source.port, 8883)
        self.assertTrue(source.use_tls)
        self.assertEqual(source.username, "everyone")
        self.assertEqual(source.password, "everyone")
        self.assertIsNone(source.node)

    def test_the_report_names_what_it_created(self):
        self.assertEqual(
            {catalogue.centre_id for catalogue in self.report.catalogues_created},
            {ECCC, DWD, CMA},
        )
        self.assertEqual(len(self.report.brokers_created), 4)


class SeedingTwiceTests(TestCase):
    """The second start, and every start after it."""

    def test_nothing_is_created_the_second_time(self):
        seed_global_services()
        report = seed_global_services()

        self.assertEqual(report.catalogues_created, [])
        self.assertEqual(report.brokers_created, [])
        self.assertEqual(GlobalDiscoveryCatalogue.objects.count(), 3)
        self.assertEqual(MessageSource.objects.count(), 4)

    def test_a_deactivated_catalogue_stays_deactivated(self):
        seed_global_services()
        GlobalDiscoveryCatalogue.objects.filter(centre_id=CMA).update(is_active=False)

        seed_global_services()

        self.assertFalse(GlobalDiscoveryCatalogue.objects.get(centre_id=CMA).is_active)

    def test_an_edited_catalogue_keeps_its_edits(self):
        seed_global_services()
        GlobalDiscoveryCatalogue.objects.filter(centre_id=DWD).update(
            base_url="https://gdc.example.int", verify_ssl=False, name="Our DWD mirror"
        )

        seed_global_services()

        catalogue = GlobalDiscoveryCatalogue.objects.get(centre_id=DWD)
        self.assertEqual(catalogue.base_url, "https://gdc.example.int")
        self.assertFalse(catalogue.verify_ssl)
        self.assertEqual(catalogue.name, "Our DWD mirror")

    def test_an_edited_broker_keeps_its_edits(self):
        seed_global_services()
        MessageSource.objects.filter(centre_id=METEO_FRANCE).update(
            host="globalbroker.example.int", username="ours"
        )

        seed_global_services()

        source = MessageSource.objects.get(centre_id=METEO_FRANCE)
        self.assertEqual(source.host, "globalbroker.example.int")
        self.assertEqual(source.username, "ours")

    def test_a_switched_global_broker_is_not_switched_back(self):
        seed_global_services()
        MessageSource.objects.filter(centre_id=METEO_FRANCE).update(is_active=False)
        MessageSource.objects.filter(centre_id=INMET).update(is_active=True)

        seed_global_services()

        active = MessageSource.objects.filter(
            source_type=MessageSource.GLOBAL_BROKER, is_active=True
        )
        self.assertEqual([source.centre_id for source in active], [INMET])


class DeletionTests(TestCase):
    """A deleted row is one the seed has never seen, so it comes back.

    What it must not bring back with it is authority the operator has moved on
    to something else: the seed only confers the writer flag, or the one active
    broker, where the table has nobody holding it.
    """

    def test_a_deleted_catalogue_returns(self):
        seed_global_services()
        GlobalDiscoveryCatalogue.objects.filter(centre_id=CMA).delete()

        seed_global_services()

        self.assertTrue(GlobalDiscoveryCatalogue.objects.filter(centre_id=CMA).exists())

    def test_the_deleted_writer_returns_read_only_and_the_promoted_one_keeps_it(self):
        seed_global_services()
        GlobalDiscoveryCatalogue.objects.filter(centre_id=ECCC).delete()
        promoted = GlobalDiscoveryCatalogue.objects.get(centre_id=DWD)
        promoted.is_writer = True
        promoted.save()

        seed_global_services()

        self.assertFalse(GlobalDiscoveryCatalogue.objects.get(centre_id=ECCC).is_writer)
        self.assertTrue(GlobalDiscoveryCatalogue.objects.get(centre_id=DWD).is_writer)

    def test_a_table_with_no_writer_gets_one(self):
        seed_global_services()
        GlobalDiscoveryCatalogue.objects.all().update(is_writer=False)
        GlobalDiscoveryCatalogue.objects.filter(centre_id=ECCC).delete()

        seed_global_services()

        writers = GlobalDiscoveryCatalogue.objects.filter(is_writer=True)
        self.assertEqual([c.centre_id for c in writers], [ECCC])

    def test_the_deleted_active_broker_returns_switched_off(self):
        seed_global_services()
        MessageSource.objects.filter(centre_id=METEO_FRANCE).delete()
        MessageSource.objects.filter(centre_id=INMET).update(is_active=True)

        seed_global_services()

        active = MessageSource.objects.filter(
            source_type=MessageSource.GLOBAL_BROKER, is_active=True
        )
        self.assertEqual([source.centre_id for source in active], [INMET])

    def test_a_deleted_broker_does_not_come_back_twice(self):
        seed_global_services()
        MessageSource.objects.filter(centre_id=INMET).delete()

        seed_global_services()

        self.assertEqual(
            MessageSource.objects.filter(centre_id=INMET).count(),
            1,
        )

    def test_the_database_rejects_a_duplicate_broker_centre_id(self):
        seed_global_services()

        with self.assertRaises(IntegrityError):
            MessageSource.objects.create(
                name="A second Meteo-France",
                source_type=MessageSource.GLOBAL_BROKER,
                centre_id=METEO_FRANCE,
                host="globalbroker.meteo.fr",
            )


class UpgradeTests(TestCase):
    """An install whose Global Broker was created before centre IDs were kept.

    The row the retired ``WIS2WATCH_GLOBAL_BROKER_URL`` produced is the one
    being dialled right now, and it names no centre. Seeding on top of it
    without adopting it first would leave two rows for one broker.
    """

    def legacy_broker(self, host="globalbroker.meteo.fr", **kwargs):
        kwargs.setdefault("name", f"Global Broker ({host})")
        kwargs.setdefault("port", 8883)
        kwargs.setdefault("use_tls", True)
        return MessageSource.objects.create(
            source_type=MessageSource.GLOBAL_BROKER,
            centre_id="",
            host=host,
            **kwargs,
        )

    def test_the_broker_being_dialled_is_named_rather_than_duplicated(self):
        legacy = self.legacy_broker()

        adopt_unnamed_global_brokers(MessageSource)
        seed_global_services()

        legacy.refresh_from_db()
        self.assertEqual(legacy.centre_id, METEO_FRANCE)
        self.assertEqual(MessageSource.objects.filter(host=legacy.host).count(), 1)

    def test_the_adopted_broker_keeps_being_the_active_one(self):
        legacy = self.legacy_broker()

        adopt_unnamed_global_brokers(MessageSource)
        seed_global_services()

        active = MessageSource.objects.filter(
            source_type=MessageSource.GLOBAL_BROKER, is_active=True
        )
        self.assertEqual([source.pk for source in active], [legacy.pk])

    def test_a_broker_of_the_operators_own_is_not_claimed(self):
        own = self.legacy_broker(host="broker.example.int")

        adopted = adopt_unnamed_global_brokers(MessageSource)

        own.refresh_from_db()
        self.assertEqual(adopted, [])
        self.assertEqual(own.centre_id, "")

    def test_a_centre_id_already_held_is_left_to_the_row_holding_it(self):
        seed_global_services()
        legacy = self.legacy_broker()

        adopted = adopt_unnamed_global_brokers(MessageSource)

        legacy.refresh_from_db()
        self.assertEqual(adopted, [])
        self.assertEqual(legacy.centre_id, "")

    def test_adopting_twice_changes_nothing_the_second_time(self):
        self.legacy_broker()
        adopt_unnamed_global_brokers(MessageSource)

        self.assertEqual(adopt_unnamed_global_brokers(MessageSource), [])


class FirstSyncTests(TestCase):
    """Which syncs still have to be kicked, and which have landed already."""

    def sync_log(self, sync_type, status=SyncLog.SUCCESS):
        return SyncLog.objects.create(
            sync_type=sync_type,
            status=status,
            completed_at=dj_timezone.now(),
        )

    def test_a_fresh_install_owes_both_syncs(self):
        self.assertEqual(
            pending_first_syncs(),
            (SyncLog.CATALOGUE, SyncLog.OSCAR_STATIONS),
        )

    def test_a_landed_sync_is_never_kicked_again(self):
        self.sync_log(SyncLog.CATALOGUE)

        self.assertEqual(pending_first_syncs(), (SyncLog.OSCAR_STATIONS,))

    def test_a_partial_sync_counts_as_landed(self):
        self.sync_log(SyncLog.CATALOGUE, status=SyncLog.PARTIAL)
        self.sync_log(SyncLog.OSCAR_STATIONS, status=SyncLog.PARTIAL)

        self.assertEqual(pending_first_syncs(), ())

    def test_a_failed_sync_is_kicked_again(self):
        self.sync_log(SyncLog.CATALOGUE, status=SyncLog.FAILED)
        self.sync_log(SyncLog.OSCAR_STATIONS, status=SyncLog.SUCCESS)

        self.assertEqual(pending_first_syncs(), (SyncLog.CATALOGUE,))

    def test_another_kind_of_sync_says_nothing_about_these_two(self):
        self.sync_log(SyncLog.NODE_STATIONS)

        self.assertEqual(
            pending_first_syncs(),
            (SyncLog.CATALOGUE, SyncLog.OSCAR_STATIONS),
        )


class SeedCommandTests(TestCase):
    """The command as the entrypoint runs it, with the queue stubbed out."""

    def run_command(self):
        out = StringIO()

        with (
            mock.patch("wis2watch.core.tasks.run_sync_catalogues.delay") as catalogues,
            mock.patch("wis2watch.core.tasks.run_sync_oscar_stations.delay") as oscar,
        ):
            call_command("seed_global_services", stdout=out)

        return out.getvalue(), catalogues, oscar

    def test_it_seeds_and_says_what_it_created(self):
        output, _, _ = self.run_command()

        self.assertEqual(GlobalDiscoveryCatalogue.objects.count(), 3)
        self.assertIn(ECCC, output)
        self.assertIn(METEO_FRANCE, output)

    def test_the_first_start_enqueues_both_syncs(self):
        output, catalogues, oscar = self.run_command()

        catalogues.assert_called_once_with()
        oscar.assert_called_once_with()
        self.assertIn("enqueued", output.lower())

    def test_a_sync_that_has_landed_is_not_enqueued_again(self):
        SyncLog.objects.create(
            sync_type=SyncLog.CATALOGUE,
            status=SyncLog.SUCCESS,
            completed_at=dj_timezone.now(),
        )

        _, catalogues, oscar = self.run_command()

        catalogues.assert_not_called()
        oscar.assert_called_once_with()

    def test_a_sync_that_was_enqueued_and_lost_is_enqueued_again(self):
        self.run_command()

        _, catalogues, oscar = self.run_command()

        catalogues.assert_called_once_with()
        oscar.assert_called_once_with()

    def test_an_unreachable_queue_neither_fails_the_start_nor_ends_the_offer(self):
        out, err = StringIO(), StringIO()

        with (
            mock.patch(
                "wis2watch.core.tasks.run_sync_catalogues.delay",
                side_effect=OSError("no route to the broker"),
            ),
            mock.patch("wis2watch.core.tasks.run_sync_oscar_stations.delay") as oscar,
        ):
            call_command("seed_global_services", stdout=out, stderr=err)

        oscar.assert_called_once_with()
        self.assertIn("could not be enqueued", err.getvalue())
        self.assertIn(SyncLog.CATALOGUE, pending_first_syncs())

    def test_running_it_twice_creates_nothing_the_second_time(self):
        self.run_command()
        output, _, _ = self.run_command()

        self.assertEqual(GlobalDiscoveryCatalogue.objects.count(), 3)
        self.assertEqual(MessageSource.objects.count(), 4)
        self.assertIn("nothing to create", output.lower())
