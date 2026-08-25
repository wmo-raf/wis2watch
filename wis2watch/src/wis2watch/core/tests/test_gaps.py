"""The gap reports, against a seeded database.

These are the findings nobody asked for, so the way they fail is by being
unreadable rather than by raising: a declared-but-silent report full of
stations closed a decade ago, a propagation report full of gaps that exist
only because this tool stopped listening, an unattributed rate that counts the
same publication once per vantage point that saw it.

What is seeded here is therefore the provenance combinations themselves --
each of the three sources that can declare a station, present and absent in
every combination that changes the answer -- and the two filters the reports
would be useless without: OSCAR's operational status, and whether a centre's
own broker was reachable at all when the gap was recorded.
"""

from datetime import timedelta

from django.test import TestCase, override_settings

from wis2watch.core.analysis import (
    GAP_REPORTS,
    OriginTransport,
    gap_report,
    gap_report_summaries,
    propagation_gaps,
    stations_declared_but_silent,
    stations_transmitting_undeclared,
    unattributed_rates,
    unregistered_centres,
)
from wis2watch.core.interpretation import OPERATIONAL
from wis2watch.core.models import (
    Dataset,
    HardFailure,
    HourlyRollup,
    MessageSource,
    PropagationGap,
    Station,
    StationSource,
    UnregisteredCentre,
    WIS2Node,
)
from wis2watch.core.tests.support import at, origin_api, origin_broker


NOW = at("2026-08-11T12:00:00")


class GapReportTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = self.node("ke-meteo")

    def node(self, centre_id):
        return WIS2Node.objects.create(centre_id=centre_id, name=centre_id.upper())

    def station(self, wigos_id, **kwargs):
        station, _ = Station.objects.get_or_create(wigos_id=wigos_id, defaults=kwargs)

        return station

    def in_oscar(self, wigos_id, *, status=OPERATIONAL, **kwargs):
        """A station the country officially declares, as OSCAR reports it."""
        station = self.station(wigos_id, operating_status=status, **kwargs)

        StationSource.objects.create(
            station=station,
            source_type=StationSource.OSCAR,
        )

        return station

    def in_registry(self, wigos_id, *, node=None, local_name=""):
        """A station the node's own registry declares."""
        station = self.station(wigos_id)

        StationSource.objects.create(
            station=station,
            source_type=StationSource.NODE_REGISTRY,
            node=self.kenya if node is None else node,
            local_name=local_name,
        )

        return station

    def transmitted(self, wigos_id, *, node=None, hours_ago=1):
        """A station heard transmitting under a centre's topics."""
        station = self.station(wigos_id)

        StationSource.objects.create(
            station=station,
            source_type=StationSource.OBSERVED,
            node=self.kenya if node is None else node,
            last_seen=NOW - timedelta(hours=hours_ago),
        )

        return station


