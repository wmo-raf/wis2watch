"""The bounded wildcard sweep, driven a window at a time.

The sweep is the one part of the ingest that deliberately asks for more than
the region, so the failures worth catching are about what it does with what
comes back: a centre outside the region written down as a finding, a centre
inside it missed because nothing in the registry names it, a window that opens
and never closes.

Time is passed in rather than waited for. What the sweep decides is a function
of the interval and the duration, and a test that slept for either would be
testing the clock.
"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings

from wis2watch.core.models import SyncLog, UnregisteredCentre, WIS2Node
from wis2watch.core.tests.support import at
from wis2watch.ingest.sweep import WildcardSweep

STARTED = at("2026-08-11T10:00:00")

KE_TOPIC = "origin/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop"
DJ_TOPIC = "origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf"
BR_TOPIC = "origin/a/wis2/br-inmet/data/core/weather/surface-based-observations/synop"


def after(seconds):
    """An instant a number of seconds into the run."""
    return STARTED + timedelta(seconds=seconds)


@override_settings(
    WIS2WATCH_SWEEP_INTERVAL_SECONDS=3600, WIS2WATCH_SWEEP_DURATION_SECONDS=60
)
class SweepTestCase(TestCase):
    def setUp(self):
        self.sweep = WildcardSweep(now=STARTED)

    def open_sweep(self, at_second=3600):
        """A sweep opened at its first due moment."""
        self.sweep.service(now=after(at_second))

        return self.sweep

    def centre(self, centre_id):
        return UnregisteredCentre.objects.get(centre_id=centre_id)


class SweepScheduleTests(SweepTestCase):
    """A window opens on a schedule and closes on its own."""

    def test_no_sweep_runs_before_the_interval_has_elapsed(self):
        """A restarting process must not ask for the world every time it comes up."""
        self.assertFalse(self.sweep.service(now=after(3599)))
        self.assertFalse(self.sweep.is_running)
        self.assertEqual(self.sweep.topics(), ())

    def test_a_sweep_opens_once_the_interval_has_elapsed(self):
        changed = self.sweep.service(now=after(3600))

        self.assertTrue(changed)
        self.assertTrue(self.sweep.is_running)

    def test_an_open_sweep_carries_a_filter_naming_no_centre(self):
        self.open_sweep()

        self.assertEqual(self.sweep.topics(), ("origin/a/wis2/+/#",))

    def test_an_open_sweep_stays_open_for_its_window(self):
        self.open_sweep()

        self.assertFalse(self.sweep.service(now=after(3600 + 59)))
        self.assertTrue(self.sweep.is_running)

    def test_a_sweep_closes_when_its_window_is_up(self):
        self.open_sweep()

        changed = self.sweep.service(now=after(3600 + 60))

        self.assertTrue(changed)
        self.assertFalse(self.sweep.is_running)
        self.assertEqual(self.sweep.topics(), ())

    def test_the_next_sweep_is_an_interval_after_the_last_one_started(self):
        self.open_sweep()
        self.sweep.service(now=after(3660))

        self.assertFalse(self.sweep.service(now=after(7199)))
        self.assertTrue(self.sweep.service(now=after(7200)))

    def test_finishing_an_unopened_sweep_changes_nothing(self):
        self.sweep.finish(now=after(10))

        self.assertFalse(self.sweep.is_running)
        self.assertEqual(SyncLog.objects.count(), 0)

    def test_an_open_window_says_when_it_is_over_before_it_closes(self):
        """The caller has to store the last of the traffic before that."""
        self.open_sweep()

        self.assertFalse(self.sweep.is_over(now=after(3600 + 59)))
        self.assertTrue(self.sweep.is_over(now=after(3600 + 60)))
        self.assertTrue(self.sweep.is_running)

    def test_a_closed_sweep_is_not_over(self):
        self.assertFalse(self.sweep.is_over(now=after(7200)))


@override_settings(
    WIS2WATCH_SWEEP_INTERVAL_SECONDS=3600, WIS2WATCH_SWEEP_DURATION_SECONDS=60
)
class ScheduleSurvivesRestartTests(TestCase):
    """The schedule is read back from the runs, not counted from startup.

    A process restarting oftener than the interval would otherwise never sweep
    at all -- and that failure is silent: the blind spot the sweep exists to
    close simply stays open while everything reports itself healthy.
    """

    def swept_at(self, moment):
        """A sweep that has already run, as its log records it."""
        return SyncLog.objects.create(
            sync_type=SyncLog.WILDCARD_SWEEP,
            status=SyncLog.SUCCESS,
            started_at=moment,
        )

    def test_a_restart_resumes_the_interval_from_the_last_run(self):
        self.swept_at(after(0))

        restarted = WildcardSweep(now=after(60))

        self.assertFalse(restarted.service(now=after(3599)))
        self.assertTrue(restarted.service(now=after(3600)))

    def test_a_process_restarting_oftener_than_the_interval_still_sweeps(self):
        self.swept_at(after(0))

        for restarted_at in range(60, 3600, 300):
            restarted = WildcardSweep(now=after(restarted_at))
            self.assertFalse(restarted.service(now=after(restarted_at)))

        overdue = WildcardSweep(now=after(3600))

        self.assertTrue(overdue.service(now=after(3600)))

    def test_a_sweep_missed_while_the_process_was_down_runs_at_once(self):
        self.swept_at(after(0))

        restarted = WildcardSweep(now=after(9000))

        self.assertTrue(restarted.service(now=after(9000)))

    def test_a_deployment_that_has_never_swept_waits_an_interval(self):
        """A crash loop must not ask for the world's traffic every time."""
        fresh = WildcardSweep(now=after(0))

        self.assertFalse(fresh.service(now=after(3599)))
        self.assertTrue(fresh.service(now=after(3600)))

    def test_only_a_sweeps_own_runs_are_read_back(self):
        SyncLog.objects.create(
            sync_type=SyncLog.OSCAR_STATIONS,
            status=SyncLog.SUCCESS,
            started_at=after(3000),
        )
        self.swept_at(after(0))

        restarted = WildcardSweep(now=after(60))

        self.assertTrue(restarted.service(now=after(3600)))


