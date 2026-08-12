"""Judging silence against what each dataset is actually expected to do.

The finding this makes is "this has gone quiet unexpectedly", and every way of
getting it wrong is expensive in the same direction: a monthly dataset
reported broken every day teaches the diagnostician to ignore the column, at
which point the tool has stopped working while continuing to look like it
works.

So the cases here are the ones where a fixed threshold gets it wrong -- an
hourly dataset and a monthly one quiet for the same five hours, a dataset with
no history to learn from, one whose learned rhythm a person knows to be wrong
-- and the boundary the whole finding turns on: quiet for longer than
expected, against quiet for not quite that long.
"""

from datetime import timedelta

from django.test import TestCase

from wis2watch.core.analysis import (
    Expectation,
    Silence,
    dataset_silence,
    silence_by_node,
)
from wis2watch.core.models import (
    CadenceBaseline,
    Dataset,
    HourlyRollup,
    MessageSource,
    WIS2Node,
)
from wis2watch.core.tests.support import at


NOW = at("2026-08-11T12:00:00")


class SilenceTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = self.node("ke-meteo")

    def node(self, centre_id):
        return WIS2Node.objects.create(centre_id=centre_id, name=centre_id.upper())

    def dataset(self, name="synop", *, node=None, expects=None, status=Dataset.ACTIVE):
        node = node or self.kenya

        return Dataset.objects.create(
            node=node,
            identifier=f"urn:wmo:md:{node.centre_id}:{name}",
            title=name,
            wmo_data_policy="core",
            wmo_topic_hierarchy=f"origin/a/wis2/{node.centre_id}/data/core/{name}",
            raw_json={},
            status=status,
            expected_interval_override_hours=expects,
        )

    def learned(self, dataset, interval_hours, observations=20):
        return CadenceBaseline.objects.create(
            dataset=dataset,
            interval_hours=interval_hours,
            observations=observations,
            learned_at=NOW - timedelta(days=1),
        )

    def last_published(self, dataset, hour):
        """A rollup saying the dataset was seen publishing in that hour."""
        return HourlyRollup.objects.create(
            hour=hour,
            source=self.global_broker,
            node=dataset.node,
            dataset=dataset,
            message_count=1,
        )

    def quiet_for(self, dataset, hours):
        """A dataset last seen publishing that many hours ago."""
        self.last_published(dataset, NOW - timedelta(hours=hours))

        return dataset

    def rows(self, **kwargs):
        kwargs.setdefault("now", NOW)

        return {row.title: row for row in dataset_silence(**kwargs)}

    def row(self, title="synop", **kwargs):
        return self.rows(**kwargs)[title]


class LearnedExpectationTests(SilenceTestCase):
    """Silence judged against what the dataset's own history said to expect."""

    def test_a_dataset_quiet_for_longer_than_its_interval_is_silent(self):
        self.learned(self.quiet_for(self.dataset(), 8), 6)

        row = self.row()

        self.assertTrue(row.is_silent)
        self.assertEqual(row.silence, Silence.SILENT)
        self.assertEqual(row.expectation, Expectation.LEARNED)
        self.assertEqual(row.expected_interval_hours, 6)

    def test_a_dataset_quiet_for_less_than_its_interval_is_not(self):
        self.learned(self.quiet_for(self.dataset(), 3), 6)

        row = self.row()

        self.assertFalse(row.is_silent)
        self.assertEqual(row.silence, Silence.ON_SCHEDULE)

    def test_an_hourly_dataset_and_a_monthly_one_quiet_the_same_five_hours(self):
        """The whole point: one of these is broken and the other is fine."""
        self.learned(self.quiet_for(self.dataset("synop"), 5), 1)
        self.quiet_for(self.dataset("climate", expects=24 * 30), 5)

        rows = self.rows()

        self.assertTrue(rows["synop"].is_silent)
        self.assertFalse(rows["climate"].is_silent)

    def test_the_hours_learned_from_are_carried_with_the_finding(self):
        self.learned(self.quiet_for(self.dataset(), 3), 6, observations=41)

        self.assertEqual(self.row().observations, 41)


