"""Summarising the hourly rollups into station-days, against a seeded database.

The daily rollups are a second derived layer, and the risk a second layer
carries is that it disagrees with the first -- the dashboard saying 412 while
the station list says 409. So the guarantee these tests are here to hold is
narrower than "the numbers look right": a day is a pure function of the hours
under it, and rebuilding it from those hours is always allowed to change the
answer and never allowed to keep a stale one.

The day boundary and the timezone are called out separately for the same reason
the hourly tests call out the hour boundary: an off-by-one day loses a day at
the edge of every window, while a date_trunc that follows the active timezone
shifts every bucket in the region and nothing raises.
"""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase, override_settings

from wis2watch.core.daily_rollups import (
    backfill_daily_rollups,
    default_window_days,
    rollup_days,
    update_daily_rollups,
)
from wis2watch.core.models import (
    Dataset,
    DailyStationRollup,
    HourlyRollup,
    MessageSource,
    NotificationMessage,
    Station,
    WIS2Node,
)
from wis2watch.core.rollups import rollup_hours
from wis2watch.core.tasks import run_update_daily_rollups
from wis2watch.core.tests.support import at


class DailyRollupTestCase(TestCase):
    def setUp(self):
        self.source = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

    def counted(self, when, count=1, node=None, dataset=None, station=None, source=None):
        """One hourly rollup, as a rollup run would have left it."""
        return HourlyRollup.objects.create(
            hour=at(when) if isinstance(when, str) else when,
            source=source or self.source,
            node=node or self.node,
            dataset=dataset,
            station=station,
            message_count=count,
        )

    def dataset(self, identifier="urn:wmo:md:ke-meteo:synop"):
        return Dataset.objects.create(
            node=self.node,
            identifier=identifier,
            title=identifier,
            wmo_data_policy="core",
            wmo_topic_hierarchy=f"origin/a/wis2/ke-meteo/{identifier}",
            raw_json={},
        )

    def roll(self, since="2026-08-01T00:00:00", until="2026-09-01T00:00:00"):
        return rollup_days(since=at(since), until=at(until))

    def days(self):
        """The days rolled up, with their counts, in order."""
        return [
            (row.day.isoformat(), row.message_count)
            for row in DailyStationRollup.objects.order_by("day")
        ]


class ScheduleTests(SimpleTestCase):
    """That the beat actually reaches the summary.

    A schedule naming a task that does not exist announces itself nowhere:
    nothing runs, nothing is logged, and the statistics surfaces go on reading
    a table that stopped being brought up to date. Worse here than most,
    because the numbers would not be missing -- they would be old.
    """

    def setUp(self):
        self.entry = settings.CELERY_BEAT_SCHEDULE["update-daily-rollups"]

    def test_the_scheduled_task_is_the_one_that_answers_to_that_name(self):
        self.assertEqual(self.entry["task"], run_update_daily_rollups.name)

    def test_it_runs_on_the_same_beat_as_the_hours_it_summarises(self):
        """A day the surfaces are reading should not lag the hours under it."""
        self.assertEqual(
            self.entry["schedule"],
            settings.CELERY_BEAT_SCHEDULE["update-rollups"]["schedule"],
        )


class DayBucketTests(DailyRollupTestCase):
    """Which day an hour falls in, in UTC."""

    def test_hours_of_one_day_become_one_row(self):
        self.counted("2026-08-11T00:00:00")
        self.counted("2026-08-11T10:00:00", count=5)
        self.counted("2026-08-11T23:00:00", count=2)

        self.roll()

        self.assertEqual(self.days(), [("2026-08-11T00:00:00+00:00", 8)])

    def test_a_day_boundary_separates_two_adjacent_hours(self):
        self.counted("2026-08-11T23:00:00")
        self.counted("2026-08-12T00:00:00")

        self.roll()

        self.assertEqual(
            self.days(),
            [("2026-08-11T00:00:00+00:00", 1), ("2026-08-12T00:00:00+00:00", 1)],
        )

    @override_settings(TIME_ZONE="Asia/Kolkata")
    def test_buckets_are_UTC_days_whatever_the_configured_timezone(self):
        """A deployment's own timezone must not decide where a day starts.

        Half an hour off UTC, for the same reason the hourly tests pick it: a
        whole-hour offset still lands the day boundary somewhere a wrong bucket
        is easy to miss, and this one cannot be read as anything else.
        """
        self.counted("2026-08-11T20:00:00")
        self.counted("2026-08-11T23:00:00")

        self.roll()

        self.assertEqual(self.days(), [("2026-08-11T00:00:00+00:00", 2)])

    def test_only_the_requested_window_is_rolled_up(self):
        self.counted("2026-08-10T23:00:00")
        self.counted("2026-08-11T10:00:00")
        self.counted("2026-08-13T00:00:00")

        self.roll(since="2026-08-11T00:00:00", until="2026-08-13T00:00:00")

        self.assertEqual(self.days(), [("2026-08-11T00:00:00+00:00", 1)])

    def test_a_window_starting_mid_day_still_rebuilds_the_whole_day(self):
        """A half-counted day would overwrite a complete one with a smaller
        number, which is the one failure this layer must not have."""
        self.counted("2026-08-11T02:00:00")
        self.counted("2026-08-11T20:00:00")

        rollup_days(since=at("2026-08-11T12:00:00"), until=at("2026-08-12T00:00:00"))

        self.assertEqual(self.days(), [("2026-08-11T00:00:00+00:00", 2)])