class UnregisteredCentreTests(SweepTestCase):
    """What a sweep hears, and what it writes down about it."""

    def test_a_region_centre_the_registry_does_not_know_is_recorded(self):
        self.open_sweep()

        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3610))

        recorded = self.centre("dj-anm")
        self.assertEqual(recorded.country.code, "DJ")
        self.assertEqual(recorded.sample_topic, DJ_TOPIC)
        self.assertEqual(recorded.first_seen_at, after(3610))
        self.assertEqual(recorded.last_seen_at, after(3610))
        self.assertIsNone(recorded.registered_at)

    def test_a_centre_seen_again_moves_its_last_seen_rather_than_repeating(self):
        self.open_sweep()
        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3610))

        self.sweep.observe({"dj-anm": KE_TOPIC}, now=after(3620))

        self.assertEqual(UnregisteredCentre.objects.count(), 1)
        self.assertEqual(self.centre("dj-anm").first_seen_at, after(3610))
        self.assertEqual(self.centre("dj-anm").last_seen_at, after(3620))

    def test_observations_outside_a_window_are_not_recorded(self):
        """Outside a sweep there is no run for a finding to belong to."""
        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(10))

        self.assertEqual(UnregisteredCentre.objects.count(), 0)

    def test_a_finding_reopens_when_the_centre_is_heard_from_again(self):
        """The observation is the evidence: no record behind it means open."""
        self.open_sweep()
        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3610))
        UnregisteredCentre.objects.update(registered_at=after(3000))

        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3620))

        self.assertIsNone(self.centre("dj-anm").registered_at)


class ClosedFindingTests(SweepTestCase):
    """A centre the registry has caught up with stops being reported."""

    def test_a_centre_since_registered_is_closed_when_the_sweep_finishes(self):
        self.open_sweep()
        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3610))
        WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")

        self.sweep.service(now=after(3660))

        self.assertEqual(self.centre("dj-anm").registered_at, after(3660))
        self.assertEqual(UnregisteredCentre.objects.unregistered().count(), 0)

    def test_a_closed_finding_is_kept_rather_than_deleted(self):
        """That a centre published before anyone registered it is worth saying."""
        self.open_sweep()
        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3610))
        WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")

        self.sweep.service(now=after(3660))

        self.assertEqual(UnregisteredCentre.objects.count(), 1)
        self.assertEqual(self.centre("dj-anm").first_seen_at, after(3610))

    def test_a_centre_still_absent_from_the_registry_stays_open(self):
        self.open_sweep()
        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3610))

        self.sweep.service(now=after(3660))

        self.assertIsNone(self.centre("dj-anm").registered_at)

    def test_a_centre_registered_since_an_earlier_sweep_is_closed_unheard(self):
        """A registered centre need not publish again for its finding to close."""
        UnregisteredCentre.objects.create(
            centre_id="dj-anm",
            country="DJ",
            first_seen_at=after(0),
            last_seen_at=after(0),
        )
        WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")

        self.open_sweep()
        self.sweep.service(now=after(3660))

        self.assertEqual(self.centre("dj-anm").registered_at, after(3660))


