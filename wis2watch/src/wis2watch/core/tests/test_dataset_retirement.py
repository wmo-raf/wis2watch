"""Retiring a dataset the centre has stopped declaring, and moving its history.

The cases here are the region's own. Rwanda's catalogue carries
``kedehn``, the centre's own metadata does not, and every message that arrived
on the centre's synop topic for thirty days was filed under it -- 995 rollup
rows and 67,685 messages attributed to a record its publisher says is not
theirs. What the centre does declare on that topic is ``aws810``, so the
counts are its counts, and the mis-attribution was this tool's resolver rather
than anything the data claimed.

So the two halves of a retirement are asserted apart. **What is retired** is a
judgement about declarations: a catalogue carries it, the centre has answered,
and the centre does not. **Where its history goes** is a correction to an
attribution, and is only made where the centre leaves no doubt about who
earned it -- Djibouti declares ``metar`` and ``speci`` on one topic, and a
run that guessed between them would write a wrong history that reads exactly
like a right one.
"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone as dj_timezone

from wis2watch.core.models import (
    CadenceBaseline,
    Dataset,
    DatasetSource,
    GlobalDiscoveryCatalogue,
    HourlyRollup,
    MessageSource,
    NotificationMessage,
    Station,
    SyncLog,
    WIS2Node,
)
from wis2watch.core.node_datasets import sync_node_datasets
from wis2watch.core.rollups import rollup_hours

from .support import at, failing_fetch, pages, published

NOW = at("2026-09-01T00:00:00")

SYNOP = "origin/a/wis2/rw-rma/data/core/weather/surface-based-observations/synop"
TEMP = "origin/a/wis2/rw-rma/data/core/weather/surface-based-observations/temp"

#: The dataset the catalogue carries and the centre does not.
GHOST = "urn:wmo:md:rw-rma:kedehn"

#: What the centre declares on the same topic, and therefore what the traffic
#: filed under the ghost was really carrying.
SUCCESSOR = "urn:wmo:md:rw-rma:aws810"


def feature(identifier, topic=SYNOP, **properties):
    """One record as a centre's own discovery metadata serves it."""
    return {
        "properties": {
            "identifier": identifier,
            "title": f"Observations ({identifier})",
            "wmo:dataPolicy": "core",
            "wmo:topicHierarchy": topic,
            **properties,
        }
    }


def declaring(*records):
    """A discovery response declaring exactly these records."""
    return {"features": list(records)}


class RetirementTestCase(TestCase):
    def setUp(self):
        self.node = WIS2Node.objects.create(
            centre_id="rw-rma",
            name="Rwanda Meteorology Agency",
            base_url="https://wis2.rma.example.int",
        )
        self.catalogue = GlobalDiscoveryCatalogue.objects.create(
            centre_id="ca-eccc-msc-global-discovery",
            name="Canadian GDC",
            base_url="https://wis2-gdc.weather.gc.ca",
            is_writer=True,
        )
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )

    def catalogued(self, identifier, topic=SYNOP, **fields):
        """A dataset as the catalogue registered it, declared by it alone."""
        dataset = Dataset.objects.create(
            node=self.node,
            identifier=identifier,
            title=identifier,
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy=topic,
            raw_json={"identifier": identifier},
            **fields,
        )

        DatasetSource.objects.create(
            dataset=dataset,
            source_type=DatasetSource.GDC,
            catalogue=self.catalogue,
            last_seen=at("2026-08-01T00:00:00"),
        )

        return dataset

    def declared_once(self, dataset, last_seen=at("2026-08-01T00:00:00")):
        """A centre's own declaration, as an earlier answer of its left it."""
        return DatasetSource.objects.create(
            dataset=dataset,
            source_type=DatasetSource.NODE,
            last_seen=last_seen,
        )

    def observed(self, identifier, topic=SYNOP):
        """A dataset nothing declared, created from the traffic that named it."""
        dataset = Dataset.objects.create(
            node=self.node,
            identifier=identifier,
            wmo_topic_hierarchy=topic,
            raw_json={},
        )

        DatasetSource.objects.create(
            dataset=dataset,
            source_type=DatasetSource.OBSERVED,
            last_seen=at("2026-08-31T00:00:00"),
        )

        return dataset

    def sync(self, *records, node=None):
        """One run of the centre's own metadata, declaring these records."""
        return sync_node_datasets(
            node or self.node, fetch=pages(declaring(*records))
        )

    def hours(self, dataset, count, *, station=None, messages=1, source=None):
        """``count`` consecutive hours of traffic filed under a dataset."""
        return [
            published(
                self.node,
                source=source or self.global_broker,
                hour=NOW - timedelta(hours=hour),
                dataset=dataset,
                station=station,
                messages=messages,
            )
            for hour in range(count)
        ]

    def notification(self, dataset, *, hour=NOW):
        """One message as the ingest stored it, attributed to a dataset."""
        return NotificationMessage.objects.create(
            source=self.global_broker,
            node=self.node,
            dataset=dataset,
            notification_id=f"{dataset.identifier}-{hour.isoformat()}",
            topic=dataset.wmo_topic_hierarchy,
            time=hour,
            raw_json={},
        )

    def status_of(self, identifier):
        return Dataset.objects.get(node=self.node, identifier=identifier).status

    def rollup_counts(self):
        """How many rollup rows each dataset holds, keyed by identifier."""
        return {
            dataset.identifier: dataset.rollups.count()
            for dataset in Dataset.objects.filter(node=self.node)
        }


