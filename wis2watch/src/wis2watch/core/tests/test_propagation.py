"""Propagation gap detection, against a seeded database.

This is the finding neither vantage point can make alone: a centre published
these specific notifications and the world never saw them. Everything that
could go wrong with it produces a confident wrong answer rather than an
exception -- a gap reported because the tool was looking too early, a gap
reported because the tool could not see the origin broker at all, a gap
reported for a message that did arrive but out of order -- so the boundaries
are what these tests are about.

A phantom gap is worse than no finding. Each of these cases is one the
diagnostician would otherwise spend a morning chasing at a centre that did
nothing wrong.
"""

from datetime import timedelta

from django.test import TestCase, override_settings

from wis2watch.core.models import (
    Dataset,
    MessageSource,
    NotificationMessage,
    PropagationGap,
    WIS2Node,
)
from wis2watch.core.propagation import evaluate_propagation
from wis2watch.core.retention import raw_retention_cutoff
from wis2watch.core.tests.support import at, origin_api, origin_broker


NOW = at("2026-08-11T12:00:00")

#: Comfortably past a fifteen-minute grace period, so that a message seeded
#: without a time of its own is one the tool is entitled to judge.
LONG_ENOUGH_AGO = NOW - timedelta(hours=1)

#: How far back the world is heard by default: wider than the evaluation
#: window these cases use, so that anything inside it is in hours the run can
#: prove it was listening through.
HEARD_SINCE = NOW - timedelta(hours=50)


class PropagationTestCase(TestCase):
    """One centre, one Global Broker, and a world that is being heard.

    The last of those is a fixture rather than a subject: a run judges only
    the hours it can prove it was listening through, so ordinary Global Broker
    traffic is the background every case about something else is set against.
    A case about blindness turns it off.
    """

    #: Whether to seed that background.
    the_world_is_heard = True

    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
            is_reachable=True,
        )
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.kenya_origin = origin_broker(self.kenya, is_reachable=True)

        if self.the_world_is_heard:
            self.world_heard_throughout()

    def world_heard_throughout(
        self, since=HEARD_SINCE, until=NOW, every=timedelta(minutes=10)
    ):
        """The rest of the world's traffic, arriving steadily on the Global Broker.

        Nothing here is about these messages: they belong to no monitored
        centre and are never matched against anything. What they are is the
        evidence that the connection was up, arriving often enough that every
        grace period in these cases contains one -- and a run will not judge a
        notification whose grace period contains none.
        """
        arrivals = []
        arrival = since

        while arrival <= until:
            arrivals.append(
                NotificationMessage(
                    source=self.global_broker,
                    notification_id=f"elsewhere-{arrival.isoformat()}",
                    topic="origin/a/wis2/xx-elsewhere/data/core/weather",
                    time=arrival,
                    received_datetime=arrival,
                    raw_json={},
                )
            )
            arrival += every

        NotificationMessage.objects.bulk_create(arrivals)

    def observe(self, source, notification_id, *, node, published=None, received=None):
        """One notification, as one vantage point observed it.

        The publication time belongs to the notification and is therefore the
        same at both vantage points; when it was received is not, and is what
        the two views actually differ by.
        """
        published = LONG_ENOUGH_AGO if published is None else published

        return NotificationMessage.objects.create(
            source=source,
            node=node,
            notification_id=notification_id,
            topic=f"origin/a/wis2/{node.centre_id}/data/core/weather",
            time=published,
            received_datetime=published if received is None else received,
            raw_json={},
        )

    def at_origin(self, notification_id, *, node=None, **kwargs):
        node = node or self.kenya

        return self.observe(
            node.origin_source, notification_id, node=node, **kwargs
        )

    def at_global(self, notification_id, *, node=None, **kwargs):
        return self.observe(
            self.global_broker, notification_id, node=node or self.kenya, **kwargs
        )

    def evaluate(self, **kwargs):
        kwargs.setdefault("now", NOW)
        return evaluate_propagation(**kwargs)

    def gaps(self):
        return list(PropagationGap.objects.order_by("notification_id"))

    def missing(self):
        return [gap.notification_id for gap in self.gaps()]


