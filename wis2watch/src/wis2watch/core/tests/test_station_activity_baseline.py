"""Learning how much of a day a station is normally heard in.

This is what every day-grain cell of the availability matrix is judged
against, so getting it wrong is not an error anyone sees: it is a healthy
three-hourly station drawn pale every day of the year, or an hourly station
down to a third of its output drawn solid. Both look entirely plausible on the
screen, and #112 measured the first one happening to two thirds of every pale
cell on the tab.

The cases here are the ones that would produce a confident wrong figure -- the
day in progress learned from as though it were whole, history from outside the
window, one centre's traffic answering for another's -- and the two things a
run must never do: claim a baseline for a station with too little history, and
forget the baseline of a station that has stopped reporting.
"""

from datetime import timedelta

from django.test import TestCase

from wis2watch.core.cadence import learn_station_activity_baselines
from wis2watch.core.models import (
    DailyStationRollup,
    MessageSource,
    Station,
    StationActivityBaseline,
    WIS2Node,
)
from wis2watch.core.tests.support import at


#: Midday, so that a test about the day in progress has a day in progress.
NOW = at("2026-08-11T12:00:00")

#: Comfortably past ``DEFAULT_STATION_MIN_OBSERVATIONS``, so a test that is not
#: about the bar does not trip over it.
ENOUGH_DAYS = 10


class StationActivityTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

    def station(self, wigos_id="0-20000-0-63740"):
        station, _ = Station.objects.get_or_create(wigos_id=wigos_id)

        return station

    def heard(self, station, hours_per_day, *, days=ENOUGH_DAYS, node=None, ending=NOW):
        """Daily rollups saying the station was heard this much, day by day.

        ``hours_per_day`` may be one number for every day or a list read
        oldest-first, which is how a decaying station is written down.
        """
        node = node or self.kenya
        run = (
            [hours_per_day] * days
            if isinstance(hours_per_day, int)
            else list(hours_per_day)
        )
        # Ending on the last *whole* day, so nothing here is about the day in
        # progress unless a test puts it there on purpose.
        last_whole = ending.replace(hour=0, minute=0, second=0, microsecond=0)

        for step, active_hours in enumerate(reversed(run)):
            DailyStationRollup.objects.create(
                day=last_whole - timedelta(days=step + 1),
                source=self.global_broker,
                node=node,
                station=station,
                message_count=active_hours,
                active_hours=active_hours,
            )

    def learn(self, **kwargs):
        kwargs.setdefault("now", NOW)

        return learn_station_activity_baselines(**kwargs)

    def baseline(self, station, node=None):
        return StationActivityBaseline.objects.filter(
            node=node or self.kenya, station=station
        ).first()


class LearnedActivityTests(StationActivityTestCase):
    """What a station's own history says about how much of a day it reports in."""

    def test_a_station_heard_all_day_every_day_is_expected_all_day(self):
        station = self.station()
        self.heard(station, 24)

        self.learn()

        self.assertEqual(self.baseline(station).active_hours, 24)

    def test_a_three_hourly_station_is_expected_at_its_own_eight_hours(self):
        """The case the clock got wrong, and the reason this table exists.

        Eight of twenty-four is a third of the day and a whole day's work for a
        station on a three-hourly schedule. Judged against the clock it was
        pale; judged against this it is exactly where it should be.
        """
        station = self.station()
        self.heard(station, 8)

        self.learn()

        self.assertEqual(self.baseline(station).active_hours, 8)

    def test_the_figure_is_the_middle_of_its_days_not_the_best_of_them(self):
        """A maximum would call an ordinary day thin for not being its best."""
        station = self.station()
        self.heard(station, [4, 4, 4, 4, 4, 4, 4, 4, 4, 24])

        self.learn()

        self.assertEqual(self.baseline(station).active_hours, 4)

    def test_one_dead_day_does_not_drag_the_figure_down(self):
        station = self.station()
        self.heard(station, [12] * 9 + [1])

        self.learn()

        self.assertEqual(self.baseline(station).active_hours, 12)

    def test_how_much_history_is_behind_the_figure_is_kept_beside_it(self):
        station = self.station()
        self.heard(station, 12, days=9)

        self.learn()

        self.assertEqual(self.baseline(station).observations, 9)


