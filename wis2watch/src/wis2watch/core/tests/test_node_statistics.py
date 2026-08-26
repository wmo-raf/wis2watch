"""The all-centres table, against a seeded database.

The panel on the admin home is read on login to answer one question -- is
anything wrong -- so the failures worth guarding against are the ones that
still return a table. A standing that reports a consequence instead of its
cause sends an operator after the wrong thing; a sparkline counted from the
wrong vantage point draws twice the traffic a centre actually got out; a
reading order that puts the worst centre last is a panel that says all clear
about a region that is not.

Nothing here re-tests what ``test_overview`` already covers. The four
judgements are ``node_overview``'s and are seeded only as far as it takes to
reach a standing.
"""

from datetime import timedelta

from django.test import TestCase

from wis2watch.core.analysis import (
    NodeStanding,
    TransmissionStanding,
    all_nodes_statistics,
)
from wis2watch.core.models import (
    CadenceBaseline,
    Dataset,
    HourlyRollup,
    MessageSource,
    NodeLastSeen,
    WIS2Node,
)
from wis2watch.core.tests.support import at, origin_api, origin_broker


NOW = at("2026-08-11T12:00:00")

#: Where the fixed window falls at ``NOW``: the last 24 *whole* hours, so it
#: ends at the top of the hour in progress rather than part way through it.
FIRST_HOUR = at("2026-08-10T12:00:00")
LAST_HOUR = at("2026-08-11T11:00:00")


class AllNodesTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )

    def node(self, centre_id, last_seen=None, **kwargs):
        kwargs.setdefault("name", centre_id.upper())
        node = WIS2Node.objects.create(centre_id=centre_id, **kwargs)

        if last_seen is not None:
            NodeLastSeen.objects.create(node=node, last_message_at=last_seen)

        return node

    def rollup(self, node, hour, count, source=None, dataset=None):
        return HourlyRollup.objects.create(
            hour=hour,
            source=source or self.global_broker,
            node=node,
            dataset=dataset,
            message_count=count,
        )

    def statistics(self, **kwargs):
        kwargs.setdefault("now", NOW)
        return all_nodes_statistics(**kwargs)

    def by_centre(self, **kwargs):
        return {row.centre_id: row for row in self.statistics(**kwargs).rows}

    def well(self, centre_id):
        """A centre with nothing wrong with it, as far as all four judgements go.

        Heard from lately, no dataset overdue, nothing uncached, and watched at
        the broker it is obliged to run -- which is the only combination that
        reaches ``HEALTHY``, and therefore the baseline every other test here
        breaks exactly one thing about.
        """
        node = self.node(centre_id, last_seen=NOW - timedelta(hours=1))
        origin_broker(node, is_reachable=True)

        return node


