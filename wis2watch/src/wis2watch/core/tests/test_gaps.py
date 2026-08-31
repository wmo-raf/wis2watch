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
    DeclarationDrift,
    DeclaringCentre,
    OriginTransport,
    RegistryStanding,
    catalogues_that_keep_failing,
    datasets_out_of_step,
    datasets_out_of_step_unasked_centres,
    gap_report,
    gap_report_summaries,
    propagation_gaps,
    registries_not_answering,
    registries_not_answering_caveat,
    stations_declared_but_silent,
    stations_declared_but_silent_unasked_centres,
    stations_transmitting_undeclared,
    syncs_stepping_over_records,
    unattributed_rates,
    unregistered_centres,
)
from wis2watch.core.interpretation import OPERATIONAL
from wis2watch.core.models import (
    Dataset,
    DatasetSource,
    GlobalDiscoveryCatalogue,
    HardFailure,
    HourlyRollup,
    MessageSource,
    PropagationGap,
    Station,
    StationSource,
    SyncLog,
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

    def node(self, centre_id, *, registry=True):
        """A centre, by default one there is somewhere to ask for its stations.

        ``registry=False`` is the centre whose catalogue records advertise no
        address for it: nothing has ever asked it what it declares, and every
        report has to keep that apart from a centre that answered and declared
        nothing.
        """
        return WIS2Node.objects.create(
            centre_id=centre_id,
            name=centre_id.upper(),
            base_url=f"https://{centre_id}.example.int" if registry else "",
        )

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


class UnaskedCentresTests(GapReportTestCase):
    """What the declared-but-silent report cannot say about who declares a station.

    The report's "Declared by centre" column is blank two ways. No centre's
    registry names the station, which is a registration to correct; or the
    centre that would name it advertises no registry and has never been asked,
    which is a catalogue record to fix and somebody else's conversation. The
    column cannot tell them apart, so the report says once how many centres
    are in the second case.
    """

    def sentence(self):
        return stations_declared_but_silent_unasked_centres(now=NOW)

    def undeclared_and_silent(self, wigos_id="0-20000-0-63741"):
        """A silent station no centre's registry declares: one blank cell."""
        return self.in_oscar(wigos_id)

    def test_nothing_is_said_where_every_centre_advertises_a_registry(self):
        self.undeclared_and_silent()

        self.assertIsNone(self.sentence())

    def test_nothing_is_said_where_no_row_has_a_blank_cell_to_qualify(self):
        """A report whose every row names a centre has nothing to qualify."""
        self.node("bf-anam", registry=False)
        self.in_oscar("0-20000-0-63741")
        self.in_registry("0-20000-0-63741")

        self.assertIsNone(self.sentence())

    def test_the_centres_nothing_has_asked_are_counted_and_said(self):
        self.node("bf-anam", registry=False)
        self.undeclared_and_silent()

        self.assertIn("1 centre advertises no station registry", self.sentence())

    def test_more_than_one_reads_as_more_than_one(self):
        self.node("bf-anam", registry=False)
        self.node("dj-anm", registry=False)
        self.undeclared_and_silent()

        self.assertIn("2 centres advertise no station registry", self.sentence())

    def test_the_declared_but_silent_report_carries_the_sentence(self):
        self.node("bf-anam", registry=False)
        self.undeclared_and_silent()

        self.assertEqual(
            gap_report("declared-but-silent").describe_caveat(now=NOW), self.sentence()
        )

    def test_the_other_reports_have_nothing_of_the_kind_to_say(self):
        """Only the report whose column cannot tell the two absences apart."""
        self.node("bf-anam", registry=False)
        self.undeclared_and_silent()

        said = {
            report.slug
            for report in GAP_REPORTS
            if report.describe_caveat(now=NOW) is not None
        }

        self.assertEqual(said, {"declared-but-silent"})

    def test_the_sentence_stays_off_the_index(self):
        """The count is right whatever it says; only the page needs the caveat."""
        self.node("bf-anam", registry=False)
        self.undeclared_and_silent()

        bounded = {
            summary.slug for summary in gap_report_summaries(now=NOW) if summary.bound
        }

        self.assertNotIn("declared-but-silent", bounded)


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

    def test_a_centre_with_a_registry_has_been_asked_what_it_declares(self):
        self.transmitted("0-20000-0-63741")

        (row,) = self.report()

        self.assertEqual(row.declaring_centre, DeclaringCentre.ASKED)

    def test_a_centre_with_no_address_of_its_own_was_never_asked(self):
        """Undeclared by a registry nothing has read is not undeclared."""
        burkina = self.node("bf-anam", registry=False)
        self.transmitted("0-20000-0-63741", node=burkina)

        (row,) = self.report()

        self.assertEqual(row.declaring_centre, DeclaringCentre.UNASKED)

    def test_a_centre_no_catalogue_knows_is_not_called_unasked(self):
        """It has nowhere to advertise a registry; that is the other report."""
        station = self.station("0-20000-0-63741")
        StationSource.objects.create(
            station=station,
            source_type=StationSource.OBSERVED,
            node=None,
            last_seen=NOW,
        )

        (row,) = self.report()

        self.assertEqual(row.declaring_centre, DeclaringCentre.UNREGISTERED)

    def test_a_centre_that_was_never_asked_is_said_so_in_the_notice(self):
        burkina = self.node("bf-anam", registry=False)
        self.transmitted("0-20000-0-63741", node=burkina)

        (row,) = self.report()
        notice = gap_report("transmitting-undeclared").describe_row(row)

        self.assertIn("advertises no station registry", notice.summary)

    def test_a_centre_that_was_asked_is_reported_as_a_registration_gap(self):
        self.transmitted("0-20000-0-63741")

        (row,) = self.report()
        notice = gap_report("transmitting-undeclared").describe_row(row)

        self.assertNotIn("advertises no station registry", notice.summary)
        self.assertIn("nor any centre's registry declares it", notice.summary)

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

        self.assertIsNone(bounds["propagation-gaps"])

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
        """The others are about the region rather than about the registry.

        A centre with no catalogue record is the one finding a frozen registry
        makes unanswerable. A station OSCAR declares and nobody has heard is
        still a station OSCAR declares and nobody has heard.

        The dataset drift report is left out for the opposite reason: its
        bound is about centres whose own metadata nothing has ever read, which
        it says whether the registry is being rebuilt or not.
        """
        self.in_oscar("0-20000-0-63741")
        self.seen("ml-meteo")
        self.registry_frozen()

        counted = {
            summary.slug: summary.count for summary in gap_report_summaries(now=NOW)
        }
        bounds = {
            summary.slug: summary.bound
            for summary in gap_report_summaries(now=NOW)
            if summary.slug not in ("unregistered-centres", "datasets-out-of-step")
        }

        self.assertEqual(counted["declared-but-silent"], 1)
        self.assertEqual(counted["unregistered-centres"], 0)
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


class RegistryRunTestCase(GapReportTestCase):
    """A centre's registry with a run history behind it.

    Seeded as sync logs rather than by running the sync: what is under test
    is a pattern over a run history that would take a week to accumulate.
    """

    def registry_run(self, node=None, *, hours_ago, status=SyncLog.FAILED, error=""):
        """One run of the station sync against a centre, as it was recorded."""
        return SyncLog.objects.create(
            node=node or self.kenya,
            sync_type=SyncLog.NODE_STATIONS,
            status=status,
            started_at=NOW - timedelta(hours=hours_ago),
            error_message=error,
        )

    def stopped_answering(self, node=None, *, hours_ago, error="connection refused"):
        """A registry that answered once and has failed every run since."""
        self.registry_run(node, hours_ago=hours_ago + 1, status=SyncLog.SUCCESS)
        self.registry_run(node, hours_ago=hours_ago, error=error)
        self.registry_run(node, hours_ago=1, error=error)

    def never_answered(self, node=None, *, first_asked, error="connection refused"):
        """A registry nothing has ever got an answer out of."""
        self.registry_run(node, hours_ago=first_asked, error=error)
        self.registry_run(node, hours_ago=1, error=error)


class RegistriesNotAnsweringTests(RegistryRunTestCase):
    """A centre's own registry that has failed every run for days on end.

    The failures were always recorded -- one ``NODE_STATIONS`` sync log per
    node per run -- and nothing read them, so a registry could fail hourly
    from March to August with every surface of the tool saying nothing.

    What the report has to get right is that a registry stops being readable
    two ways, and they are different errands. An address that worked and
    stopped is a host that has moved or died. One that has never answered is
    an address derived wrong from the start, which is what the four centres
    publishing their canonical links from bare IP addresses make ordinary.
    """

    def report(self, **kwargs):
        return registries_not_answering(now=NOW, **kwargs)

    def by_centre(self):
        return [row.centre_id for row in self.report()]

    def test_a_registry_that_has_failed_every_run_for_days_is_reported(self):
        self.stopped_answering(hours_ago=90)

        (row,) = self.report()

        self.assertEqual(row.centre_id, "ke-meteo")
        self.assertEqual(row.last_answered_at, NOW - timedelta(hours=91))
        self.assertEqual(row.unanswered_since, NOW - timedelta(hours=91))
        self.assertAlmostEqual(row.hours_unanswered, 91)

    def test_a_registry_that_started_failing_this_morning_is_not_reported(self):
        """One bad morning is what the next run fixes."""
        self.stopped_answering(hours_ago=6)

        self.assertEqual(self.by_centre(), [])

    def test_a_registry_whose_newest_run_answered_is_not_reported(self):
        """However long it was failing before that."""
        self.registry_run(hours_ago=300, status=SyncLog.SUCCESS)
        self.registry_run(hours_ago=200, error="read timed out")
        self.registry_run(hours_ago=1, status=SyncLog.SUCCESS)

        self.assertEqual(self.by_centre(), [])

    def test_a_run_that_stepped_over_records_still_answered(self):
        """Partly is a registry this tool reached; the report is about reaching it."""
        self.registry_run(hours_ago=300, error="read timed out")
        self.registry_run(hours_ago=1, status=SyncLog.PARTIAL)

        self.assertEqual(self.by_centre(), [])

    def test_a_centre_advertising_no_registry_is_not_reported(self):
        """Nothing has ever asked it, so nothing has failed to answer."""
        unasked = self.node("cg-met", registry=False)
        self.registry_run(unasked, hours_ago=300)
        self.registry_run(unasked, hours_ago=1)

        self.assertEqual(self.by_centre(), [])

    def test_a_centre_whose_registry_nothing_has_asked_yet_is_not_reported(self):
        """A registry with no run against it has not failed one."""
        self.node("cg-met")

        self.assertEqual(self.by_centre(), [])

    def test_a_run_of_another_kind_against_the_same_centre_is_not_an_answer(self):
        """Its message archive answering says nothing about its registry."""
        self.never_answered(first_asked=300)
        SyncLog.objects.create(
            node=self.kenya,
            sync_type=SyncLog.MESSAGE_ARCHIVE,
            status=SyncLog.SUCCESS,
            started_at=NOW - timedelta(hours=1),
        )

        self.assertEqual(self.by_centre(), ["ke-meteo"])

    def test_a_registry_that_never_answered_is_timed_from_when_it_was_first_asked(self):
        self.never_answered(first_asked=200)

        (row,) = self.report()

        self.assertEqual(row.standing, RegistryStanding.NEVER_ANSWERED)
        self.assertIsNone(row.last_answered_at)
        self.assertEqual(row.unanswered_since, NOW - timedelta(hours=200))

    def test_a_registry_that_worked_and_stopped_is_named_as_that(self):
        self.stopped_answering(hours_ago=90)

        (row,) = self.report()

        self.assertEqual(row.standing, RegistryStanding.STOPPED)
        self.assertTrue(row.standing_label)

    def test_the_row_carries_the_address_that_is_not_answering(self):
        """Which is the thing an operator has to check, and the thing to correct."""
        self.stopped_answering(hours_ago=90)

        (row,) = self.report()

        self.assertEqual(row.stations_url, self.kenya.stations_url)
        self.assertIn("ke-meteo.example.int", row.stations_url)

    def test_the_row_carries_what_the_last_run_said_went_wrong(self):
        """A read timeout and a 404 send somebody to different places."""
        self.stopped_answering(hours_ago=90, error="read timed out")

        (row,) = self.report()

        self.assertEqual(row.last_error, "read timed out")

    def test_a_run_that_never_got_as_far_as_saying_why_carries_nothing(self):
        self.registry_run(hours_ago=200, status=SyncLog.SUCCESS)
        self.registry_run(hours_ago=1)

        (row,) = self.report()

        self.assertEqual(row.last_error, "")

    def test_the_report_reads_longest_unanswered_first(self):
        congo = self.node("cg-met")
        self.stopped_answering(hours_ago=90)
        self.stopped_answering(congo, hours_ago=400)

        self.assertEqual(self.by_centre(), ["cg-met", "ke-meteo"])

    def test_a_centre_carries_the_country_it_is_registered_under(self):
        self.stopped_answering(hours_ago=90)

        (row,) = self.report()

        self.assertEqual(row.country_name, "Kenya")

    @override_settings(WIS2WATCH_REGISTRY_UNANSWERED_HOURS=240)
    def test_how_long_a_registry_may_fail_for_is_a_setting(self):
        self.stopped_answering(hours_ago=90)

        self.assertEqual(self.by_centre(), [])

    def test_the_index_counts_what_the_report_lists(self):
        congo = self.node("cg-met")
        self.stopped_answering(hours_ago=90)
        self.stopped_answering(congo, hours_ago=6)

        counts = {
            summary.slug: summary.count for summary in gap_report_summaries(now=NOW)
        }

        self.assertEqual(counts["registries-not-answering"], 1)


class UnansweredRegistryNoticeTests(RegistryRunTestCase):
    """What the digest is told about a registry nobody can read."""

    def notice(self):
        report = gap_report("registries-not-answering")
        (row,) = report.find_rows(now=NOW)

        return report.describe_row(row)

    def test_the_notice_names_the_centre_the_address_and_when_it_last_answered(self):
        self.stopped_answering(hours_ago=90, error="read timed out")

        notice = self.notice()

        self.assertEqual(notice.key, "ke-meteo")
        self.assertIn("ke-meteo", notice.summary)
        self.assertIn(self.kenya.stations_url, notice.summary)
        self.assertIn("read timed out", notice.summary)

    def test_the_notice_for_a_registry_that_never_answered_says_so(self):
        """A different errand: the address was wrong from the start."""
        self.never_answered(first_asked=200)

        self.assertIn("has never answered", self.notice().summary)

    def test_the_notice_for_a_registry_that_stopped_says_when_it_last_worked(self):
        self.stopped_answering(hours_ago=90)

        self.assertIn("has not answered since", self.notice().summary)

    def test_the_notice_keeps_a_talkative_failure_to_one_line(self):
        """An error the digest quotes whole is a digest nobody reads."""
        self.stopped_answering(hours_ago=90, error="oh dear\n" * 40)

        self.assertNotIn("\n", self.notice().summary)
        self.assertLess(len(self.notice().summary), 400)


class EveryRegistryFailingTests(GapReportTestCase):
    """What this report cannot say when nothing at all is answering.

    A handful of the region's registries failing is the region; every one of
    them failing at once is very much more likely to be this tool -- an
    outbound route lost, a proxy gone -- and a list of thirty centres offered
    without that said is thirty conversations to have with the wrong people.
    """

    def registry(self, centre_id, *, answering):
        node = self.node(centre_id)
        SyncLog.objects.create(
            node=node,
            sync_type=SyncLog.NODE_STATIONS,
            status=SyncLog.SUCCESS if answering else SyncLog.FAILED,
            started_at=NOW - timedelta(hours=1),
        )

        return node

    def caveat(self):
        return registries_not_answering_caveat(now=NOW)

    def test_nothing_is_said_while_some_of_them_answer(self):
        self.registry("cg-met", answering=True)
        self.registry("rw-rma", answering=False)

        self.assertIsNone(self.caveat())

    def test_nothing_is_said_where_no_registry_has_been_asked_at_all(self):
        self.node("cg-met")

        self.assertIsNone(self.caveat())

    def test_nothing_is_said_of_a_single_centre_failing_on_its_own(self):
        """One centre down is one centre down whichever way you count it."""
        self.registry("cg-met", answering=False)

        self.assertIsNone(self.caveat())

    def test_none_of_them_answering_is_said_above_the_table(self):
        self.registry("cg-met", answering=False)
        self.registry("rw-rma", answering=False)

        self.assertIn("2", self.caveat())

    def test_the_sentence_stays_off_the_index(self):
        """A caveat is about what a column means, not about what is listed."""
        self.registry("cg-met", answering=False)
        self.registry("rw-rma", answering=False)

        bounded = {
            summary.slug for summary in gap_report_summaries(now=NOW) if summary.bound
        }

        self.assertNotIn("registries-not-answering", bounded)


class SteppedOverRunTestCase(GapReportTestCase):
    """Syncs with runs behind them, seeded as the runs recorded them.

    Seeded as sync logs rather than by running a sync: what is under test is
    which of a run history a report reads, and the runs a real sync would have
    to lose records on are the ones the syncs' own tests already cover.
    """

    def setUp(self):
        super().setUp()
        self.writer = GlobalDiscoveryCatalogue.objects.create(
            centre_id="int-wmo-global-discovery",
            name="WMO Global Discovery Catalogue",
            base_url="https://gdc.example.int",
            is_writer=True,
        )

    def sync_run(
        self,
        *,
        node=None,
        catalogue=None,
        sync_type=SyncLog.NODE_STATIONS,
        status=SyncLog.PARTIAL,
        hours_ago=1,
        found=63,
        stepped_over=(),
        errored=None,
    ):
        """One run of a sync, as it was recorded."""
        records = [{"item": item, "reason": reason} for item, reason in stepped_over]

        return SyncLog.objects.create(
            node=node,
            catalogue=catalogue,
            sync_type=sync_type,
            status=status,
            started_at=NOW - timedelta(hours=hours_ago),
            items_found=found,
            items_errored=len(records) if errored is None else errored,
            stepped_over=records,
        )

    def partial_registry_run(self, **kwargs):
        """The centre's own registry, having stepped over one station."""
        return self.sync_run(
            node=self.kenya,
            stepped_over=[("0-20000-0-63741", "value too long for column name")],
            **kwargs,
        )

    def report(self, **kwargs):
        return syncs_stepping_over_records(now=NOW, **kwargs)

    def counted(self):
        return {
            summary.slug: summary.count for summary in gap_report_summaries(now=NOW)
        }["syncs-stepping-over-records"]


class SyncsSteppingOverRecordsTests(SteppedOverRunTestCase):
    """A sync that reached its source, stored most of what it read and lost the rest.

    The distinction this report exists to keep is the one the not-answering
    report makes from the other side. A run that failed is a network or a
    source to chase and has brought nothing back. A run that succeeded and
    stepped over records reached the source perfectly well and is quietly
    short: the registry has fifty-four of sixty-three datasets, every surface
    of the tool reports the missing nine as absent from the region, and the
    run that dropped them is a green-enough row on one centre's page.

    It is the newest run that is asked, not the run history, because what a
    reader acts on is whether records are being lost now -- a run whose
    successor got them down is not a finding, it is a fixed one.
    """

    def test_a_run_that_stepped_over_records_is_reported(self):
        self.partial_registry_run()

        (row,) = self.report()

        self.assertEqual(row.read_from, "ke-meteo")
        self.assertEqual(row.kind, SyncLog.NODE_STATIONS)
        self.assertEqual(row.items_errored, 1)
        self.assertEqual(row.items_found, 63)

    def test_the_row_names_the_records_and_what_refused_them(self):
        """The whole of the report: a count is not something anybody can chase."""
        self.partial_registry_run()

        (row,) = self.report()

        self.assertEqual(
            row.stepped_over,
            [{"item": "0-20000-0-63741", "reason": "value too long for column name"}],
        )

    def test_a_run_that_stored_everything_it_read_is_not_reported(self):
        self.sync_run(node=self.kenya, status=SyncLog.SUCCESS)

        self.assertEqual(self.report(), [])

    def test_a_run_that_failed_outright_is_not_reported(self):
        """That is a source to chase, and the not-answering report chases it."""
        self.sync_run(
            node=self.kenya,
            status=SyncLog.FAILED,
            stepped_over=[("0-20000-0-63741", "value too long")],
        )

        self.assertEqual(self.report(), [])

    def test_a_partial_run_that_stepped_over_nothing_is_not_reported(self):
        """OSCAR calls a run partial for a territory it could not read at all."""
        self.sync_run(
            sync_type=SyncLog.OSCAR_STATIONS, status=SyncLog.PARTIAL, errored=0
        )

        self.assertEqual(self.report(), [])

    def test_a_sync_whose_next_run_got_the_records_down_is_not_reported(self):
        self.partial_registry_run(hours_ago=5)
        self.sync_run(node=self.kenya, status=SyncLog.SUCCESS, hours_ago=1)

        self.assertEqual(self.report(), [])

    def test_a_sync_still_stepping_over_records_is_reported_once(self):
        """However many runs it has lost them on."""
        self.partial_registry_run(hours_ago=5)
        self.partial_registry_run(hours_ago=3)
        self.partial_registry_run(hours_ago=1)

        self.assertEqual(len(self.report()), 1)

    def test_each_sync_against_a_centre_answers_for_itself(self):
        """Its registry stepping over a station says nothing about its archive."""
        self.partial_registry_run()
        self.sync_run(
            node=self.kenya,
            sync_type=SyncLog.MESSAGE_ARCHIVE,
            hours_ago=2,
            stepped_over=[("a-notification", "no such dataset")],
        )

        self.assertEqual(
            sorted(row.kind for row in self.report()),
            [SyncLog.MESSAGE_ARCHIVE, SyncLog.NODE_STATIONS],
        )

    def test_each_centre_answers_for_itself(self):
        self.partial_registry_run()
        self.sync_run(
            node=self.node("dj-anm"),
            stepped_over=[("0-20000-0-63125", "value too long")],
        )

        self.assertEqual(
            sorted(row.read_from for row in self.report()), ["dj-anm", "ke-meteo"]
        )

    def test_the_run_that_builds_the_registry_is_reported_by_its_catalogue(self):
        """The one that dropped nine of the region's sixty-three datasets."""
        self.sync_run(
            catalogue=self.writer,
            sync_type=SyncLog.CATALOGUE,
            stepped_over=[("urn:wmo:md:ke-meteo:synop", "duplicate key value")],
        )

        (row,) = self.report()

        self.assertEqual(row.read_from, "int-wmo-global-discovery")
        self.assertEqual(row.kind, SyncLog.CATALOGUE)

    def test_a_run_against_no_centre_at_all_says_it_read_the_region(self):
        """OSCAR answers territory by territory; a sweep hears whoever publishes."""
        self.sync_run(
            sync_type=SyncLog.OSCAR_STATIONS,
            stepped_over=[("0-404-0-toolong", "value too long")],
        )

        (row,) = self.report()

        self.assertEqual(row.read_from, "")
        self.assertTrue(row.read_from_label)

    def test_a_run_older_than_the_window_is_not_reported(self):
        """A sync nobody runs any more would otherwise stand here for good."""
        self.partial_registry_run(hours_ago=24 * 30)

        self.assertEqual(self.report(), [])

    def test_how_far_back_a_run_still_speaks_for_a_sync_is_a_setting(self):
        self.partial_registry_run(hours_ago=24 * 30)

        self.assertEqual(len(self.report(within_days=60)), 1)

    def test_the_report_reads_worst_first(self):
        self.partial_registry_run()
        self.sync_run(
            node=self.node("dj-anm"),
            stepped_over=[("a", "refused"), ("b", "refused")],
            hours_ago=2,
        )

        self.assertEqual(
            [row.read_from for row in self.report()], ["dj-anm", "ke-meteo"]
        )

    def test_a_run_that_kept_fewer_reasons_than_it_stepped_over_says_so(self):
        """The ceiling on reasons is a fault named once, not a report shortened."""
        self.sync_run(
            node=self.kenya,
            stepped_over=[("0-20000-0-63741", "value too long")],
            errored=9,
        )

        (row,) = self.report()

        self.assertEqual(row.reasons_withheld, 8)

    def test_the_index_counts_what_the_report_lists(self):
        self.partial_registry_run()
        self.sync_run(node=self.node("dj-anm"), status=SyncLog.SUCCESS)

        self.assertEqual(self.counted(), len(self.report()))
        self.assertEqual(self.counted(), 1)


class SteppedOverRunNoticeTests(SteppedOverRunTestCase):
    """What a digest says about a sync that is losing records."""

    def notice(self, row):
        return gap_report("syncs-stepping-over-records").describe_row(row)

    def test_the_notice_names_the_sync_the_count_and_the_first_reason(self):
        self.partial_registry_run()

        (row,) = self.report()
        notice = self.notice(row)

        self.assertIn("ke-meteo", notice.summary)
        self.assertIn("1 of 63", notice.summary)
        self.assertIn("value too long for column name", notice.summary)

    def test_the_notice_keys_on_the_sync_rather_than_on_the_run(self):
        """A sync losing records every hour is one finding, not one an hour."""
        self.partial_registry_run(hours_ago=3)

        (first,) = self.report()

        self.partial_registry_run(hours_ago=1)

        (second,) = self.report()

        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(self.notice(first).key, self.notice(second).key)

    def test_a_run_that_kept_no_reasons_still_says_what_it_lost(self):
        self.sync_run(node=self.kenya, errored=9, stepped_over=[])

        (row,) = self.report()

        self.assertIn("9 of 63", self.notice(row).summary)


class GapReportSummaryTests(GapReportTestCase):
    """The index that says which of the nine is worth opening."""

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


class CatalogueRunTestCase(GapReportTestCase):
    """Catalogues with run histories behind them, seeded as the runs recorded them.

    Seeded as sync logs rather than by running a sync: what is under test is
    which of a run history the report reads, and how a run comes to fail is
    what the sync's own tests cover.
    """

    def setUp(self):
        super().setUp()
        self.writer = GlobalDiscoveryCatalogue.objects.create(
            centre_id="ca-eccc-msc-global-discovery-catalogue",
            name="Meteorological Service of Canada",
            base_url="https://wis2-gdc.example.ca",
            is_writer=True,
        )

    def reader(self, centre_id="de-dwd-global-discovery-catalogue", **kwargs):
        return GlobalDiscoveryCatalogue.objects.create(
            centre_id=centre_id,
            name=centre_id,
            base_url=f"https://{centre_id}.example.int",
            **kwargs,
        )

    def run_of(
        self,
        catalogue=None,
        *,
        status=SyncLog.SUCCESS,
        hours_ago=1,
        sync_type=SyncLog.CATALOGUE,
        found=559,
        error="",
    ):
        """One run of the catalogue sync, as it was recorded."""
        started = NOW - timedelta(hours=hours_ago)

        return SyncLog.objects.create(
            catalogue=catalogue or self.writer,
            sync_type=sync_type,
            status=status,
            started_at=started,
            completed_at=started,
            items_found=found if status != SyncLog.FAILED else 0,
            error_message=error,
        )

    def history(self, catalogue=None, *, failed, succeeded, error="Connection aborted"):
        """A run history: so many failures, so many runs that brought records back."""
        for n in range(failed):
            self.run_of(
                catalogue, status=SyncLog.FAILED, hours_ago=n * 6 + 1, error=error
            )

        for n in range(succeeded):
            self.run_of(catalogue, hours_ago=(failed + n) * 6 + 1)

    def report(self, **kwargs):
        return catalogues_that_keep_failing(now=NOW, **kwargs)

    def counted(self):
        return {
            summary.slug: summary.count for summary in gap_report_summaries(now=NOW)
        }["catalogues-that-keep-failing"]


class CataloguesThatKeepFailingTests(CatalogueRunTestCase):
    """A catalogue whose runs fail some of the time and succeed the rest.

    This is the failure ADR-0004's staleness check cannot see. The writing
    catalogue was failing about half its six-hourly runs on refused
    connections, and because every other run brought the registry back the
    24-hour threshold was never reached: nothing was ever announced, the
    registry was rebuilt at half the rate it was supposed to be, and the only
    evidence was seven rows in a sync log nobody opens.
    """

    def test_a_catalogue_failing_half_its_runs_is_reported(self):
        self.history(failed=14, succeeded=14)

        (row,) = self.report()

        self.assertEqual(row.centre_id, "ca-eccc-msc-global-discovery-catalogue")
        self.assertEqual(row.runs, 28)
        self.assertEqual(row.failures, 14)
        self.assertEqual(row.share, 50)

    def test_a_catalogue_that_answers_every_time_is_not_reported(self):
        self.history(failed=0, succeeded=28)

        self.assertEqual(self.report(), [])

    def test_a_catalogue_failing_the_odd_run_is_not_reported(self):
        """One blip in a week is the schedule working, not a finding."""
        self.history(failed=1, succeeded=27)

        self.assertEqual(self.report(), [])

    def test_a_catalogue_failing_every_run_is_reported_too(self):
        """It is stale as well, and announced as such; that is a different reader."""
        self.history(failed=28, succeeded=0)

        (row,) = self.report()

        self.assertEqual(row.share, 100)

    def test_too_few_runs_to_judge_is_not_a_finding(self):
        """A fresh installation with one failed run behind it has no rate yet."""
        self.history(failed=2, succeeded=0)

        self.assertEqual(self.report(), [])

    def test_runs_older_than_the_window_are_not_counted(self):
        self.history(failed=14, succeeded=14)
        self.run_of(status=SyncLog.FAILED, hours_ago=24 * 30)

        (row,) = self.report()

        self.assertEqual(row.runs, 28)

    def test_a_partial_run_reached_the_catalogue_and_is_not_a_failure(self):
        self.history(failed=0, succeeded=20)

        for n in range(8):
            self.run_of(status=SyncLog.PARTIAL, hours_ago=n * 6 + 3)

        self.assertEqual(self.report(), [])

    def test_another_sync_logged_against_the_catalogue_is_not_evidence(self):
        self.history(failed=0, succeeded=28)

        for n in range(20):
            self.run_of(
                status=SyncLog.FAILED,
                sync_type=SyncLog.WILDCARD_SWEEP,
                hours_ago=n + 1,
            )

        self.assertEqual(self.report(), [])

    def test_a_catalogue_switched_off_is_not_reported(self):
        self.history(failed=14, succeeded=14)
        self.writer.is_active = False
        self.writer.save()

        self.assertEqual(self.report(), [])

    def test_the_row_says_which_of_them_writes_the_registry(self):
        self.history(failed=14, succeeded=14)
        self.history(self.reader(), failed=14, succeeded=14)

        writing = {row.centre_id: row.is_writer for row in self.report()}

        self.assertEqual(
            writing,
            {
                "ca-eccc-msc-global-discovery-catalogue": True,
                "de-dwd-global-discovery-catalogue": False,
            },
        )

    def test_the_writer_is_listed_first(self):
        """The registry is one catalogue's to build; the others cost nothing yet."""
        self.history(self.reader(), failed=28, succeeded=0)
        self.history(failed=14, succeeded=14)

        self.assertTrue(self.report()[0].is_writer)

    def test_the_row_carries_what_the_last_failure_said(self):
        self.history(
            failed=14, succeeded=14, error="Connection aborted, RemoteDisconnected"
        )

        (row,) = self.report()

        self.assertIn("RemoteDisconnected", row.last_error)

    def test_the_row_says_when_records_last_came_back(self):
        self.history(failed=14, succeeded=14)

        (row,) = self.report()

        self.assertEqual(row.records_last_read_at, NOW - timedelta(hours=14 * 6 + 1))

    def test_a_catalogue_that_has_never_brought_records_back_says_so(self):
        self.history(failed=28, succeeded=0)

        (row,) = self.report()

        self.assertIsNone(row.records_last_read_at)

    def test_the_index_counts_what_the_report_lists(self):
        self.history(failed=14, succeeded=14)

        self.assertEqual(self.counted(), 1)
        self.assertEqual(self.counted(), len(self.report()))

    def test_the_share_it_takes_to_be_reported_is_a_setting(self):
        self.history(failed=3, succeeded=25)

        with override_settings(WIS2WATCH_CATALOGUE_FAILING_SHARE=10):
            self.assertEqual(len(self.report()), 1)


class FailingCatalogueNoticeTests(CatalogueRunTestCase):
    """The same finding as the sentence the morning digest carries."""

    def notice(self):
        report = gap_report("catalogues-that-keep-failing")

        return report.describe_row(self.report()[0])

    def test_the_notice_names_the_catalogue_and_how_often_it_fails(self):
        self.history(failed=14, succeeded=14)

        notice = self.notice()

        self.assertIn("ca-eccc-msc-global-discovery-catalogue", notice.summary)
        self.assertIn("14 of 28", notice.summary)

    def test_the_notice_says_which_of_them_writes_the_registry(self):
        self.history(failed=14, succeeded=14)

        self.assertIn("writ", self.notice().summary)

    def test_the_notice_quotes_the_last_failure(self):
        self.history(failed=14, succeeded=14, error="Max retries exceeded")

        self.assertIn("Max retries exceeded", self.notice().summary)

    def test_it_is_keyed_on_the_catalogue_rather_than_on_the_run(self):
        """A catalogue failing all week is one finding, announced once."""
        self.history(failed=14, succeeded=14)

        self.assertEqual(self.notice().key, "ca-eccc-msc-global-discovery-catalogue")


class DatasetDriftTestCase(GapReportTestCase):
    """Datasets as each of the three sources describes them.

    What is seeded is the provenance combinations again, one level up from a
    station: a Global Discovery Catalogue's record of what a centre once
    registered, the centre's own record of what it publishes now, and traffic
    that proves what it is actually sending -- present and absent in every
    combination that changes the answer.

    Beside them, the fact the whole report is bounded by: whether anything
    ever got an answer out of the centre's own metadata. Seeded as the
    discovery-metadata sync logs recorded it, because that is where the report
    reads it -- a live probe of a host that hangs would have the same centre in
    and out of the bound on consecutive readings.
    """

    def setUp(self):
        super().setUp()
        self.catalogue = GlobalDiscoveryCatalogue.objects.create(
            centre_id="ca-eccc-msc-global-discovery-catalogue",
            name="Meteorological Service of Canada",
            base_url="https://wis2-gdc.example.ca",
            is_writer=True,
        )
        self.answered(self.kenya)

    def answered(self, node, *, hours_ago=1, status=SyncLog.SUCCESS):
        """A run that asked a centre what it publishes and got an answer."""
        return SyncLog.objects.create(
            node=node,
            sync_type=SyncLog.DISCOVERY_METADATA,
            status=status,
            started_at=NOW - timedelta(hours=hours_ago),
        )

    def failed_to_answer(self, node, *, hours_ago=1):
        """A run that asked and was refused."""
        return self.answered(node, hours_ago=hours_ago, status=SyncLog.FAILED)

    def dataset(
        self,
        identifier,
        *,
        node=None,
        in_catalogue=False,
        at_node=False,
        heard=False,
        hours_ago=1,
    ):
        """One dataset, declared by whichever sources the test names."""
        node = self.kenya if node is None else node
        dataset = Dataset.objects.create(
            node=node,
            identifier=identifier,
            title=identifier.rsplit(":", 1)[-1].upper(),
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy=f"origin/a/wis2/{node.centre_id}/data/core/weather",
            raw_json={},
        )

        declaring = (
            (DatasetSource.GDC, in_catalogue, self.catalogue),
            (DatasetSource.NODE, at_node, None),
            (DatasetSource.OBSERVED, heard, None),
        )

        for source_type, declared, catalogue in declaring:
            if declared:
                DatasetSource.objects.create(
                    dataset=dataset,
                    source_type=source_type,
                    catalogue=catalogue,
                    last_seen=NOW - timedelta(hours=hours_ago),
                )

        return dataset

    def report(self):
        return datasets_out_of_step(now=NOW)

    def drifts(self):
        """Which way each reported dataset drifts, by identifier."""
        return {row.identifier: row.drift for row in self.report()}

    def bound(self):
        return datasets_out_of_step_unasked_centres(now=NOW)


class DatasetDriftTests(DatasetDriftTestCase):
    """The datasets the catalogue and the centre do not both declare."""

    def test_a_dataset_only_the_catalogue_carries_is_reported(self):
        """A stale global record, advertising data the centre no longer serves."""
        self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True)

        self.assertEqual(
            self.drifts(),
            {"urn:wmo:md:ke-meteo:synop": DeclarationDrift.CATALOGUE_ONLY},
        )

    def test_a_dataset_only_the_centre_declares_is_reported(self):
        """A centre publishing data nobody reading a catalogue can discover."""
        self.dataset("urn:wmo:md:ke-meteo:temp", at_node=True)

        self.assertEqual(
            self.drifts(),
            {"urn:wmo:md:ke-meteo:temp": DeclarationDrift.NODE_ONLY},
        )

    def test_a_dataset_heard_that_neither_declares_is_reported(self):
        """Traffic arriving under a record no registry of either kind holds."""
        self.dataset("urn:wmo:md:ke-meteo:climate", heard=True)

        self.assertEqual(
            self.drifts(),
            {"urn:wmo:md:ke-meteo:climate": DeclarationDrift.UNDECLARED},
        )

    def test_a_dataset_both_of_them_declare_is_not_reported(self):
        self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True, at_node=True)

        self.assertEqual(self.report(), [])

    def test_traffic_settles_nothing_where_the_two_already_agree(self):
        """The third source is what the other two are compared against."""
        self.dataset(
            "urn:wmo:md:ke-meteo:synop", in_catalogue=True, at_node=True, heard=True
        )

        self.assertEqual(self.report(), [])

    def test_a_dataset_the_catalogue_carries_and_traffic_confirms_still_drifts(self):
        """Traffic is not the centre's own declaration and cannot stand in for it."""
        self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True, heard=True)

        self.assertEqual(
            self.drifts(),
            {"urn:wmo:md:ke-meteo:synop": DeclarationDrift.CATALOGUE_ONLY},
        )

    def test_two_catalogues_carrying_it_is_still_one_absence_at_the_centre(self):
        dataset = self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True)
        DatasetSource.objects.create(
            dataset=dataset,
            source_type=DatasetSource.GDC,
            catalogue=GlobalDiscoveryCatalogue.objects.create(
                centre_id="de-dwd-global-discovery-catalogue",
                name="DWD",
                base_url="https://wis2-gdc.example.de",
            ),
            last_seen=NOW,
        )

        (row,) = self.report()

        self.assertEqual(row.drift, DeclarationDrift.CATALOGUE_ONLY)

    def test_the_row_carries_what_it_takes_to_open_the_conversation(self):
        self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True, hours_ago=3)

        (row,) = self.report()

        self.assertEqual(row.centre_id, "ke-meteo")
        self.assertEqual(row.node_id, self.kenya.pk)
        self.assertEqual(row.identifier, "urn:wmo:md:ke-meteo:synop")
        self.assertEqual(row.title, "SYNOP")
        self.assertEqual(row.topic, "origin/a/wis2/ke-meteo/data/core/weather")
        self.assertEqual(row.last_declared_at, NOW - timedelta(hours=3))

    def test_a_dataset_nothing_has_ever_named_carries_its_identifier(self):
        """A dataset created from traffic has no title to show."""
        dataset = self.dataset("urn:wmo:md:ke-meteo:climate", heard=True)
        dataset.title = ""
        dataset.save(update_fields=["title"])

        (row,) = self.report()

        self.assertEqual(row.title, "urn:wmo:md:ke-meteo:climate")

    def test_the_report_reads_by_centre_and_then_by_identifier(self):
        uganda = self.node("ug-unma")
        self.answered(uganda)
        self.dataset("urn:wmo:md:ug-unma:synop", node=uganda, in_catalogue=True)
        self.dataset("urn:wmo:md:ke-meteo:temp", in_catalogue=True)
        self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True)

        self.assertEqual(
            [row.identifier for row in self.report()],
            [
                "urn:wmo:md:ke-meteo:synop",
                "urn:wmo:md:ke-meteo:temp",
                "urn:wmo:md:ug-unma:synop",
            ],
        )

    def test_the_index_counts_what_the_report_lists(self):
        """A count arrived at twice is a count that comes to disagree."""
        self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True)
        self.dataset("urn:wmo:md:ke-meteo:temp", at_node=True)
        self.dataset("urn:wmo:md:ke-meteo:climate", heard=True)
        self.dataset("urn:wmo:md:ke-meteo:agreed", in_catalogue=True, at_node=True)

        counts = {
            summary.slug: summary.count for summary in gap_report_summaries(now=NOW)
        }

        self.assertEqual(counts["datasets-out-of-step"], 3)
        self.assertEqual(len(self.report()), 3)

    def test_the_report_writes_nothing(self):
        """It reads: nothing is retired, created or restamped by looking."""
        self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True, hours_ago=5)
        before = list(
            DatasetSource.objects.values_list("pk", "source_type", "last_seen")
        )

        self.report()

        self.assertEqual(
            list(DatasetSource.objects.values_list("pk", "source_type", "last_seen")),
            before,
        )
        self.assertEqual(Dataset.objects.count(), 1)


