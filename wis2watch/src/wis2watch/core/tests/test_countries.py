from django.test import SimpleTestCase, override_settings

from wis2watch.core.countries import (
    AFRICA_COUNTRY_CODES,
    centre_id_prefix,
    get_monitored_country_codes,
    is_monitored_centre_id,
    monitored_country_code_for_centre_id,
)


class MonitoredCountryCodesTests(SimpleTestCase):
    def test_defaults_to_africa(self):
        self.assertEqual(get_monitored_country_codes(), AFRICA_COUNTRY_CODES)

    def test_africa_covers_the_fifty_four_member_states(self):
        self.assertEqual(len(AFRICA_COUNTRY_CODES), 54)
        self.assertIn("KE", AFRICA_COUNTRY_CODES)
        self.assertIn("ZA", AFRICA_COUNTRY_CODES)
        self.assertNotIn("FR", AFRICA_COUNTRY_CODES)

    @override_settings(WIS2WATCH_MONITORED_COUNTRIES=["ke", "ug"])
    def test_settings_override_wins_and_is_normalised(self):
        self.assertEqual(get_monitored_country_codes(), ("KE", "UG"))

    @override_settings(WIS2WATCH_MONITORED_COUNTRIES=[])
    def test_empty_override_falls_back_to_africa(self):
        self.assertEqual(get_monitored_country_codes(), AFRICA_COUNTRY_CODES)


class CentreIdPrefixTests(SimpleTestCase):
    def test_prefix_is_the_token_before_the_first_hyphen(self):
        self.assertEqual(centre_id_prefix("ke-kmd"), "ke")
        self.assertEqual(centre_id_prefix("ma-marocmeteo-global-monitor"), "ma")

    def test_prefix_is_lowercased_and_stripped(self):
        self.assertEqual(centre_id_prefix("  KE-KMD "), "ke")

    def test_non_country_prefixes_are_returned_verbatim(self):
        self.assertEqual(
            centre_id_prefix("data-metoffice-noaa-global-cache"), "data"
        )

    def test_missing_centre_id_has_no_prefix(self):
        self.assertEqual(centre_id_prefix(""), "")
        self.assertEqual(centre_id_prefix(None), "")


class MonitoredMembershipTests(SimpleTestCase):
    def test_monitored_centre_id_resolves_to_its_country(self):
        self.assertTrue(is_monitored_centre_id("ke-kmd"))
        self.assertEqual(monitored_country_code_for_centre_id("ke-kmd"), "KE")

    def test_centre_id_outside_the_monitored_region_resolves_to_nothing(self):
        self.assertFalse(is_monitored_centre_id("br-inmet-global-broker"))
        self.assertEqual(
            monitored_country_code_for_centre_id("br-inmet-global-broker"), ""
        )

    def test_non_country_prefix_resolves_to_nothing(self):
        self.assertFalse(is_monitored_centre_id("data-metoffice-noaa-global-cache"))
        self.assertEqual(
            monitored_country_code_for_centre_id("data-metoffice-noaa-global-cache"),
            "",
        )

    def test_centre_id_without_a_hyphen_resolves_to_nothing(self):
        self.assertFalse(is_monitored_centre_id("ke"))
        self.assertEqual(monitored_country_code_for_centre_id("ke"), "")

    def test_blank_centre_id_resolves_to_nothing(self):
        self.assertFalse(is_monitored_centre_id(""))
        self.assertEqual(monitored_country_code_for_centre_id(""), "")

    @override_settings(WIS2WATCH_MONITORED_COUNTRIES=["ke"])
    def test_membership_follows_the_configured_list(self):
        self.assertTrue(is_monitored_centre_id("ke-kmd"))
        self.assertFalse(is_monitored_centre_id("ug-unma"))
