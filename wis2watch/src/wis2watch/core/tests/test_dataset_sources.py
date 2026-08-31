"""Where a dataset came from, recorded beside the dataset itself.

Two things are guarded here. That a declaration is provenance rather than
identity -- several sources describing one dataset are several rows and one
canonical record, which is the whole reason the shape exists. And that the
backfill is a faithful reading of the contract as it stood: the writer
catalogue has been the only thing that ever created a dataset, so every row
already in the database is one it declared, and a first node sync must not be
able to look like the only source anything ever had.
"""

from django.test import TestCase

from wis2watch.core.dataset_sources import (
    backfill_gdc_declarations,
    record_declaration,
)
from wis2watch.core.models import (
    Dataset,
    DatasetSource,
    GlobalDiscoveryCatalogue,
    WIS2Node,
)

from .support import at


class DatasetSourceTestCase(TestCase):
    def setUp(self):
        self.node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.writer = self.catalogue("ca-eccc-msc-global-discovery", is_writer=True)

    def catalogue(self, centre_id, is_writer=False):
        return GlobalDiscoveryCatalogue.objects.create(
            centre_id=centre_id,
            name=centre_id,
            base_url=f"https://{centre_id}.example.int",
            is_writer=is_writer,
        )

    def dataset(self, name="synop", *, last_synced=None, **kwargs):
        return Dataset.objects.create(
            node=self.node,
            identifier=f"urn:wmo:md:ke-meteo:{name}",
            title=name,
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy=f"origin/a/wis2/ke-meteo/data/core/{name}",
            raw_json={"id": f"urn:wmo:md:ke-meteo:{name}"},
            last_synced=last_synced,
            **kwargs,
        )


class RecordDeclarationTests(DatasetSourceTestCase):
    """A source saying a dataset exists, written down as what it is: a claim."""

    def test_a_declaration_keeps_what_the_source_said(self):
        dataset = self.dataset()

        declaration, created = record_declaration(
            dataset,
            DatasetSource.GDC,
            catalogue=self.writer,
            raw={"title": "Surface observations"},
        )

        self.assertTrue(created)
        self.assertEqual(declaration.raw_json, {"title": "Surface observations"})
        self.assertEqual(declaration.catalogue, self.writer)

    def test_declaring_again_refreshes_the_row_rather_than_adding_one(self):
        dataset = self.dataset()

        record_declaration(dataset, DatasetSource.GDC, catalogue=self.writer)
        _, created = record_declaration(
            dataset, DatasetSource.GDC, catalogue=self.writer, seen_at=at("2026-08-11T12:00:00")
        )

        self.assertFalse(created)
        self.assertEqual(dataset.sources.count(), 1)
        self.assertEqual(
            dataset.sources.get().last_seen, at("2026-08-11T12:00:00")
        )

    def test_when_a_source_was_first_heard_saying_so_never_moves(self):
        """A six-hourly sync would otherwise reset it to the last run, forever."""
        dataset = self.dataset()

        first, _ = record_declaration(dataset, DatasetSource.GDC, catalogue=self.writer)
        record_declaration(dataset, DatasetSource.GDC, catalogue=self.writer)

        self.assertEqual(dataset.sources.get().first_seen, first.first_seen)

    def test_two_catalogues_declaring_one_dataset_are_two_declarations(self):
        """Which of them says what is the whole of a divergence report."""
        dataset = self.dataset()
        reader = self.catalogue("cn-cma-global-discovery")

        record_declaration(dataset, DatasetSource.GDC, catalogue=self.writer)
        record_declaration(dataset, DatasetSource.GDC, catalogue=reader)

        self.assertEqual(
            set(dataset.sources.values_list("catalogue__centre_id", flat=True)),
            {self.writer.centre_id, reader.centre_id},
        )

    def test_the_sources_of_one_dataset_are_the_three_kinds(self):
        """The canonical record stays one row however many sources describe it."""
        dataset = self.dataset()

        for source_type in (DatasetSource.GDC, DatasetSource.NODE, DatasetSource.OBSERVED):
            record_declaration(dataset, source_type)

        self.assertEqual(Dataset.objects.count(), 1)
        self.assertEqual(dataset.sources.count(), 3)

    def test_the_operators_expectation_is_no_sources_to_touch(self):
        dataset = self.dataset(expected_interval_override_hours=72)

        record_declaration(dataset, DatasetSource.GDC, catalogue=self.writer)

        dataset.refresh_from_db()
        self.assertEqual(dataset.expected_interval_override_hours, 72)


class BackfillTests(DatasetSourceTestCase):
    """Every dataset already held came from the writer catalogue, so say so."""

    def test_every_existing_dataset_gains_a_catalogue_declaration(self):
        self.dataset("synop")
        self.dataset("climat")

        self.assertEqual(backfill_gdc_declarations(), 2)
        self.assertEqual(
            DatasetSource.objects.filter(source_type=DatasetSource.GDC).count(), 2
        )

    def test_the_declaration_names_the_catalogue_that_writes_the_registry(self):
        self.dataset()
        self.catalogue("cn-cma-global-discovery")

        backfill_gdc_declarations()

        self.assertEqual(DatasetSource.objects.get().catalogue, self.writer)

    def test_the_declaration_carries_what_the_catalogue_said(self):
        dataset = self.dataset()

        backfill_gdc_declarations()

        self.assertEqual(DatasetSource.objects.get().raw_json, dataset.raw_json)

    def test_a_declaration_is_last_seen_when_the_catalogue_last_confirmed_it(self):
        """Stamping the backfill as now would erase the staleness it is for."""
        self.dataset(last_synced=at("2026-08-01T06:00:00"))

        backfill_gdc_declarations()

        self.assertEqual(
            DatasetSource.objects.get().last_seen, at("2026-08-01T06:00:00")
        )

    def test_running_it_again_writes_nothing(self):
        self.dataset()

        backfill_gdc_declarations()

        self.assertEqual(backfill_gdc_declarations(), 0)
        self.assertEqual(DatasetSource.objects.count(), 1)

    def test_with_no_writer_catalogue_nothing_is_credited(self):
        """A declaration naming no catalogue is a claim about nobody.

        And one the next real sync could not recognise as its own, since which
        catalogue said it is part of a declaration's key -- so it would leave
        two. The sync that follows a re-designated writer records the
        declaration itself, which is why nothing is lost by waiting.
        """
        GlobalDiscoveryCatalogue.objects.update(is_writer=False)
        self.dataset()

        self.assertEqual(backfill_gdc_declarations(), 0)
        self.assertEqual(DatasetSource.objects.count(), 0)

    def test_a_dataset_the_traffic_declared_is_not_given_to_the_catalogue(self):
        """Only what the catalogue wrote is the catalogue's to be credited with.

        A dataset created from a message is one no catalogue has ever named,
        and a backfill that swept it up would invent the very disagreement the
        declarations exist to report.
        """
        observed = self.dataset("aws810")
        record_declaration(observed, DatasetSource.OBSERVED)

        backfill_gdc_declarations()

        self.assertEqual(
            set(observed.sources.values_list("source_type", flat=True)),
            {DatasetSource.OBSERVED},
        )
