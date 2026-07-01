from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.connectors.calendar import fetch_calendar_records


ICS_TEXT = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:event-1@example.com
DTSTAMP:20260630T100000Z
DTSTART:20260701T160000Z
DTEND:20260701T170000Z
SUMMARY:Cortex review
DESCRIPTION:We decided Calendar events should sync as cited memory\\nwith comma\\, semicolon\\; and folded
 continuation.
LOCATION:Doppl HQ
ORGANIZER;CN=Sarp:mailto:sarp@example.com
ATTENDEE;CN=Ada:mailto:ada@example.com
ATTENDEE;CN=Grace:mailto:grace@example.com
BEGIN:VALARM
DESCRIPTION:Reminder text should not replace the event description.
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:event-2@example.com
DTSTART;VALUE=DATE:20260702
DTEND;VALUE=DATE:20260703
SUMMARY:All-day planning
END:VEVENT
BEGIN:VEVENT
SUMMARY:Missing UID
END:VEVENT
END:VCALENDAR
"""


class CalendarConnectorTests(unittest.TestCase):
    def test_fetch_calendar_records_parses_events_without_leaking_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private-calendar.ics"
            path.write_text(ICS_TEXT, encoding="utf-8")

            sync = fetch_calendar_records(ics_path=str(path), max_records=10)

        self.assertEqual(sync.records_found, 3)
        self.assertEqual(sync.records_returned, 2)
        self.assertEqual(sync.high_water_mark, "20260702")
        self.assertTrue(any(error["error"] == "Calendar event missing UID" for error in sync.errors))
        self.assertNotIn(str(path), str(sync.to_summary()))
        first = sync.records[0].to_source_account_record()
        self.assertEqual(first["external_id"], "calendar:event:event-1@example.com")
        self.assertEqual(first["source_url"], "calendar://event/event-1%40example.com")
        self.assertIn("Source: Calendar", first["content"])
        self.assertIn("with comma, semicolon; and foldedcontinuation.", first["content"])
        self.assertNotIn("Reminder text", first["content"])
        self.assertEqual(first["metadata"]["url"], "calendar://event/event-1%40example.com")
        self.assertEqual(first["metadata"]["attendees"], ["mailto:ada@example.com", "mailto:grace@example.com"])
        self.assertNotIn(str(path), str(first))

    def test_fetch_calendar_records_supports_feed_url_without_exposing_token(self) -> None:
        calls: list[str] = []

        def fake_request(url: str) -> str:
            calls.append(url)
            self.assertEqual(url, "https://calendar.example.com/private.ics?token=secret-feed-token")
            return ICS_TEXT

        sync = fetch_calendar_records(
            feed_url="webcal://calendar.example.com/private.ics?token=secret-feed-token",
            max_records=1,
            request_text=fake_request,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(sync.records_returned, 1)
        self.assertEqual(sync.source_label, "calendar.example.com")
        self.assertNotIn("secret-feed-token", str(sync.to_summary()))
        self.assertNotIn("calendar.example.com/private", str(sync.records[0].to_source_account_record()))
        self.assertEqual(sync.records[0].source_url, "calendar://event/event-1%40example.com")

    def test_fetch_calendar_records_requires_exactly_one_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            fetch_calendar_records()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            fetch_calendar_records(ics_path="/tmp/calendar.ics", feed_url="https://calendar.example.com/private.ics")


if __name__ == "__main__":
    unittest.main()
