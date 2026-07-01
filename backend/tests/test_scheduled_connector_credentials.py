from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import MethodType
from typing import Any

from backend.app.database import init_db
from backend.app.mcp_tools import call_tool
from backend.app.storage import CortexStore


GITHUB_TOKEN = "ghp_local_only_contract_token_123"
GITHUB_REPOSITORY = "doppl-tech/private-scheduled-credential-contract"
GITHUB_API_BASE_URL = "https://api.github.invalid/private-config"
SLACK_TOKEN = "xoxb_local_only_contract_token_123"
CALENDAR_FEED_URL = "webcal://calendar.example.com/private.ics?token=calendar-local-only-secret"
JIRA_EMAIL = "sarp@example.com"
JIRA_API_TOKEN = "jira_local_only_contract_token_123"
JIRA_SITE_URL = "https://doppl.atlassian.net"
JIRA_JQL = "project = COR ORDER BY updated DESC"
SECRET_VALUES = (GITHUB_TOKEN, SLACK_TOKEN, "calendar-local-only-secret", JIRA_EMAIL, JIRA_API_TOKEN)
SECRET_KEYS = ("token", "api_token", "email")


class ScheduledConnectorCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "cortex-test.db"
        self.vault_path = self.root / "vault"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=self.vault_path)
        self.user_id = "scheduled-credential-user"
        self.maxDiff = None

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_connector_sync_stores_automation_credentials_in_local_credentials_file(self) -> None:
        account = self._connect_github_account()

        credentials = json.loads(self.store.vault.credentials_path.read_text(encoding="utf-8"))
        stored = credentials["users"][self.user_id][account["id"]]

        self.assertEqual(stored["source"], "github")
        self.assertEqual(
            stored["payload"],
            {
                "token": GITHUB_TOKEN,
                "repositories": [GITHUB_REPOSITORY],
                "include_comments": False,
                "max_comments_per_item": 0,
                "api_base_url": GITHUB_API_BASE_URL,
            },
        )
        self.assertEqual(account["metadata"]["credential_ref"], f"source_credential:{account['id']}")
        self.assertEqual(
            self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"])["payload"],
            stored["payload"],
        )

    def test_source_account_state_and_vault_records_do_not_store_connector_tokens(self) -> None:
        account = self._connect_github_account()
        refreshed = self.store.list_source_accounts(self.user_id)[0]

        with self.subTest("sqlite source account metadata"):
            self.assertEqual(refreshed["metadata"]["credential_ref"], f"source_credential:{account['id']}")
            for key in SECRET_KEYS:
                self.assertNotIn(key, refreshed["metadata"])
            self._assert_values_absent(refreshed, SECRET_VALUES)

        with self.subTest("source account vault json"):
            source_account_json = self._source_account_vault_json(account["id"])
            for key in SECRET_KEYS:
                self.assertNotIn(key, source_account_json.get("metadata", {}))
            self._assert_values_absent(source_account_json, SECRET_VALUES)

    def test_due_credential_ref_connector_is_scheduler_supported_and_enqueues_without_credentials(self) -> None:
        account = self._mark_account_due(self._connect_github_account())

        readiness = self.store.source_readiness_report(self.user_id)
        github = next(item for item in readiness["sources"] if item["source"] == "github")
        self.assertEqual(github["sync_plan"]["managed_sync_status"], "due")
        self.assertTrue(github["sync_plan"]["due_now"])
        self.assertTrue(github["sync_plan"]["scheduler_supported"])
        self.assertIsNone(github["sync_plan"]["blocked_reason"])

        scheduled = self.store.enqueue_due_source_syncs(self.user_id, limit=5)
        self.assertEqual(scheduled["scheduled"], 1)
        self.assertEqual(scheduled["jobs"][0]["object_id"], account["id"])
        self.assertEqual(scheduled["jobs"][0]["job_type"], "source_account_sync")
        for key in SECRET_KEYS:
            self.assertNotIn(key, scheduled["jobs"][0]["payload"])
        self._assert_values_absent(scheduled["jobs"][0]["payload"], SECRET_VALUES)

    def test_due_credential_ref_connector_dispatches_with_local_credential_payload(self) -> None:
        account = self._mark_account_due(self._connect_github_account())
        calls: list[dict[str, Any]] = []

        def fake_sync_github_account(store: CortexStore, user_id: str, **kwargs: Any) -> dict[str, Any]:
            calls.append({"user_id": user_id, **kwargs})
            return self._sync_result("github", kwargs)

        self.store.sync_github_account = MethodType(fake_sync_github_account, self.store)

        ran = self.store.run_due_jobs(self.user_id, limit=1)

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["scheduled_source_syncs"]["scheduled"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["user_id"], self.user_id)
        self.assertEqual(calls[0]["source_account_id"], account["id"])
        self.assertEqual(calls[0]["token"], GITHUB_TOKEN)
        self.assertEqual(calls[0]["repositories"], [GITHUB_REPOSITORY])
        self.assertEqual(calls[0]["api_base_url"], GITHUB_API_BASE_URL)
        self.assertEqual(calls[0]["processing"], "async")
        self.assertEqual(calls[0]["cursor_name"], "issues")
        self.assertEqual(calls[0]["max_records"], 200)
        self.assertFalse(calls[0]["include_comments"])
        self.assertEqual(calls[0]["max_comments_per_item"], 0)

        job = ran["jobs"][0]
        self.assertEqual(job["job_type"], "source_account_sync")
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["sync_status"], "complete")
        self._assert_values_absent(job["payload"], SECRET_VALUES)
        self._assert_values_absent(job["result"], SECRET_VALUES)

    def test_mcp_sync_connected_sources_runs_due_local_credentials_without_exposing_them(self) -> None:
        account = self._mark_account_due(self._connect_github_account())
        calls: list[dict[str, Any]] = []

        def fake_sync_github_account(store: CortexStore, user_id: str, **kwargs: Any) -> dict[str, Any]:
            calls.append({"user_id": user_id, **kwargs})
            return self._sync_result("github", kwargs)

        self.store.sync_github_account = MethodType(fake_sync_github_account, self.store)
        queued_capture = self.store.enqueue_capture(
            user_id=self.user_id,
            content="This ordinary queued capture should wait for the general worker.",
            source="unit-test",
            source_url=None,
            title="General queued capture",
        )

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_connected_sources",
                {"limit": 5},
                token_scopes=["write"],
            )

        self.store.update_settings(self.user_id, {"allow_agent_maintenance": True})
        ran = call_tool(
            self.store,
            self.user_id,
            "sync_connected_sources",
            {"limit": 5},
            token_scopes=["maintenance"],
        )

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["scheduled_source_syncs"]["scheduled"], 1)
        self.assertEqual(ran["scheduled_source_syncs"]["jobs"][0]["object_id"], account["id"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["token"], GITHUB_TOKEN)
        self.assertEqual(calls[0]["repositories"], [GITHUB_REPOSITORY])
        self.assertEqual(ran["jobs"][0]["job_type"], "source_account_sync")
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")
        self._assert_values_absent(ran, SECRET_VALUES)

        extract_jobs = self.store.list_jobs(self.user_id, status="queued", job_type="extract_capture", limit=10)
        self.assertEqual([job["object_id"] for job in extract_jobs], [queued_capture["capture_id"]])

    def test_slack_dispatches_with_local_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "slack",
            self._connect_slack_account,
            "sync_slack_account",
            {
                "token": SLACK_TOKEN,
                "channels": ["C123ABC|general"],
                "workspace_url": "https://doppl.slack.com",
                "api_base_url": "https://slack.invalid/api",
                "cursor_name": "messages",
                "max_records": 200,
            },
        )

    def test_calendar_dispatches_with_local_feed_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "calendar",
            self._connect_calendar_account,
            "sync_calendar_account",
            {
                "feed_url": CALENDAR_FEED_URL,
                "ics_path": None,
                "cursor_name": "events",
                "max_records": 200,
            },
        )

    def test_jira_dispatches_with_local_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "jira",
            self._connect_jira_account,
            "sync_jira_account",
            {
                "email": JIRA_EMAIL,
                "api_token": JIRA_API_TOKEN,
                "site_url": JIRA_SITE_URL,
                "jql": JIRA_JQL,
                "cursor_name": "issues",
                "max_records": 200,
            },
        )

    def test_backup_zip_excludes_credentials_file_and_connector_tokens(self) -> None:
        self._mark_account_due(self._connect_github_account())
        self.store.enqueue_due_source_syncs(self.user_id, limit=5)

        backup = self.store.create_backup(self.user_id)
        leaks: list[str] = []
        with zipfile.ZipFile(backup["backup_path"]) as archive:
            names = archive.namelist()
            self.assertNotIn("credentials.json", names)
            for name in names:
                data = archive.read(name)
                for value in SECRET_VALUES:
                    if value.encode("utf-8") in data:
                        leaks.append(f"{name}: {value}")

        self.assertEqual(leaks, [])

    def test_disconnect_source_account_preserves_local_connector_token_for_reconnect(self) -> None:
        account = self._connect_github_account()
        self.assertIsNotNone(self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"]))

        disconnected = self.store.disconnect_source_account(self.user_id, account["id"])

        self.assertIsNotNone(disconnected)
        self.assertEqual(disconnected["status"], "disconnected")
        self.assertIsNotNone(self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"]))

    def test_delete_user_data_reports_and_removes_local_connector_tokens(self) -> None:
        account = self._connect_github_account()
        self.assertIsNotNone(self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"]))

        deleted = self.store.delete_user_data(self.user_id, include_backups=False)

        self.assertEqual(deleted["vault"]["credentials"], 1)
        self.assertIsNone(self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"]))
        if self.store.vault.credentials_path.exists():
            self._assert_values_absent(json.loads(self.store.vault.credentials_path.read_text(encoding="utf-8")), SECRET_VALUES)

    def _connect_github_account(self) -> dict[str, Any]:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_request_json(url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
            calls.append((url, headers))
            self.assertTrue(url.startswith(f"{GITHUB_API_BASE_URL}/repos/{GITHUB_REPOSITORY}/issues?"))
            self.assertEqual(headers["Authorization"], f"Bearer {GITHUB_TOKEN}")
            return []

        result = self.store.sync_github_account(
            self.user_id,
            token=GITHUB_TOKEN,
            repositories=[GITHUB_REPOSITORY],
            account_label="GitHub Scheduled Contract",
            account_identifier="github-scheduled-contract",
            processing="sync",
            max_records=10,
            include_comments=False,
            max_comments_per_item=0,
            api_base_url=GITHUB_API_BASE_URL,
            request_json=fake_request_json,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_slack_account(self) -> dict[str, Any]:
        def fake_request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
            self.assertIn("/conversations.history", url)
            self.assertEqual(headers["Authorization"], f"Bearer {SLACK_TOKEN}")
            return {"ok": True, "messages": []}

        result = self.store.sync_slack_account(
            self.user_id,
            token=SLACK_TOKEN,
            channels=["C123ABC|general"],
            account_label="Slack Scheduled Contract",
            account_identifier="slack-scheduled-contract",
            processing="sync",
            max_records=10,
            workspace_url="https://doppl.slack.com",
            api_base_url="https://slack.invalid/api",
            request_json=fake_request_json,
        )
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_calendar_account(self) -> dict[str, Any]:
        def fake_request_text(url: str) -> str:
            self.assertEqual(url, "https://calendar.example.com/private.ics?token=calendar-local-only-secret")
            return "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"

        result = self.store.sync_calendar_account(
            self.user_id,
            feed_url=CALENDAR_FEED_URL,
            account_label="Calendar Scheduled Contract",
            account_identifier="calendar-scheduled-contract",
            processing="sync",
            max_records=10,
            request_text=fake_request_text,
        )
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_jira_account(self) -> dict[str, Any]:
        def fake_request_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(url, f"{JIRA_SITE_URL}/rest/api/3/search/jql")
            self.assertEqual(body["jql"], JIRA_JQL)
            self.assertIn("Authorization", headers)
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
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _sync_result(self, source: str, kwargs: dict[str, Any]) -> dict[str, Any]:
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

    def _assert_credential_backed_dispatch(
        self,
        source: str,
        connect_account: Any,
        method_name: str,
        expected: dict[str, Any],
    ) -> None:
        account = self._mark_account_due(connect_account())
        credential = self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"])
        self.assertIsNotNone(credential)
        self.assertEqual(account["metadata"]["credential_ref"], f"source_credential:{account['id']}")
        self._assert_values_absent(account["metadata"], SECRET_VALUES)
        calls: list[dict[str, Any]] = []

        def fake_sync(store: CortexStore, user_id: str, **kwargs: Any) -> dict[str, Any]:
            calls.append({"user_id": user_id, **kwargs})
            return self._sync_result(source, kwargs)

        setattr(self.store, method_name, MethodType(fake_sync, self.store))

        ran = self.store.run_due_jobs(self.user_id, limit=1)

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["source_account_id"], account["id"])
        self.assertEqual(calls[0]["processing"], "async")
        for key, value in expected.items():
            self.assertEqual(calls[0].get(key), value)
        self._assert_values_absent(ran["jobs"][0]["payload"], SECRET_VALUES)
        self._assert_values_absent(ran["jobs"][0]["result"], SECRET_VALUES)

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

    def _source_account_vault_json(self, account_id: str) -> dict[str, Any]:
        matches = list((self.vault_path / "source_accounts" / self.user_id / "github").glob(f"{account_id}.json"))
        self.assertEqual(len(matches), 1)
        return json.loads(matches[0].read_text(encoding="utf-8"))

    def _assert_values_absent(self, payload: Any, values: tuple[str, ...]) -> None:
        serialized = json.dumps(payload, sort_keys=True, default=str)
        for value in values:
            self.assertNotIn(value, serialized)


if __name__ == "__main__":
    unittest.main()