class DetectionTests(PropagationTestCase):
    """The comparison itself, which runs one way and one centre at a time.

    Matching the wrong direction, or attributing a gap to whichever centre
    happened to be nearby, would produce a report that reads perfectly and
    sends someone to the wrong place.
    """

    def test_a_notification_seen_only_at_origin_is_recorded_as_a_gap(self):
        self.at_origin("lost-one")

        self.evaluate()

        self.assertEqual(self.missing(), ["lost-one"])

    def test_a_notification_both_vantage_points_saw_is_not_a_gap(self):
        self.at_origin("arrived")
        self.at_global("arrived")

        self.evaluate()

        self.assertEqual(self.missing(), [])

    def test_the_gap_identifies_the_notification_rather_than_counting_it(self):
        """Naming the message is the point: a count cannot be investigated."""
        synop = Dataset.objects.create(
            node=self.kenya,
            identifier="urn:wmo:md:ke-meteo:synop",
            title="Synop",
            wmo_data_policy="core",
            wmo_topic_hierarchy="origin/a/wis2/ke-meteo/data/core/weather",
            raw_json={},
        )
        message = self.at_origin("lost-one")
        message.dataset = synop
        message.save()

        self.evaluate()

        gap = self.gaps()[0]

        self.assertEqual(gap.notification_id, "lost-one")
        self.assertEqual(gap.node, self.kenya)
        self.assertEqual(gap.topic, "origin/a/wis2/ke-meteo/data/core/weather")
        self.assertEqual(gap.published_at, LONG_ENOUGH_AGO)
        self.assertEqual(gap.dataset, synop)
        self.assertEqual(gap.origin_source, self.kenya_origin)
        self.assertEqual(gap.detected_at, NOW)
        self.assertIsNone(gap.resolved_at)

    def test_a_gap_belongs_to_the_centre_whose_broker_published_it(self):
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti ANM")
        origin_broker(djibouti, is_reachable=True)

        self.at_origin("kenyan")
        self.at_origin("djiboutian", node=djibouti)

        self.evaluate()

        self.assertEqual(
            {gap.notification_id: gap.node_id for gap in self.gaps()},
            {"kenyan": self.kenya.pk, "djiboutian": djibouti.pk},
        )

    def test_traffic_the_global_broker_alone_saw_is_not_a_gap(self):
        """The comparison runs one way: origin is the claim, global the check."""
        self.at_global("seen-only-by-the-world")

        self.evaluate()

        self.assertEqual(self.missing(), [])

    def test_the_run_reports_what_it_found(self):
        self.at_origin("lost-one")
        self.at_origin("lost-two")
        self.at_origin("arrived")
        self.at_global("arrived")

        counts = self.evaluate()

        self.assertEqual(counts.detected, 2)
        self.assertEqual(counts.nodes_evaluated, 1)
        self.assertEqual(counts.nodes_suppressed, 0)


class GracePeriodTests(PropagationTestCase):
    """Normal latency is not loss.

    The grace period is measured from when this tool saw the message at
    origin, not from when the centre says it published. A publisher's clock is
    not ours to trust: one running hours behind would have every message
    instantly past a grace period measured against it, and every one of those
    would be a phantom gap.
    """

    @override_settings(WIS2WATCH_PROPAGATION_GRACE_MINUTES=15)
    def test_a_message_still_inside_the_grace_period_is_not_yet_a_gap(self):
        self.at_origin("in-flight", received=NOW - timedelta(minutes=5))

        self.evaluate()

        self.assertEqual(self.missing(), [])

    @override_settings(WIS2WATCH_PROPAGATION_GRACE_MINUTES=15)
    def test_a_message_past_the_grace_period_is_a_gap(self):
        self.at_origin("never-made-it", received=NOW - timedelta(minutes=20))

        self.evaluate()

        self.assertEqual(self.missing(), ["never-made-it"])

    def test_the_grace_period_is_an_argument(self):
        self.at_origin("in-flight", received=NOW - timedelta(minutes=20))

        self.evaluate(grace_minutes=30)

        self.assertEqual(self.missing(), [])

    @override_settings(WIS2WATCH_PROPAGATION_GRACE_MINUTES=15)
    def test_grace_is_counted_from_the_origin_sighting_not_the_stated_time(self):
        """A centre publishing with a skewed clock must not manufacture gaps."""
        self.at_origin(
            "clock-is-hours-behind",
            published=NOW - timedelta(hours=6),
            received=NOW - timedelta(minutes=2),
        )

        self.evaluate()

        self.assertEqual(self.missing(), [])