class DailyGrainTests(DailyRollupTestCase):
    """What a daily rollup counts separately, and what it deliberately does not."""

    def grains(self):
        return {
            (row.node_id, row.station_id, row.source_id): row.message_count
            for row in DailyStationRollup.objects.all()
        }

    def test_two_stations_are_counted_apart(self):
        one = Station.objects.create(wigos_id="0-20000-0-63708")
        two = Station.objects.create(wigos_id="0-20000-0-63710")
        self.counted("2026-08-11T10:00:00", station=one)
        self.counted("2026-08-11T11:00:00", station=two, count=3)

        self.roll()

        self.assertEqual(
            self.grains(),
            {
                (self.node.pk, one.pk, self.source.pk): 1,
                (self.node.pk, two.pk, self.source.pk): 3,
            },
        )

    def test_two_centres_are_counted_apart(self):
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        self.counted("2026-08-11T10:00:00")
        self.counted("2026-08-11T10:00:00", node=djibouti)

        self.roll()

        self.assertEqual(
            self.grains(),
            {
                (self.node.pk, None, self.source.pk): 1,
                (djibouti.pk, None, self.source.pk): 1,
            },
        )

    def test_the_same_day_from_two_vantage_points_is_not_one_count(self):
        """Summing the vantage points would double every number for the region."""
        origin = MessageSource.objects.create(
            name="ke-meteo origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            host="wis.meteo.example.int",
        )
        self.counted("2026-08-11T10:00:00")
        self.counted("2026-08-11T10:00:00", source=origin)

        self.roll()

        self.assertEqual(
            self.grains(),
            {
                (self.node.pk, None, self.source.pk): 1,
                (self.node.pk, None, origin.pk): 1,
            },
        )

    def test_datasets_are_summed_together_rather_than_counted_apart(self):
        """Dropping the dataset is the whole point: it multiplies the rows a
        station question has to read and answers none of it."""
        station = Station.objects.create(wigos_id="0-20000-0-63708")
        self.counted("2026-08-11T10:00:00", dataset=self.dataset("synop"), station=station)
        self.counted("2026-08-11T10:00:00", dataset=self.dataset("temp"), station=station, count=4)

        self.roll()

        self.assertEqual(
            self.grains(), {(self.node.pk, station.pk, self.source.pk): 5}
        )

    def test_messages_naming_no_station_keep_their_own_bucket(self):
        """Undecided whether the surfaces say anything about these; dropping
        them here would decide it by making the question unanswerable."""
        station = Station.objects.create(wigos_id="0-20000-0-63708")
        self.counted("2026-08-11T10:00:00", station=station)
        self.counted("2026-08-11T10:00:00", station=None, count=7)

        self.roll()

        self.assertEqual(
            self.grains(),
            {
                (self.node.pk, station.pk, self.source.pk): 1,
                (self.node.pk, None, self.source.pk): 7,
            },
        )


