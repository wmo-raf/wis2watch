"""The hourly poll of the centres whose own broker will not answer.

Only the schedule's own decisions are here: which centres a run asks, how deep
it asks them, and that one centre's poll is a run of its own. What a poll makes
of what comes back -- the attribution, the reachability, the counts -- is
asserted against the captured archives in ``test_archive``.

Both halves of the choosing are worth guarding. A centre wrongly left out has
no origin witness at all, and is precisely the centre this path exists for; a
centre wrongly asked is a second copy of traffic already held, fetched off a
small national server every hour for ever.
"""

from unittest import mock

from celery.schedules import crontab
from celery_singleton import Singleton
from django.conf import settings
from django.test import SimpleTestCase, TestCase

from wis2watch.core.models import MessageSource, SyncLog, WIS2Node
from wis2watch.core.rollups import window_start
from wis2watch.core.tests.support import failing_fetch, origin_api, origin_broker, pages
from wis2watch.ingest.tasks import (
    LOCK_EXPIRY_SECONDS,
    POLL_HOURS,
    run_poll_all_message_archives,
    run_poll_message_archive,
)

#: An archive that answered and had nothing to offer for the window. Answering
#: is all these cases need of it.
EMPTY_PAGE = {"type": "FeatureCollection", "features": [], "links": []}

#: The poll a centre's lock is being held by, where a case is about what
#: happens to a second one.
POLL_ALREADY_RUNNING = "the-poll-already-running"


class ScheduleTests(SimpleTestCase):
    """That the beat actually reaches the fan-out.

    A schedule naming a task that does not exist is the one failure here that
    announces itself nowhere: nothing runs, nothing is logged, and the centres
    this path exists for go on having no origin witness.
    """

    def setUp(self):
        self.entry = settings.CELERY_BEAT_SCHEDULE["poll-message-archives"]

    def test_the_scheduled_task_is_the_one_that_answers_to_that_name(self):
        self.assertEqual(self.entry["task"], run_poll_all_message_archives.name)

    def test_it_runs_once_an_hour(self):
        """Each poll asks for a window several hours deep, so a shorter beat
        would re-fetch rather than reach any further."""
        self.assertEqual(self.entry["schedule"], crontab(minute=20))


class PollFanOutTests(TestCase):
    """That an hourly run asks the centres, one run each.

    Which centres those are is the queryset's question and is asserted case by
    case where the queryset lives; what is here is that a run asks the ones it
    answers with, and asks each of them separately.
    """

    def setUp(self):
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.archive = origin_api(self.kenya)
        self.broker = origin_broker(self.kenya, is_reachable=False)

    def queued(self):
        with mock.patch(
            "wis2watch.ingest.tasks.run_poll_message_archive.delay"
        ) as delay:
            source_ids = run_poll_all_message_archives()

        self.delay = delay

        return source_ids

    def test_a_centre_whose_broker_will_not_answer_is_asked(self):
        self.assertEqual(self.queued(), [self.archive.id])

    def test_a_centre_whose_broker_answers_is_not_asked(self):
        """Its archive carries the same notifications the broker already
        delivered, so a poll would buy storage rather than evidence -- and a
        region whose brokers all answer is a run that queues nothing."""
        self.broker.is_reachable = True
        self.broker.save()

        self.assertEqual(self.queued(), [])
        self.assertEqual(self.delay.call_count, 0)

    def test_every_centre_asked_is_a_run_of_its_own(self):
        """Which is what keeps one hanging centre from costing the region its
        hour: the centres this path exists for are the ones whose servers are
        slow or never answer, and a single run over all of them would be held
        up by the first."""
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti ANM")
        elsewhere = origin_api(djibouti)

        self.assertEqual(set(self.queued()), {self.archive.id, elsewhere.id})
        self.assertEqual(
            {call.args for call in self.delay.call_args_list},
            {(self.archive.id,), (elsewhere.id,)},
        )

    def test_a_region_whose_brokers_all_answer_queues_nothing(self):
        self.broker.is_reachable = True
        self.broker.save()

        self.assertEqual(self.queued(), [])
        self.assertEqual(self.delay.call_count, 0)


