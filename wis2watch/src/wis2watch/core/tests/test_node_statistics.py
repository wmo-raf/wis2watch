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
    WIS2Node,
)
from wis2watch.core.tests.support import at, origin_api, origin_broker


NOW = at("2026-08-11T12:00:00")

#: Where the fixed window falls at ``NOW``: the last 24 *whole* hours, so it
#: ends at the top of the hour in progress rather than part way through it.
FIRST_HOUR = at("2026-08-10T12:00:00")
LAST_HOUR = at("2026-08-11T11:00:00")

#: A data category the topic hierarchy files observations under, which is what
#: the two verdicts are measured over (ADR-0017).
OBSERVATIONS = "surface-based-observations"


class AllNodesTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )

    def node(self, centre_id, observed=None, **kwargs):
        """A registered centre, and where asked for, an observation it published.

        Both verdicts are measured over observation traffic, so a centre meant
        to read as heard-from needs a dataset the hierarchy calls an
        observation and an hour it published in. One seeded without an
        ``observed`` hour declares none, which is a verdict of its own.
        """
        kwargs.setdefault("name", centre_id.upper())
        node = WIS2Node.objects.create(centre_id=centre_id, **kwargs)

        if observed is not None:
            self.rollup(node, observed, 1, dataset=self.observation(node))

        return node

    def observation(self, node, name="synop", policy=Dataset.RECOMMENDED, **kwargs):
        """A dataset of the node's that the topic hierarchy calls an observation.

        Recommended rather than core, so that seeding one does not also seed
        an expectation of the Global Caches -- a fixture that quietly
        published core data would make every centre here read as uncached,
        which is a judgement these tests break on purpose one at a time.
        """
        return Dataset.objects.create(
            node=node,
            identifier=f"urn:wmo:md:{node.centre_id}:{name}",
            title=name,
            wmo_data_policy=policy,
            wmo_topic_hierarchy=(
                f"origin/a/wis2/{node.centre_id}/data/core/weather/{OBSERVATIONS}/{name}"
            ),
            raw_json={},
            **kwargs,
        )

    def learned(self, dataset, interval_hours=6):
        """A dataset whose own history says how often it publishes."""
        CadenceBaseline.objects.create(
            dataset=dataset,
            interval_hours=interval_hours,
            observations=20,
            learned_at=NOW - timedelta(days=1),
        )

        return dataset

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

        Its observation lands in the hour *in progress*, which is inside what
        staleness measures and outside the fixed window the shapes are drawn
        over. So a centre can be seeded well without putting a message into a
        sparkline a test is counting.
        """
        node = self.node(centre_id, observed=NOW)
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
        # silence rather than findings of their own. It declares an
        # observation and has never published one, which is the finding --
        # a centre declaring none at all is a different row entirely.
        self.observation(self.node("bj-meteobenin"))

        self.assertEqual(self.standing("bj-meteobenin"), NodeStanding.NEVER_SEEN)

    def test_a_centre_gone_quiet_is_stale_rather_than_anything_downstream(self):
        self.node("bi-igebu", observed=NOW - timedelta(days=7))

        self.assertEqual(self.standing("bi-igebu"), NodeStanding.STALE)

    def test_a_publishing_centre_with_a_dataset_overdue_is_silent(self):
        node = self.well("gh-gmet")
        temp = self.learned(self.observation(node, "temp"))
        self.rollup(node, NOW - timedelta(days=3), 1, dataset=temp)

        self.assertEqual(self.standing("gh-gmet"), NodeStanding.SILENT)

    def test_an_overdue_non_observation_leaves_the_standing_alone(self):
        """Aviation and advisories keep their own verdict on the node detail
        page. They never decide the headline word."""
        node = self.well("gh-gmet")
        metar = self.observation(node, "metar")
        metar.wmo_topic_hierarchy = (
            "origin/a/wis2/gh-gmet/data/core/weather/advisories-warnings/metar"
        )
        metar.save(update_fields=["wmo_topic_hierarchy"])
        self.learned(metar)
        self.rollup(node, NOW - timedelta(days=3), 1, dataset=metar)

        self.assertEqual(self.standing("gh-gmet"), NodeStanding.HEALTHY)

    def test_a_centre_declaring_no_observations_says_that_and_not_a_fault(self):
        """Nothing this installation watches is coming out of it, and nothing
        it watches has stopped either. Reported as neither."""
        self.well("td-meteotchad").datasets.all().delete()

        self.assertEqual(
            self.standing("td-meteotchad"), NodeStanding.NO_OBSERVATIONS
        )

    def test_a_centre_publishing_core_data_no_cache_carried_is_not_cached(self):
        node = self.well("ke-meteo")
        core = self.observation(node, "temp", policy=Dataset.CORE)
        self.rollup(node, NOW - timedelta(hours=1), 12, dataset=core)

        self.assertEqual(self.standing("ke-meteo"), NodeStanding.NOT_CACHED)

    def test_a_centre_nothing_is_watching_is_not_watched(self):
        self.node("cd-mettelsat", observed=NOW)

        self.assertEqual(self.standing("cd-mettelsat"), NodeStanding.NO_BROKER)

    def test_a_centre_answering_only_at_its_archive_says_which(self):
        # Not folded in with the centre above it. Both are failing the same
        # obligation, and one of them is publishing perfectly well through a
        # transport this tool can read -- on a region where that is most of the
        # centres, one standing over both sorts nothing.
        node = self.node("ma-marocmeteo", observed=NOW)
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
                # With the transmission judgements above it rather than with
                # the plumbing below, because that is what it is -- and
                # because the two verdicts can only share one sort order
                # while the ranks they share come in one sequence.
                NodeStanding.NO_OBSERVATIONS,
                NodeStanding.NOT_CACHED,
                NodeStanding.NO_BROKER,
                NodeStanding.ARCHIVE_ONLY,
                NodeStanding.HEALTHY,
            ],
        )
        self.assertEqual(NodeStanding.RANK[NodeStanding.NEVER_SEEN], 0)
        self.assertEqual(NodeStanding.RANK[NodeStanding.HEALTHY], 7)


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
        self.observation(self.node("bj-meteobenin"))

        self.assertEqual(
            self.transmission("bj-meteobenin"), TransmissionStanding.NEVER_SEEN
        )

    def test_a_centre_gone_quiet_is_stale(self):
        self.node("bi-igebu", observed=NOW - timedelta(days=7))

        self.assertEqual(self.transmission("bi-igebu"), TransmissionStanding.STALE)

    def test_a_centre_whose_observations_stopped_is_quiet_however_much_else_flows(self):
        """The finding this whole slice exists for. Its aviation advisories
        are arriving by the hundred and the thing it is watched for stopped
        three days ago."""
        node = self.node("ne-meteo", observed=NOW - timedelta(days=3))
        self.rollup(node, LAST_HOUR, 400)

        row = self.by_centre()["ne-meteo"]

        self.assertEqual(row.transmission, TransmissionStanding.STALE)
        self.assertEqual(row.messages_in_window, 400)

    def test_a_centre_declaring_no_observations_is_not_read_as_transmitting(self):
        """It may be publishing warnings by the hour. None of it is what this
        panel is watching for, and saying "Transmitting" would be the panel
        answering a question nobody asked it."""
        node = self.node("td-meteotchad")
        self.rollup(node, LAST_HOUR, 400)

        row = self.by_centre()["td-meteotchad"]

        self.assertEqual(row.transmission, TransmissionStanding.NO_OBSERVATIONS)
        self.assertEqual(row.messages_in_window, 400)

    def test_a_centre_with_a_dataset_overdue_says_so_however_much_it_publishes(self):
        """The label says "Behind schedule" and never that the centre is quiet.

        On the live region this lands on centres sending three hundred
        notifications an hour and last heard from six minutes ago: one dataset
        past its own cadence is enough. A verdict that read as silence there
        would be a verdict nobody believed twice.

        Nor does it say "dataset". That was the label until a reader who has
        never registered a WCMP2 record met it on the front page with nothing
        beside it; the count that makes the word teachable rides under the
        badge on the glance table instead.
        """
        node = self.well("dz-meteoalgerie")
        temp = self.learned(self.observation(node, "temp"))
        self.rollup(node, NOW - timedelta(days=3), 1, dataset=temp)
        # And publishing hard, right now.
        self.rollup(node, LAST_HOUR, 310)

        row = self.by_centre()["dz-meteoalgerie"]

        self.assertEqual(row.transmission, TransmissionStanding.SILENT)
        self.assertEqual(row.messages_in_window, 310)
        self.assertEqual(
            TransmissionStanding.LABELS[row.transmission], "Behind schedule"
        )

    def test_a_centre_answering_only_at_its_archive_is_still_transmitting(self):
        """The whole point of the second verdict.

        Twenty-eight of thirty-two centres in the region fall back to their
        archives. Folding that in put twenty-one of them under "Archive only"
        on a panel whose job is to say whether data is flowing.
        """
        node = self.node("ma-marocmeteo", observed=NOW)
        origin_broker(node, is_reachable=False)
        origin_api(node, is_reachable=True)

        row = self.by_centre()["ma-marocmeteo"]

        self.assertEqual(row.transmission, TransmissionStanding.TRANSMITTING)
        self.assertEqual(row.standing, NodeStanding.ARCHIVE_ONLY)

    def test_a_centre_nothing_watches_is_still_transmitting(self):
        self.node("cd-mettelsat", observed=NOW)

        row = self.by_centre()["cd-mettelsat"]

        self.assertEqual(row.transmission, TransmissionStanding.TRANSMITTING)
        self.assertEqual(row.standing, NodeStanding.NO_BROKER)

    def test_uncached_core_data_does_not_make_a_centre_stop_transmitting(self):
        node = self.well("ke-meteo")
        core = self.observation(node, "temp", policy=Dataset.CORE)
        self.rollup(node, LAST_HOUR, 12, dataset=core)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.transmission, TransmissionStanding.TRANSMITTING)
        self.assertEqual(row.standing, NodeStanding.NOT_CACHED)

    def test_it_is_a_coarsening_of_the_full_standing_and_not_a_rival(self):
        """Its four verdicts are the full standing's four worst, same names.

        This is what lets one server order serve both tables, and what stops
        the two surfaces disagreeing about which centre to look at first.
        """
        self.assertEqual(
            [key for key, _label in TransmissionStanding.CHOICES][:4],
            [key for key, _label in NodeStanding.CHOICES][:4],
        )
        self.assertEqual(
            TransmissionStanding.RANK[TransmissionStanding.TRANSMITTING],
            len(NodeStanding.CHOICES) - 4,
        )

    def test_four_of_its_five_labels_are_the_full_standings_own(self):
        """One vocabulary across two tables, not two."""
        for key in ("never_seen", "stale", "silent", "no_observations"):
            self.assertEqual(
                TransmissionStanding.LABELS[key], NodeStanding.LABELS[key]
            )


class CoverageTests(AllNodesTestCase):
    """Every registered centre is a row, whatever has been heard from it."""

    def test_a_centre_nothing_has_arrived_from_is_a_row_not_an_absence(self):
        self.node("ke-meteo", observed=NOW)
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
        node = self.node("ke-meteo", observed=NOW)
        self.rollup(node, FIRST_HOUR, 5)
        self.rollup(node, LAST_HOUR, 7)

        row = self.by_centre()["ke-meteo"]

        self.assertEqual(row.sparkline[0], 5)
        self.assertEqual(row.sparkline[-1], 7)
        self.assertEqual(sum(row.sparkline), 12)

    def test_the_hour_in_progress_is_outside_the_window(self):
        node = self.node("ke-meteo", observed=NOW)
        self.rollup(node, at("2026-08-11T12:00:00"), 99)

        self.assertEqual(self.by_centre()["ke-meteo"].messages_in_window, 0)

    def test_traffic_older_than_the_window_is_outside_it(self):
        node = self.node("ke-meteo", observed=NOW)
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
        self.node_ = self.node("ke-meteo", observed=NOW)

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
        self.node("bi-igebu", observed=NOW - timedelta(days=7))
        self.observation(self.node("bj-meteobenin"))

        self.assertEqual(self.centres(), ["bj-meteobenin", "bi-igebu", "cg-met"])

    def test_within_one_standing_the_longest_quiet_comes_first(self):
        self.node("aa-recent", observed=NOW - timedelta(days=2))
        self.node("zz-ancient", observed=NOW - timedelta(days=30))

        self.assertEqual(self.centres(), ["zz-ancient", "aa-recent"])

    def test_centres_alike_in_every_respect_are_ordered_by_id(self):
        # A fresh install: nothing has been heard from anybody, so every row
        # carries the same standing and the same absent last-seen. Without a
        # final key the order is whatever the database felt like that morning,
        # and the panel reshuffles between two readers looking at one region.
        for centre_id in ("zz-last", "aa-first", "mm-middle"):
            self.observation(self.node(centre_id))

        self.assertEqual(self.centres(), ["aa-first", "mm-middle", "zz-last"])