class OutOfOrderTests(PropagationTestCase):
    """Two vantage points do not see the same message in the same order."""

    @override_settings(WIS2WATCH_PROPAGATION_GRACE_MINUTES=15)
    def test_a_message_the_world_saw_first_is_not_a_gap(self):
        self.at_global("overtaken", received=NOW - timedelta(minutes=40))
        self.at_origin("overtaken", received=NOW - timedelta(minutes=20))

        self.evaluate()

        self.assertEqual(self.missing(), [])

    @override_settings(WIS2WATCH_PROPAGATION_GRACE_MINUTES=15)
    def test_a_message_that_reached_the_world_within_grace_is_not_a_gap(self):
        self.at_origin("slow-but-fine", received=NOW - timedelta(minutes=25))
        self.at_global("slow-but-fine", received=NOW - timedelta(minutes=17))

        self.evaluate()

        self.assertEqual(self.missing(), [])

    @override_settings(WIS2WATCH_PROPAGATION_GRACE_MINUTES=15)
    def test_an_older_message_arriving_after_a_newer_one_is_judged_on_its_own(self):
        """A burst delivered out of order says nothing about any one message."""
        self.at_origin(
            "older",
            published=NOW - timedelta(hours=3),
            received=NOW - timedelta(minutes=5),
        )
        self.at_origin(
            "newer",
            published=NOW - timedelta(hours=1),
            received=NOW - timedelta(hours=1),
        )

        self.evaluate()

        self.assertEqual(self.missing(), ["newer"])


class LateArrivalTests(PropagationTestCase):
    """A gap that turns out to have been slower than the grace period.

    Leaving it open would leave the tool asserting something that stopped
    being true, which is the one thing a diagnostic must not do.
    """

    @override_settings(WIS2WATCH_PROPAGATION_GRACE_MINUTES=15)
    def test_a_notification_that_arrives_after_the_gap_was_recorded_closes_it(self):
        self.at_origin("very-slow", received=NOW - timedelta(minutes=20))
        self.evaluate()

        self.at_global("very-slow", received=NOW - timedelta(minutes=2))
        counts = self.evaluate()

        gap = self.gaps()[0]

        self.assertEqual(gap.resolved_at, NOW - timedelta(minutes=2))
        self.assertEqual(counts.resolved, 1)

    @override_settings(WIS2WATCH_PROPAGATION_GRACE_MINUTES=15)
    def test_a_closed_gap_is_no_longer_something_to_investigate(self):
        """It stays on the record as evidence of how slow that path was."""
        self.at_origin("very-slow", received=NOW - timedelta(minutes=20))
        self.evaluate()

        self.at_global("very-slow", received=NOW - timedelta(minutes=2))
        self.evaluate()

        self.assertEqual(list(PropagationGap.objects.open()), [])
        self.assertEqual(self.missing(), ["very-slow"])

    @override_settings(WIS2WATCH_PROPAGATION_GRACE_MINUTES=15)
    def test_a_gap_the_world_still_has_not_seen_stays_open(self):
        self.at_origin("still-lost", received=NOW - timedelta(minutes=20))

        self.evaluate()
        counts = self.evaluate()

        self.assertIsNone(self.gaps()[0].resolved_at)
        self.assertEqual(counts.resolved, 0)


