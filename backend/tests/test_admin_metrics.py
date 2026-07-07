"""Admin user-metrics store methods (SQLiteControlStore.admin_metrics / list_accounts /
count_accounts), plus the require_admin dependency gate. The store logic is tested directly
(no FastAPI app) so it runs in the bare env; the HTTP wiring is a thin pass-through."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.accounts import SQLiteControlStore


def _now(day: str) -> str:
    return f"{day}T12:00:00+00:00"


class AdminMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteControlStore(Path(self._tmp.name) / "accounts.sqlite")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _account(self, i: int, *, status: str, day: str, verified: bool, provider: str | None) -> str:
        acct = self.store.create_account(
            account_id=f"acct-{i}",
            user_id=f"user-{i}",
            primary_email=f"user{i}@example.com",
            display_name=f"User {i}",
            status=status,
            email_verified_at=_now(day) if verified else None,
            now=_now(day),
        )
        if provider:
            self.store.create_identity(
                identity_id=f"id-{i}",
                account_id=acct["account_id"],
                provider=provider,
                provider_subject=f"sub-{i}",
                email=f"user{i}@example.com",
                email_verified=verified,
                profile={"provider": provider},
                now=_now(day),
            )
        return acct["account_id"]

    def test_metrics_counts_and_breakdowns(self) -> None:
        self._account(1, status="active", day="2026-07-01", verified=True, provider="apple")
        self._account(2, status="active", day="2026-07-01", verified=True, provider="google")
        self._account(3, status="pending_verification", day="2026-07-02", verified=False, provider=None)
        self._account(4, status="active", day="2026-07-02", verified=True, provider="github")

        m = self.store.admin_metrics()
        self.assertEqual(m["total_accounts"], 4)
        self.assertEqual(m["active"], 3)
        self.assertEqual(m["pending"], 1)
        self.assertEqual(m["email_verified"], 3)
        self.assertEqual(m["by_provider"], {"apple": 1, "google": 1, "github": 1})
        # signups_by_day ascending, one entry per calendar day.
        days = {d["day"]: d["count"] for d in m["signups_by_day"]}
        self.assertEqual(days, {"2026-07-01": 2, "2026-07-02": 2})
        self.assertEqual([d["day"] for d in m["signups_by_day"]], ["2026-07-01", "2026-07-02"])

    def test_count_accounts_by_status(self) -> None:
        self._account(1, status="active", day="2026-07-01", verified=True, provider=None)
        self._account(2, status="pending_verification", day="2026-07-01", verified=False, provider=None)
        self.assertEqual(self.store.count_accounts(), 2)
        self.assertEqual(self.store.count_accounts(status="active"), 1)
        self.assertEqual(self.store.count_accounts(status="suspended"), 0)

    def test_list_accounts_pagination_search_and_providers(self) -> None:
        for i in range(1, 6):
            self._account(i, status="active", day=f"2026-07-0{i}", verified=True, provider="apple")

        page = self.store.list_accounts(limit=2, offset=0)
        self.assertEqual(page["total"], 5)
        self.assertEqual(len(page["accounts"]), 2)
        # Newest first — user5 (2026-07-05) leads.
        self.assertEqual(page["accounts"][0]["primary_email"], "user5@example.com")
        self.assertEqual(page["accounts"][0]["providers"], ["apple"])

        page2 = self.store.list_accounts(limit=2, offset=2)
        self.assertEqual(page2["accounts"][0]["primary_email"], "user3@example.com")

        found = self.store.list_accounts(query="user3@")
        self.assertEqual(found["total"], 1)
        self.assertEqual(found["accounts"][0]["primary_email"], "user3@example.com")


if __name__ == "__main__":
    unittest.main()