class DeclaredButSilentTests(GapReportTestCase):
    """Stations a country declares to the world that have never been heard."""

    def report(self):
        return stations_declared_but_silent(now=NOW)

    def wigos_ids(self):
        return [row.wigos_id for row in self.report()]

    def test_an_operational_station_never_heard_from_is_reported(self):
        self.in_oscar("0-20000-0-63741", name="Dagoretti", territory="KEN")

        (row,) = self.report()

        self.assertEqual(row.wigos_id, "0-20000-0-63741")
        self.assertEqual(row.name, "Dagoretti")
        self.assertEqual(row.territory, "KEN")

    def test_a_station_that_has_transmitted_is_not_reported(self):
        self.in_oscar("0-20000-0-63741")
        self.transmitted("0-20000-0-63741")

        self.assertEqual(self.wigos_ids(), [])

    def test_a_station_that_transmitted_long_ago_is_not_reported(self):
        """This report is about never, not about lately.

        A station that stopped in March is the node detail page's finding, and
        naming it here as well would report one fault twice under two names.
        """
        self.in_oscar("0-20000-0-63741")
        self.transmitted("0-20000-0-63741", hours_ago=24 * 200)

        self.assertEqual(self.wigos_ids(), [])

    def test_a_station_oscar_does_not_call_operational_is_not_reported(self):
        for status in ("partlyOperational", "closed", "unknown", ""):
            with self.subTest(status=status):
                Station.objects.all().delete()
                self.in_oscar("0-20000-0-63741", status=status)

                self.assertEqual(self.wigos_ids(), [])

    def test_a_station_only_the_node_registry_declares_is_not_reported(self):
        """OSCAR is what this report is about: it is the official declaration."""
        self.in_registry("0-20000-0-63741")

        self.assertEqual(self.wigos_ids(), [])

    def test_the_centre_whose_registry_also_declares_it_is_named(self):
        """Two different conversations, so the row says which one to have.

        A station its own centre knows about and never transmits is a centre to
        ask; a station nobody but OSCAR has heard of is a registration to
        correct.
        """
        self.in_oscar("0-20000-0-63741")
        self.in_registry("0-20000-0-63741")

        (row,) = self.report()

        self.assertEqual(row.registry_centre_id, "ke-meteo")

    def test_a_station_two_centres_declare_names_the_same_one_every_time(self):
        """An answer that changes between readings is not one anybody can act on."""
        self.in_oscar("0-20000-0-63741")
        self.in_registry("0-20000-0-63741", node=self.node("ug-unma"))
        self.in_registry("0-20000-0-63741")

        for _reading in range(2):
            (row,) = self.report()

            self.assertEqual(row.registry_centre_id, "ke-meteo")

    def test_a_station_no_centre_declares_carries_no_centre(self):
        self.in_oscar("0-20000-0-63741")

        (row,) = self.report()

        self.assertEqual(row.registry_centre_id, "")

    def test_the_report_reads_by_territory_and_then_by_name(self):
        self.in_oscar("0-20000-0-63741", name="Wilson", territory="KEN")
        self.in_oscar("0-20000-0-63737", name="Dagoretti", territory="KEN")
        self.in_oscar("0-20000-0-63125", name="Entebbe", territory="UGA")

        self.assertEqual(
            [row.name for row in self.report()], ["Dagoretti", "Wilson", "Entebbe"]
        )


class TransmittingUndeclaredTests(GapReportTestCase):
    """Stations the world is hearing from that no registry admits to."""

    def report(self):
        return stations_transmitting_undeclared(now=NOW)

    def wigos_ids(self):
        return [row.wigos_id for row in self.report()]

    def test_a_station_declared_nowhere_is_reported_with_its_centre(self):
        self.transmitted("0-20000-0-63741", hours_ago=3)

        (row,) = self.report()

        self.assertEqual(row.wigos_id, "0-20000-0-63741")
        self.assertEqual(row.centre_id, "ke-meteo")
        self.assertEqual(row.last_transmitted, NOW - timedelta(hours=3))
        self.assertEqual(row.hours_quiet, 3)

    def test_a_station_oscar_declares_is_not_reported(self):
        self.in_oscar("0-20000-0-63741")
        self.transmitted("0-20000-0-63741")

        self.assertEqual(self.wigos_ids(), [])

    def test_a_station_its_own_centre_declares_is_not_reported(self):
        self.in_registry("0-20000-0-63741")
        self.transmitted("0-20000-0-63741")

        self.assertEqual(self.wigos_ids(), [])

    def test_a_station_another_centre_declares_is_not_reported(self):
        """Declared anywhere is declared: the registration gap is closed."""
        uganda = self.node("ug-unma")
        self.in_registry("0-20000-0-63741", node=uganda)
        self.transmitted("0-20000-0-63741")

        self.assertEqual(self.wigos_ids(), [])

    def test_a_station_transmitting_under_two_centres_is_reported_for_each(self):
        """One station, two findings: each centre has its own registration to fix."""
        uganda = self.node("ug-unma")
        self.transmitted("0-20000-0-63741")
        self.transmitted("0-20000-0-63741", node=uganda)

        self.assertEqual(
            sorted(row.centre_id for row in self.report()), ["ke-meteo", "ug-unma"]
        )

    def test_a_station_transmitting_under_no_registered_centre_is_still_reported(self):
        """A centre with no catalogue record still has stations.

        Dropping them would hide the traffic most worth asking about: a centre
        nobody registered, transmitting for stations nobody declared.
        """
        station = self.station("0-20000-0-63741")
        StationSource.objects.create(
            station=station,
            source_type=StationSource.OBSERVED,
            node=None,
            last_seen=NOW,
        )

        (row,) = self.report()

        self.assertEqual(row.wigos_id, "0-20000-0-63741")
        self.assertEqual(row.centre_id, "")

    def test_the_report_reads_by_centre_and_then_by_identifier(self):
        uganda = self.node("ug-unma")
        self.transmitted("0-20000-0-63741")
        self.transmitted("0-20000-0-63737")
        self.transmitted("0-20000-0-63125", node=uganda)

        self.assertEqual(
            [(row.centre_id, row.wigos_id) for row in self.report()],
            [
                ("ke-meteo", "0-20000-0-63737"),
                ("ke-meteo", "0-20000-0-63741"),
                ("ug-unma", "0-20000-0-63125"),
            ],
        )


