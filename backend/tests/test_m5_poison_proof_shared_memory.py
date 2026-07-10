from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.app import mcp_tools
from backend.app.database import init_db
from backend.app.provenance import sign_shared_write
from backend.app.storage import CortexStore, connect


class PoisonProofSharedMemoryTests(unittest.TestCase):
    """M5 attack-first contract for multi-writer shared memory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "m5-user"
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": False,
                "allow_agent_maintenance": True,
            },
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_user_fact(
        self,
        memory_id: str,
        content: str,
        *,
        occurred_at: str = "2026-01-01T00:00:00Z",
    ) -> dict:
        result = self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="note",
            source_url=f"cortex-capture://{memory_id}",
            title=None,
            extracted={
                "_timestamp": occurred_at,
                "summary": content,
                "records": [
                    {
                        "id": memory_id,
                        "kind": "decision",
                        "layer": "decision",
                        "content": content,
                        "summary": content,
                        "confidence": "confirmed",
                        "importance": 5,
                        "occurred_at": occurred_at,
                        "topics": ["database"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        return result["memories"][0]

    def _principal(
        self,
        label: str = "Research agent",
        *,
        kind: str = "agent",
        trust_score: float = 0.5,
    ) -> dict:
        return self.store.create_shared_principal(
            self.user_id,
            label=label,
            kind=kind,
            trust_score=trust_score,
        )

    def _write(
        self,
        principal: dict,
        *,
        nonce: str,
        content: str,
        source_url: str = "agent-report://fixture",
        title: str = "Shared report",
        supersedes_memory_id: str = "",
        signature: str | None = None,
    ) -> dict:
        principal_id = principal["principal"]["id"]
        resolved_signature = signature or sign_shared_write(
            principal["secret"],
            principal_id=principal_id,
            nonce=nonce,
            content=content,
            source_url=source_url,
            title=title,
            supersedes_memory_id=supersedes_memory_id,
        )
        return self.store.record_shared_memory(
            self.user_id,
            principal_id=principal_id,
            nonce=nonce,
            content=content,
            signature=resolved_signature,
            source_url=source_url,
            title=title,
            supersedes_memory_id=supersedes_memory_id,
        )

    def _attempts(self) -> dict:
        return self.store.get_poisoning_attempts(self.user_id, limit=200)

    def test_low_trust_signed_novel_write_is_review_gated_not_falsely_flagged(self) -> None:
        principal = self._principal()

        result = self._write(
            principal,
            nonce="novel-1",
            content="The research agent observed a new benchmark candidate named quartz-lantern.",
        )

        self.assertEqual(result["disposition"], "review_required")
        self.assertTrue(result["memories"])
        self.assertTrue(all(m["author_class"] == "agent" for m in result["memories"]))
        self.assertTrue(
            all(m["author_principal_id"] == principal["principal"]["id"] for m in result["memories"])
        )
        self.assertEqual(self._attempts()["count"], 0)
        ids = {item["id"] for item in self.store.search(self.user_id, "quartz lantern", limit=10)}
        self.assertFalse(ids, "review-gated shared writes must not leak into normal retrieval")

    def test_false_low_trust_conflict_is_quarantined_and_trusted_fact_wins(self) -> None:
        trusted = self._seed_user_fact("mem_user_db", "The database is postgres for the launch.")
        principal = self._principal(trust_score=0.4)

        result = self._write(
            principal,
            nonce="poison-db-1",
            content="The database is mysql for the launch.",
            supersedes_memory_id=trusted["id"],
        )

        self.assertEqual(result["disposition"], "quarantined")
        self.assertTrue(result["conflicts"])
        self.assertTrue(all(m["status"] == "quarantined" for m in result["memories"]))
        attempts = self._attempts()
        self.assertEqual(attempts["count"], 1)
        self.assertEqual(attempts["attempts"][0]["reason"], "lower_authority_supersession")
        answer = self.store.answer_query(self.user_id, "What is the database for the launch?")
        cited_text = " ".join(item.get("content", "") for item in answer.get("results") or [])
        self.assertIn("postgres", cited_text.lower())
        self.assertNotIn("mysql", cited_text.lower())

    def test_invalid_signature_is_rejected_and_audited(self) -> None:
        principal = self._principal()
        with self.assertRaises(ValueError):
            self._write(
                principal,
                nonce="bad-signature-1",
                content="The database is mysql for the launch.",
                signature="0" * 64,
            )

        attempts = self._attempts()
        self.assertEqual(attempts["count"], 1)
        self.assertEqual(attempts["attempts"][0]["reason"], "invalid_signature")

    def test_nonce_replay_is_rejected_and_audited(self) -> None:
        principal = self._principal()
        kwargs = {
            "principal": principal,
            "nonce": "replay-1",
            "content": "A signed shared observation names the rollout token indigo-harbor.",
        }
        self._write(**kwargs)
        with self.assertRaises(ValueError):
            self._write(**kwargs)

        attempts = self._attempts()
        self.assertEqual(attempts["count"], 1)
        self.assertEqual(attempts["attempts"][0]["reason"], "replay")

    def test_same_principal_signed_revision_is_allowed_and_keeps_timeline(self) -> None:
        principal = self._principal(trust_score=0.9)
        first = self._write(
            principal,
            nonce="revision-1",
            content="The database is sqlite for the launch.",
        )
        first_id = first["memories"][0]["id"]
        second = self._write(
            principal,
            nonce="revision-2",
            content="The database is postgres for the launch.",
            supersedes_memory_id=first_id,
        )

        self.assertEqual(first["disposition"], "accepted")
        self.assertEqual(second["disposition"], "accepted")
        second_id = second["memories"][0]["id"]
        with connect(self.db_path) as conn:
            stale = conn.execute(
                "SELECT superseded_by, trust_score FROM memories WHERE user_id = ? AND id = ?",
                (self.user_id, first_id),
            ).fetchone()
        self.assertEqual(stale["superseded_by"], second_id)
        self.assertEqual(stale["trust_score"], 0.45)
        timeline = self.store.get_belief_timeline(self.user_id, "database launch")
        payload = str(timeline).lower()
        self.assertIn("sqlite", payload)
        self.assertIn("postgres", payload)
        self.assertEqual(self._attempts()["count"], 0)

    def test_authorship_principal_flip_breaks_shared_verification(self) -> None:
        principal = self._principal(trust_score=0.9)
        result = self._write(
            principal,
            nonce="principal-tamper-1",
            content="The launch owner is Alice for the verified shared plan.",
        )
        memory_id = result["memories"][0]["id"]
        self.assertTrue(self.store.verify_shared_memory(self.user_id)["verified"])

        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memories SET author_principal_id = ?, author_class = 'user' WHERE user_id = ? AND id = ?",
                ("prn_forged", self.user_id, memory_id),
            )

        verification = self.store.verify_shared_memory(self.user_id)
        self.assertFalse(verification["verified"])
        self.assertTrue(any("authorship" in error for error in verification["errors"]))

    def test_per_principal_chain_detects_silent_write_tampering(self) -> None:
        principal = self._principal(trust_score=0.9)
        self._write(
            principal,
            nonce="chain-tamper-1",
            content="The deployment region is us-east-1 for the signed shared plan.",
        )
        self.assertTrue(self.store.verify_shared_memory(self.user_id)["verified"])

        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE shared_memory_writes SET payload_sha256 = ? WHERE user_id = ? AND principal_id = ?",
                ("0" * 64, self.user_id, principal["principal"]["id"]),
            )

        verification = self.store.verify_shared_memory(self.user_id)
        self.assertFalse(verification["verified"])
        self.assertTrue(any("chain" in error for error in verification["errors"]))

    def test_per_principal_chain_detects_deleted_tail_row(self) -> None:
        principal = self._principal(trust_score=0.9)
        self._write(
            principal,
            nonce="chain-delete-1",
            content="The deployment lane is blue for the signed shared plan.",
        )
        with connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM shared_memory_writes WHERE user_id = ? AND principal_id = ?",
                (self.user_id, principal["principal"]["id"]),
            )

        verification = self.store.verify_shared_memory(self.user_id)
        self.assertFalse(verification["verified"])
        self.assertTrue(any("no shared-write row" in error for error in verification["errors"]))

    def test_concurrent_writes_extend_one_serial_principal_chain(self) -> None:
        principal = self._principal(trust_score=0.9)

        def write(index: int) -> dict:
            return self._write(
                principal,
                nonce=f"concurrent-{index}",
                content=f"The concurrent signed observation token is chain-{index}-quartz.",
                source_url=f"agent-report://concurrent/{index}",
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(write, range(8)))

        self.assertTrue(all(result["disposition"] == "accepted" for result in results))
        verification = self.store.verify_shared_memory(
            self.user_id,
            principal_id=principal["principal"]["id"],
        )
        self.assertTrue(verification["verified"], verification["errors"])
        self.assertEqual(verification["write_count"], 8)

    def test_write_principal_reassignment_cannot_hide_from_verification(self) -> None:
        principal = self._principal(trust_score=0.9)
        self._write(
            principal,
            nonce="chain-principal-flip-1",
            content="The deployment lane is green for the signed shared plan.",
        )
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE shared_memory_writes SET principal_id = ? WHERE user_id = ?",
                ("prn_forged", self.user_id),
            )

        verification = self.store.verify_shared_memory(self.user_id)
        self.assertFalse(verification["verified"])
        self.assertTrue(any("unknown principal" in error for error in verification["errors"]))

    def test_trust_rescore_preserves_configured_principal_trust(self) -> None:
        principal = self._principal(trust_score=0.9)
        result = self._write(
            principal,
            nonce="rescore-1",
            content="The rollout owner is Dana for the signed shared plan.",
        )
        memory_id = result["memories"][0]["id"]

        self.store.rescore_trust(self.user_id)

        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT trust_score FROM memories WHERE user_id = ? AND id = ?",
                (self.user_id, memory_id),
            ).fetchone()
        self.assertEqual(row["trust_score"], 0.9)

    def test_revoked_principal_cannot_write(self) -> None:
        principal = self._principal()
        self.assertTrue(
            self.store.revoke_shared_principal(self.user_id, principal["principal"]["id"])["revoked"]
        )
        with self.assertRaises(PermissionError):
            self._write(
                principal,
                nonce="revoked-1",
                content="The revoked principal should not be able to add shared memory.",
            )

    def test_revocation_preserves_verifiable_historical_writes(self) -> None:
        principal = self._principal(trust_score=0.9)
        self._write(
            principal,
            nonce="before-revocation-1",
            content="The principal wrote this signed observation before its key was revoked.",
        )
        self.store.revoke_shared_principal(self.user_id, principal["principal"]["id"])

        verification = self.store.verify_shared_memory(
            self.user_id,
            principal_id=principal["principal"]["id"],
        )
        self.assertTrue(verification["verified"], verification["errors"])
        self.assertEqual(verification["write_count"], 1)

    def test_legitimate_write_false_positive_rate_is_zero(self) -> None:
        principal = self._principal()
        writes = 20
        for index in range(writes):
            result = self._write(
                principal,
                nonce=f"legitimate-{index}",
                content=(
                    f"The research agent observed independent benchmark token legit-{index:02d}-quartz "
                    f"for experiment lane {index}."
                ),
                source_url=f"agent-report://legitimate/{index}",
            )
            self.assertIn(result["disposition"], {"review_required", "accepted"})
        attempts = self._attempts()
        false_positive_rate = attempts["count"] / writes
        self.assertEqual(false_positive_rate, 0.0)
        self.assertLess(false_positive_rate, 0.01)

    def test_delete_user_data_purges_principals_nonces_and_write_chains(self) -> None:
        principal = self._principal(trust_score=0.9)
        self._write(
            principal,
            nonce="delete-me-1",
            content="The temporary shared plan should disappear with user data deletion.",
        )

        deleted = self.store.delete_user_data(self.user_id, include_backups=True)

        self.assertEqual(deleted["sqlite"]["shared_memory_principals"], 1)
        self.assertEqual(deleted["sqlite"]["shared_memory_writes"], 1)
        with connect(self.db_path) as conn:
            for table in (
                "shared_memory_principals",
                "shared_memory_nonces",
                "shared_memory_writes",
            ):
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (self.user_id,)
                ).fetchone()[0]
                self.assertEqual(count, 0, table)

    def test_shared_memory_tools_have_strict_scopes(self) -> None:
        names = {tool["name"] for tool in mcp_tools.TOOLS}
        expected = {
            "create_shared_principal",
            "revoke_shared_principal",
            "record_shared_memory",
            "verify_shared_memory",
            "get_poisoning_attempts",
        }
        self.assertTrue(expected.issubset(names))
        self.assertEqual(mcp_tools.tool_required_capabilities("create_shared_principal"), ["maintenance"])
        self.assertEqual(mcp_tools.tool_required_capabilities("revoke_shared_principal"), ["maintenance"])
        self.assertEqual(mcp_tools.tool_required_capabilities("record_shared_memory"), ["write"])
        self.assertEqual(mcp_tools.tool_required_capabilities("verify_shared_memory"), ["read"])
        self.assertEqual(mcp_tools.tool_required_capabilities("get_poisoning_attempts"), ["read"])

        with self.assertRaises(PermissionError):
            mcp_tools.call_tool(
                self.store,
                self.user_id,
                "create_shared_principal",
                {"label": "Sybil", "kind": "agent"},
                token_scopes=["write"],
            )

    def test_mcp_signed_write_and_verification_roundtrip(self) -> None:
        created = mcp_tools.call_tool(
            self.store,
            self.user_id,
            "create_shared_principal",
            {"label": "MCP agent", "kind": "agent", "trust_score": 0.9},
            token_scopes=["maintenance"],
        )
        principal_id = created["principal"]["id"]
        content = "The signed MCP agent says the release train is cobalt-summit."
        signature = sign_shared_write(
            created["secret"],
            principal_id=principal_id,
            nonce="mcp-roundtrip-1",
            content=content,
            source_url="agent-report://mcp",
            title="MCP signed write",
        )
        written = mcp_tools.call_tool(
            self.store,
            self.user_id,
            "record_shared_memory",
            {
                "principal_id": principal_id,
                "nonce": "mcp-roundtrip-1",
                "content": content,
                "signature": signature,
                "source_url": "agent-report://mcp",
                "title": "MCP signed write",
            },
            token_scopes=["write"],
        )
        self.assertEqual(written["disposition"], "accepted")
        verified = mcp_tools.call_tool(
            self.store,
            self.user_id,
            "verify_shared_memory",
            {"principal_id": principal_id},
            token_scopes=["read"],
        )
        self.assertTrue(verified["verified"])


if __name__ == "__main__":
    unittest.main()