class SuppressionTests(PropagationTestCase):
    """The tool's own blind spot must not read as a centre's failure.

    An origin broker this tool cannot currently reach means it is seeing part
    of what the centre publishes, or none of it. Comparing that partial view
    against the Global Broker's complete one would report the tool's own
    limitation as the centre's loss.
    """

    def test_a_node_whose_broker_is_unreachable_produces_no_findings(self):
        self.at_origin("seen-before-the-broker-went-dark")
        self.kenya_origin.is_reachable = False
        self.kenya_origin.save()

        counts = self.evaluate()

        self.assertEqual(self.missing(), [])
        self.assertEqual(counts.nodes_evaluated, 0)
        self.assertEqual(counts.nodes_suppressed, 1)

    def test_a_broker_nothing_has_attempted_yet_produces_no_findings(self):
        """Not attempted is not reachable, and guessing either way is wrong."""
        self.at_origin("unjudged")
        self.kenya_origin.is_reachable = None
        self.kenya_origin.save()

        self.evaluate()

        self.assertEqual(self.missing(), [])

    def test_a_broker_no_longer_being_watched_produces_no_findings(self):
        """A deactivated broker's last known reachability says nothing now."""
        self.at_origin("stale-view")
        self.kenya_origin.is_active = False
        self.kenya_origin.save()

        self.evaluate()

        self.assertEqual(self.missing(), [])

    def test_only_the_unreachable_node_is_suppressed(self):
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti ANM")
        origin_broker(djibouti, is_reachable=False)

        self.at_origin("kenyan")
        self.at_origin("djiboutian", node=djibouti)

        counts = self.evaluate()

        self.assertEqual(self.missing(), ["kenyan"])
        self.assertEqual(counts.nodes_evaluated, 1)
        self.assertEqual(counts.nodes_suppressed, 1)

    def test_a_second_vantage_on_one_centre_is_still_one_centre_suppressed(self):
        """The count is of centres passed over, not of rows behind them."""
        self.kenya_origin.is_reachable = False
        self.kenya_origin.save()
        origin_api(self.kenya)

        counts = self.evaluate()

        self.assertEqual(counts.nodes_evaluated, 0)
        self.assertEqual(counts.nodes_suppressed, 1)

    def test_a_centre_watched_through_one_vantage_is_not_also_suppressed(self):
        origin_api(self.kenya)

        counts = self.evaluate()

        self.assertEqual(counts.nodes_evaluated, 1)
        self.assertEqual(counts.nodes_suppressed, 0)

    def test_what_the_connection_record_says_now_does_not_unjudge_past_hours(self):
        """Blindness is read off the traffic, not off a side record.

        A connection marked down at the moment a run happens says nothing
        about the hours that run is judging, which are hours it heard the
        world throughout.
        """
        self.at_origin("really-lost")
        self.global_broker.is_reachable = False
        self.global_broker.save()

        self.evaluate()

        self.assertEqual(self.missing(), ["really-lost"])

    def test_gaps_already_recorded_survive_the_broker_going_dark(self):
        """What was observed while the view was good remains a real finding."""
        self.at_origin("lost-while-watching", received=NOW - timedelta(hours=2))
        self.evaluate()

        self.kenya_origin.is_reachable = False
        self.kenya_origin.save()
        self.evaluate()

        self.assertEqual(self.missing(), ["lost-while-watching"])