class ActiveHoursTests(DailyRollupTestCase):
    """How much of the day a station was heard in, as against how loudly."""

    def active_hours(self):
        return [row.active_hours for row in DailyStationRollup.objects.order_by("day")]

    def test_a_station_heard_in_three_hours_has_three_active_hours(self):
        station = Station.objects.create(wigos_id="0-20000-0-63708")
        for hour in ("00", "06", "18"):
            self.counted(f"2026-08-11T{hour}:00:00", station=station, count=50)

        self.roll()

        self.assertEqual(self.active_hours(), [3])

    def test_two_datasets_in_one_hour_are_one_active_hour(self):
        """Distinct hours, not rows -- otherwise a node publishing many datasets
        would look like a station reporting round the clock."""
        station = Station.objects.create(wigos_id="0-20000-0-63708")
        self.counted("2026-08-11T10:00:00", station=station, dataset=self.dataset("synop"))
        self.counted("2026-08-11T10:00:00", station=station, dataset=self.dataset("temp"))

        self.roll()

        self.assertEqual(self.active_hours(), [1])

    def test_a_station_heard_every_hour_has_a_full_day(self):
        station = Station.objects.create(wigos_id="0-20000-0-63708")
        for hour in range(24):
            self.counted(f"2026-08-11T{hour:02d}:00:00", station=station)

        self.roll()

        self.assertEqual(self.active_hours(), [24])


class DailyRerunTests(DailyRollupTestCase):
    """A day is a function of its hours, so it is rebuilt rather than added to."""

    def test_rolling_the_same_day_twice_does_not_double_it(self):
        self.counted("2026-08-11T10:00:00", count=3)

        self.roll()
        self.roll()

        self.assertEqual(self.days(), [("2026-08-11T00:00:00+00:00", 3)])

    def test_a_corrected_hour_corrects_the_day(self):
        """The stale-number failure this layer exists to avoid."""
        rollup = self.counted("2026-08-11T10:00:00", count=3)
        self.roll()

        rollup.message_count = 9
        rollup.save(update_fields=["message_count"])
        self.roll()

        self.assertEqual(self.days(), [("2026-08-11T00:00:00+00:00", 9)])

    def test_an_hour_added_to_a_settled_day_reopens_it(self):
        self.counted("2026-08-11T10:00:00")
        self.roll()

        self.counted("2026-08-11T14:00:00", count=2)
        self.roll()

        self.assertEqual(self.days(), [("2026-08-11T00:00:00+00:00", 3)])


class TrailingWindowTests(DailyRollupTestCase):
    """What the scheduled run covers."""

    def test_the_window_reaches_every_day_the_hourly_window_can_still_change(self):
        """The two windows are one decision. An hour the hourly run may still
        revise, sitting in a day this run no longer visits, is a number that
        stays wrong for ever."""
        now = at("2026-08-13T00:30:00")
        self.counted(now - timedelta(hours=48))

        update_daily_rollups(now=now)

        self.assertEqual(self.days(), [("2026-08-11T00:00:00+00:00", 1)])

    def test_the_window_is_taken_from_the_hourly_windows_length(self):
        with override_settings(WIS2WATCH_ROLLUP_WINDOW_HOURS=48):
            self.assertEqual(default_window_days(), 3)

        with override_settings(WIS2WATCH_ROLLUP_WINDOW_HOURS=1):
            self.assertEqual(default_window_days(), 2)

        with override_settings(WIS2WATCH_ROLLUP_WINDOW_HOURS=24 * 7):
            self.assertEqual(default_window_days(), 8)

    def test_nothing_in_the_window_writes_nothing(self):
        self.counted("2026-08-01T10:00:00")

        counts = update_daily_rollups(now=at("2026-08-13T00:30:00"))

        self.assertEqual(counts.rows, 0)
        self.assertEqual(DailyStationRollup.objects.count(), 0)

    def test_days_outside_the_window_are_left_as_they_stand(self):
        self.counted("2026-08-01T10:00:00")
        self.roll()

        HourlyRollup.objects.all().delete()
        update_daily_rollups(now=at("2026-08-13T00:30:00"))

        self.assertEqual(self.days(), [("2026-08-01T00:00:00+00:00", 1)])