class RetiredDatasetTests(RetirementTestCase):
    """A centre that has answered decides which of its datasets still exist."""

    def test_a_dataset_the_centre_no_longer_declares_is_retired(self):
        self.catalogued(GHOST)

        self.sync(feature(SUCCESSOR))

        self.assertEqual(self.status_of(GHOST), Dataset.INACTIVE)

    def test_a_dataset_the_centre_still_declares_stays_active(self):
        self.catalogued(GHOST)

        self.sync(feature(GHOST), feature(SUCCESSOR))

        self.assertEqual(self.status_of(GHOST), Dataset.ACTIVE)

    def test_a_dataset_the_centre_declared_last_month_and_not_today_is_retired(self):
        """What retires it is the answer in hand, not the declarations on
        file: a stored declaration is never expired, so a rule that read one
        would only ever retire what the centre had never declared at all."""
        ghost = self.catalogued(GHOST)
        self.declared_once(ghost)

        self.sync(feature(SUCCESSOR))

        self.assertEqual(self.status_of(GHOST), Dataset.INACTIVE)

    def test_a_retired_dataset_no_longer_carries_the_centres_declaration(self):
        """The centre has just been found not to declare it, and a row saying
        it does would have the divergence report reading agreement."""
        ghost = self.catalogued(GHOST)
        self.declared_once(ghost)

        self.sync(feature(SUCCESSOR))

        self.assertEqual(
            set(ghost.sources.values_list("source_type", flat=True)),
            {DatasetSource.GDC},
        )

    def test_a_dataset_no_catalogue_carries_is_not_retired(self):
        """Traffic under a record nobody registered is a finding, not a ghost.

        Nothing has claimed it exists but the centre's own wire, and retiring
        it would take the evidence off the very report that is meant to carry
        it.
        """
        self.observed("urn:wmo:md:rw-rma:heard-only")

        self.sync(feature(SUCCESSOR))

        self.assertEqual(
            self.status_of("urn:wmo:md:rw-rma:heard-only"), Dataset.ACTIVE
        )

    def test_a_retired_dataset_keeps_its_row_and_what_declared_it(self):
        ghost = self.catalogued(GHOST)

        self.sync(feature(SUCCESSOR))

        self.assertEqual(Dataset.objects.filter(pk=ghost.pk).count(), 1)
        self.assertEqual(
            set(ghost.sources.values_list("source_type", flat=True)),
            {DatasetSource.GDC},
        )

    def test_another_centres_dataset_is_not_retired_by_this_ones_answer(self):
        burundi = WIS2Node.objects.create(centre_id="bi-igebu", name="IGEBU")
        theirs = Dataset.objects.create(
            node=burundi, identifier="urn:wmo:md:bi-igebu:synop", raw_json={}
        )
        DatasetSource.objects.create(
            dataset=theirs,
            source_type=DatasetSource.GDC,
            catalogue=self.catalogue,
            last_seen=at("2026-08-01T00:00:00"),
        )

        self.sync(feature(SUCCESSOR))

        theirs.refresh_from_db()

        self.assertEqual(theirs.status, Dataset.ACTIVE)


class UnansweredCentreTests(RetirementTestCase):
    """Silence is not an answer, and neither is a record nothing could store."""

    def test_a_centre_that_could_not_be_reached_retires_nothing(self):
        self.catalogued(GHOST)

        sync_log = sync_node_datasets(
            self.node, fetch=failing_fetch("connection refused")
        )

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertEqual(self.status_of(GHOST), Dataset.ACTIVE)
        self.assertEqual(sync_log.items_retired, 0)

    def test_a_centre_that_declared_nothing_at_all_retires_nothing(self):
        """An empty answer and a broken endpoint are the same answer."""
        self.catalogued(GHOST)

        sync_log = self.sync()

        self.assertEqual(sync_log.status, SyncLog.SUCCESS)
        self.assertEqual(self.status_of(GHOST), Dataset.ACTIVE)

    def test_a_record_the_run_could_not_store_is_still_a_record_it_read(self):
        """A run that stepped over the ghost's own record has not been told
        the centre stopped declaring it -- only that this tool could not write
        down that it does."""
        self.catalogued(GHOST)

        def refuse_the_ghost(dataset, *args, **kwargs):
            if dataset.identifier == GHOST:
                raise RuntimeError("refused")

            return None, True

        with mock.patch(
            "wis2watch.core.node_datasets.record_declaration",
            side_effect=refuse_the_ghost,
        ):
            sync_log = self.sync(feature(GHOST), feature(SUCCESSOR))

        self.assertEqual(sync_log.status, SyncLog.PARTIAL)
        self.assertEqual(self.status_of(GHOST), Dataset.ACTIVE)