class PropagationGapTestCase(GapReportTestCase):
    """One recorded gap, and the report that lists it."""

    def report(self):
        return propagation_gaps(now=NOW)

    def gap(
        self,
        node=None,
        *,
        notification_id="d9a1",
        hours_ago=2,
        resolved=False,
        dataset=None,
        source=None,
    ):
        node = node or self.kenya

        return PropagationGap.objects.create(
            node=node,
            origin_source=node.origin_source if source is None else source,
            dataset=dataset,
            notification_id=notification_id,
            topic=f"origin/a/wis2/{node.centre_id}/data/core/weather/surface-based-observations/synop",
            published_at=NOW - timedelta(hours=hours_ago),
            observed_at_origin=NOW - timedelta(hours=hours_ago),
            detected_at=NOW - timedelta(hours=hours_ago) + timedelta(minutes=20),
            resolved_at=NOW if resolved else None,
        )


class PropagationGapTests(PropagationGapTestCase):
    """Notifications a centre published that the world never received."""

    def test_an_open_gap_at_a_reachable_centre_names_the_notification(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="d9a1", hours_ago=2)

        (row,) = self.report()

        self.assertEqual(row.centre_id, "ke-meteo")
        self.assertEqual(row.notification_id, "d9a1")
        self.assertIn("synop", row.topic)
        self.assertEqual(row.published_at, NOW - timedelta(hours=2))
        self.assertEqual(row.hours_missing, 2)

    def test_a_gap_the_world_has_since_carried_is_not_reported(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(resolved=True)

        self.assertEqual(self.report(), [])

    def test_gaps_at_a_centre_whose_own_broker_is_unreachable_are_not_reported(self):
        """The tool's blind spot is not the centre's loss.

        A broker that has gone dark since the gaps were recorded makes every
        one of them unverifiable, and reporting them anyway sends somebody to a
        centre to ask about messages this tool may simply have stopped seeing.
        """
        origin_broker(self.kenya, is_reachable=False)
        self.gap()

        self.assertEqual(self.report(), [])

    def test_gaps_at_a_centre_whose_broker_has_not_been_dialled_are_not_reported(self):
        origin_broker(self.kenya)
        self.gap()

        self.assertEqual(self.report(), [])

    def test_gaps_at_a_centre_whose_broker_is_switched_off_are_not_reported(self):
        origin_broker(self.kenya, is_reachable=True, is_active=False)
        self.gap()

        self.assertEqual(self.report(), [])

    def test_gaps_at_a_centre_with_no_broker_of_its_own_are_not_reported(self):
        self.gap()

        self.assertEqual(self.report(), [])

    def test_the_report_reads_newest_first(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="older", hours_ago=6)
        self.gap(notification_id="newer", hours_ago=1)

        self.assertEqual(
            [row.notification_id for row in self.report()], ["newer", "older"]
        )

    def test_a_gap_seen_at_the_centres_own_broker_says_so(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap()

        (row,) = self.report()

        self.assertEqual(row.origin_transport, OriginTransport.BROKER)

    def test_a_gap_seen_in_the_centres_archive_says_so(self):
        """How the tool knows, on a finding that goes to a met service by email.

        A centre whose broker is dark is watched through its archive, and a
        gap that did not say so would read as evidence from a broker the
        centre knows nobody outside can reach.
        """
        origin_broker(self.kenya, is_reachable=False)
        archive = origin_api(self.kenya, is_reachable=True)
        self.gap(source=archive)

        (row,) = self.report()

        self.assertEqual(row.origin_transport, OriginTransport.ARCHIVE)
        self.assertEqual(
            row.origin_transport_label, OriginTransport.label(OriginTransport.ARCHIVE)
        )

    def test_a_gap_whose_vantage_point_is_gone_does_not_invent_one(self):
        """Something saw it; what has been lost is the note of which."""
        origin_broker(self.kenya, is_reachable=True)
        archive = origin_api(self.kenya, is_reachable=True)
        self.gap(source=archive)
        archive.delete()

        (row,) = self.report()

        self.assertEqual(row.origin_transport, OriginTransport.UNRECORDED)

    def test_the_notice_a_digest_sends_names_the_transport(self):
        origin_broker(self.kenya, is_reachable=False)
        archive = origin_api(self.kenya, is_reachable=True)
        self.gap(source=archive)

        (row,) = self.report()
        notice = gap_report("propagation-gaps").describe_row(row)

        archive_label = str(OriginTransport.label(OriginTransport.ARCHIVE))

        self.assertIn(archive_label, notice.summary)

    def test_a_gap_names_the_dataset_where_the_registry_knows_one(self):
        origin_broker(self.kenya, is_reachable=True)
        dataset = Dataset.objects.create(
            node=self.kenya,
            identifier="urn:wmo:md:ke-meteo:synop",
            title="Kenya SYNOP",
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy="origin/a/wis2/ke-meteo/data/core/synop",
            raw_json={},
        )
        self.gap(dataset=dataset)

        (row,) = self.report()

        self.assertEqual(row.dataset_title, "Kenya SYNOP")


@override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
class PropagationGapHorizonTests(PropagationGapTestCase):
    """What the propagation report does as its gaps accumulate.

    An open gap outlives the evidence that would close it: past the raw
    retention window the Global Broker rows that could settle it have been
    expired, so it can never be closed and never be checked again. Left in the
    report those rows accumulate for ever, and last spring's gaps sitting
    permanently above this morning's is how a report stops being opened --
    which is the failure the reports exist to prevent.

    So the report is bounded at the horizon its evidence ends at, and says so.
    A bound that goes unsaid is truncation, and a report that quietly drops
    findings is worse than one that is long.
    """

    def note(self, now=NOW):
        return gap_report("propagation-gaps").describe_bound(now=now)

    def counted(self, now=NOW):
        return {
            summary.slug: summary.count
            for summary in gap_report_summaries(now=now)
        }

    def test_a_gap_whose_evidence_has_expired_is_not_listed(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)

        self.assertEqual(self.report(), [])

    def test_a_gap_either_side_of_the_cutoff_is_listed_or_left_out(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)
        self.gap(notification_id="this-morning", hours_ago=2)

        self.assertEqual(
            [row.notification_id for row in self.report()], ["this-morning"]
        )

    def test_the_report_says_how_many_gaps_it_left_out_and_why(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)
        self.gap(notification_id="the-one-before", hours_ago=24 * 21)

        note = self.note()

        self.assertIn("2 older gaps", note)
        self.assertIn("2026-07-28", note)
        self.assertIn("expired", note)

    def test_the_index_says_what_the_count_it_shows_left_out(self):
        """The count is what decides whether the report is worth opening."""
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)

        (summary,) = [
            summary
            for summary in gap_report_summaries(now=NOW)
            if summary.slug == "propagation-gaps"
        ]

        self.assertEqual(summary.count, 0)
        self.assertIn("1 older gap is not listed", summary.bound)

    def test_a_report_that_bounds_nothing_says_nothing_on_the_index(self):
        bounds = {
            summary.slug: summary.bound for summary in gap_report_summaries(now=NOW)
        }

        self.assertEqual(set(bounds.values()), {None})

    def test_a_report_listing_everything_it_holds_says_nothing(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="this-morning", hours_ago=2)

        self.assertIsNone(self.note())

    def test_an_old_gap_the_world_turned_out_to_carry_is_not_left_out(self):
        """It was settled while the evidence stood; nothing is being withheld."""
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20, resolved=True)

        self.assertIsNone(self.note())

    def test_gaps_withheld_for_an_unreachable_broker_are_not_counted_as_old(self):
        """One sentence, one reason: those are withheld for the broker."""
        origin_broker(self.kenya, is_reachable=False)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)

        self.assertIsNone(self.note())

    def test_the_index_counts_what_the_report_actually_lists(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)
        self.gap(notification_id="this-morning", hours_ago=2)

        self.assertEqual(self.counted()["propagation-gaps"], 1)