class QuietTimeTests(SilenceTestCase):
    """How long a dataset has been quiet, given hourly buckets to read it from."""

    def test_quiet_is_measured_from_the_end_of_the_hour_it_last_published_in(self):
        """Within the bucket the moment is unknown; its end is what is certain."""
        self.learned(self.quiet_for(self.dataset(), 2), 6)

        row = self.row()

        self.assertEqual(row.last_active_hour, NOW - timedelta(hours=2))
        self.assertEqual(row.hours_quiet, 1)

    def test_a_dataset_publishing_in_the_hour_in_progress_is_not_quiet_at_all(self):
        self.learned(self.quiet_for(self.dataset(), 0), 1)

        self.assertEqual(self.row().hours_quiet, 0)

    def test_the_latest_hour_it_published_in_is_the_one_that_counts(self):
        synop = self.dataset()
        self.learned(synop, 6)
        self.last_published(synop, NOW - timedelta(hours=30))
        self.last_published(synop, NOW - timedelta(hours=2))

        self.assertEqual(self.row().last_active_hour, NOW - timedelta(hours=2))

    def test_publishing_at_a_centres_own_broker_counts_as_publishing(self):
        """Whether the world received it is the propagation report's question."""
        synop = self.dataset()
        self.learned(synop, 6)
        HourlyRollup.objects.create(
            hour=NOW - timedelta(hours=2),
            source=MessageSource.objects.create(
                name="ke-meteo origin broker",
                source_type=MessageSource.ORIGIN_BROKER,
                node=self.kenya,
                host="wis.ke-meteo.example.int",
            ),
            node=self.kenya,
            dataset=synop,
            message_count=1,
        )

        self.assertFalse(self.row().is_silent)


class OverrideTests(SilenceTestCase):
    """A person's expectation takes precedence over the learned one."""

    def test_an_override_tighter_than_the_learned_interval_is_what_is_judged(self):
        self.learned(self.quiet_for(self.dataset(expects=2), 5), 48)

        row = self.row()

        self.assertTrue(row.is_silent)
        self.assertEqual(row.expectation, Expectation.OVERRIDDEN)
        self.assertEqual(row.expected_interval_hours, 2)

    def test_an_override_looser_than_the_learned_interval_is_too(self):
        self.learned(self.quiet_for(self.dataset(expects=48), 5), 1)

        row = self.row()

        self.assertFalse(row.is_silent)
        self.assertEqual(row.expected_interval_hours, 48)

    def test_a_dataset_with_too_little_history_to_learn_from_can_be_told(self):
        """The sparse-history case the override exists for."""
        self.quiet_for(self.dataset("monthly", expects=24 * 30), 24 * 40)

        row = self.row("monthly")

        self.assertTrue(row.is_silent)
        self.assertEqual(row.expectation, Expectation.OVERRIDDEN)

    def test_without_an_override_the_learned_interval_stands(self):
        self.learned(self.quiet_for(self.dataset(), 8), 6)

        self.assertEqual(self.row().expectation, Expectation.LEARNED)


class NothingToJudgeTests(SilenceTestCase):
    """Datasets there is no expectation to judge against."""

    def test_a_dataset_with_nothing_learned_and_no_override_is_never_silent(self):
        """A tool that cannot say is worth more than one that guesses."""
        self.quiet_for(self.dataset(), 24 * 40)

        row = self.row()

        self.assertFalse(row.is_silent)
        self.assertEqual(row.silence, Silence.UNKNOWN)
        self.assertEqual(row.expectation, Expectation.UNKNOWN)
        self.assertIsNone(row.expected_interval_hours)

    def test_a_dataset_never_seen_publishing_at_all_still_reports_what_is_known(self):
        self.dataset()

        row = self.row()

        self.assertIsNone(row.last_active_hour)
        self.assertEqual(row.silence, Silence.UNKNOWN)


class NeverHeardFromTests(SilenceTestCase):
    """Datasets nothing has ever been seen publishing.

    What such a dataset is measured against is how far this tool's own records
    go back, because that is the whole of what its absence is evidence of.
    """

    def records_reaching_back(self, hours):
        """Records of the region publishing, starting that long ago."""
        self.last_published(self.dataset("something-else"), NOW - timedelta(hours=hours))

    def test_a_dataset_never_seen_publishing_is_silent(self):
        self.records_reaching_back(40)
        self.learned(self.dataset(), 6)

        row = self.row()

        self.assertIsNone(row.last_active_hour)
        self.assertTrue(row.is_silent)

    def test_it_is_quiet_for_as_long_as_the_records_go_back(self):
        self.records_reaching_back(40)
        self.learned(self.dataset(), 6)

        self.assertEqual(self.row().hours_quiet, 40)

    def test_a_dataset_that_may_simply_not_be_due_yet_is_not_called_silent(self):
        """Expected less often than the records go back: absence proves nothing."""
        self.records_reaching_back(40)
        self.dataset("annual", expects=24 * 365)

        self.assertFalse(self.row("annual").is_silent)

    def test_a_tool_holding_no_records_at_all_calls_nothing_silent(self):
        """A fresh deployment has not witnessed an outage, only its own start."""
        self.learned(self.dataset(), 6)

        self.assertFalse(self.row().is_silent)


