"""Timestamp reading, against the forms the captured sources actually use."""

from datetime import datetime, timedelta, timezone

from wis2watch.core.interpretation import parse_timestamp

from .support import NoNetworkTestCase


class ParseTimestampTests(NoNetworkTestCase):
    def test_a_zulu_timestamp_is_read_as_utc(self):
        self.assertEqual(
            parse_timestamp("2026-08-11T10:45:48Z"),
            datetime(2026, 8, 11, 10, 45, 48, tzinfo=timezone.utc),
        )

    def test_a_timestamp_with_an_offset_keeps_its_offset(self):
        parsed = parse_timestamp("2020-09-22T00:00:00.000+03:00")

        self.assertEqual(parsed.utcoffset(), timedelta(hours=3))
        self.assertEqual(
            parsed.astimezone(timezone.utc),
            datetime(2020, 9, 21, 21, 0, tzinfo=timezone.utc),
        )

    def test_a_timestamp_with_no_zone_is_read_as_utc(self):
        self.assertEqual(
            parse_timestamp("2025-10-14T04:34:19"),
            datetime(2025, 10, 14, 4, 34, 19, tzinfo=timezone.utc),
        )

    def test_a_date_alone_is_read_as_midnight_utc(self):
        self.assertEqual(
            parse_timestamp("2025-10-14"),
            datetime(2025, 10, 14, 0, 0, tzinfo=timezone.utc),
        )

    def test_an_unusable_timestamp_is_absent_rather_than_now(self):
        self.assertIsNone(parse_timestamp("not-a-time"))
        self.assertIsNone(parse_timestamp("2025-13-45T99:99:99Z"))
        self.assertIsNone(parse_timestamp(""))
        self.assertIsNone(parse_timestamp(None))
        self.assertIsNone(parse_timestamp(1760000000))