class BackfillTests(DailyRollupTestCase):
    """Covering the history that was there before this table was."""

    def test_the_backfill_reaches_the_oldest_hour_the_region_holds(self):
        self.counted("2024-01-15T10:00:00", count=4)
        self.counted("2026-08-11T10:00:00", count=2)

        counts = backfill_daily_rollups(now=at("2026-08-13T00:30:00"))

        self.assertEqual(counts.rows, 2)
        self.assertEqual(
            self.days(),
            [("2024-01-15T00:00:00+00:00", 4), ("2026-08-11T00:00:00+00:00", 2)],
        )

    def test_the_backfill_covers_the_day_in_progress(self):
        self.counted("2026-08-13T00:00:00", count=5)

        backfill_daily_rollups(now=at("2026-08-13T00:30:00"))

        self.assertEqual(self.days(), [("2026-08-13T00:00:00+00:00", 5)])

    def test_running_the_backfill_twice_changes_nothing(self):
        self.counted("2026-08-11T10:00:00", count=3)

        backfill_daily_rollups(now=at("2026-08-13T00:30:00"))
        backfill_daily_rollups(now=at("2026-08-13T00:30:00"))

        self.assertEqual(self.days(), [("2026-08-11T00:00:00+00:00", 3)])

    def test_the_chunking_does_not_change_the_answer(self):
        """Chunk boundaries are where a walk of history loses or repeats a day."""
        for day in range(1, 8):
            self.counted(f"2026-08-{day:02d}T10:00:00", count=day)

        backfill_daily_rollups(now=at("2026-08-13T00:30:00"), chunk_days=2)

        self.assertEqual(
            self.days(),
            [(f"2026-08-{day:02d}T00:00:00+00:00", day) for day in range(1, 8)],
        )

    def test_an_empty_region_backfills_nothing(self):
        counts = backfill_daily_rollups(now=at("2026-08-13T00:30:00"))

        self.assertEqual(counts.rows, 0)
        self.assertEqual(DailyStationRollup.objects.count(), 0)


class DeletedStationTests(DailyRollupTestCase):
    """What becomes of a permanent count when the station it named goes.

    The hourly table already answers this -- the counts stay and join the
    unattributed bucket, and a delete that would duplicate that bucket is
    refused rather than allowed to leave two rows for one day for ever. The
    daily table has to answer it the same way, or the two layers disagree at
    exactly the moment somebody is deleting things by hand.
    """

    def test_deleting_a_station_leaves_the_counts_it_earned_as_unattributed(self):
        station = Station.objects.create(wigos_id="0-20000-0-63708")
        self.counted("2026-08-11T10:00:00", station=station)
        self.roll()

        station.delete()

        row = DailyStationRollup.objects.get()
        self.assertIsNone(row.station_id)
        self.assertEqual(row.message_count, 1)

    def test_a_delete_that_would_duplicate_an_unattributed_bucket_is_refused(self):
        station = Station.objects.create(wigos_id="0-20000-0-63708")
        self.counted("2026-08-11T10:00:00", station=station)
        self.counted("2026-08-11T11:00:00", station=None)
        self.roll()

        with self.assertRaises(IntegrityError):
            station.delete()


class LayersAgreeTests(DailyRollupTestCase):
    """The objection this table has to answer.

    A third derived layer over the second is where "the dashboard says 412 and
    the station list says 409" comes from. What makes it safe is that the daily
    row is a function of the hourly rows and nothing else -- so the two can only
    differ while a run is pending, never because they counted differently.
    """

    def test_the_days_distinct_stations_match_the_hours_they_were_derived_from(self):
        stations = [
            Station.objects.create(wigos_id=f"0-20000-0-6370{n}") for n in range(4)
        ]

        for index, station in enumerate(stations):
            for hour in range(0, 24, 6):
                NotificationMessage.objects.create(
                    source=self.source,
                    node=self.node,
                    station=station,
                    notification_id=f"{station.wigos_id}-{hour}",
                    topic=f"origin/a/wis2/ke-meteo/data/core/weather/{index}",
                    time=at(f"2026-08-11T{hour:02d}:30:00"),
                    raw_json={},
                )

        rollup_hours(since=at("2026-08-11T00:00:00"), until=at("2026-08-12T00:00:00"))
        self.roll()

        from_hours = (
            HourlyRollup.objects.filter(node=self.node)
            .values("station")
            .distinct()
            .count()
        )
        from_days = (
            DailyStationRollup.objects.filter(node=self.node)
            .values("station")
            .distinct()
            .count()
        )

        self.assertEqual(from_days, from_hours)
        self.assertEqual(from_days, 4)

    def test_the_days_message_total_matches_the_hours_it_was_derived_from(self):
        for hour in range(24):
            self.counted(f"2026-08-11T{hour:02d}:00:00", count=hour)

        self.roll()

        self.assertEqual(
            DailyStationRollup.objects.get().message_count,
            sum(range(24)),
        )