class PollRunTests(TestCase):
    """What one centre's scheduled poll asks for, and what it writes down."""

    def setUp(self):
        self.node = WIS2Node.objects.create(
            centre_id="sc-seychelles-met",
            name="Seychelles Meteorological Authority",
            base_url="https://wis2.meteo.sc",
        )
        self.archive = origin_api(self.node)
        origin_broker(self.node, is_reachable=False)

    def poll(self, fetch=None):
        with mock.patch(
            "wis2watch.ingest.archive.fetch_archive_pages",
            fetch or pages(EMPTY_PAGE),
        ):
            return run_poll_message_archive(self.archive.id)

    def test_a_run_is_written_down_in_the_usual_sync_log(self):
        sync_log = SyncLog.objects.get(id=self.poll())

        self.assertEqual(sync_log.node, self.node)
        self.assertEqual(sync_log.sync_type, SyncLog.MESSAGE_ARCHIVE)
        self.assertEqual(sync_log.status, SyncLog.SUCCESS)

    def test_a_centre_that_answers_is_recorded_as_reachable(self):
        """A successful poll is itself the probe: nothing else asks this
        vantage point whether it is there."""
        self.poll()
        self.archive.refresh_from_db()

        self.assertIs(self.archive.is_reachable, True)
        self.assertIsNotNone(self.archive.last_connected_at)

    def test_a_centre_with_no_archive_is_recorded_and_does_not_fail_the_run(self):
        """Most centres serve none, and that is a fact worth holding rather
        than an accident to raise about."""
        sync_log = SyncLog.objects.get(
            id=self.poll(failing_fetch("404 Client Error: Not Found"))
        )
        self.archive.refresh_from_db()

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertIs(self.archive.is_reachable, False)
        self.assertIn("404", self.archive.last_error)

    def test_the_window_asked_for_is_the_scheduled_depth(self):
        asked = {}

        def fetch(source, *, since, until, max_pages):
            asked.update(since=since, until=until)

            yield EMPTY_PAGE

        self.poll(fetch)

        # Whole hourly buckets, because what is asked for is afterwards counted
        # into them: a window beginning mid-hour would have its first bucket
        # recomputed from a fraction of that hour's messages.
        self.assertEqual(asked["since"], window_start(asked["until"], POLL_HOURS))

    def test_a_poll_of_a_centre_already_being_polled_is_not_queued(self):
        """An archive slower to read than the beat would otherwise have the
        next hour's read of the same window queued behind it, and the one
        after that behind them both."""
        self.assertIsInstance(run_poll_message_archive, Singleton)
        self.assertEqual(self.dispatch(self.archive.id).id, POLL_ALREADY_RUNNING)

    def test_one_centre_being_polled_does_not_hold_up_another(self):
        """The lock is a centre's own, which is the whole of what it is for."""
        self.assertNotEqual(*(self.lock_for(source_id) for source_id in (1, 2)))

    def lock_for(self, source_id):
        return run_poll_message_archive.generate_lock(
            run_poll_message_archive.name, [source_id]
        )

    def dispatch(self, source_id):
        """Queue a poll while its centre's lock is held by a run already going.

        The lock is taken through the backend celery-singleton keeps it in, so
        this exercises the refusal itself rather than the attributes that
        configure it.
        """
        held = mock.Mock(
            lock=mock.Mock(return_value=False),
            get=mock.Mock(return_value=POLL_ALREADY_RUNNING),
        )

        run_poll_message_archive._singleton_backend = held
        self.addCleanup(setattr, run_poll_message_archive, "_singleton_backend", None)

        return run_poll_message_archive.delay(source_id)

    def test_a_lock_outlives_no_worker_that_held_it(self):
        """A lock left behind by a killed worker would retire that centre from
        the schedule for good, and say nothing about having done so."""
        self.assertEqual(run_poll_message_archive.lock_expiry, LOCK_EXPIRY_SECONDS)

    def test_a_centre_asked_this_hour_may_be_asked_again_next_hour(self):
        """The lock is a singleton on the run, not a record that it happened:
        the window is asked for outright each time, and re-reading it is what
        absorbs a publisher's clock skew."""
        self.poll()
        self.poll()

        self.assertEqual(
            SyncLog.objects.filter(sync_type=SyncLog.MESSAGE_ARCHIVE).count(), 2
        )


class PollSelectionAfterAnswerTests(TestCase):
    """What a poll's own answer does to the next hour's run: nothing.

    Which centres are asked is a question about their brokers, and a poll says
    nothing about a broker. The two halves are written in different modules --
    the poll records its answer, the fan-out chooses without reading it -- so
    that they agree is asserted here rather than assumed at both ends.
    """

    def setUp(self):
        self.node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.archive = origin_api(self.node)
        origin_broker(self.node, is_reachable=False)

    def poll(self, fetch):
        with mock.patch("wis2watch.ingest.archive.fetch_archive_pages", fetch):
            run_poll_message_archive(self.archive.id)

    def asked_next(self):
        return [source.id for source in MessageSource.objects.archives_to_poll()]

    def test_a_centre_that_answered_is_still_asked_the_next_hour(self):
        """Its broker has not come back, so it is still the only witness."""
        self.poll(pages(EMPTY_PAGE))

        self.assertEqual(self.asked_next(), [self.archive.id])

    def test_a_centre_that_serves_no_archive_is_still_asked_the_next_hour(self):
        """A 404 is written down as what this centre is rather than acted on.

        Serving an archive is a wis2box convention, so a centre may begin to
        one release later, and where it serves it is a guess an operator can
        correct in the admin at any time. Retiring a centre from the schedule
        on one 404 would mean neither was ever noticed -- and there is nothing
        else asking this vantage point whether it is there.
        """
        self.poll(failing_fetch("404 Client Error: Not Found"))
        self.archive.refresh_from_db()

        self.assertIs(self.archive.is_reachable, False)
        self.assertEqual(self.asked_next(), [self.archive.id])