class SweepLogTests(SweepTestCase):
    """A sweep is a sync run like any other, with its own outcome."""

    def log(self):
        return SyncLog.objects.get(sync_type=SyncLog.WILDCARD_SWEEP)

    def test_an_open_sweep_has_a_log_that_has_not_succeeded(self):
        """A process that dies mid-sweep must leave a run that plainly failed."""
        self.open_sweep()

        self.assertEqual(self.log().status, SyncLog.FAILED)
        self.assertIsNone(self.log().completed_at)

    def test_a_finished_sweep_records_what_it_found(self):
        self.open_sweep()
        self.sweep.observe({"dj-anm": DJ_TOPIC, "ng-nimet": KE_TOPIC}, now=after(3610))

        self.sweep.service(now=after(3660))

        log = self.log()
        self.assertEqual(log.status, SyncLog.SUCCESS)
        self.assertEqual(log.items_found, 2)
        self.assertEqual(log.items_created, 2)
        self.assertIsNotNone(log.completed_at)

    def test_a_centre_heard_throughout_a_window_is_counted_once(self):
        """A centre publishing steadily is heard on every drain of a sweep."""
        self.open_sweep()

        for second in range(3610, 3650):
            self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(second))

        self.sweep.service(now=after(3660))

        self.assertEqual(self.log().items_found, 1)
        self.assertEqual(self.log().items_created, 1)
        self.assertEqual(self.log().items_updated, 0)

    def test_a_centre_carried_over_from_an_earlier_sweep_counts_as_updated(self):
        self.open_sweep()
        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3610))
        self.sweep.service(now=after(3660))

        self.sweep.service(now=after(7200))
        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(7210))
        self.sweep.service(now=after(7260))

        latest = SyncLog.objects.filter(sync_type=SyncLog.WILDCARD_SWEEP).first()
        self.assertEqual(latest.items_found, 1)
        self.assertEqual(latest.items_created, 0)
        self.assertEqual(latest.items_updated, 1)

    def test_what_a_run_found_adds_up_to_what_became_of_it(self):
        self.open_sweep()
        self.sweep.observe({"dj-anm": DJ_TOPIC, "ng-nimet": KE_TOPIC}, now=after(3610))
        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3620))

        self.sweep.service(now=after(3660))

        log = self.log()
        self.assertEqual(
            log.items_found,
            log.items_created + log.items_updated + log.items_errored,
        )

    def test_a_sweep_that_heard_nothing_is_still_a_successful_run(self):
        """A region with no unregistered centres is a finding of its own."""
        self.open_sweep()

        self.sweep.service(now=after(3660))

        self.assertEqual(self.log().status, SyncLog.SUCCESS)
        self.assertEqual(self.log().items_found, 0)

    def test_closed_findings_are_counted_on_the_run_that_closed_them(self):
        self.open_sweep()
        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3610))
        WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")

        self.sweep.service(now=after(3660))

        self.assertEqual(self.log().items_deleted, 1)

    def test_each_sweep_gets_its_own_log(self):
        self.open_sweep()
        self.sweep.service(now=after(3660))

        self.sweep.service(now=after(7200))
        self.sweep.service(now=after(7260))

        self.assertEqual(
            SyncLog.objects.filter(sync_type=SyncLog.WILDCARD_SWEEP).count(), 2
        )

    def test_a_centre_that_cannot_be_written_does_not_lose_the_others(self):
        self.open_sweep()

        self.sweep.observe(
            {"dj-anm": DJ_TOPIC, "x" * 300: BR_TOPIC}, now=after(3610)
        )
        self.sweep.service(now=after(3660))

        self.assertEqual(self.log().items_errored, 1)
        self.assertEqual(self.log().items_created, 1)
        self.assertEqual(self.log().status, SyncLog.PARTIAL)

    def test_a_centre_that_cannot_be_written_says_which_one_and_why(self):
        self.open_sweep()

        self.sweep.observe({"x" * 300: BR_TOPIC}, now=after(3610))
        self.sweep.service(now=after(3660))

        (stepped_over,) = self.log().stepped_over

        self.assertEqual(stepped_over["item"], "x" * 300)
        self.assertTrue(stepped_over["reason"])

    def test_a_centre_written_on_a_later_flush_stops_counting_as_an_error(self):
        self.open_sweep()

        with mock.patch(
            "wis2watch.ingest.sweep._record_unregistered_centre",
            side_effect=RuntimeError("the database went away"),
        ):
            self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3610))

        self.sweep.observe({"dj-anm": DJ_TOPIC}, now=after(3620))
        self.sweep.service(now=after(3660))

        self.assertEqual(self.log().items_errored, 0)
        self.assertEqual(self.log().items_created, 1)

    def test_no_window_opens_when_its_log_cannot_be_written(self):
        """A wildcard filter must never be carried with no record that it was."""
        with mock.patch.object(
            SyncLog.objects, "create", side_effect=RuntimeError("the database went away")
        ):
            with self.assertRaises(RuntimeError):
                self.sweep.service(now=after(3600))

        self.assertFalse(self.sweep.is_running)
        self.assertEqual(self.sweep.topics(), ())

    def test_a_window_that_could_not_open_is_tried_again(self):
        with mock.patch.object(
            SyncLog.objects, "create", side_effect=RuntimeError("the database went away")
        ):
            with self.assertRaises(RuntimeError):
                self.sweep.service(now=after(3600))

        self.assertTrue(self.sweep.service(now=after(3601)))
        self.assertTrue(self.sweep.is_running)