class WindowTests(StationActivityTestCase):
    """Which days a run is allowed to learn from."""

    def test_history_older_than_the_window_is_not_learned_from(self):
        station = self.station()
        self.heard(station, 20)
        self.heard(station, 2, days=ENOUGH_DAYS, ending=NOW - timedelta(days=120))

        self.learn()

        self.assertEqual(self.baseline(station).active_hours, 20)
        self.assertEqual(self.baseline(station).observations, ENOUGH_DAYS)

    def test_the_day_in_progress_is_not_learned_from(self):
        """The one place this parts company with the dataset rhythm.

        At midday every station in the region has had twelve hours to report
        in, so a run that counted today would drag every baseline in the region
        down by however early it happened to run.
        """
        station = self.station()
        self.heard(station, 24)
        DailyStationRollup.objects.create(
            day=NOW.replace(hour=0, minute=0, second=0, microsecond=0),
            source=self.global_broker,
            node=self.kenya,
            station=station,
            message_count=3,
            active_hours=3,
        )

        self.learn()

        self.assertEqual(self.baseline(station).active_hours, 24)


class TooLittleHistoryTests(StationActivityTestCase):
    """What is claimed about a station nothing much is known about."""

    def test_a_station_with_too_few_days_gets_no_baseline_at_all(self):
        """Unjudged, not guessed. A mark nobody can trust is worse than none."""
        station = self.station()
        self.heard(station, 12, days=3)

        self.learn()

        self.assertIsNone(self.baseline(station))

    def test_a_station_that_has_stopped_reporting_keeps_the_one_it_had(self):
        """Falling below the bar is what a station does when it stops.

        Forgetting its baseline then would make the matrix go unjudged about a
        station at the very moment the station went quiet.
        """
        station = self.station()
        self.heard(station, 12)
        self.learn()

        DailyStationRollup.objects.all().delete()
        self.learn(now=NOW + timedelta(days=200))

        self.assertEqual(self.baseline(station).active_hours, 12)

    def test_a_day_it_was_heard_in_no_hours_is_not_a_day_it_reported(self):
        """Zero-hour days are the finding, not the baseline.

        They are absent from this table in practice; where one exists it must
        not pull the expectation down, or a station that died halfway through
        the window would end up expected at half of what it did when alive.
        """
        station = self.station()
        self.heard(station, [20] * 8 + [0, 0])

        self.learn()

        self.assertEqual(self.baseline(station).active_hours, 20)
        self.assertEqual(self.baseline(station).observations, 8)


class NodeScopeTests(StationActivityTestCase):
    """Whose observation of a station a baseline is."""

    def test_two_centres_hearing_one_station_get_a_baseline_each(self):
        """A station may transmit under more than one centre's topics.

        Every figure on this tab is one centre's own observation, so pooling
        the two would judge a centre against traffic it never received.
        """
        uganda = WIS2Node.objects.create(centre_id="ug-meteo", name="Uganda Met")
        station = self.station()
        self.heard(station, 24)
        self.heard(station, 6, node=uganda)

        self.learn()

        self.assertEqual(self.baseline(station).active_hours, 24)
        self.assertEqual(self.baseline(station, node=uganda).active_hours, 6)

    def test_a_second_run_moves_the_figure_rather_than_adding_a_second(self):
        station = self.station()
        self.heard(station, 24)
        self.learn()

        DailyStationRollup.objects.all().delete()
        self.heard(station, 6)
        self.learn()

        self.assertEqual(
            StationActivityBaseline.objects.filter(station=station).count(), 1
        )
        self.assertEqual(self.baseline(station).active_hours, 6)