@override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
class PropagationGapUnsettledTests(PropagationGapTestCase):
    """Which centres left the report for want of anything left to check.

    Leaving a report is ordinarily a problem going away, and the digest reads
    it that way once it has stood long enough to mean something. Past the
    horizon it is not: the Global Broker rows that would settle the gap have
    been expired, so the centre leaves for good with the question still open,
    and no amount of waiting will bring it back. Only the report knows which
    of the two happened, so this is where it says.
    """

    def unsettled(self, now=NOW):
        return gap_report("propagation-gaps").find_unsettled(now=now)

    def note(self, now=NOW):
        return gap_report("propagation-gaps").describe_bound(now=now)

    def test_a_centre_whose_gaps_passed_the_horizon_is_named(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)

        self.assertEqual(self.unsettled(), {"ke-meteo"})

    def test_a_centre_whose_gaps_can_still_be_checked_is_not_named(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="this-morning", hours_ago=2)

        self.assertEqual(self.unsettled(), set())

    def test_an_old_gap_the_world_turned_out_to_carry_settles_it(self):
        """Closed while the evidence stood: that centre really did recover."""
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20, resolved=True)

        self.assertEqual(self.unsettled(), set())

    def test_a_centre_whose_broker_is_unreachable_is_not_named(self):
        """A different absence, and one that can end: the grace is for that."""
        origin_broker(self.kenya, is_reachable=False)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)

        self.assertEqual(self.unsettled(), set())

    def test_a_centre_is_named_once_however_many_gaps_it_holds(self):
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)
        self.gap(notification_id="the-one-before", hours_ago=24 * 21)

        self.assertEqual(self.unsettled(), {"ke-meteo"})

    def test_each_centre_answers_for_itself(self):
        uganda = self.node("ug-unma")
        origin_broker(self.kenya, is_reachable=True)
        origin_broker(uganda, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)
        self.gap(uganda, notification_id="this-morning", hours_ago=2)

        self.assertEqual(self.unsettled(), {"ke-meteo"})

    def test_what_is_named_is_what_the_bound_sentence_counts(self):
        """One horizon, asked two ways: they cannot be allowed to differ."""
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)

        self.assertEqual(self.unsettled(), {"ke-meteo"})
        self.assertIn("1 older gap is not listed", self.note())

    def test_a_centre_heard_from_since_is_not_named(self):
        """Something it published was seen to arrive after the question closed.

        The old gap is still unanswerable and always will be. What has changed
        is that the path has been observed working since, so a centre that
        leaves the report now leaves it having been heard from -- which is a
        clearing somebody can be told about.
        """
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)
        self.gap(notification_id="yesterday", hours_ago=24, resolved=True)

        self.assertEqual(self.unsettled(), set())

    def test_a_centre_whose_last_word_is_unanswered_is_still_named(self):
        """The late arrival came first; the silence since is the unanswerable part."""
        origin_broker(self.kenya, is_reachable=True)
        self.gap(notification_id="the-one-before", hours_ago=24 * 21, resolved=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)

        self.assertEqual(self.unsettled(), {"ke-meteo"})

    def test_another_centres_late_arrival_says_nothing_about_this_one(self):
        uganda = self.node("ug-unma")
        origin_broker(self.kenya, is_reachable=True)
        origin_broker(uganda, is_reachable=True)
        self.gap(notification_id="last-fortnight", hours_ago=24 * 20)
        self.gap(uganda, notification_id="yesterday", hours_ago=24, resolved=True)

        self.assertEqual(self.unsettled(), {"ke-meteo"})

    def test_a_report_bounded_by_its_filters_leaves_nothing_unsettled(self):
        """Every other report's absences are ones the grace period can rescue."""
        unsettled = {
            report.slug: report.find_unsettled(now=NOW)
            for report in GAP_REPORTS
            if report.slug != "propagation-gaps"
        }

        self.assertEqual([slug for slug, keys in unsettled.items() if keys], [])


