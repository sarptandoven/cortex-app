from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType
from typing import Any

from backend.app.database import init_db
from backend.app.storage import CortexStore


SLACK_TOKEN = "xoxb_scheduled_remaining_secret_123"
SLACK_CHANNEL = "C123ABC|private-contract"
SLACK_WORKSPACE_URL = "https://doppl.slack.com"
SLACK_API_BASE_URL = "https://slack.invalid/api"

JIRA_EMAIL = "sarp+scheduled@example.com"
JIRA_API_TOKEN = "jira_scheduled_remaining_secret_123"
JIRA_SITE_URL = "https://doppl.atlassian.net"
JIRA_JQL = "project = COR ORDER BY updated DESC"

CALENDAR_FEED_TOKEN = "calendar_feed_scheduled_secret_123"
CALENDAR_FEED_PATH = "/private/scheduled-calendar.ics"
CALENDAR_FEED_URL = f"webcal://calendar.example.com{CALENDAR_FEED_PATH}?token={CALENDAR_FEED_TOKEN}"
NORMALIZED_CALENDAR_FEED_URL = f"https://calendar.example.com{CALENDAR_FEED_PATH}?token={CALENDAR_FEED_TOKEN}"
CALENDAR_LOCAL_PATH = "/Users/sarp/Private/Scheduled Calendar.ics"

EMPTY_ICS = """BEGIN:VCALENDAR
VERSION:2.0
END:VCALENDAR
"""

SECRET_VALUES = (
    SLACK_TOKEN,
    JIRA_EMAIL,
    JIRA_API_TOKEN,
    CALENDAR_FEED_TOKEN,
    CALENDAR_FEED_PATH,
    CALENDAR_FEED_URL,
    NORMALIZED_CALENDAR_FEED_URL,
    CALENDAR_LOCAL_PATH,
)

EXPECTED_CURSOR_NAMES = {
    "slack": "messages",
    "calendar": "events",
    "jira": "issues",
}


class ScheduledRemainingConnectorCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "cortex-test.db"
        self.vault_path = self.root / "vault"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=self.vault_path)
        self.user_id = "scheduled-remaining-credential-user"
        self.maxDiff = None

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_initial_sync_stores_local_credentials_and_redacts_source_accounts(self) -> None:
        accounts = self._connect_accounts()

        credentials = json.loads(self.store.vault.credentials_path.read_text(encoding="utf-8"))
        stored_by_id = credentials["users"][self.user_id]

        self.assertEqual(
            stored_by_id[accounts["slack"]["id"]]["payload"],
            {
                "token": SLACK_TOKEN,
                "channels": [SLACK_CHANNEL],
                "workspace_url": SLACK_WORKSPACE_URL,
                "api_base_url": SLACK_API_BASE_URL,
            },
        )
        self.assertEqual(
            stored_by_id[accounts["jira"]["id"]]["payload"],
            {
                "email": JIRA_EMAIL,
                "api_token": JIRA_API_TOKEN,
                "site_url": JIRA_SITE_URL,
                "jql": JIRA_JQL,
            },
        )
        self.assertEqual(
            stored_by_id[accounts["calendar_feed"]["id"]]["payload"],
            {
                "ics_path": "",
                "feed_url": CALENDAR_FEED_URL,
                "input_type": "feed",
                "source_label": "calendar.example.com",
            },
        )
        self.assertEqual(
            stored_by_id[accounts["calendar_local"]["id"]]["payload"],
            {
                "ics_path": CALENDAR_LOCAL_PATH,
                "feed_url": "",
                "input_type": "local_file",
                "source_label": "Scheduled Calendar.ics",
            },
        )
        for account in accounts.values():
            self.assertEqual(stored_by_id[account["id"]]["source"], account["source"])
            self.assertEqual(
                self.store.vault.read_source_credential(
                    user_id=self.user_id,
                    source_account_id=account["id"],
                )["payload"],
                stored_by_id[account["id"]]["payload"],
            )

        refreshed_by_id = {
            account["id"]: account
            for account in self.store.list_source_accounts(self.user_id)
        }
        for name, account in accounts.items():
            with self.subTest(account=name):
                refreshed = refreshed_by_id[account["id"]]
                metadata = refreshed["metadata"]
                self.assertEqual(metadata["credential_ref"], f"source_credential:{account['id']}")
                self._assert_secret_keys_absent(account["source"], metadata)
                self._assert_values_absent(refreshed, SECRET_VALUES)

                source_account_json = self._source_account_vault_json(account)
                self._assert_secret_keys_absent(account["source"], source_account_json.get("metadata", {}))
                self._assert_values_absent(source_account_json, SECRET_VALUES)

        for account_name in ("calendar_feed", "calendar_local"):
            metadata = refreshed_by_id[accounts[account_name]["id"]]["metadata"]
            self.assertTrue(metadata["path_redacted"])
            self.assertTrue(metadata["feed_url_redacted"])
            self.assertNotIn("ics_path", metadata)
            self.assertNotIn("feed_url", metadata)

    def test_due_accounts_enqueue_without_secrets(self) -> None:
        accounts = {
            name: self._mark_account_due(account)
            for name, account in self._connect_accounts().items()
        }

        readiness = self.store.source_readiness_report(self.user_id)
        readiness_by_source = {
            item["source"]: item
            for item in readiness["sources"]
            if item["source"] in {"slack", "jira", "calendar"}
        }
        for source in ("slack", "jira", "calendar"):
            with self.subTest(source=source):
                sync_plan = readiness_by_source[source]["sync_plan"]
                self.assertEqual(sync_plan["managed_sync_status"], "due")
                self.assertTrue(sync_plan["due_now"])
                self.assertTrue(sync_plan["scheduler_supported"])
                self.assertIsNone(sync_plan["blocked_reason"])

        scheduled = self.store.enqueue_due_source_syncs(self.user_id, limit=10)

        self.assertEqual(scheduled["scheduled"], len(accounts))
        self.assertEqual(
            {job["object_id"] for job in scheduled["jobs"]},
            {account["id"] for account in accounts.values()},
        )
        for job in scheduled["jobs"]:
            with self.subTest(job=job["object_id"]):
                account = accounts[self._account_name_for_id(accounts, job["object_id"])]
                self.assertEqual(job["job_type"], "source_account_sync")
                self.assertEqual(
                    job["payload"],
                    {
                        "source_account_id": account["id"],
                        "source": account["source"],
                        "processing": "async",
                        "cursor_name": EXPECTED_CURSOR_NAMES[account["source"]],
                        "max_records": 200,
                    },
                )
                self._assert_values_absent(job["payload"], SECRET_VALUES)

    def test_due_jobs_dispatch_with_stored_connector_credentials(self) -> None:
        accounts = {
            name: self._mark_account_due(account)
            for name, account in self._connect_accounts().items()
        }
        self.store.upsert_sync_cursor(
            self.user_id,
            source="slack",
            source_account_id=accounts["slack"]["id"],
            cursor_name="messages",
            cursor_value="2026-06-30T10:00:00Z",
            high_water_mark="2026-06-30T10:00:00Z",
            state={"next_cursors": {"C123ABC": "cursor-next-page"}},
        )
        calls: dict[str, list[dict[str, Any]]] = {
            "slack": [],
            "jira": [],
            "calendar": [],
        }

        def fake_sync_slack_account(store: CortexStore, user_id: str, **kwargs: Any) -> dict[str, Any]:
            calls["slack"].append({"user_id": user_id, **kwargs})
            return self._fake_sync_result("slack", kwargs)

        def fake_sync_jira_account(store: CortexStore, user_id: str, **kwargs: Any) -> dict[str, Any]:
            calls["jira"].append({"user_id": user_id, **kwargs})
            return self._fake_sync_result("jira", kwargs)

        def fake_sync_calendar_account(store: CortexStore, user_id: str, **kwargs: Any) -> dict[str, Any]:
            calls["calendar"].append({"user_id": user_id, **kwargs})
            return self._fake_sync_result("calendar", kwargs)

        self.store.sync_slack_account = MethodType(fake_sync_slack_account, self.store)
        self.store.sync_jira_account = MethodType(fake_sync_jira_account, self.store)
        self.store.sync_calendar_account = MethodType(fake_sync_calendar_account, self.store)

        ran = self.store.run_due_jobs(self.user_id, limit=10)

        self.assertEqual(ran["processed"], len(accounts))
        self.assertEqual(ran["scheduled_source_syncs"]["scheduled"], len(accounts))
        self.assertEqual(len(calls["slack"]), 1)
        self.assertEqual(len(calls["jira"]), 1)
        self.assertEqual(len(calls["calendar"]), 2)

        slack_call = calls["slack"][0]
        self.assertEqual(slack_call["user_id"], self.user_id)
        self.assertEqual(slack_call["source_account_id"], accounts["slack"]["id"])
        self.assertEqual(slack_call["token"], SLACK_TOKEN)
        self.assertEqual(slack_call["channels"], [SLACK_CHANNEL])
        self.assertEqual(slack_call["workspace_url"], SLACK_WORKSPACE_URL)
        self.assertEqual(slack_call["api_base_url"], SLACK_API_BASE_URL)
        self.assertEqual(slack_call["processing"], "async")
        self.assertEqual(slack_call["cursor_name"], "messages")
        self.assertEqual(slack_call["max_records"], 200)
        self.assertEqual(slack_call["page_cursors"], {"C123ABC": "cursor-next-page"})

        jira_call = calls["jira"][0]
        self.assertEqual(jira_call["user_id"], self.user_id)
        self.assertEqual(jira_call["source_account_id"], accounts["jira"]["id"])
        self.assertEqual(jira_call["email"], JIRA_EMAIL)
        self.assertEqual(jira_call["api_token"], JIRA_API_TOKEN)
        self.assertEqual(jira_call["site_url"], JIRA_SITE_URL)
        self.assertEqual(jira_call["jql"], JIRA_JQL)
        self.assertEqual(jira_call["processing"], "async")
        self.assertEqual(jira_call["cursor_name"], "issues")
        self.assertEqual(jira_call["max_records"], 200)

        calendar_calls_by_id = {
            call["source_account_id"]: call
            for call in calls["calendar"]
        }
        feed_call = calendar_calls_by_id[accounts["calendar_feed"]["id"]]
        self.assertIsNone(feed_call["ics_path"])
        self.assertEqual(feed_call["feed_url"], CALENDAR_FEED_URL)
        self.assertEqual(feed_call["processing"], "async")
        self.assertEqual(feed_call["cursor_name"], "events")
        self.assertEqual(feed_call["max_records"], 200)

        local_call = calendar_calls_by_id[accounts["calendar_local"]["id"]]
        self.assertEqual(local_call["ics_path"], CALENDAR_LOCAL_PATH)
        self.assertIsNone(local_call["feed_url"])
        self.assertEqual(local_call["processing"], "async")
        self.assertEqual(local_call["cursor_name"], "events")
        self.assertEqual(local_call["max_records"], 200)

        for job in ran["jobs"]:
            with self.subTest(job=job["object_id"]):
                self.assertEqual(job["job_type"], "source_account_sync")
                self.assertEqual(job["status"], "succeeded")
                self.assertEqual(job["result"]["sync_status"], "complete")
                self._assert_values_absent(job["payload"], SECRET_VALUES)
                self._assert_values_absent(job["result"], SECRET_VALUES)

    def test_disconnect_preserves_local_credentials_and_delete_user_data_removes_them(self) -> None:
        accounts = self._connect_accounts()

        for name, account in accounts.items():
            with self.subTest(disconnect=name):
                self.assertIsNotNone(
                    self.store.vault.read_source_credential(
                        user_id=self.user_id,
                        source_account_id=account["id"],
                    )
                )
                disconnected = self.store.disconnect_source_account(self.user_id, account["id"])
                self.assertIsNotNone(disconnected)
                self.assertEqual(disconnected["status"], "disconnected")
                self.assertIsNotNone(
                    self.store.vault.read_source_credential(
                        user_id=self.user_id,
                        source_account_id=account["id"],
                    )
                )

        deleted = self.store.delete_user_data(self.user_id, include_backups=False)

        self.assertEqual(deleted["vault"]["credentials"], len(accounts))
        for account in accounts.values():
            self.assertIsNone(
                self.store.vault.read_source_credential(
                    user_id=self.user_id,
                    source_account_id=account["id"],
                )
            )
        if self.store.vault.credentials_path.exists():
            self._assert_values_absent(
                json.loads(self.store.vault.credentials_path.read_text(encoding="utf-8")),
                SECRET_VALUES,
            )

    def _connect_accounts(self) -> dict[str, dict[str, Any]]:
        return {
            "slack": self._connect_slack_account(),
            "jira": self._connect_jira_account(),
            "calendar_feed": self._connect_calendar_feed_account(),
            "calendar_local": self._connect_calendar_local_account(),
        }

    def _connect_slack_account(self) -> dict[str, Any]:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
            calls.append((url, headers))
            self.assertTrue(url.startswith(f"{SLACK_API_BASE_URL}/conversations.history?"))
            self.assertIn("channel=C123ABC", url)
            self.assertEqual(headers["Authorization"], f"Bearer {SLACK_TOKEN}")
            return {"ok": True, "messages": []}

        result = self.store.sync_slack_account(
            self.user_id,
            token=SLACK_TOKEN,
            channels=[SLACK_CHANNEL],
            account_label="Slack Scheduled Contract",
            account_identifier="slack-scheduled-contract",
            workspace_url=SLACK_WORKSPACE_URL,
            api_base_url=SLACK_API_BASE_URL,
            processing="sync",
            max_records=10,
            request_json=fake_request_json,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_jira_account(self) -> dict[str, Any]:
        calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        expected_auth = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode("utf-8")).decode("ascii")

        def fake_request_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
            calls.append((url, headers, body))
            self.assertEqual(url, f"{JIRA_SITE_URL}/rest/api/3/search/jql")
            self.assertEqual(headers["Authorization"], f"Basic {expected_auth}")
            self.assertEqual(body["jql"], JIRA_JQL)
            self.assertEqual(body["maxResults"], 10)
            self.assertNotIn("nextPageToken", body)
            return {"isLast": True, "issues": []}

        result = self.store.sync_jira_account(
            self.user_id,
            email=JIRA_EMAIL,
            api_token=JIRA_API_TOKEN,
            site_url=JIRA_SITE_URL,
            jql=JIRA_JQL,
            account_label="Jira Scheduled Contract",
            account_identifier="jira-scheduled-contract",
            processing="sync",
            max_records=10,
            request_json=fake_request_json,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_calendar_feed_account(self) -> dict[str, Any]:
        calls: list[str] = []

        def fake_request_text(url: str) -> str:
            calls.append(url)
            self.assertEqual(url, NORMALIZED_CALENDAR_FEED_URL)
            return EMPTY_ICS

        result = self.store.sync_calendar_account(
            self.user_id,
            feed_url=CALENDAR_FEED_URL,
            account_label="Calendar Feed Scheduled Contract",
            account_identifier="calendar-feed-contract",
            processing="sync",
            max_records=10,
            request_text=fake_request_text,
        )

        self.assertEqual(calls, [NORMALIZED_CALENDAR_FEED_URL])
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_calendar_local_account(self) -> dict[str, Any]:
        calls: list[str] = []

        def fake_read_text(path: Path) -> str:
            calls.append(str(path))
            self.assertEqual(str(path), CALENDAR_LOCAL_PATH)
            return EMPTY_ICS

        result = self.store.sync_calendar_account(
            self.user_id,
            ics_path=CALENDAR_LOCAL_PATH,
            account_label="Calendar Local Scheduled Contract",
            account_identifier="calendar-local-contract",
            processing="sync",
            max_records=10,
            read_text=fake_read_text,
        )

        self.assertEqual(calls, [CALENDAR_LOCAL_PATH])
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _mark_account_due(self, account: dict[str, Any]) -> dict[str, Any]:
        return self.store.upsert_source_account(
            self.user_id,
            source=account["source"],
            account_label=account["account_label"],
            account_identifier=account["account_identifier"],
            connection_type=account["connection_type"],
            status="connected",
            auth_state="healthy",
            policy=account["policy"],
            metadata={
                **account["metadata"],
                "sync_interval_seconds": 60,
                "next_sync_due_at": "2000-01-01T00:00:00Z",
            },
            account_id=account["id"],
        )

    def _fake_sync_result(self, source: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_account_id": kwargs["source_account_id"],
            "source": source,
            "status": "complete",
            "processing": kwargs["processing"],
            "received": 0,
            "queued": 0,
            "saved": 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": [],
            "cursor": {"cursor_name": kwargs["cursor_name"]},
        }

    def _source_account_vault_json(self, account: dict[str, Any]) -> dict[str, Any]:
        path = self.vault_path / "source_accounts" / self.user_id / account["source"] / f"{account['id']}.json"
        self.assertTrue(path.exists(), str(path))
        return json.loads(path.read_text(encoding="utf-8"))

    def _assert_secret_keys_absent(self, source: str, payload: dict[str, Any]) -> None:
        if source == "slack":
            self.assertNotIn("token", payload)
        elif source == "jira":
            self.assertNotIn("email", payload)
            self.assertNotIn("api_token", payload)
        elif source == "calendar":
            self.assertNotIn("ics_path", payload)
            self.assertNotIn("feed_url", payload)

    def _assert_values_absent(self, payload: Any, values: tuple[str, ...]) -> None:
        serialized = json.dumps(payload, sort_keys=True, default=str)
        for value in values:
            self.assertNotIn(value, serialized)

    def _account_name_for_id(self, accounts: dict[str, dict[str, Any]], account_id: str) -> str:
        for name, account in accounts.items():
            if account["id"] == account_id:
                return name
        raise AssertionError(f"unknown account id {account_id}")


if __name__ == "__main__":
    unittest.main()
