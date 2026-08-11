"""Notification message parsing, against messages captured from a Global Broker.

The fixture is real traffic taken off ``globalbroker.meteo.fr``: African
surface observations that carry a WIGOS station identifier, gridded products
that carry none, the same publication seen at origin and again from two Global
Caches, and a publisher whose messages advertise no canonical link at all.
"""

from datetime import datetime, timezone

from wis2watch.core.interpretation import (
    canonical_link,
    parse_notification,
    station_attribution,
)

from .support import NoNetworkTestCase, load_jsonl_fixture

CAPTURE = "global_broker_notifications.jsonl"

#: A Kenyan synoptic observation, published at origin.
KE_SYNOP = "adeb7de7-7712-4f70-8871-7c5b0302c4f1"
#: A Canadian gridded forecast: no station, and none to be inferred.
CA_GRIDDED = "13850455-e90a-48f6-b3e6-705d77a37297"
#: A Brazilian observation whose links advertise ``update``, not ``canonical``.
BR_SYNOP = "d2e51045-2039-41ad-85cc-e1245a993200"


def captured():
    return load_jsonl_fixture(CAPTURE)


def payload(notification_id):
    for message in captured():
        if message["payload"].get("id") == notification_id:
            return message["payload"]

    raise AssertionError(f"{notification_id} is not in the capture")


class ParseNotificationTests(NoNetworkTestCase):
    def test_notification_fields_are_taken_from_the_message(self):
        parsed = parse_notification(payload(KE_SYNOP))

        self.assertEqual(parsed.notification_id, KE_SYNOP)
        self.assertEqual(
            parsed.publication_time,
            datetime(2026, 8, 11, 10, 45, 48, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parsed.data_id,
            "ke-meteo:synop-dataset-surface-observations/"
            "WIGOS_0-20000-0-63708_20260811T100000",
        )
        self.assertEqual(
            parsed.metadata_id, "urn:wmo:md:ke-meteo:synop-dataset-surface-observations"
        )
        self.assertEqual(
            parsed.canonical_link,
            "http://wis.meteo.go.ke/data/2026-08-11/wis/"
            "urn:wmo:md:ke-meteo:synop-dataset-surface-observations/"
            "WIGOS_0-20000-0-63708_20260811T100000.bufr4",
        )

    def test_publication_time_is_the_messages_own_not_the_time_it_arrived(self):
        message = payload(CA_GRIDDED)

        self.assertEqual(
            parse_notification(message).publication_time,
            datetime(2026, 8, 11, 10, 38, 2, tzinfo=timezone.utc),
        )

    def test_a_publication_time_without_a_zone_is_read_as_utc(self):
        message = dict(payload(KE_SYNOP))
        message["properties"] = dict(message["properties"], pubtime="2026-08-11T10:45:48")

        self.assertEqual(
            parse_notification(message).publication_time,
            datetime(2026, 8, 11, 10, 45, 48, tzinfo=timezone.utc),
        )

    def test_a_message_with_no_uuid_cannot_be_used(self):
        message = dict(payload(KE_SYNOP))
        message.pop("id")

        self.assertIsNone(parse_notification(message))

    def test_a_message_with_no_usable_publication_time_cannot_be_used(self):
        for pubtime in (None, "", "not-a-time"):
            with self.subTest(pubtime=pubtime):
                message = dict(payload(KE_SYNOP))
                message["properties"] = dict(message["properties"], pubtime=pubtime)

                self.assertIsNone(parse_notification(message))

    def test_an_empty_message_cannot_be_used(self):
        self.assertIsNone(parse_notification({}))
        self.assertIsNone(parse_notification(None))


class StationAttributionTests(NoNetworkTestCase):
    def test_the_declared_wigos_station_identifier_attributes_the_message(self):
        parsed = parse_notification(payload(KE_SYNOP))

        self.assertEqual(parsed.wigos_station_id, "0-20000-0-63708")
        self.assertTrue(parsed.is_attributed)

    def test_a_message_with_no_station_property_is_unattributed(self):
        parsed = parse_notification(payload(CA_GRIDDED))

        self.assertEqual(parsed.wigos_station_id, "")
        self.assertFalse(parsed.is_attributed)

    def test_a_station_identifier_embedded_elsewhere_is_never_read_as_attribution(self):
        message = payload(BR_SYNOP)

        # The data identifier spells the station out, and the OSCAR `via` link
        # names it again -- neither is attribution, only the property is.
        self.assertIn("0-76-0-5218003000000581", message["properties"]["data_id"])

        stripped = dict(message)
        stripped["properties"] = {
            key: value
            for key, value in message["properties"].items()
            if key != "wigos_station_identifier"
        }

        self.assertEqual(station_attribution(stripped), "")
        self.assertFalse(parse_notification(stripped).is_attributed)

    def test_a_blank_station_identifier_is_no_attribution(self):
        self.assertEqual(
            station_attribution({"properties": {"wigos_station_identifier": "   "}}), ""
        )
        self.assertEqual(
            station_attribution({"properties": {"wigos_station_identifier": None}}), ""
        )
        self.assertEqual(station_attribution({}), "")
        self.assertEqual(station_attribution(None), "")

    def test_a_message_whose_properties_are_null_is_unattributed(self):
        self.assertEqual(station_attribution({"id": "x", "properties": None}), "")
        self.assertIsNone(parse_notification({"id": "x", "properties": None}))


class CanonicalLinkTests(NoNetworkTestCase):
    def test_a_publisher_that_advertises_no_canonical_link_reports_none(self):
        message = payload(BR_SYNOP)

        self.assertEqual({link["rel"] for link in message["links"]}, {"update", "via"})
        self.assertEqual(canonical_link(message), "")
        self.assertEqual(parse_notification(message).canonical_link, "")

    def test_a_message_with_no_links_reports_none(self):
        self.assertEqual(canonical_link({"id": "x", "properties": {}}), "")
        self.assertEqual(canonical_link({}), "")
        self.assertEqual(canonical_link(None), "")


class CapturedTrafficTests(NoNetworkTestCase):
    def test_every_captured_message_parses(self):
        messages = captured()

        self.assertTrue(messages, "the capture fixture is empty")

        for message in messages:
            with self.subTest(notification_id=message["payload"].get("id")):
                parsed = parse_notification(message["payload"])

                self.assertIsNotNone(parsed)
                self.assertTrue(parsed.notification_id)
                self.assertIsNotNone(parsed.publication_time.tzinfo)
                self.assertTrue(parsed.metadata_id)

    def test_the_capture_covers_attributed_and_unattributed_traffic(self):
        attribution = {
            parse_notification(m["payload"]).is_attributed for m in captured()
        }

        self.assertEqual(attribution, {True, False})

    def test_the_capture_covers_the_monitored_region(self):
        from wis2watch.core.interpretation import is_monitored_centre_id, parse_topic

        centres = {parse_topic(m["topic"]).centre_id for m in captured()}

        self.assertTrue(any(is_monitored_centre_id(centre) for centre in centres))
        self.assertTrue(any(not is_monitored_centre_id(centre) for centre in centres))