class UnregisteredCentreTests(GapReportTestCase):
    """Centres of the region publishing that no catalogue has indexed."""

    def report(self):
        return unregistered_centres(now=NOW)

    def seen(self, centre_id, *, first_seen_hours_ago=48, registered=False):
        return UnregisteredCentre.objects.create(
            centre_id=centre_id,
            country=centre_id[:2].upper(),
            sample_topic=f"origin/a/wis2/{centre_id}/data/core/synop",
            first_seen_at=NOW - timedelta(hours=first_seen_hours_ago),
            last_seen_at=NOW - timedelta(hours=1),
            registered_at=NOW if registered else None,
        )

    def test_a_centre_the_registry_does_not_know_is_reported(self):
        self.seen("ml-meteo", first_seen_hours_ago=48)

        (row,) = self.report()

        self.assertEqual(row.centre_id, "ml-meteo")
        self.assertEqual(row.country_name, "Mali")
        self.assertIn("ml-meteo", row.sample_topic)
        self.assertEqual(row.hours_unregistered, 48)

    def test_a_centre_the_registry_has_caught_up_with_is_not_reported(self):
        self.seen("ml-meteo", registered=True)

        self.assertEqual(self.report(), [])

    def test_the_report_reads_longest_unregistered_first(self):
        self.seen("ml-meteo", first_seen_hours_ago=10)
        self.seen("td-meteo", first_seen_hours_ago=200)

        self.assertEqual(
            [row.centre_id for row in self.report()], ["td-meteo", "ml-meteo"]
        )