class UnheardWorldTests(PropagationTestCase):
    """The hours the tool cannot prove it was listening through.

    A Global Broker connection that drops does not stop the origin brokers
    delivering, so every notification observed at origin during the outage
    looks unpropagated -- and a broker does not redeliver what it sent while
    nobody was listening, so each of those would be a phantom gap nothing
    could ever close. The connection coming back does not make those hours
    judgeable; it only makes a recompute reach them.

    So blindness is read off the evidence: a notification is judged only if
    some Global Broker message arrived inside the same grace period it is
    being judged against. Hours with no arrivals in them are hours the tool
    was not hearing the world, whatever the reason -- a dropped connection, an
    instance switched off, a machine that never ran at all.

    One edge stays open, and is known: at the moment a connection comes back,
    the burst delivered on reconnect can fall inside the grace period of a
    notification observed shortly before it, which makes that one notification
    judgeable on evidence that arrived after its window opened rather than
    through it. See the module's own documentation.
    """

    the_world_is_heard = False

    OUTAGE_BEGAN = NOW - timedelta(hours=4)
    OUTAGE_ENDED = NOW - timedelta(hours=1)

    def the_connection_dropped_and_came_back(self):
        """What an outage leaves behind, which is not a record of itself.

        Traffic up to the moment the connection dropped, traffic from the
        moment it returned, and hours in between with nothing in them.
        """
        self.world_heard_throughout(until=self.OUTAGE_BEGAN)
        self.world_heard_throughout(since=self.OUTAGE_ENDED)

    def published_at_origin(self, notification_id, when):
        """A notification the centre published, and this tool saw, at one instant.

        Which hour a notification belongs to is the whole subject here, so
        both times are the same one and are said rather than defaulted.
        """
        return self.at_origin(notification_id, published=when, received=when)

    def test_a_notification_seen_at_origin_during_the_outage_is_never_a_gap(self):
        """The whole point: a recompute after recovery must not judge those hours."""
        self.the_connection_dropped_and_came_back()
        self.published_at_origin("published-in-the-dark", NOW - timedelta(hours=3))

        self.evaluate()

        self.assertEqual(self.missing(), [])

    def test_a_notification_lost_while_the_world_was_heard_is_still_a_gap(self):
        """Suppressing the blind hours must not suppress the ones either side."""
        self.the_connection_dropped_and_came_back()
        self.published_at_origin("lost-before-the-outage", NOW - timedelta(hours=6))
        self.published_at_origin("lost-after-the-outage", NOW - timedelta(minutes=40))

        self.evaluate()

        self.assertEqual(
            self.missing(), ["lost-after-the-outage", "lost-before-the-outage"]
        )

    def test_an_installation_that_has_heard_no_global_broker_traffic_judges_nothing(
        self,
    ):
        """A tool that has never heard the world knows nothing about the world.

        The failure mode this shape has to survive: not an outage but an
        instance that was simply never listening, which recorded no outage
        because nothing was running to record one. Judging on that would call
        every notification every centre published a gap.
        """
        self.at_origin("the-only-thing-anyone-saw")

        counts = self.evaluate()

        self.assertEqual(self.missing(), [])
        self.assertEqual(counts.detected, 0)

    def test_the_run_says_how_much_it_could_not_judge(self):
        """A run that judged nothing because it was blind is not good news.

        Nothing found and nothing judgeable read identically in a log unless
        the run says which it was.
        """
        self.the_connection_dropped_and_came_back()
        self.published_at_origin("in-the-dark", NOW - timedelta(hours=3))
        self.published_at_origin("also-in-the-dark", NOW - timedelta(hours=2))
        self.published_at_origin("really-lost", NOW - timedelta(hours=6))

        counts = self.evaluate()

        self.assertEqual(counts.detected, 1)
        self.assertEqual(counts.unheard, 2)
        self.assertIn("unheard=2", counts.summary)

    def test_a_gap_found_before_the_outage_keeps_the_moment_it_was_found(self):
        """The change is to what is newly found, not to what is on the record."""
        self.the_connection_dropped_and_came_back()
        self.published_at_origin("lost-before-the-outage", NOW - timedelta(hours=6))

        found_at = NOW - timedelta(hours=5)
        self.evaluate(now=found_at)

        counts = self.evaluate()

        self.assertEqual(self.missing(), ["lost-before-the-outage"])
        self.assertEqual(self.gaps()[0].detected_at, found_at)
        self.assertEqual(counts.detected, 0)

    def test_a_gap_recorded_before_the_outage_is_still_closed_by_a_late_arrival(self):
        """Blindness bounds what is found, not what is settled.

        A message the world turns out to have carried after all is closed
        whenever the evidence of it shows up, including the reconnection burst
        that ends an outage.
        """
        self.the_connection_dropped_and_came_back()
        self.published_at_origin("very-slow", NOW - timedelta(hours=6))
        self.evaluate(now=NOW - timedelta(hours=5))

        self.at_global(
            "very-slow",
            published=NOW - timedelta(hours=6),
            received=self.OUTAGE_ENDED,
        )
        counts = self.evaluate()

        self.assertEqual(self.gaps()[0].resolved_at, self.OUTAGE_ENDED)
        self.assertEqual(counts.resolved, 1)


class WindowTests(PropagationTestCase):
    """Detection reaches back only as far as the raw messages do."""

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_messages_older_than_the_retention_window_are_not_judged(self):
        """Beyond it the Global Broker's rows are gone too: absence proves nothing."""
        self.at_origin("ancient", published=NOW - timedelta(days=20))

        self.evaluate(window_hours=24 * 30)

        self.assertEqual(self.missing(), [])

    def test_messages_older_than_the_evaluation_window_are_not_revisited(self):
        """The window is a trailing recompute; older hours were judged already."""
        self.at_origin("last-week", published=NOW - timedelta(days=7))

        self.evaluate(window_hours=48)

        self.assertEqual(self.missing(), [])

    def test_messages_inside_the_evaluation_window_are_judged(self):
        self.at_origin("yesterday", published=NOW - timedelta(hours=30))

        self.evaluate(window_hours=48)

        self.assertEqual(self.missing(), ["yesterday"])