class ReinstatementTests(RetirementTestCase):
    """Only the centre reinstates what only the centre retired."""

    def test_a_centre_declaring_a_retired_dataset_again_reinstates_it(self):
        self.catalogued(GHOST)

        self.sync(feature(SUCCESSOR))
        self.sync(feature(GHOST), feature(SUCCESSOR))

        self.assertEqual(self.status_of(GHOST), Dataset.ACTIVE)

    def test_a_reinstated_dataset_can_be_retired_again(self):
        """Reinstating writes a declaration, and a rule that read declarations
        would let that one stop the dataset ever being retired again."""
        self.catalogued(GHOST)

        self.sync(feature(SUCCESSOR))
        self.sync(feature(GHOST), feature(SUCCESSOR))
        self.sync(feature(SUCCESSOR))

        self.assertEqual(self.status_of(GHOST), Dataset.INACTIVE)

    def test_a_reinstated_dataset_is_not_retired_again_by_the_same_run(self):
        self.catalogued(GHOST)
        self.sync(feature(SUCCESSOR))

        sync_log = self.sync(feature(GHOST), feature(SUCCESSOR))

        self.assertEqual(sync_log.items_retired, 0)


class RepointedHistoryTests(RetirementTestCase):
    """The counts follow the dataset that really earned them."""

    def test_history_moves_where_the_centre_declares_one_dataset_on_the_topic(self):
        ghost = self.catalogued(GHOST)
        self.hours(ghost, 3)

        self.sync(feature(SUCCESSOR))

        self.assertEqual(
            self.rollup_counts(), {GHOST: 0, SUCCESSOR: 3}
        )

    def test_a_moved_hour_keeps_its_count_and_the_station_that_earned_it(self):
        ghost = self.catalogued(GHOST)
        station, _ = Station.objects.get_or_create(wigos_id="0-646-0-64001")
        self.hours(ghost, 1, station=station, messages=244)

        self.sync(feature(SUCCESSOR))

        moved = HourlyRollup.objects.get(dataset__identifier=SUCCESSOR)

        self.assertEqual(moved.message_count, 244)
        self.assertEqual(moved.station, station)
        self.assertEqual(moved.hour, NOW.replace(minute=0, second=0, microsecond=0))

    def test_an_hour_both_datasets_counted_is_added_up_rather_than_duplicated(self):
        """One bucket per hour, vantage point and station is the whole of what
        makes a count readable, so the two rows have to become one."""
        ghost = self.catalogued(GHOST)
        successor = self.catalogued(SUCCESSOR)
        self.hours(ghost, 1, messages=40)
        self.hours(successor, 1, messages=2)

        sync_log = self.sync(feature(SUCCESSOR))

        rollup = HourlyRollup.objects.get(dataset__identifier=SUCCESSOR)

        self.assertEqual(rollup.message_count, 42)
        self.assertEqual(HourlyRollup.objects.count(), 1)
        self.assertEqual(sync_log.rollups_repointed, 1)

    def test_the_history_of_another_dataset_on_the_topic_is_left_alone(self):
        ghost = self.catalogued(GHOST)
        other = self.catalogued("urn:wmo:md:rw-rma:temp", topic=TEMP)
        self.hours(ghost, 2)
        self.hours(other, 5)

        self.sync(feature(SUCCESSOR), feature("urn:wmo:md:rw-rma:temp", topic=TEMP))

        self.assertEqual(other.rollups.count(), 5)

    def test_a_dataset_the_centre_has_itself_stopped_declaring_is_no_successor(self):
        """A stale declaration on the topic would send a centre's whole
        observation feed to a dataset it no longer serves."""
        ghost = self.catalogued(GHOST)
        self.hours(ghost, 3)
        self.declared_once(self.catalogued("urn:wmo:md:rw-rma:also-gone"))

        self.sync(feature(SUCCESSOR))

        self.assertEqual(self.rollup_counts()[SUCCESSOR], 3)

    def test_the_ghosts_cadence_baseline_is_deleted_rather_than_moved(self):
        """The rhythm was learned from traffic that was never the ghost's, and
        the scheduled run relearns it against the corrected rollups."""
        ghost = self.catalogued(GHOST)
        self.hours(ghost, 3)
        CadenceBaseline.objects.create(
            dataset=ghost,
            interval_hours=2.0,
            observations=244,
            learned_at=dj_timezone.now(),
        )

        self.sync(feature(SUCCESSOR))

        self.assertEqual(CadenceBaseline.objects.count(), 0)


    def test_the_raw_notifications_move_with_the_counts_they_were_summed_into(self):
        """The rollups are derived from these, and a scheduled run recomputes
        the last forty-eight hours from them: messages left behind would
        rebuild the ghost's buckets and write the successor's back down."""
        ghost = self.catalogued(GHOST)
        message = self.notification(ghost)

        self.sync(feature(SUCCESSOR))

        message.refresh_from_db()

        self.assertEqual(message.dataset.identifier, SUCCESSOR)

    def test_a_recompute_after_a_retirement_leaves_the_counts_where_they_moved(self):
        """The whole of what re-pointing is for, asserted against the run that
        would undo it."""
        ghost = self.catalogued(GHOST)
        self.notification(ghost)

        self.sync(feature(SUCCESSOR))

        rollup_hours(since=NOW - timedelta(hours=2), until=NOW + timedelta(hours=1))

        self.assertEqual(self.rollup_counts(), {GHOST: 0, SUCCESSOR: 1})