class FrozenRegistryTests(GapReportTestCase):
    """What the unregistered report may say while the registry is frozen.

    The report is the wildcard sweep's answer to a question it puts to the
    registry: is this centre publishing that no catalogue has indexed? While
    the catalogue that writes the registry is not syncing, the question has no
    answer -- a centre with no record cannot be told from a centre whose
    record this tool has not read -- and the report would go on naming centres
    with more and more confidence the longer the sync stayed broken.

    So it is withheld, the way propagation gaps are withheld at a centre whose
    own broker has gone dark, and for the same reason: sending somebody to ask
    a centre about a registration that may be perfectly in order is how a
    diagnostic stops being believed.
    """

    def report(self):
        return unregistered_centres(now=NOW)

    def note(self):
        return gap_report("unregistered-centres").describe_bound(now=NOW)

    def unsettled(self):
        return gap_report("unregistered-centres").find_unsettled(now=NOW)

    def counted(self):
        return {
            summary.slug: summary.count for summary in gap_report_summaries(now=NOW)
        }["unregistered-centres"]

    def seen(self, centre_id):
        return UnregisteredCentre.objects.create(
            centre_id=centre_id,
            country=centre_id[:2].upper(),
            sample_topic=f"origin/a/wis2/{centre_id}/data/core/synop",
            first_seen_at=NOW - timedelta(hours=48),
            last_seen_at=NOW - timedelta(hours=1),
        )

    def registry_frozen(self, *, cleared=False):
        return HardFailure.objects.create(
            kind=HardFailure.CATALOGUE_WRITER_STALE,
            detail="io-wis2dev-12-test: no records read since 2026-08-09 06:00 UTC",
            started_at=NOW - timedelta(days=2),
            notified_at=NOW - timedelta(days=2),
            resolved_at=NOW - timedelta(hours=1) if cleared else None,
        )

    def test_a_centre_is_withheld_while_the_registry_is_frozen(self):
        self.seen("ml-meteo")
        self.registry_frozen()

        self.assertEqual(self.report(), [])

    def test_the_index_count_is_withheld_with_it(self):
        """A count is a claim too, and it is the one people act on."""
        self.seen("ml-meteo")
        self.registry_frozen()

        self.assertEqual(self.counted(), 0)

    def test_the_report_says_what_it_is_holding_and_why(self):
        self.seen("ml-meteo")
        self.seen("td-meteo")
        self.registry_frozen()

        note = self.note()

        self.assertIn("2 centres", note)
        self.assertIn("not syncing", note)

    def test_an_empty_report_still_says_it_is_withholding(self):
        """With nothing listed and nothing said it would read as all clear."""
        self.registry_frozen()

        self.assertIn("not syncing", self.note())

    def test_a_withheld_centre_has_stopped_being_checkable(self):
        """What the digest needs, so it lets the finding go rather than
        announcing a registration nobody made."""
        self.seen("ml-meteo")
        self.registry_frozen()

        self.assertEqual(self.unsettled(), {"ml-meteo"})

    def test_a_registry_being_rebuilt_again_withholds_nothing(self):
        self.seen("ml-meteo")
        self.registry_frozen(cleared=True)

        self.assertEqual([row.centre_id for row in self.report()], ["ml-meteo"])
        self.assertIsNone(self.note())
        self.assertEqual(self.unsettled(), set())

    def test_nothing_else_is_withheld_by_a_frozen_registry(self):
        """The other four are about the region rather than about the registry."""
        self.registry_frozen()

        counted = self.counted()
        bounds = {
            summary.slug: summary.bound
            for summary in gap_report_summaries(now=NOW)
            if summary.slug != "unregistered-centres"
        }

        self.assertEqual(counted, 0)
        self.assertEqual(set(bounds.values()), {None})


