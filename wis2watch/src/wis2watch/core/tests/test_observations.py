"""What a centre's observation datasets amount to, and what they leave out.

This installation's front page answers one question -- are observations coming
out of these centres -- so the fold here is what every verdict downstream is
computed from. The failure worth guarding against is a quiet one: a centre
whose aerodrome reports are flowing while its synops died three days ago, read
as though the traffic said something about the observations.

The classification itself is `interpretation.topics`' and is tested there.
What these hold is the join: that the rule reaches the fold, that a
non-observation dataset never decides anything here, and that a centre
declaring no observations at all is absent rather than reported quiet.
"""

from datetime import timedelta

from django.test import TestCase

from wis2watch.core.analysis import (
    NodeObservations,
    Silence,
    observations_by_node,
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

#: A category the topic hierarchy files observations under, and one it does
#: not. Spelled as whole topics rather than assembled from parts, because what
#: is being asserted is that the fold reads a real published topic.
OBSERVATIONS = "surface-based-observations"
ADVISORIES = "advisories-warnings"


class ObservationsTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = self.node("ke-meteo")

    def node(self, centre_id):
        return WIS2Node.objects.create(centre_id=centre_id, name=centre_id.upper())

    def dataset(
        self,
        name="synop",
        *,
        node=None,
        category=OBSERVATIONS,
        expects=None,
        status=Dataset.ACTIVE,
    ):
        node = node or self.kenya

        return Dataset.objects.create(
            node=node,
            identifier=f"urn:wmo:md:{node.centre_id}:{name}",
            title=name,
            wmo_data_policy="core",
            wmo_topic_hierarchy=(
                f"origin/a/wis2/{node.centre_id}/data/core/weather/{category}/{name}"
            ),
            raw_json={},
            status=status,
            expected_interval_override_hours=expects,
        )

    def learned(self, dataset, interval_hours):
        CadenceBaseline.objects.create(
            dataset=dataset,
            interval_hours=interval_hours,
            observations=20,
            learned_at=NOW - timedelta(days=1),
        )

        return dataset

    def quiet_for(self, dataset, hours):
        """A dataset last seen publishing that many hours ago."""
        HourlyRollup.objects.create(
            hour=NOW - timedelta(hours=hours),
            source=self.global_broker,
            node=dataset.node,
            dataset=dataset,
            message_count=1,
        )

        return dataset

    def by_node(self, **kwargs):
        kwargs.setdefault("now", NOW)

        return observations_by_node(**kwargs)

    def kenyan(self, **kwargs):
        return self.by_node(**kwargs)[self.kenya.pk]


class ScopeTests(ObservationsTestCase):
    """Which of a centre's datasets are folded in, and which are not."""

    def test_a_centre_publishing_observations_is_folded_from_them(self):
        self.learned(self.quiet_for(self.dataset("synop"), 8), 6)

        self.assertEqual(self.kenyan().dataset_count, 1)

    def test_a_centre_publishing_no_observations_is_absent(self):
        self.learned(self.quiet_for(self.dataset("metar", category=ADVISORIES), 8), 6)

        self.assertEqual(self.by_node(), {})

    def test_a_centre_with_no_datasets_at_all_is_absent(self):
        self.assertEqual(self.by_node(), {})

    def test_a_non_observation_dataset_is_left_out_of_a_centre_that_has_both(self):
        self.learned(self.quiet_for(self.dataset("synop"), 1), 6)
        self.learned(self.quiet_for(self.dataset("metar", category=ADVISORIES), 1), 6)

        self.assertEqual(self.kenyan().dataset_count, 1)

    def test_a_withdrawn_observation_dataset_is_not_one_the_centre_still_publishes(self):
        self.quiet_for(self.dataset("synop", status=Dataset.INACTIVE), 1)

        self.assertEqual(self.by_node(), {})

    def test_a_topic_this_tool_cannot_read_is_not_an_observation(self):
        malformed = self.dataset("synop")
        malformed.wmo_topic_hierarchy = "not-a-topic"
        malformed.save(update_fields=["wmo_topic_hierarchy"])
        self.quiet_for(malformed, 1)

        self.assertEqual(self.by_node(), {})


class LastActiveHourTests(ObservationsTestCase):
    """When the centre's observations were last seen, and nothing else."""

    def test_the_most_recent_hour_any_observation_published_in(self):
        self.quiet_for(self.dataset("synop"), 8)
        self.quiet_for(self.dataset("temp"), 3)

        self.assertEqual(
            self.kenyan().last_active_hour, NOW - timedelta(hours=3)
        )

    def test_a_non_observation_publishing_does_not_answer_for_the_centre(self):
        self.quiet_for(self.dataset("synop"), 30)
        self.quiet_for(self.dataset("metar", category=ADVISORIES), 1)

        self.assertEqual(
            self.kenyan().last_active_hour, NOW - timedelta(hours=30)
        )

    def test_a_centre_whose_observations_have_never_published_has_no_hour(self):
        self.dataset("synop")

        seen = self.kenyan()

        self.assertIsNone(seen.last_active_hour)
        self.assertEqual(seen.dataset_count, 1)

    def test_each_centre_carries_its_own_last_hour(self):
        djibouti = self.node("dj-anm")
        self.quiet_for(self.dataset("synop"), 30)
        self.quiet_for(self.dataset("synop", node=djibouti), 2)

        by_node = self.by_node()

        self.assertEqual(
            by_node[self.kenya.pk].last_active_hour, NOW - timedelta(hours=30)
        )
        self.assertEqual(
            by_node[djibouti.pk].last_active_hour, NOW - timedelta(hours=2)
        )


class SilenceTests(ObservationsTestCase):
    """The centre's silence, judged over its observations alone."""

    def test_an_observation_past_its_expectation_makes_the_centre_silent(self):
        self.learned(self.quiet_for(self.dataset("synop"), 8), 6)
        self.learned(self.quiet_for(self.dataset("temp"), 1), 6)

        seen = self.kenyan()

        self.assertEqual(seen.silence, Silence.SILENT)
        self.assertEqual(seen.silent_dataset_count, 1)
        self.assertEqual(seen.judged_dataset_count, 2)

    def test_an_overdue_non_observation_does_not_make_the_centre_silent(self):
        self.learned(self.quiet_for(self.dataset("synop"), 1), 6)
        self.learned(self.quiet_for(self.dataset("metar", category=ADVISORIES), 40), 6)

        seen = self.kenyan()

        self.assertEqual(seen.silence, Silence.ON_SCHEDULE)
        self.assertEqual(seen.silent_dataset_count, 0)
        self.assertEqual(seen.judged_dataset_count, 1)

    def test_a_centre_with_nothing_that_can_be_judged_says_so(self):
        self.quiet_for(self.dataset("synop"), 500)

        seen = self.kenyan()

        self.assertEqual(seen.silence, Silence.UNKNOWN)
        self.assertEqual(seen.judged_dataset_count, 0)

    def test_each_centre_carries_its_own_verdict(self):
        djibouti = self.node("dj-anm")
        self.learned(self.quiet_for(self.dataset("synop"), 8), 6)
        self.learned(self.quiet_for(self.dataset("synop", node=djibouti), 1), 6)

        by_node = self.by_node()

        self.assertEqual(by_node[self.kenya.pk].silence, Silence.SILENT)
        self.assertEqual(by_node[djibouti.pk].silence, Silence.ON_SCHEDULE)


class NoneDeclaredTests(TestCase):
    """What stands in for a centre the fold never mentioned."""

    def test_a_centre_declaring_no_observations_declares_none(self):
        seen = NodeObservations.none_declared()

        self.assertFalse(seen.declares_observations)
        self.assertEqual(seen.dataset_count, 0)
        self.assertIsNone(seen.last_active_hour)
        self.assertEqual(seen.silence, Silence.UNKNOWN)

    def test_a_centre_that_declares_one_declares_observations(self):
        self.assertTrue(
            NodeObservations(
                dataset_count=1,
                last_active_hour=None,
                silence=Silence.UNKNOWN,
                silent_dataset_count=0,
                judged_dataset_count=0,
            ).declares_observations
        )
