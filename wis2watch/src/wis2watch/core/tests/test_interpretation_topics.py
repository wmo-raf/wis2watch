"""WIS2 topic parsing, against topics captured from a Global Broker."""

from django.test import override_settings

from wis2watch.core.interpretation import (
    CACHE,
    announces_catalogue_record,
    centre_id_prefix,
    is_monitored_centre_id,
    is_observation_topic,
    monitored_country_code_for_centre_id,
    parse_topic,
    subscription_topic,
    sweep_topic,
)

from .support import NoNetworkTestCase, load_json_fixture, load_jsonl_fixture


class ParseTopicTests(NoNetworkTestCase):
    def test_origin_topic_yields_prefix_centre_id_and_hierarchy(self):
        parsed = parse_topic(
            "origin/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop"
        )

        self.assertEqual(parsed.prefix, "origin")
        self.assertEqual(parsed.centre_id, "ke-meteo")
        self.assertEqual(
            parsed.hierarchy,
            ("data", "core", "weather", "surface-based-observations", "synop"),
        )

    def test_cache_topic_is_recognised_as_cached_traffic(self):
        parsed = parse_topic(
            "cache/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop"
        )

        self.assertEqual(parsed.prefix, "cache")
        self.assertTrue(parsed.is_cache)
        self.assertFalse(parsed.is_origin)
        self.assertEqual(parsed.centre_id, "ke-meteo")

    def test_a_cache_topic_reduces_to_the_origin_topic_it_mirrors(self):
        cached = parse_topic(
            "cache/a/wis2/cg-met/data/core/climate/surface-based-observations/climat"
        )

        self.assertEqual(
            cached.as_origin().raw,
            "origin/a/wis2/cg-met/data/core/climate/surface-based-observations/climat",
        )

    def test_centre_id_and_prefix_are_normalised(self):
        parsed = parse_topic(
            "  ORIGIN/a/wis2/KE-METEO/data/core/weather/surface-based-observations/synop  "
        )

        self.assertEqual(parsed.prefix, "origin")
        self.assertEqual(parsed.centre_id, "ke-meteo")

    def test_a_topic_with_no_hierarchy_below_the_centre_still_parses(self):
        parsed = parse_topic("origin/a/wis2/ke-meteo")

        self.assertEqual(parsed.centre_id, "ke-meteo")
        self.assertEqual(parsed.hierarchy, ())

    def test_a_topic_that_is_not_a_wis2_topic_does_not_parse(self):
        self.assertIsNone(parse_topic("origin/a/wis1/ke-meteo/data"))
        self.assertIsNone(parse_topic("origin/b/wis2/ke-meteo/data"))
        self.assertIsNone(parse_topic("some/other/thing"))
        self.assertIsNone(parse_topic(""))
        self.assertIsNone(parse_topic(None))


class ObservationTopicTests(NoNetworkTestCase):
    """Which topics carry observations, read off the hierarchy the centre chose.

    The question this installation is standing up to answer is whether
    observations are coming out of the region, and the only thing that says
    whether a dataset is one is where its publisher put it in the WMO topic
    hierarchy. So it is read there, for every discipline, rather than asked of
    an operator per dataset.
    """

    def topic(self, discipline, category, prefix="origin"):
        return f"{prefix}/a/wis2/ke-meteo/data/core/{discipline}/{category}/synop"

    def test_surface_based_observations_are_observations(self):
        self.assertTrue(
            is_observation_topic(self.topic("weather", "surface-based-observations"))
        )

    def test_space_based_observations_are_observations(self):
        self.assertTrue(
            is_observation_topic(self.topic("weather", "space-based-observations"))
        )

    def test_every_discipline_counts_and_not_only_weather(self):
        """Four centres in the region publish observations under `climate`."""
        for discipline in (
            "climate",
            "hydrology",
            "atmospheric-composition",
            "ocean",
            "cryosphere",
            "space-weather",
        ):
            with self.subTest(discipline=discipline):
                self.assertTrue(
                    is_observation_topic(
                        self.topic(discipline, "surface-based-observations")
                    )
                )

    def test_another_data_category_is_not_an_observation(self):
        for category in ("aviation", "prediction", "advisories-warnings", "hazard"):
            with self.subTest(category=category):
                self.assertFalse(is_observation_topic(self.topic("weather", category)))

    def test_a_cached_observation_topic_is_still_an_observation(self):
        """A Global Cache mirrors the hierarchy, so it carries the same answer."""
        self.assertTrue(
            is_observation_topic(
                self.topic("weather", "surface-based-observations", prefix=CACHE)
            )
        )

    def test_a_topic_that_is_not_a_wis2_topic_is_not_an_observation(self):
        """An unreadable topic is answered, not raised on."""
        for topic in (None, "", "   ", "some/other/thing", "origin/a/wis1/ke-meteo"):
            with self.subTest(topic=topic):
                self.assertFalse(is_observation_topic(topic))

    def test_a_topic_stopping_above_the_category_is_not_an_observation(self):
        for topic in (
            "origin/a/wis2/ke-meteo",
            "origin/a/wis2/ke-meteo/data",
            "origin/a/wis2/ke-meteo/data/core",
            "origin/a/wis2/ke-meteo/data/core/weather",
        ):
            with self.subTest(topic=topic):
                self.assertFalse(is_observation_topic(topic))

    def test_a_centres_own_catalogue_announcement_is_not_an_observation(self):
        self.assertFalse(is_observation_topic("origin/a/wis2/ke-meteo/metadata"))

    def test_a_category_named_above_the_discipline_does_not_count(self):
        """The level is what makes it a category, not the word appearing."""
        self.assertFalse(
            is_observation_topic(
                "origin/a/wis2/ke-meteo/data/core/surface-based-observations"
            )
        )

    def test_a_parsed_topic_carries_the_discipline_and_the_category_it_names(self):
        parsed = parse_topic(self.topic("climate", "surface-based-observations"))

        self.assertEqual(parsed.discipline, "climate")
        self.assertEqual(parsed.data_category, "surface-based-observations")
        self.assertTrue(parsed.is_observation)

    def test_a_topic_below_a_category_still_reads_as_that_category(self):
        """wis2box publishes several levels below the category; all of them count."""
        self.assertTrue(
            is_observation_topic(
                "origin/a/wis2/rw-rma/data/core/weather/"
                "surface-based-observations/synop/landfixed"
            )
        )