class RepeatedRunTests(PropagationTestCase):
    """The job recomputes a window, so running it twice must change nothing."""

    def test_a_gap_is_recorded_once_however_often_the_job_runs(self):
        self.at_origin("lost-one")

        self.evaluate()
        counts = self.evaluate(now=NOW + timedelta(minutes=15))

        self.assertEqual(self.missing(), ["lost-one"])
        self.assertEqual(counts.detected, 0)

    def test_the_first_sighting_of_a_gap_is_the_one_that_stands(self):
        self.at_origin("lost-one")

        self.evaluate()
        self.evaluate(now=NOW + timedelta(hours=1))

        self.assertEqual(self.gaps()[0].detected_at, NOW)

    def test_a_redelivery_at_origin_does_not_double_the_finding(self):
        self.at_origin("lost-one")
        self.evaluate()

        # The same notification, observed a second time from a second origin
        # broker record -- what a broker record rewritten by a sync leaves.
        self.kenya_origin.delete()
        second_record = origin_broker(self.kenya, is_reachable=True)
        self.observe(second_record, "lost-one", node=self.kenya)

        self.evaluate()

        self.assertEqual(self.missing(), ["lost-one"])


class EvidenceHorizonTests(PropagationTestCase):
    """Which of the recorded gaps the tool can still stand behind.

    A gap is written down because the rows that prove it expire, and past the
    raw retention window they have: the Global Broker copy that would close it
    is gone, so nothing can settle it either way ever again. The row stays --
    it is the last thing holding the UUID -- but it has stopped being
    something the tool is judging, and a gap this tool merely stopped being
    able to check must not read like one the world is still not carrying.
    """

    def gap(self, notification_id, *, published):
        return PropagationGap.objects.create(
            node=self.kenya,
            origin_source=self.kenya_origin,
            notification_id=notification_id,
            topic=f"origin/a/wis2/{self.kenya.centre_id}/data/core/weather",
            published_at=published,
            observed_at_origin=published,
            detected_at=published + timedelta(minutes=20),
        )

    def within(self, now=NOW):
        return [
            gap.notification_id
            for gap in PropagationGap.objects.within_evidence(now).order_by(
                "notification_id"
            )
        ]

    def beyond(self, now=NOW):
        return [
            gap.notification_id
            for gap in PropagationGap.objects.beyond_evidence(now).order_by(
                "notification_id"
            )
        ]

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_the_two_sides_of_the_retention_cutoff_are_told_apart(self):
        self.gap("last-fortnight", published=NOW - timedelta(days=20))
        self.gap("this-morning", published=NOW - timedelta(hours=2))

        self.assertEqual(self.within(), ["this-morning"])
        self.assertEqual(self.beyond(), ["last-fortnight"])

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_a_gap_published_on_the_cutoff_is_still_being_judged(self):
        """The boundary belongs to the side whose rows are still held."""
        self.gap("on-the-hour", published=raw_retention_cutoff(now=NOW))

        self.assertEqual(self.within(), ["on-the-hour"])
        self.assertEqual(self.beyond(), [])

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=30)
    def test_the_horizon_follows_how_long_raw_messages_are_kept(self):
        """An installation keeping messages longer can check for longer."""
        self.gap("last-fortnight", published=NOW - timedelta(days=20))

        self.assertEqual(self.within(), ["last-fortnight"])
        self.assertEqual(self.beyond(), [])

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_a_gap_passes_the_horizon_as_time_goes_on(self):
        """Nothing has to run for a gap to stop being checkable."""
        self.gap("this-morning", published=NOW - timedelta(hours=2))

        later = NOW + timedelta(days=15)

        self.assertEqual(self.within(later), [])
        self.assertEqual(self.beyond(later), ["this-morning"])

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_a_gap_past_the_horizon_is_still_open_and_still_recorded(self):
        """Bounding what the reports lead with is not forgetting it happened."""
        self.gap("last-fortnight", published=NOW - timedelta(days=20))

        self.assertEqual(
            [gap.notification_id for gap in PropagationGap.objects.open()],
            ["last-fortnight"],
        )