class AmbiguousHistoryTests(RetirementTestCase):
    """Where the centre leaves a choice, history stays where it is."""

    def test_history_stays_put_where_several_datasets_claim_the_topic(self):
        ghost = self.catalogued(GHOST)
        self.hours(ghost, 4)

        self.sync(
            feature("urn:wmo:md:rw-rma:metar"), feature("urn:wmo:md:rw-rma:speci")
        )

        self.assertEqual(ghost.rollups.count(), 4)

    def test_the_dataset_is_retired_even_where_its_history_cannot_move(self):
        self.catalogued(GHOST)

        self.sync(
            feature("urn:wmo:md:rw-rma:metar"), feature("urn:wmo:md:rw-rma:speci")
        )

        self.assertEqual(self.status_of(GHOST), Dataset.INACTIVE)

    def test_which_datasets_the_ambiguity_lies_between_is_recorded(self):
        ghost = self.catalogued(GHOST)
        self.hours(ghost, 4)

        sync_log = self.sync(
            feature("urn:wmo:md:rw-rma:metar"), feature("urn:wmo:md:rw-rma:speci")
        )

        (retired,) = sync_log.retired

        self.assertEqual(retired["item"], GHOST)
        self.assertEqual(retired["moved_to"], "")
        self.assertEqual(retired["rollups_moved"], 0)
        self.assertEqual(
            retired["claimed_by"],
            ["urn:wmo:md:rw-rma:metar", "urn:wmo:md:rw-rma:speci"],
        )
        self.assertEqual(sync_log.rollups_repointed, 0)

    def test_a_ghost_on_a_topic_the_centre_no_longer_declares_keeps_its_history(self):
        ghost = self.catalogued(GHOST)
        self.hours(ghost, 2)

        sync_log = self.sync(feature("urn:wmo:md:rw-rma:temp", topic=TEMP))

        (retired,) = sync_log.retired

        self.assertEqual(ghost.rollups.count(), 2)
        self.assertEqual(retired["claimed_by"], [])
        self.assertEqual(retired["moved_to"], "")


class RetirementSyncLogTests(RetirementTestCase):
    """What a run retired is counted, not only logged."""

    def test_what_was_retired_and_how_much_moved_is_on_the_log(self):
        ghost = self.catalogued(GHOST)
        self.hours(ghost, 3)

        sync_log = self.sync(feature(SUCCESSOR))

        self.assertEqual(sync_log.items_retired, 1)
        self.assertEqual(sync_log.rollups_repointed, 3)
        self.assertEqual(
            sync_log.retired,
            [
                {
                    "item": GHOST,
                    "moved_to": SUCCESSOR,
                    "rollups_moved": 3,
                    "claimed_by": [],
                }
            ],
        )

    def test_a_run_that_retired_nothing_says_so(self):
        self.catalogued(GHOST)

        sync_log = self.sync(feature(GHOST))

        self.assertEqual(sync_log.items_retired, 0)
        self.assertEqual(sync_log.rollups_repointed, 0)
        self.assertEqual(sync_log.retired, [])

    def test_a_second_run_over_the_same_centre_moves_nothing(self):
        ghost = self.catalogued(GHOST)
        self.hours(ghost, 3)

        self.sync(feature(SUCCESSOR))
        again = self.sync(feature(SUCCESSOR))

        self.assertEqual(again.items_retired, 0)
        self.assertEqual(again.rollups_repointed, 0)
        self.assertEqual(again.retired, [])
        self.assertEqual(self.rollup_counts(), {GHOST: 0, SUCCESSOR: 3})