class SubscriptionTopicTests(NoNetworkTestCase):
    """One topic filter per centre, so the region is ingested and not the world."""

    def test_a_centre_subscribes_to_everything_it_publishes(self):
        self.assertEqual(
            subscription_topic("ke-meteo"), "origin/a/wis2/ke-meteo/#"
        )

    def test_the_cache_prefix_is_subscribed_separately(self):
        self.assertEqual(
            subscription_topic("ke-meteo", prefix=CACHE), "cache/a/wis2/ke-meteo/#"
        )

    def test_a_centre_id_is_normalised_the_way_a_parsed_topic_is(self):
        self.assertEqual(
            subscription_topic("  KE-Meteo "), "origin/a/wis2/ke-meteo/#"
        )

    def test_a_filter_matches_the_topics_that_centre_really_publishes(self):
        captured = load_jsonl_fixture("global_broker_notifications.jsonl")
        origin_topics = [
            m["topic"] for m in captured if parse_topic(m["topic"]).is_origin
        ]

        subscribed = subscription_topic("ke-meteo").removesuffix("#")

        matched = {t for t in origin_topics if t.startswith(subscribed)}

        self.assertEqual(
            matched,
            {"origin/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop"},
        )

    def test_no_centre_is_no_filter(self):
        self.assertIsNone(subscription_topic(""))
        self.assertIsNone(subscription_topic(None))
        self.assertIsNone(subscription_topic("   "))


class CatalogueRecordAnnouncementTests(NoNetworkTestCase):
    """Telling a centre announcing its record from a centre publishing data."""

    def test_the_metadata_topic_carries_a_catalogue_record(self):
        self.assertTrue(
            announces_catalogue_record("origin/a/wis2/ke-meteo/metadata")
        )

    def test_a_cache_republishing_one_is_the_same_announcement(self):
        self.assertTrue(
            announces_catalogue_record("cache/a/wis2/ke-meteo/metadata")
        )

    def test_a_record_named_below_the_metadata_level_is_still_one(self):
        self.assertTrue(
            announces_catalogue_record(
                "origin/a/wis2/gh-gmet/metadata/core.surface-based-observations.synop"
            )
        )

    def test_a_data_topic_is_not_one(self):
        self.assertFalse(
            announces_catalogue_record(
                "origin/a/wis2/ke-meteo/data/core/weather/"
                "surface-based-observations/synop"
            )
        )

    def test_a_centre_publishing_data_about_metadata_is_not_one(self):
        """Only the level directly below the centre decides it."""
        self.assertFalse(
            announces_catalogue_record("origin/a/wis2/ke-meteo/data/core/metadata")
        )

    def test_a_topic_that_is_not_a_wis2_topic_is_not_one(self):
        self.assertFalse(announces_catalogue_record("some/other/metadata"))

    def test_with_no_topic_the_data_identifier_answers_it(self):
        """A centre's own archive returns the notification without a topic."""
        self.assertTrue(
            announces_catalogue_record(
                "",
                data_id=(
                    "gh-gmet/metadata/"
                    "urn:wmo:md:gh-gmet:core.surface-based-observations.synop"
                ),
            )
        )

    def test_with_no_topic_a_data_identifier_naming_data_is_not_one(self):
        self.assertFalse(
            announces_catalogue_record(
                "",
                data_id=(
                    "gh-gmet:core.surface-based-observations.synop/"
                    "WIGOS_0-288-0-65492_20260809T160000"
                ),
            )
        )

    def test_a_topic_that_was_observed_settles_it_alone(self):
        """What a message went out on outranks what it says about itself."""
        self.assertFalse(
            announces_catalogue_record(
                "origin/a/wis2/gh-gmet/data/core/weather/"
                "surface-based-observations/synop",
                data_id="gh-gmet/metadata/urn:wmo:md:gh-gmet:whatever",
            )
        )

    def test_nothing_named_at_all_is_not_one(self):
        self.assertFalse(announces_catalogue_record("", data_id=""))
        self.assertFalse(announces_catalogue_record(None, data_id=None))


