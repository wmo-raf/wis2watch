"""Learning what each dataset's normal publishing rhythm is, from its history.

This is the number every silence finding is judged against, so getting it
wrong is not an error anyone sees: it is a monthly climate summary reported as
broken every afternoon, or a centre that publishes hourly going dark for a
fortnight without a word. Both look entirely plausible on the screen.

The cases here are the ones that would produce a confident wrong interval --
the same hour counted once per station or once per vantage point, history from
outside the window, a dataset with too little rhythm to learn from at all --
and the one thing a run must never do, which is forget the baseline of a
dataset that has stopped publishing.
"""

from datetime import timedelta

from django.test import TestCase

from wis2watch.core.cadence import learn_cadence_baselines
from wis2watch.core.models import (
    CadenceBaseline,
    Dataset,
    HourlyRollup,
    MessageSource,
    Station,
    WIS2Node,
)
from wis2watch.core.tests.support import at, origin_broker


NOW = at("2026-08-11T12:00:00")


class CadenceTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

    def dataset(self, name="synop", node=None):
        node = node or self.kenya

        return Dataset.objects.create(
            node=node,
            identifier=f"urn:wmo:md:{node.centre_id}:{name}",
            title=name,
            wmo_data_policy="core",
            wmo_topic_hierarchy=f"origin/a/wis2/{node.centre_id}/data/core/{name}",
            raw_json={},
        )

    def station(self, wigos_id):
        station, _ = Station.objects.get_or_create(wigos_id=wigos_id)

        return station

    def published_in(self, dataset, hours, *, source=None, station=None):
        """Rollups saying the dataset was seen publishing in each of these hours."""
        for hour in hours:
            HourlyRollup.objects.create(
                hour=hour,
                source=source or self.global_broker,
                node=dataset.node,
                dataset=dataset,
                station=station,
                message_count=1,
            )

    def every(self, spacing_hours, times, *, ending=NOW):
        """A run of publishing hours at a fixed spacing, ending when it says."""
        return [
            ending - timedelta(hours=spacing_hours * step)
            for step in reversed(range(times))
        ]

    def after_gaps(self, gaps, *, ending=NOW):
        """Publishing hours ending at ``ending``, spaced by the gaps given."""
        hours = [ending]

        for gap in reversed(gaps):
            hours.append(hours[-1] - timedelta(hours=gap))

        return list(reversed(hours))

    def learn(self, **kwargs):
        kwargs.setdefault("now", NOW)

        return learn_cadence_baselines(**kwargs)

    def baseline(self, dataset):
        return CadenceBaseline.objects.filter(dataset=dataset).first()


class LearnedIntervalTests(CadenceTestCase):
    """What a dataset's own history says about how often it publishes."""

    def test_a_dataset_publishing_every_hour_is_expected_every_hour(self):
        synop = self.dataset()
        self.published_in(synop, self.every(1, 24))

        self.learn()

        self.assertEqual(self.baseline(synop).interval_hours, 1)

    def test_a_dataset_publishing_every_six_hours_is_expected_every_six(self):
        synop = self.dataset()
        self.published_in(synop, self.every(6, 20))

        self.learn()

        self.assertEqual(self.baseline(synop).interval_hours, 6)

    def test_a_dataset_publishing_daily_is_expected_daily(self):
        climate = self.dataset("climate")
        self.published_in(climate, self.every(24, 30))

        self.learn()

        self.assertEqual(self.baseline(climate).interval_hours, 24)

    def test_each_dataset_is_judged_against_its_own_rhythm(self):
        synop = self.dataset("synop")
        climate = self.dataset("climate")
        self.published_in(synop, self.every(1, 24))
        self.published_in(climate, self.every(24, 30))

        self.learn()

        self.assertEqual(self.baseline(synop).interval_hours, 1)
        self.assertEqual(self.baseline(climate).interval_hours, 24)

    def test_the_expectation_allows_for_the_gaps_a_dataset_regularly_has(self):
        """Judged on its ordinary behaviour, not on its best hour.

        A dataset that misses a couple of hours now and again is not broken
        when it misses them again, so the expectation is a high percentile of
        its own gaps rather than the shortest of them.
        """
        synop = self.dataset()
        self.published_in(synop, self.after_gaps([1] * 18 + [3, 3]))

        self.learn()

        self.assertEqual(self.baseline(synop).interval_hours, 3)

    def test_a_single_outage_does_not_become_the_expectation(self):
        """One bad week must not make a dataset unreportable ever after."""
        synop = self.dataset()
        self.published_in(synop, self.after_gaps([1] * 20 + [480] + [1] * 19))

        self.learn()

        self.assertEqual(self.baseline(synop).interval_hours, 1)

    def test_the_interval_says_how_many_gaps_it_was_learned_from(self):
        synop = self.dataset()
        self.published_in(synop, self.every(1, 24))

        self.learn()

        self.assertEqual(self.baseline(synop).observations, 23)

    def test_the_run_records_when_it_read_the_history(self):
        synop = self.dataset()
        self.published_in(synop, self.every(1, 24))

        self.learn()

        self.assertEqual(self.baseline(synop).learned_at, NOW)