class LongMemoryTests(SilenceTestCase):
    """How far back the last publication is looked for: all the way.

    A trailing window is exactly what a monthly or yearly dataset defeats --
    bound the search and a dataset's absence can never exceed what was looked
    at, so the rarest datasets, which are the ones the override exists for,
    would be the ones that could never be found silent.
    """

    def test_a_publication_older_than_any_window_is_still_the_one_it_last_made(self):
        monthly = self.dataset("monthly", expects=24 * 30)
        self.last_published(monthly, NOW - timedelta(days=200))

        row = self.row("monthly")

        self.assertEqual(row.last_active_hour, NOW - timedelta(days=200))
        self.assertTrue(row.is_silent)

    def test_a_yearly_dataset_still_within_its_interval_is_not_silent(self):
        annual = self.dataset("annual", expects=24 * 365)
        self.last_published(annual, NOW - timedelta(days=200))

        self.assertFalse(self.row("annual").is_silent)


class ScopeTests(SilenceTestCase):
    """Which datasets are judged, and whose."""

    def test_a_dataset_the_catalogue_has_dropped_is_not_judged(self):
        self.learned(self.quiet_for(self.dataset("gone", status=Dataset.DELETED), 40), 6)

        self.assertEqual(self.rows(), {})

    def test_a_dataset_the_registry_calls_inactive_is_not_judged(self):
        self.learned(
            self.quiet_for(self.dataset("paused", status=Dataset.INACTIVE), 40), 6
        )

        self.assertEqual(self.rows(), {})

    def test_the_findings_can_be_narrowed_to_one_node(self):
        djibouti = self.node("dj-anm")
        self.learned(self.quiet_for(self.dataset("synop"), 8), 6)
        self.learned(self.quiet_for(self.dataset("temp", node=djibouti), 8), 6)

        self.assertEqual(list(self.rows(node=djibouti)), ["temp"])

    def test_each_finding_names_the_centre_it_belongs_to(self):
        self.learned(self.quiet_for(self.dataset(), 8), 6)

        row = self.row()

        self.assertEqual(row.centre_id, "ke-meteo")
        self.assertEqual(row.node_id, self.kenya.pk)


class OrderingTests(SilenceTestCase):
    """What a diagnostician reading the list needs to see first."""

    def test_the_silent_come_first_and_the_furthest_overdue_before_them(self):
        self.learned(self.quiet_for(self.dataset("fine"), 1), 6)
        self.learned(self.quiet_for(self.dataset("late"), 9), 6)
        self.learned(self.quiet_for(self.dataset("later"), 48), 6)

        titles = [row.title for row in dataset_silence(now=NOW)]

        self.assertEqual(titles[:2], ["later", "late"])

    def test_what_cannot_be_judged_comes_last(self):
        self.dataset("unjudgeable")
        self.learned(self.quiet_for(self.dataset("fine"), 1), 6)

        titles = [row.title for row in dataset_silence(now=NOW)]

        self.assertEqual(titles[-1], "unjudgeable")


class NodeSilenceTests(SilenceTestCase):
    """What the overview says about a centre as a whole."""

    def by_node(self, **kwargs):
        kwargs.setdefault("now", NOW)

        return silence_by_node(**kwargs)

    def test_a_centre_with_a_dataset_past_its_expectation_is_silent(self):
        self.learned(self.quiet_for(self.dataset("synop"), 8), 6)
        self.learned(self.quiet_for(self.dataset("temp"), 1), 6)

        node = self.by_node()[self.kenya.pk]

        self.assertEqual(node.silence, Silence.SILENT)
        self.assertEqual(node.silent_dataset_count, 1)
        self.assertEqual(node.judged_dataset_count, 2)

    def test_a_centre_whose_datasets_are_all_within_their_expectations_is_not(self):
        self.learned(self.quiet_for(self.dataset("synop"), 1), 6)

        node = self.by_node()[self.kenya.pk]

        self.assertEqual(node.silence, Silence.ON_SCHEDULE)
        self.assertEqual(node.silent_dataset_count, 0)

    def test_a_centre_with_nothing_that_can_be_judged_says_so(self):
        self.quiet_for(self.dataset("synop"), 500)

        node = self.by_node()[self.kenya.pk]

        self.assertEqual(node.silence, Silence.UNKNOWN)
        self.assertEqual(node.judged_dataset_count, 0)

    def test_a_centre_with_no_datasets_at_all_is_not_reported_on(self):
        self.assertEqual(self.by_node(), {})

    def test_each_centre_carries_its_own_datasets_verdict(self):
        djibouti = self.node("dj-anm")
        self.learned(self.quiet_for(self.dataset("synop"), 8), 6)
        self.learned(self.quiet_for(self.dataset("temp", node=djibouti), 1), 6)

        by_node = self.by_node()

        self.assertEqual(by_node[self.kenya.pk].silence, Silence.SILENT)
        self.assertEqual(by_node[djibouti.pk].silence, Silence.ON_SCHEDULE)