class DatasetDriftBoundTests(DatasetDriftTestCase):
    """The centres the report could not ask, which its count is measured against.

    Eleven findings computed from twenty-seven of thirty-two centres is not
    "the region has eleven": it is eleven among the centres something could
    ask. A count read without that is read as the region.
    """

    def test_a_centre_that_has_never_answered_contributes_no_rows(self):
        silent = self.node("bi-igebu")
        self.failed_to_answer(silent)
        self.dataset("urn:wmo:md:bi-igebu:synop", node=silent, in_catalogue=True)

        self.assertEqual(self.report(), [])

    def test_that_centre_is_named_in_the_bound_instead(self):
        silent = self.node("bi-igebu")
        self.failed_to_answer(silent)
        self.dataset("urn:wmo:md:bi-igebu:synop", node=silent, in_catalogue=True)

        self.assertIn("bi-igebu", self.bound())

    def test_a_centre_nothing_could_ask_at_all_is_named_too(self):
        """No address of its own is the same absence as one that never answers."""
        self.node("bf-anam", registry=False)

        self.assertIn("bf-anam", self.bound())

    def test_a_centre_that_answered_once_and_stopped_is_still_read(self):
        """The bound is about never having been read, not about being current.

        A probe that failed this sweep and answered the last is the ordinary
        case in this region. What the centre last said stands, and dropping
        its rows on a failed run would have the report empty every time a host
        blinked.
        """
        blinking = self.node("bi-igebu")
        self.answered(blinking, hours_ago=26)
        self.failed_to_answer(blinking, hours_ago=1)
        self.dataset("urn:wmo:md:bi-igebu:synop", node=blinking, in_catalogue=True)

        self.assertEqual(
            self.drifts(),
            {"urn:wmo:md:bi-igebu:synop": DeclarationDrift.CATALOGUE_ONLY},
        )
        self.assertIsNone(self.bound())

    def test_a_run_that_stepped_over_a_record_still_answered(self):
        """It reached the centre and read it; what it lost is another report's."""
        partial = self.node("cg-met")
        self.answered(partial, status=SyncLog.PARTIAL)
        self.dataset("urn:wmo:md:cg-met:synop", node=partial, in_catalogue=True)

        self.assertEqual(len(self.report()), 1)
        self.assertIsNone(self.bound())

    def test_a_station_registry_answering_says_nothing_about_the_metadata(self):
        """Two endpoints that fail independently, and only one of them is this."""
        silent = self.node("bi-igebu")
        SyncLog.objects.create(
            node=silent,
            sync_type=SyncLog.NODE_STATIONS,
            status=SyncLog.SUCCESS,
            started_at=NOW - timedelta(hours=1),
        )

        self.assertIn("bi-igebu", self.bound())

    def test_nothing_is_said_where_every_centre_has_answered(self):
        self.assertIsNone(self.bound())

    def test_more_than_one_reads_as_more_than_one(self):
        for centre_id in ("bi-igebu", "cg-met"):
            self.failed_to_answer(self.node(centre_id))

        bound = self.bound()

        self.assertIn("bi-igebu", bound)
        self.assertIn("cg-met", bound)
        self.assertIn("2", bound)

    def test_the_bound_is_said_over_a_report_that_found_nothing(self):
        """The reading it exists to prevent: no rows read as no drift."""
        self.failed_to_answer(self.node("bi-igebu"))

        self.assertEqual(self.report(), [])
        self.assertIsNotNone(self.bound())

    def test_the_bound_travels_with_the_count_on_the_index(self):
        self.failed_to_answer(self.node("bi-igebu"))

        (summary,) = [
            summary
            for summary in gap_report_summaries(now=NOW)
            if summary.slug == "datasets-out-of-step"
        ]

        self.assertIn("bi-igebu", summary.bound)

    def test_the_report_carries_the_sentence_as_its_bound(self):
        self.failed_to_answer(self.node("bi-igebu"))

        self.assertEqual(
            gap_report("datasets-out-of-step").describe_bound(now=NOW), self.bound()
        )