class StandingTests(AllNodesTestCase):
    """One word per centre, and it names the worst thing true of it.

    Read in rank order so the first fault wins. The cases that matter are the
    ones where more than one is true at once: every judgement after staleness
    is downstream of it, so a centre nothing has ever been heard from is also
    uncached, also unjudged, and reporting any of those instead of the silence
    would send a reader to look at the wrong thing entirely.
    """

    def standing(self, centre_id):
        return self.by_centre()[centre_id].standing

    def test_a_centre_nothing_has_ever_been_heard_from_says_so_first(self):
        # Also uncached and unwatched, both of which are consequences of the
        # silence rather than findings of their own.
        self.node("bj-meteobenin")

        self.assertEqual(self.standing("bj-meteobenin"), NodeStanding.NEVER_SEEN)

    def test_a_centre_gone_quiet_is_stale_rather_than_anything_downstream(self):
        self.node("bi-igebu", last_seen=NOW - timedelta(days=7))

        self.assertEqual(self.standing("bi-igebu"), NodeStanding.STALE)

    def test_a_publishing_centre_with_a_dataset_overdue_is_silent(self):
        node = self.well("gh-gmet")
        synop = Dataset.objects.create(
            node=node,
            identifier="urn:wmo:md:gh-gmet:synop",
            title="synop",
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy="origin/a/wis2/gh-gmet/data/core/synop",
            raw_json={},
        )
        CadenceBaseline.objects.create(
            dataset=synop,
            interval_hours=6,
            observations=20,
            learned_at=NOW - timedelta(days=1),
        )
        self.rollup(node, NOW - timedelta(days=3), 1, dataset=synop)

        self.assertEqual(self.standing("gh-gmet"), NodeStanding.SILENT)

    def test_a_centre_publishing_core_data_no_cache_carried_is_not_cached(self):
        node = self.well("ke-meteo")
        synop = Dataset.objects.create(
            node=node,
            identifier="urn:wmo:md:ke-meteo:synop",
            title="synop",
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy="origin/a/wis2/ke-meteo/data/core/synop",
            raw_json={},
        )
        self.rollup(node, NOW - timedelta(hours=1), 12, dataset=synop)

        self.assertEqual(self.standing("ke-meteo"), NodeStanding.NOT_CACHED)

    def test_a_centre_nothing_is_watching_is_not_watched(self):
        self.node("cd-mettelsat", last_seen=NOW - timedelta(hours=1))

        self.assertEqual(self.standing("cd-mettelsat"), NodeStanding.NO_BROKER)

    def test_a_centre_answering_only_at_its_archive_says_which(self):
        # Not folded in with the centre above it. Both are failing the same
        # obligation, and one of them is publishing perfectly well through a
        # transport this tool can read -- on a region where that is most of the
        # centres, one standing over both sorts nothing.
        node = self.node("ma-marocmeteo", last_seen=NOW - timedelta(hours=1))
        origin_broker(node, is_reachable=False)
        origin_api(node, is_reachable=True)

        self.assertEqual(self.standing("ma-marocmeteo"), NodeStanding.ARCHIVE_ONLY)

    def test_a_centre_with_all_four_judgements_clear_is_healthy(self):
        self.well("cg-met")

        self.assertEqual(self.standing("cg-met"), NodeStanding.HEALTHY)

    def test_the_ranks_run_worst_to_best_without_a_gap(self):
        # The order the rows arrive in, the order the filter offers, and the
        # order the client sorts by are all this one list.
        self.assertEqual(
            [key for key, _label in NodeStanding.CHOICES],
            [
                NodeStanding.NEVER_SEEN,
                NodeStanding.STALE,
                NodeStanding.SILENT,
                NodeStanding.NOT_CACHED,
                NodeStanding.NO_BROKER,
                NodeStanding.ARCHIVE_ONLY,
                NodeStanding.HEALTHY,
            ],
        )
        self.assertEqual(NodeStanding.RANK[NodeStanding.NEVER_SEEN], 0)
        self.assertEqual(NodeStanding.RANK[NodeStanding.HEALTHY], 6)