class UnattributedRateTests(GapReportTestCase):
    """Which centres publish without saying which station the data came from."""

    def report(self, **kwargs):
        kwargs.setdefault("now", NOW)
        return unattributed_rates(**kwargs)

    def by_centre(self, **kwargs):
        return {row.centre_id: row for row in self.report(**kwargs)}

    def rollup(self, node=None, *, hours_ago=1, count=1, station=None, source=None):
        return HourlyRollup.objects.create(
            hour=NOW.replace(minute=0, second=0, microsecond=0)
            - timedelta(hours=hours_ago),
            source=source or self.global_broker,
            node=node or self.kenya,
            station=station,
            message_count=count,
        )

    def test_a_centre_naming_no_station_is_reported_at_the_full_rate(self):
        self.rollup(count=8)

        (row,) = self.report()

        self.assertEqual(row.centre_id, "ke-meteo")
        self.assertEqual(row.message_count, 8)
        self.assertEqual(row.unattributed_count, 8)
        self.assertEqual(row.rate, 1)

    def test_a_centre_naming_every_station_is_reported_at_no_rate(self):
        self.rollup(count=8, station=self.station("0-20000-0-63741"))

        (row,) = self.report()

        self.assertEqual(row.unattributed_count, 0)
        self.assertEqual(row.rate, 0)

    def test_the_rate_is_the_share_of_messages_carrying_no_station(self):
        self.rollup(count=3)
        self.rollup(count=1, station=self.station("0-20000-0-63741"))

        (row,) = self.report()

        self.assertEqual(row.message_count, 4)
        self.assertEqual(row.unattributed_count, 3)
        self.assertEqual(row.rate, 0.75)
        self.assertEqual(row.percent, 75)

    def test_only_what_the_world_saw_is_counted(self):
        """One publication is one message, however many vantage points saw it.

        The same notification is observed at the centre's own broker and again
        on every cache that carried it, so counting them all would report a
        healthy centre's traffic several times over.
        """
        origin = origin_broker(self.kenya)
        cache = MessageSource.objects.create(
            name="Global Cache",
            source_type=MessageSource.GLOBAL_CACHE,
            host="cache.example.int",
        )
        self.rollup(count=2)
        self.rollup(count=2, source=origin)
        self.rollup(count=2, source=cache)

        (row,) = self.report()

        self.assertEqual(row.message_count, 2)

    def test_traffic_older_than_the_window_is_not_counted(self):
        self.rollup(count=5, hours_ago=1)
        self.rollup(count=100, hours_ago=500)

        (row,) = self.report(window_hours=24)

        self.assertEqual(row.message_count, 5)

    def test_a_centre_with_no_traffic_in_the_window_is_absent(self):
        """There is no rate to report of a centre that published nothing.

        A centre that has gone quiet is the overview's finding; reporting it
        here at nought per cent would read as a centre doing this right.
        """
        self.node("dj-anm")
        self.rollup(count=1)

        self.assertEqual(list(self.by_centre()), ["ke-meteo"])

    def test_the_report_reads_worst_rate_first(self):
        uganda = self.node("ug-unma")
        self.rollup(count=4, station=self.station("0-20000-0-63741"))
        self.rollup(node=uganda, count=4)

        self.assertEqual(
            [row.centre_id for row in self.report()], ["ug-unma", "ke-meteo"]
        )

    def test_a_centre_carries_the_country_it_is_registered_under(self):
        self.rollup(count=1)

        (row,) = self.report()

        self.assertEqual(row.country_name, "Kenya")