class ArchivedCatalogueRecordTests(NoNetworkTestCase):
    """The captured archives carry one announcement each, and it is found."""

    def announcements_in(self, capture):
        return [
            feature
            for feature in load_json_fixture(capture)["features"]
            if announces_catalogue_record(
                "", data_id=feature["properties"].get("data_id", "")
            )
        ]

    def test_each_captured_archive_page_carries_exactly_one(self):
        for capture in (
            "node_messages_sc_seychelles_met.json",
            "node_messages_gh_gmet.json",
        ):
            with self.subTest(capture=capture):
                found = self.announcements_in(capture)

                self.assertEqual(len(found), 1)
                self.assertEqual(
                    [link["rel"] for link in found[0]["links"]], ["update"]
                )


class SweepTopicTests(NoNetworkTestCase):
    """The one filter that names no centre, for the centres nothing names."""

    def test_the_sweep_asks_for_every_centre_publishing_at_origin(self):
        self.assertEqual(sweep_topic(), "origin/a/wis2/+/#")


class CapturedTopicTests(NoNetworkTestCase):
    """Every topic captured from a Global Broker parses to its own centre."""

    def test_every_captured_topic_parses(self):
        captured = load_jsonl_fixture("global_broker_notifications.jsonl")

        self.assertTrue(captured, "the capture fixture is empty")

        for message in captured:
            with self.subTest(topic=message["topic"]):
                parsed = parse_topic(message["topic"])

                self.assertIsNotNone(parsed)
                self.assertIn(parsed.prefix, ("origin", "cache"))
                self.assertTrue(parsed.centre_id)
                self.assertTrue(parsed.hierarchy)

    def test_the_capture_covers_both_origin_and_cache_traffic(self):
        captured = load_jsonl_fixture("global_broker_notifications.jsonl")
        prefixes = {parse_topic(m["topic"]).prefix for m in captured}

        self.assertEqual(prefixes, {"origin", "cache"})


class MonitoredRegionFromCapturedTrafficTests(NoNetworkTestCase):
    """Region membership decided from the centre IDs the sources really carry."""

    def captured_centre_ids(self):
        return {
            parse_topic(message["topic"]).centre_id
            for message in load_jsonl_fixture("global_broker_notifications.jsonl")
        }

    def catalogue_centre_ids(self):
        return {
            (feature["properties"].get("centre-id") or "").lower()
            for feature in load_json_fixture("gdc_discovery_metadata.json")["features"]
            if feature["properties"].get("centre-id")
        }

    def test_captured_traffic_splits_into_the_region_and_the_rest_of_the_world(self):
        monitored = {c for c in self.captured_centre_ids() if is_monitored_centre_id(c)}
        rest = self.captured_centre_ids() - monitored

        self.assertEqual(monitored, {"ke-meteo", "ng-nimet", "dj-anm"})
        self.assertEqual(rest, {"br-inmet", "ca-eccc-msc"})

    def test_a_monitored_centre_resolves_to_its_own_country(self):
        self.assertEqual(monitored_country_code_for_centre_id("ke-meteo"), "KE")
        self.assertEqual(monitored_country_code_for_centre_id("ng-nimet"), "NG")
        self.assertEqual(monitored_country_code_for_centre_id("dj-anm"), "DJ")

    def test_a_non_country_prefix_in_the_catalogue_belongs_to_no_country(self):
        self.assertIn("int-eumetsat", self.catalogue_centre_ids())

        self.assertEqual(centre_id_prefix("int-eumetsat"), "int")
        self.assertFalse(is_monitored_centre_id("int-eumetsat"))
        self.assertEqual(monitored_country_code_for_centre_id("int-eumetsat"), "")

    @override_settings(WIS2WATCH_MONITORED_COUNTRIES=["KE"])
    def test_narrowing_the_monitored_list_narrows_what_the_capture_matches(self):
        monitored = {c for c in self.captured_centre_ids() if is_monitored_centre_id(c)}

        self.assertEqual(monitored, {"ke-meteo"})