class TransmissionTests(AllNodesTestCase):
    """Whether data is flowing, which is the only thing the front page asks.

    A second verdict on the same row rather than a narrowing of the first,
    because the two surfaces ask different questions and a row has to answer
    both from one pass. What is guarded here is that it ignores the plumbing --
    the whole reason it exists -- and that it stays a coarsening of the full
    standing rather than drifting into a rival ordering.
    """

    def transmission(self, centre_id):
        return self.by_centre()[centre_id].transmission

    def test_a_centre_nothing_has_ever_been_heard_from_says_so_first(self):
        self.node("bj-meteobenin")

        self.assertEqual(
            self.transmission("bj-meteobenin"), TransmissionStanding.NEVER_SEEN
        )

    def test_a_centre_gone_quiet_is_stale(self):
        self.node("bi-igebu", last_seen=NOW - timedelta(days=7))

        self.assertEqual(self.transmission("bi-igebu"), TransmissionStanding.STALE)

    def test_a_centre_with_a_dataset_overdue_says_so_however_much_it_publishes(self):
        """The label says "Datasets overdue" and never that the centre is quiet.

        On the live region this lands on centres sending three hundred
        notifications an hour and last heard from six minutes ago: one dataset
        past its own cadence is enough. A verdict that read as silence there
        would be a verdict nobody believed twice.
        """
        node = self.well("dz-meteoalgerie")
        synop = Dataset.objects.create(
            node=node,
            identifier="urn:wmo:md:dz-meteoalgerie:synop",
            title="synop",
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy="origin/a/wis2/dz-meteoalgerie/data/core/synop",
            raw_json={},
        )
        CadenceBaseline.objects.create(
            dataset=synop,
            interval_hours=6,
            observations=20,
            learned_at=NOW - timedelta(days=1),
        )
        self.rollup(node, NOW - timedelta(days=3), 1, dataset=synop)
        # And publishing hard, right now.
        self.rollup(node, LAST_HOUR, 310)

        row = self.by_centre()["dz-meteoalgerie"]

        self.assertEqual(row.transmission, TransmissionStanding.SILENT)
        self.assertEqual(row.messages_in_window, 310)
        self.assertEqual(
            TransmissionStanding.LABELS[row.transmission], "Datasets overdue"
        )

    def test_a_centre_answering_only_at_its_archive_is_still_transmitting(self):
        """The whole point of the second verdict.

        Twenty-eight of thirty-two centres in the region fall back to their
        archives. Folding that in put twenty-one of them under "Archive only"
        on a panel whose job is to say whether data is flowing.
        """
        node = self.node("ma-marocmeteo", last_seen=NOW - timedelta(hours=1))
        origin_broker(node, is_reachable=False)
        origin_api(node, is_reachable=True)

        row = self.by_centre()["ma-marocmeteo"]

        self.assertEqual(row.transmission, TransmissionStanding.TRANSMITTING)
        self.assertEqual(row.standing, NodeStanding.ARCHIVE_ONLY)

    def test_a_centre_nothing_watches_is_still_transmitting(self):
        self.node("cd-mettelsat", last_seen=NOW - timedelta(hours=1))

        row = self.by_centre()["cd-mettelsat"]

        self.assertEqual(row.transmission, TransmissionStanding.TRANSMITTING)
        self.assertEqual(row.standing, NodeStanding.NO_BROKER)

    def test_uncached_core_data_does_not_make_a_centre_stop_transmitting(self):
        node = self.well("ke-meteo")
        synop = Dataset.objects.create(
            node=node,
            identifier="urn:wmo:md:ke-meteo:synop",
            title="synop",
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy="origin/a/wis2/ke-meteo/data/core/synop",
            raw_json={},
        )
        self.rollup(node, LAST_HOUR, 12, dataset=synop)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.transmission, TransmissionStanding.TRANSMITTING)
        self.assertEqual(row.standing, NodeStanding.NOT_CACHED)

    def test_it_is_a_coarsening_of_the_full_standing_and_not_a_rival(self):
        """Its three faults are the full standing's three worst, same names.

        This is what lets one server order serve both tables, and what stops
        the two surfaces disagreeing about which centre to look at first.
        """
        self.assertEqual(
            [key for key, _label in TransmissionStanding.CHOICES][:3],
            [key for key, _label in NodeStanding.CHOICES][:3],
        )
        self.assertEqual(
            TransmissionStanding.RANK[TransmissionStanding.TRANSMITTING],
            len(NodeStanding.CHOICES) - 4,
        )

    def test_three_of_its_four_labels_are_the_full_standings_own(self):
        """One vocabulary across two tables, not two."""
        for key in ("never_seen", "stale", "silent"):
            self.assertEqual(
                TransmissionStanding.LABELS[key], NodeStanding.LABELS[key]
            )


class CoverageTests(AllNodesTestCase):
    """Every registered centre is a row, whatever has been heard from it."""

    def test_a_centre_nothing_has_arrived_from_is_a_row_not_an_absence(self):
        self.node("ke-meteo", last_seen=NOW - timedelta(hours=1))
        self.node("bj-meteobenin")

        self.assertEqual(
            set(self.by_centre()), {"ke-meteo", "bj-meteobenin"}
        )

    def test_a_region_with_no_centres_at_all_is_an_empty_table(self):
        # A fresh install before the first catalogue sync. Not an error and
        # not a crash: the panel has its own sentence for it.
        self.assertEqual(self.statistics().rows, [])