class DatasetDriftNoticeTests(DatasetDriftTestCase):
    """The same findings as the sentences the morning digest carries."""

    def notice(self):
        (row,) = self.report()

        return gap_report("datasets-out-of-step").describe_row(row)

    def test_a_catalogue_only_dataset_says_which_way_it_drifts(self):
        self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True)

        summary = self.notice().summary

        self.assertIn("urn:wmo:md:ke-meteo:synop", summary)
        self.assertIn("ke-meteo", summary)
        self.assertIn("catalogue", summary)

    def test_a_node_only_dataset_says_which_way_it_drifts(self):
        self.dataset("urn:wmo:md:ke-meteo:temp", at_node=True)

        self.assertIn("no catalogue", self.notice().summary)

    def test_an_undeclared_dataset_says_the_traffic_is_what_found_it(self):
        self.dataset("urn:wmo:md:ke-meteo:climate", heard=True)

        summary = self.notice().summary

        self.assertIn("transmitting", summary)
        self.assertIn("neither", summary)

    def test_it_is_keyed_on_the_centre_and_the_identifier(self):
        """One dataset drifting is one finding, wherever else it is declared."""
        self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True)

        self.assertEqual(self.notice().key, "ke-meteo:urn:wmo:md:ke-meteo:synop")

    def test_a_dataset_that_stops_drifting_leaves_the_report(self):
        """Which is what the digest reads as the drift having been settled."""
        dataset = self.dataset("urn:wmo:md:ke-meteo:synop", in_catalogue=True)
        DatasetSource.objects.create(
            dataset=dataset, source_type=DatasetSource.NODE, last_seen=NOW
        )

        self.assertEqual(self.report(), [])