class ObservationTests(CadenceTestCase):
    """What counts as one observation of a dataset publishing."""

    def test_a_dataset_publishing_from_many_stations_still_publishes_hourly(self):
        """An hour is one observation however many stations reported in it."""
        synop = self.dataset()
        hours = self.every(1, 24)

        for wigos_id in ["0-20000-0-63708", "0-20000-0-63710", "0-20000-0-63741"]:
            self.published_in(synop, hours, station=self.station(wigos_id))

        self.learn()
        baseline = self.baseline(synop)

        self.assertEqual(baseline.interval_hours, 1)
        self.assertEqual(baseline.observations, 23)

    def test_the_same_hour_seen_at_two_vantage_points_is_one_observation(self):
        synop = self.dataset()
        hours = self.every(1, 24)
        self.published_in(synop, hours)
        self.published_in(synop, hours, source=origin_broker(self.kenya))

        self.learn()
        baseline = self.baseline(synop)

        self.assertEqual(baseline.interval_hours, 1)
        self.assertEqual(baseline.observations, 23)

    def test_a_centre_heard_only_at_its_own_broker_still_has_a_rhythm(self):
        """What the world received is the propagation question, not this one."""
        synop = self.dataset()
        self.published_in(synop, self.every(6, 20), source=origin_broker(self.kenya))

        self.learn()

        self.assertEqual(self.baseline(synop).interval_hours, 6)


class WindowTests(CadenceTestCase):
    """Which history a run learns from."""

    def test_history_older_than_the_window_is_not_learned_from(self):
        synop = self.dataset()
        self.published_in(synop, self.every(1, 24, ending=NOW - timedelta(days=200)))

        self.learn()

        self.assertIsNone(self.baseline(synop))

    def test_the_window_is_an_argument(self):
        synop = self.dataset()
        self.published_in(synop, self.every(1, 24, ending=NOW - timedelta(days=20)))

        self.learn(window_days=10)

        self.assertIsNone(self.baseline(synop))


class SparseHistoryTests(CadenceTestCase):
    """Datasets there is not enough of a rhythm to learn from."""

    def test_a_dataset_seen_publishing_once_learns_nothing(self):
        annual = self.dataset("annual")
        self.published_in(annual, [NOW - timedelta(days=3)])

        self.learn()

        self.assertIsNone(self.baseline(annual))

    def test_a_dataset_with_too_few_gaps_to_go_on_learns_nothing(self):
        monthly = self.dataset("monthly")
        self.published_in(monthly, self.every(24 * 30, 2))

        self.learn()

        self.assertIsNone(self.baseline(monthly))

    def test_a_dataset_never_seen_publishing_learns_nothing(self):
        self.dataset("silent")

        counts = self.learn()

        self.assertEqual(CadenceBaseline.objects.count(), 0)
        self.assertEqual(counts.learned, 0)

    def test_how_much_history_is_enough_is_an_argument(self):
        monthly = self.dataset("monthly")
        self.published_in(monthly, self.every(24 * 20, 3))

        self.learn(min_observations=2)

        self.assertEqual(self.baseline(monthly).interval_hours, 24 * 20)

    def test_traffic_no_dataset_claims_learns_nothing(self):
        for hour in self.every(1, 24):
            HourlyRollup.objects.create(
                hour=hour,
                source=self.global_broker,
                node=self.kenya,
                dataset=None,
                message_count=1,
            )

        self.learn()

        self.assertEqual(CadenceBaseline.objects.count(), 0)


class RecomputeTests(CadenceTestCase):
    """Running again, which the schedule does daily."""

    def test_running_again_updates_the_interval_rather_than_adding_another(self):
        synop = self.dataset()
        self.published_in(synop, self.every(1, 24))
        self.learn()

        HourlyRollup.objects.all().delete()
        self.published_in(synop, self.every(6, 20))
        self.learn()

        self.assertEqual(CadenceBaseline.objects.count(), 1)
        self.assertEqual(self.baseline(synop).interval_hours, 6)

    def test_a_dataset_that_has_gone_quiet_keeps_what_it_last_learned(self):
        """The moment a centre goes dark is not the moment to forget its rhythm."""
        synop = self.dataset()
        self.published_in(synop, self.every(1, 24))
        self.learn()

        self.learn(now=NOW + timedelta(days=200))
        baseline = self.baseline(synop)

        self.assertEqual(baseline.interval_hours, 1)
        self.assertEqual(baseline.learned_at, NOW)

    def test_the_run_says_what_it_learned(self):
        self.published_in(self.dataset("synop"), self.every(1, 24))
        self.published_in(self.dataset("temp"), self.every(6, 20))

        counts = self.learn()

        self.assertEqual(counts.learned, 2)
        self.assertIn("learned=2", counts.summary)