class WindowTests(AllNodesTestCase):
    """The 24 hours the shape is drawn over, and what is counted in them."""

    def test_the_axis_is_the_last_twenty_four_whole_hours(self):
        # The same fixed window the station sparklines are drawn over, so a
        # centre's shape here and its stations' shapes on its own page are
        # read against one set of hours. The hour in progress is excluded
        # rather than served half counted.
        found = self.statistics()

        self.assertEqual(len(found.hours), 24)
        self.assertEqual(found.hours[0].start, FIRST_HOUR)
        self.assertEqual(found.hours[-1].start, LAST_HOUR)
        self.assertEqual([hour for hour in found.hours if hour.partial], [])
        self.assertEqual(found.window.since, FIRST_HOUR)
        self.assertEqual(found.window.until, at("2026-08-11T12:00:00"))

    def test_a_centre_heard_from_in_no_hour_carries_zeros(self):
        # The commonest row on a region in trouble, and the one that must
        # never be missing: the vector is read positionally, and a flat line
        # is the finding the column is drawn for.
        self.node("bj-meteobenin")

        row = self.by_centre()["bj-meteobenin"]

        self.assertEqual(row.sparkline, [0] * 24)
        self.assertEqual(row.messages_in_window, 0)

    def test_each_hour_lands_at_its_own_index(self):
        node = self.node("ke-meteo", last_seen=NOW - timedelta(hours=1))
        self.rollup(node, FIRST_HOUR, 5)
        self.rollup(node, LAST_HOUR, 7)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.sparkline[0], 5)
        self.assertEqual(row.sparkline[-1], 7)
        self.assertEqual(sum(row.sparkline), 12)

    def test_the_hour_in_progress_is_outside_the_window(self):
        node = self.node("ke-meteo", last_seen=NOW)
        self.rollup(node, at("2026-08-11T12:00:00"), 99)

        self.assertEqual(self.by_centre()["ke-meteo"].messages_in_window, 0)

    def test_traffic_older_than_the_window_is_outside_it(self):
        node = self.node("ke-meteo", last_seen=NOW - timedelta(hours=1))
        self.rollup(node, FIRST_HOUR - timedelta(hours=1), 99)

        self.assertEqual(self.by_centre()["ke-meteo"].messages_in_window, 0)


class VantageTests(AllNodesTestCase):
    """What the world saw, and only that.

    The same publication is a row per vantage point on purpose, so a shape
    counted across all of them draws a centre publishing twice as much the
    moment origin ingestion is switched on -- and the column that is supposed
    to show a propagation gap would show none.
    """

    def setUp(self):
        super().setUp()
        self.node_ = self.node("ke-meteo", last_seen=NOW - timedelta(hours=1))

    def test_the_same_publication_seen_at_origin_too_is_counted_once(self):
        origin = origin_broker(self.node_, is_reachable=True)
        self.rollup(self.node_, LAST_HOUR, 10)
        self.rollup(self.node_, LAST_HOUR, 10, source=origin)

        self.assertEqual(self.by_centre()["ke-meteo"].messages_in_window, 10)

    def test_a_centre_heard_only_at_its_own_broker_reads_as_no_traffic(self):
        # Which is the finding: it published, and nothing reached the world.
        origin = origin_broker(self.node_, is_reachable=True)
        self.rollup(self.node_, LAST_HOUR, 10, source=origin)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.sparkline, [0] * 24)
        self.assertEqual(row.messages_in_window, 0)

    def test_traffic_that_named_no_station_still_counts(self):
        # Unlike the station sparklines, which are per station by
        # construction. This row is the centre's, and a centre's own
        # unattributed traffic is traffic it published -- dropping it would
        # draw a working centre as silent.
        self.rollup(self.node_, LAST_HOUR, 4)

        self.assertEqual(self.by_centre()["ke-meteo"].messages_in_window, 4)

    def test_the_count_is_the_sum_of_the_shape_beside_it(self):
        # One set of numbers, not two. The overview's own volume column ends
        # with the hour in progress while this window ends with the last whole
        # one, so a separately counted total would sit beside a shape covering
        # a different stretch of time.
        self.rollup(self.node_, FIRST_HOUR, 3)
        self.rollup(self.node_, LAST_HOUR, 8)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.messages_in_window, sum(row.sparkline))


class ReadingOrderTests(AllNodesTestCase):
    """What is broken first, then the longest quiet, then the centre ID."""

    def centres(self):
        return [row.centre_id for row in self.statistics().rows]

    def test_the_worst_standing_comes_first(self):
        self.well("cg-met")
        self.node("bi-igebu", last_seen=NOW - timedelta(days=7))
        self.node("bj-meteobenin")

        self.assertEqual(self.centres(), ["bj-meteobenin", "bi-igebu", "cg-met"])

    def test_within_one_standing_the_longest_quiet_comes_first(self):
        self.node("aa-recent", last_seen=NOW - timedelta(days=2))
        self.node("zz-ancient", last_seen=NOW - timedelta(days=30))

        self.assertEqual(self.centres(), ["zz-ancient", "aa-recent"])

    def test_centres_alike_in_every_respect_are_ordered_by_id(self):
        # A fresh install: nothing has been heard from anybody, so every row
        # carries the same standing and the same absent last-seen. Without a
        # final key the order is whatever the database felt like that morning,
        # and the panel reshuffles between two readers looking at one region.
        for centre_id in ("zz-last", "aa-first", "mm-middle"):
            self.node(centre_id)

        self.assertEqual(self.centres(), ["aa-first", "mm-middle", "zz-last"])