class GapReportSummaryTests(GapReportTestCase):
    """The index that says which of the five is worth opening."""

    def test_every_report_is_summarised(self):
        self.assertEqual(
            [summary.slug for summary in gap_report_summaries(now=NOW)],
            [report.slug for report in GAP_REPORTS],
        )

    def test_a_summary_counts_what_its_report_would_list(self):
        self.in_oscar("0-20000-0-63741")
        self.in_oscar("0-20000-0-63737")
        self.transmitted("0-20000-0-63125")

        counts = {
            summary.slug: summary.count for summary in gap_report_summaries(now=NOW)
        }

        self.assertEqual(counts["declared-but-silent"], 2)
        self.assertEqual(counts["transmitting-undeclared"], 1)
        self.assertEqual(counts["propagation-gaps"], 0)

    def test_the_unattributed_summary_counts_the_centres_with_something_to_answer_for(
        self,
    ):
        """Not the rows it lists, which are every centre publishing.

        An index promising findings that turn out to be centres doing it right
        is an index that stops being read.
        """
        attributing = self.node("ug-unma")
        HourlyRollup.objects.create(
            hour=NOW.replace(minute=0, second=0, microsecond=0),
            source=self.global_broker,
            node=self.kenya,
            message_count=4,
        )
        HourlyRollup.objects.create(
            hour=NOW.replace(minute=0, second=0, microsecond=0),
            source=self.global_broker,
            node=attributing,
            station=self.station("0-20000-0-63741"),
            message_count=4,
        )

        counts = {
            summary.slug: summary.count for summary in gap_report_summaries(now=NOW)
        }

        self.assertEqual(counts["unattributed-messages"], 1)
        self.assertEqual(len(unattributed_rates(now=NOW)), 2)

    def test_a_summary_carries_what_the_report_is_for(self):
        (summary,) = [
            summary
            for summary in gap_report_summaries(now=NOW)
            if summary.slug == "declared-but-silent"
        ]

        self.assertTrue(summary.title)
        self.assertTrue(summary.description)
