"""MemoryTruth-light: a deterministic, verifiable-by-construction memory benchmark.

This is Moonshot 1 from docs/MEMORY_MOONSHOTS.md kept deliberately *light*:

- No LLM judge. Every probe's gold answer is a unique random slug seeded into the
  store, so grading is exact string/id matching. The answer key is verifiable by
  construction - the failure mode that broke LoCoMo (wrong keys, lenient judges)
  is structurally impossible here.
- Deterministic. One integer seed fully determines the scenario set, so runs are
  reproducible and inter-run variance at a fixed seed is zero.
- Two clients, one scorer. The same scenario drives either an in-process
  CortexStore (unit speed) or a live HTTP server via /v1/tools/call (which makes a
  bench run a start-to-finish proof of the real pipeline: auth -> tool dispatch ->
  storage -> vault -> retrieval -> citation gate -> integrity chain).

Categories (each maps to a moonshot-doc probe class):
- recall:      seeded fact -> question -> answer must cite a memory containing the slug
- abstention:  never-seeded question -> must return no_cited_evidence, not confabulate
- temporal:    fact v1 superseded by v2 -> answer cites v2, never leaks v1; belief
               timeline records the revision with the v2 head
- provenance:  every citation id must resolve to a real memory containing the slug,
               author_class must survive the pipeline as "user", and the integrity
               chain must verify (matches=True) after all writes
- canvas:      M3 working-memory canvas - every verbose raw evidence blob offloaded
               into a node must come back byte-identical on drill-down (the bench
               compares bytes, it does NOT trust the verified flag), the compact
               canvas must never leak raw evidence and must be >=40% smaller than
               the raw it replaced, and every node must carry a receipt event id
- belief_proof: M2 Proof-of-Belief - bi-temporal reconstruction with cryptographic
               receipts. Current state shows v2 (never v1); a retro known_at just
               before v2 was recorded shows v1 (never v2); every proof is checked
               by the BENCH'S OWN verifier (chain-segment fold, event fingerprints,
               snapshot hashes - an independent reimplementation, so a store that
               lies about `verified` is caught); a doctored proof must be rejected;
               a prefix head pinned early must reproduce byte-identically at the
               end of the run (external-pin detection of coherent history rewrites)
- metacognition: M6 calibrated known-unknowns - seeded preferences must produce
                 cited likely_yes predictions above the answerability threshold;
                 never-seeded decisions must produce zero-confidence abstentions.
                 The bench computes ECE, Brier, abstention precision/recall,
                 coverage, and confident-wrong rate from its OWN gold labels, then
                 cross-checks the first-class Cortex calibration scorecard.
- shared_memory: M5 poison-proof multi-writer memory - independently signed
                 principal writes, replay/signature/revocation/authority attacks,
                 per-principal chain verification, trusted-conflict survival, and
                 a measured legitimate-write false-positive rate.

Run: python3 -m backend.bench.memorytruth --seed 7
     python3 -m backend.bench.memorytruth --url http://127.0.0.1:8766 --token <api-key>
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import string
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

BENCH_NAME = "memorytruth-light"
BENCH_VERSION = 6

_METACOGNITION_THRESHOLD = 0.6
_METACOGNITION_BIN_COUNT = 5
_METACOGNITION_ECE_MAX = 0.05
_METACOGNITION_BRIER_MAX = 0.01

_ADJECTIVES = (
    "amber", "basalt", "cedar", "delta", "ember", "flint", "garnet", "harbor",
    "indigo", "juniper", "krypton", "larch", "mesa", "nimbus", "onyx", "pumice",
)
_NOUNS = (
    "anchor", "beacon", "compass", "dynamo", "engine", "falcon", "glacier",
    "horizon", "isotope", "jetty", "kestrel", "lattice", "meridian", "nexus",
    "orbit", "prism",
)
_ARTIFACTS = (
    "launch codename", "build identifier", "release tag", "vault passphrase label",
    "cluster name", "billing code", "runbook id", "gateway alias",
)


def _slug(rng: random.Random) -> str:
    """A distinctive, extraction-surviving token like 'qk7-xj42-mz9'."""
    def chunk(n: int) -> str:
        return "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(n))
    return f"{chunk(3)}-{chunk(4)}-{chunk(3)}"


@dataclass
class Probe:
    category: str
    question: str
    expect_slug: str = ""
    reject_slug: str = ""
    topic: str = ""


@dataclass
class Scenario:
    """A fully deterministic scenario: facts to seed, revisions to apply, probes to score."""
    seed: int
    facts: list[dict[str, str]] = field(default_factory=list)          # {content, slug, question}
    revisions: list[dict[str, str]] = field(default_factory=list)      # {v1_content, v2_content, v1_slug, v2_slug, question, topic}
    abstention_probes: list[Probe] = field(default_factory=list)
    canvas_nodes: list[dict[str, Any]] = field(default_factory=list)   # {node_id, label, summary, raw_text, slug, predecessor_node_id}
    metacognition_cases: list[dict[str, Any]] = field(default_factory=list)  # {content?, question, slug, answerability, expected_verdict}
    shared_memory_cases: list[dict[str, Any]] = field(default_factory=list)
    associative_cases: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "facts": self.facts,
            "revisions": self.revisions,
            "abstention_probes": [vars(p) for p in self.abstention_probes],
            "canvas_nodes": self.canvas_nodes,
            "metacognition_cases": self.metacognition_cases,
            "shared_memory_cases": self.shared_memory_cases,
            "associative_cases": self.associative_cases,
        }


def generate_scenario(
    seed: int,
    *,
    recall_n: int = 8,
    abstain_n: int = 6,
    temporal_n: int = 4,
    canvas_n: int = 5,
    metacognition_n: int = 4,
    shared_memory_n: int = 20,
    associative_n: int = 4,
) -> Scenario:
    rng = random.Random(seed)
    scenario = Scenario(seed=seed)
    # Unique project names per probe so facts never collide into accidental
    # contradictions (the write-path contradiction detector is part of the real
    # pipeline; temporal probes exercise it deliberately, recall probes must not).
    combos = [(a, n) for a in _ADJECTIVES for n in _NOUNS]
    rng.shuffle(combos)
    picks = iter(combos)

    for _ in range(recall_n):
        adj, noun = next(picks)
        project = f"{adj}-{noun}"
        artifact = rng.choice(_ARTIFACTS)
        slug = _slug(rng)
        scenario.facts.append(
            {
                "content": f"The {project} project {artifact} is {slug}.",
                "slug": slug,
                "question": f"What is the {artifact} for the {project} project?",
            }
        )

    for _ in range(abstain_n):
        adj, noun = next(picks)
        project = f"{adj}-{noun}"
        artifact = rng.choice(_ARTIFACTS)
        scenario.abstention_probes.append(
            Probe(
                category="abstention",
                question=f"What is the {artifact} for the {project} project?",
                topic=f"{project} {artifact}",
            )
        )

    for _ in range(temporal_n):
        adj, noun = next(picks)
        team = f"{adj}-{noun}"
        v1, v2 = _slug(rng), _slug(rng)
        topic = f"{team} team meeting room"
        scenario.revisions.append(
            {
                "v1_content": f"The {team} team meeting room is {v1}.",
                "v2_content": f"The {team} team meeting room is {v2}.",
                "v1_slug": v1,
                "v2_slug": v2,
                "question": f"What is the meeting room for the {team} team?",
                "topic": topic,
            }
        )

    # M3 canvas nodes are generated LAST so adding this category never changes
    # the facts/abstention/temporal content of pre-existing seeds (the rng draw
    # order for those sections is untouched).
    for i in range(canvas_n):
        slug = _slug(rng)
        filler = " ".join(rng.choice(_NOUNS) for _ in range(160))
        scenario.canvas_nodes.append(
            {
                "node_id": f"n{i}_step",
                "label": f"Tool step {i}",
                "summary": f"Step {i} evidence recorded",
                # Verbose, unique raw evidence with the slug buried mid-stream -
                # the exact thing an agent would offload instead of carrying.
                "raw_text": f"[tool:{i}] stdout begins\n{filler}\nevidence token {slug}\n{filler}\n",
                "slug": slug,
                "predecessor_node_id": f"n{i - 1}_step" if i > 0 else None,
            }
        )

    # M6 cases are generated LAST so v4 preserves every byte of the v3 scenario
    # for a given seed. Each pair contains one preference Cortex can answer from
    # cited memory and one decision whose unique slug/project were never seeded.
    # After question scaffolding is stripped, no terms overlap across cases.
    for _ in range(metacognition_n):
        known_adj, known_noun = next(picks)
        unknown_adj, unknown_noun = next(picks)
        known_project = f"{known_adj}-{known_noun}"
        unknown_project = f"{unknown_adj}-{unknown_noun}"
        known_slug = _slug(rng)
        unknown_slug = _slug(rng)
        scenario.metacognition_cases.extend(
            [
                {
                    "content": f"I prefer {known_slug} for {known_project}.",
                    "question": f"Would I use {known_slug} for {known_project}?",
                    "slug": known_slug,
                    "project": known_project,
                    "answerability": "answerable",
                    "expected_verdict": "likely_yes",
                },
                {
                    "content": None,
                    "question": f"Would I use {unknown_slug} for {unknown_project}?",
                    "slug": unknown_slug,
                    "project": unknown_project,
                    "answerability": "unknown",
                    "expected_verdict": "insufficient_evidence",
                },
            ]
        )

    # M5 uses a separate RNG so adding shared-memory probes cannot perturb any
    # byte of the v4 scenario for an existing seed.
    shared_rng = random.Random(f"{seed}:memorytruth-m5")
    if shared_memory_n > 0:
        scenario.shared_memory_cases.append(
            {
                "accepted_slug": _slug(shared_rng),
                "trusted_slug": _slug(shared_rng),
                "poison_slug": _slug(shared_rng),
                "invalid_slug": _slug(shared_rng),
                "replay_slug": _slug(shared_rng),
                "revoked_slug": _slug(shared_rng),
                "legitimate_slugs": [_slug(shared_rng) for _ in range(shared_memory_n)],
            }
        )

    # M7 uses an independent RNG namespace so adding associative cases cannot
    # change any v5 scenario bytes. Each case is a true two-hop chain plus
    # stronger one-hop distractors. Direct, one-hop, and bounded strongest-path
    # retrieval should miss the hidden target under the two-slot related budget;
    # personalized PageRank's verified deep-slot policy should recover it.
    associative_rng = random.Random(f"{seed}:memorytruth-m7")
    associative_combos = [(a, n) for a in _ADJECTIVES for n in _NOUNS]
    associative_rng.shuffle(associative_combos)
    for index in range(max(0, associative_n)):
        # The person name consumes the FULL (adjective, noun) combo so it is unique
        # by construction: combos never repeat, while bare adjectives can (seed 42
        # produced two "Dana Flint" chains that merged through the shared person
        # entity and broke the two-hop-only property).
        person_adj, person_noun = associative_combos[index * 4]
        project_adj, project_noun = associative_combos[index * 4 + 1]
        program_adj, program_noun = associative_combos[index * 4 + 2]
        isolated_adj, isolated_noun = associative_combos[index * 4 + 3]
        person = f"Dana {person_adj.title()}{person_noun.title()}"
        project = f"{project_adj.title()} {project_noun.title()}"
        program = f"{program_adj.title()} {program_noun.title()}"
        isolated = f"{isolated_adj.title()} {isolated_noun.title()}"
        slug = _slug(associative_rng)
        reject_slug = _slug(associative_rng)
        scenario.associative_cases.append(
            {
                "query": f"{person} accountable portfolio",
                "slug": slug,
                "reject_slug": reject_slug,
                "seed_content": f"{person} is accountable for the portfolio named {project}.",
                "bridge_content": f"The {project} portfolio maps to the internal program called {program}.",
                "target_content": f"The {program} program uses calibrated release threshold {slug}.",
                "distractor_contents": [
                    f"The {project} portfolio review lane {lane} uses checklist marker {lane + 11}."
                    for lane in range(3)
                ],
                "isolated_content": f"The unrelated {isolated} program uses isolation token {reject_slug}.",
            }
        )
    return scenario


# --------------------------------------------------------------------------
# Clients: the same scenario drives an in-process store or a live HTTP server.
# --------------------------------------------------------------------------

class BenchClient(Protocol):
    def configure(self) -> None: ...
    def remember(self, content: str) -> list[str]: ...
    def ask(self, question: str) -> dict[str, Any]: ...
    def search(
        self,
        query: str,
        *,
        associative: bool = False,
        association_mode: str | None = None,
    ) -> Any: ...
    def resolve_conflict(self, stale_id: str, current_id: str) -> bool: ...
    def belief_timeline(self, topic: str) -> dict[str, Any]: ...
    def integrity_digest(self) -> dict[str, Any]: ...
    def verify_integrity(self, expected_head: str) -> dict[str, Any]: ...
    def record_canvas_node(self, session_id: str, node: dict[str, Any]) -> dict[str, Any]: ...
    def get_canvas(self, session_id: str) -> dict[str, Any]: ...
    def get_canvas_node(self, session_id: str, node_id: str) -> dict[str, Any]: ...
    def belief_proof(
        self,
        topic: str,
        *,
        valid_at: str | None = None,
        known_at: str | None = None,
        expected_head: str | None = None,
    ) -> dict[str, Any]: ...
    def verify_belief_proof(self, proof: dict[str, Any]) -> dict[str, Any]: ...
    def would_i(self, question: str) -> dict[str, Any]: ...
    def grade_twin_prediction(
        self,
        prediction_id: str,
        outcome: str,
        *,
        answerability: str,
        actual: str = "",
    ) -> dict[str, Any]: ...
    def get_twin_calibration(
        self,
        *,
        days: int = 365,
        prediction_ids: list[str] | None = None,
    ) -> dict[str, Any]: ...
    def create_shared_principal(self, *, label: str, trust_score: float) -> dict[str, Any]: ...
    def record_shared_memory(self, **kwargs: Any) -> dict[str, Any]: ...
    def revoke_shared_principal(self, principal_id: str) -> dict[str, Any]: ...
    def verify_shared_memory(self, principal_id: str | None = None) -> dict[str, Any]: ...
    def get_poisoning_attempts(self, principal_id: str) -> dict[str, Any]: ...


class InProcessClient:
    """Drives the real MCP tool dispatch (mcp_tools.call_tool) against a CortexStore."""

    def __init__(self, store: Any, user_id: str) -> None:
        self.store = store
        self.user_id = user_id

    def _tool(self, name: str, args: dict[str, Any]) -> Any:
        from backend.app import mcp_tools

        return mcp_tools.call_tool(self.store, self.user_id, name, args)

    def configure(self) -> None:
        self.store.update_settings(
            self.user_id,
            {
                "review_new_captures": False,
                "allow_pending_in_context": False,
                "allow_agent_maintenance": True,
            },
        )

    def remember(self, content: str) -> list[str]:
        result = self._tool("remember_this", {"content": content, "source": "note"})
        return [str(m.get("id") or "") for m in (result.get("memories") or []) if m.get("id")]

    def ask(self, question: str) -> dict[str, Any]:
        return self._tool("ask_memory", {"query": question})

    def search(
        self,
        query: str,
        *,
        associative: bool = False,
        association_mode: str | None = None,
    ) -> Any:
        args: dict[str, Any] = {"query": query, "top_k": 8, "associative": associative}
        if association_mode:
            args["association_mode"] = association_mode
        return self._tool("search_memory", args)

    def resolve_conflict(self, stale_id: str, current_id: str) -> bool:
        return bool(self.store.resolve_conflict(self.user_id, stale_id=stale_id, current_id=current_id))

    def belief_timeline(self, topic: str) -> dict[str, Any]:
        return self._tool("get_belief_timeline", {"topic": topic})

    def integrity_digest(self) -> dict[str, Any]:
        return self._tool("get_memory_integrity", {})

    def verify_integrity(self, expected_head: str) -> dict[str, Any]:
        return self._tool("verify_memory_integrity", {"expected_head": expected_head})

    def record_canvas_node(self, session_id: str, node: dict[str, Any]) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "node_id": node["node_id"],
            "label": node.get("label") or "",
            "summary": node.get("summary") or "",
            "raw_text": node["raw_text"],
        }
        if node.get("predecessor_node_id"):
            args["predecessor_node_id"] = node["predecessor_node_id"]
        return self._tool("record_working_canvas_node", args)

    def get_canvas(self, session_id: str) -> dict[str, Any]:
        return self._tool("get_working_canvas", {"session_id": session_id})

    def get_canvas_node(self, session_id: str, node_id: str) -> dict[str, Any]:
        return self._tool("get_working_canvas_node", {"session_id": session_id, "node_id": node_id})

    def belief_proof(
        self,
        topic: str,
        *,
        valid_at: str | None = None,
        known_at: str | None = None,
        expected_head: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"topic": topic}
        if valid_at:
            args["valid_at"] = valid_at
        if known_at:
            args["known_at"] = known_at
        if expected_head:
            args["expected_head"] = expected_head
        return self._tool("get_belief_proof", args)

    def verify_belief_proof(self, proof: dict[str, Any]) -> dict[str, Any]:
        return self._tool("verify_belief_proof", {"proof": proof})

    def would_i(self, question: str) -> dict[str, Any]:
        return self._tool("would_i", {"question": question})

    def grade_twin_prediction(
        self,
        prediction_id: str,
        outcome: str,
        *,
        answerability: str,
        actual: str = "",
    ) -> dict[str, Any]:
        return self._tool(
            "grade_twin_prediction",
            {
                "prediction_id": prediction_id,
                "outcome": outcome,
                "answerability": answerability,
                "actual": actual,
            },
        )

    def get_twin_calibration(
        self,
        *,
        days: int = 365,
        prediction_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"days": days}
        if prediction_ids is not None:
            args["prediction_ids"] = prediction_ids
        return self._tool("get_twin_calibration", args)

    def create_shared_principal(self, *, label: str, trust_score: float) -> dict[str, Any]:
        return self._tool(
            "create_shared_principal",
            {"label": label, "kind": "agent", "trust_score": trust_score},
        )

    def record_shared_memory(self, **kwargs: Any) -> dict[str, Any]:
        return self._tool("record_shared_memory", dict(kwargs))

    def revoke_shared_principal(self, principal_id: str) -> dict[str, Any]:
        return self._tool("revoke_shared_principal", {"principal_id": principal_id})

    def verify_shared_memory(self, principal_id: str | None = None) -> dict[str, Any]:
        return self._tool(
            "verify_shared_memory",
            {"principal_id": principal_id} if principal_id else {},
        )

    def get_poisoning_attempts(self, principal_id: str) -> dict[str, Any]:
        return self._tool(
            "get_poisoning_attempts",
            {"principal_id": principal_id, "limit": 100},
        )


class HTTPClient:
    """Drives a live Cortex standalone/FastAPI server over /v1 - the whole pipeline."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        user_id: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.user_id = user_id.strip()

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        from urllib import error as _error
        from urllib import request as _request

        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if self.user_id:
            headers["X-Cortex-User"] = self.user_id
        for attempt in range(6):
            req = _request.Request(
                f"{self.base_url}{path}",
                data=body,
                method=method,
                headers=headers,
            )
            try:
                with _request.urlopen(req, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except _error.HTTPError as exc:
                if exc.code not in {429, 503} or attempt == 5:
                    raise
                try:
                    retry_after = float(exc.headers.get("Retry-After") or 1.0)
                except (TypeError, ValueError):
                    retry_after = 1.0
                time.sleep(max(0.05, min(retry_after, 5.0)))
        raise RuntimeError("unreachable HTTP retry state")

    def _tool(self, name: str, args: dict[str, Any]) -> Any:
        wrapped = self._request("POST", "/v1/tools/call", {"name": name, "arguments": args})
        return wrapped.get("result") if isinstance(wrapped, dict) and "result" in wrapped else wrapped

    def configure(self) -> None:
        self._request(
            "PUT",
            "/v1/settings",
            {
                "review_new_captures": False,
                "allow_pending_in_context": False,
                "allow_agent_maintenance": True,
            },
        )

    def remember(self, content: str) -> list[str]:
        result = self._tool("remember_this", {"content": content, "source": "note"})
        return [str(m.get("id") or "") for m in (result.get("memories") or []) if m.get("id")]

    def ask(self, question: str) -> dict[str, Any]:
        return self._tool("ask_memory", {"query": question})

    def search(
        self,
        query: str,
        *,
        associative: bool = False,
        association_mode: str | None = None,
    ) -> Any:
        args: dict[str, Any] = {"query": query, "top_k": 8, "associative": associative}
        if association_mode:
            args["association_mode"] = association_mode
        return self._tool("search_memory", args)

    def resolve_conflict(self, stale_id: str, current_id: str) -> bool:
        result = self._request(
            "POST", "/v1/memory/conflicts/resolve", {"stale_id": stale_id, "current_id": current_id}
        )
        return bool(isinstance(result, dict) and result.get("resolved"))

    def belief_timeline(self, topic: str) -> dict[str, Any]:
        return self._tool("get_belief_timeline", {"topic": topic})

    def integrity_digest(self) -> dict[str, Any]:
        # Read-only REST surface, NOT /v1/tools/call: over HTTP every tool call
        # is itself recorded to the audit event log (by design), which advances
        # the chain head between pin and verify. The Phase D endpoints are pure
        # reads, so pin -> verify stays write-free exactly like in-process.
        return self._request("GET", "/v1/integrity/digest")

    def verify_integrity(self, expected_head: str) -> dict[str, Any]:
        return self._request("POST", "/v1/integrity/verify", {"expected_head": expected_head})

    def record_canvas_node(self, session_id: str, node: dict[str, Any]) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "node_id": node["node_id"],
            "label": node.get("label") or "",
            "summary": node.get("summary") or "",
            "raw_text": node["raw_text"],
        }
        if node.get("predecessor_node_id"):
            args["predecessor_node_id"] = node["predecessor_node_id"]
        return self._tool("record_working_canvas_node", args)

    def get_canvas(self, session_id: str) -> dict[str, Any]:
        return self._tool("get_working_canvas", {"session_id": session_id})

    def get_canvas_node(self, session_id: str, node_id: str) -> dict[str, Any]:
        return self._tool("get_working_canvas_node", {"session_id": session_id, "node_id": node_id})

    def belief_proof(
        self,
        topic: str,
        *,
        valid_at: str | None = None,
        known_at: str | None = None,
        expected_head: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"topic": topic}
        if valid_at:
            args["valid_at"] = valid_at
        if known_at:
            args["known_at"] = known_at
        if expected_head:
            args["expected_head"] = expected_head
        return self._tool("get_belief_proof", args)

    def verify_belief_proof(self, proof: dict[str, Any]) -> dict[str, Any]:
        return self._tool("verify_belief_proof", {"proof": proof})

    def would_i(self, question: str) -> dict[str, Any]:
        return self._tool("would_i", {"question": question})

    def grade_twin_prediction(
        self,
        prediction_id: str,
        outcome: str,
        *,
        answerability: str,
        actual: str = "",
    ) -> dict[str, Any]:
        return self._tool(
            "grade_twin_prediction",
            {
                "prediction_id": prediction_id,
                "outcome": outcome,
                "answerability": answerability,
                "actual": actual,
            },
        )

    def get_twin_calibration(
        self,
        *,
        days: int = 365,
        prediction_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"days": days}
        if prediction_ids is not None:
            args["prediction_ids"] = prediction_ids
        return self._tool("get_twin_calibration", args)

    def create_shared_principal(self, *, label: str, trust_score: float) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/shared-memory/principals",
            {"label": label, "kind": "agent", "trust_score": trust_score},
        )

    def record_shared_memory(self, **kwargs: Any) -> dict[str, Any]:
        return self._request("POST", "/v1/shared-memory/writes", dict(kwargs))

    def revoke_shared_principal(self, principal_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/shared-memory/principals/{principal_id}/revoke",
            {},
        )

    def verify_shared_memory(self, principal_id: str | None = None) -> dict[str, Any]:
        from urllib.parse import urlencode

        query = f"?{urlencode({'principal_id': principal_id})}" if principal_id else ""
        return self._request("GET", f"/v1/shared-memory/verify{query}")

    def get_poisoning_attempts(self, principal_id: str) -> dict[str, Any]:
        from urllib.parse import urlencode

        query = urlencode({"principal_id": principal_id, "limit": 100})
        return self._request("GET", f"/v1/shared-memory/poisoning-attempts?{query}")


# --------------------------------------------------------------------------
# Scoring helpers: defensive, exact, and judge-free.
# --------------------------------------------------------------------------

def _collect_strings(obj: Any, key: str, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, str):
                out.append(v)
            else:
                _collect_strings(v, key, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_strings(item, key, out)


def _citation_ids(answer: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for citation in answer.get("citations") or []:
        if isinstance(citation, dict) and citation.get("id"):
            ids.append(str(citation["id"]))
    return ids


def _cited_contents(answer: dict[str, Any]) -> str:
    """Text of results that are actually cited (falls back to all results/answer)."""
    cited = set(_citation_ids(answer))
    chunks: list[str] = []
    for item in answer.get("results") or []:
        if not isinstance(item, dict):
            continue
        if cited and str(item.get("id") or "") not in cited:
            continue
        for key in ("content", "summary", "title"):
            value = item.get(key)
            if isinstance(value, str):
                chunks.append(value)
    if not chunks and isinstance(answer.get("answer"), str):
        chunks.append(answer["answer"])
    return "\n".join(chunks)


def _expected_shared_rejection(exc: Exception) -> bool:
    if isinstance(exc, (PermissionError, ValueError)):
        return True
    return getattr(exc, "code", None) in {403, 422}


def _finite_confidence(value: Any) -> float | None:
    """Parse a confidence without accepting NaN/inf or silently clamping lies."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    if not 0.0 <= parsed <= 1.0:
        return None
    return parsed


def _twin_evidence_text(prediction: dict[str, Any]) -> str:
    evidence = [
        *(prediction.get("supporting") or []),
        *(prediction.get("opposing") or []),
        *(prediction.get("hard_constraints") or []),
    ]
    return json.dumps(evidence, sort_keys=True)


def _independent_metacognition_metrics(
    observations: list[dict[str, Any]],
    *,
    threshold: float = _METACOGNITION_THRESHOLD,
) -> dict[str, Any]:
    """Compute M6 metrics only from benchmark gold labels and observed outputs.

    This deliberately does not call a Cortex metric helper. The benchmark knows
    which cases were seeded and which were not, so answerability targets are exact.
    """
    labeled = [
        item
        for item in observations
        if item.get("answerability") in {"answerable", "unknown"}
        and _finite_confidence(item.get("confidence")) is not None
    ]

    def target(item: dict[str, Any]) -> int:
        return 1 if item["answerability"] == "answerable" else 0

    bins: list[dict[str, Any]] = []
    weighted_error = 0.0
    for index in range(_METACOGNITION_BIN_COUNT):
        lower = index / _METACOGNITION_BIN_COUNT
        upper = (index + 1) / _METACOGNITION_BIN_COUNT
        members = [
            item
            for item in labeled
            if min(
                _METACOGNITION_BIN_COUNT - 1,
                int(float(item["confidence"]) * _METACOGNITION_BIN_COUNT),
            )
            == index
        ]
        average_confidence = (
            sum(float(item["confidence"]) for item in members) / len(members)
            if members
            else None
        )
        empirical_answerability = (
            sum(target(item) for item in members) / len(members) if members else None
        )
        absolute_error = (
            abs(average_confidence - empirical_answerability)
            if average_confidence is not None and empirical_answerability is not None
            else None
        )
        if absolute_error is not None and labeled:
            weighted_error += absolute_error * (len(members) / len(labeled))
        bins.append(
            {
                "index": index,
                "lower": round(lower, 2),
                "upper": round(upper, 2),
                "count": len(members),
                "average_confidence": round(average_confidence, 4) if average_confidence is not None else None,
                "empirical_answerability": round(empirical_answerability, 4) if empirical_answerability is not None else None,
                "absolute_error": round(absolute_error, 4) if absolute_error is not None else None,
            }
        )

    brier = (
        sum((float(item["confidence"]) - target(item)) ** 2 for item in labeled) / len(labeled)
        if labeled
        else None
    )
    abstained = [item for item in labeled if item.get("known_unknown") is True]
    answered = [item for item in labeled if item.get("known_unknown") is False]
    true_positive = sum(1 for item in abstained if target(item) == 0)
    false_positive = sum(1 for item in abstained if target(item) == 1)
    false_negative = sum(1 for item in answered if target(item) == 0)
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    high_confidence_answered = [
        item
        for item in observations
        if item.get("known_unknown") is False
        and _finite_confidence(item.get("confidence")) is not None
        and float(item["confidence"]) >= threshold
        and item.get("outcome") in {"correct", "incorrect"}
    ]
    confident_wrong = sum(1 for item in high_confidence_answered if item["outcome"] == "incorrect")
    all_answered = [item for item in observations if item.get("known_unknown") is False]
    outcome_graded = [item for item in observations if item.get("outcome") in {"correct", "incorrect"}]
    return {
        "threshold": threshold,
        "prediction_samples": len(observations),
        "graded_samples": len(labeled),
        "outcome_graded_samples": len(outcome_graded),
        "legacy_unlabeled_samples": 0,
        "expected_calibration_error": round(weighted_error, 4) if labeled else None,
        "brier_score": round(brier, 4) if brier is not None else None,
        "bins": bins,
        "abstention": {
            "graded": len(abstained),
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": round(true_positive / precision_denominator, 4) if precision_denominator else None,
            "recall": round(true_positive / recall_denominator, 4) if recall_denominator else None,
        },
        "confident_wrong": {
            "count": confident_wrong,
            "high_confidence_answered": len(high_confidence_answered),
            "rate": round(confident_wrong / len(high_confidence_answered), 4)
            if high_confidence_answered
            else None,
        },
        "coverage": round(len(all_answered) / len(observations), 4) if observations else None,
    }


def _metacognition_scorecard_mismatches(
    reported: dict[str, Any],
    independent: dict[str, Any],
    *,
    prediction_ids: list[str],
    window_days: int,
) -> list[str]:
    """Return stable field-level differences between Cortex and independent gold."""
    mismatches: list[str] = []
    if reported.get("window_days") != window_days:
        mismatches.append(
            f"window_days: reported={reported.get('window_days')!r} expected={window_days!r}"
        )
    expected_cohort = sorted(set(prediction_ids))
    if reported.get("cohort_prediction_ids") != expected_cohort:
        mismatches.append(
            "cohort_prediction_ids: reported cohort differs from exact benchmark prediction ids"
        )
    scalar_fields = (
        "threshold",
        "prediction_samples",
        "graded_samples",
        "outcome_graded_samples",
        "legacy_unlabeled_samples",
        "expected_calibration_error",
        "brier_score",
        "coverage",
    )
    for key in scalar_fields:
        if reported.get(key) != independent.get(key):
            mismatches.append(f"{key}: reported={reported.get(key)!r} expected={independent.get(key)!r}")
    for section in ("abstention", "confident_wrong"):
        expected_section = independent.get(section) or {}
        reported_section = reported.get(section) if isinstance(reported.get(section), dict) else {}
        for key, expected in expected_section.items():
            if reported_section.get(key) != expected:
                mismatches.append(
                    f"{section}.{key}: reported={reported_section.get(key)!r} expected={expected!r}"
                )
    if reported.get("bins") != independent.get("bins"):
        mismatches.append("bins: reported fixed-bin calibration differs from independent gold")
    if reported.get("ungraded_abstentions") != []:
        mismatches.append(
            f"ungraded_abstentions: reported={reported.get('ungraded_abstentions')!r} expected=[]"
        )
    if reported.get("unlabeled_answerability") != []:
        mismatches.append(
            f"unlabeled_answerability: reported={reported.get('unlabeled_answerability')!r} expected=[]"
        )
    return mismatches


# --------------------------------------------------------------------------
# M2 independent proof verifier. Deliberately REIMPLEMENTS the crypto contract
# (canonical event fingerprint, chain fold, canonical snapshot hash) instead of
# importing storage helpers: the bench must be able to catch a store whose own
# verify_belief_proof lies, so grading never calls store code for this check.
# The constants mirror the documented v1 contract in integrity_digest().
# --------------------------------------------------------------------------

_CHAIN_GENESIS = "cortex:integrity:v1:genesis"


def _independent_event_fingerprint(event: dict[str, Any]) -> str:
    body = {
        "id": event.get("id"),
        "object_id": event.get("object_id"),
        "object_type": event.get("object_type"),
        "event_type": event.get("event_type"),
        "metadata": event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
        "created_at": event.get("created_at"),
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _independent_chain_link(prev_hash: str, fingerprint: str) -> str:
    return hashlib.sha256(f"{prev_hash}:{fingerprint}".encode("utf-8")).hexdigest()


def _independent_snapshot_sha(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _independent_belief_snapshot(belief: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the canonical snapshot from a returned belief (mirror of _belief_snapshot)."""
    return {
        "memory_id": str(belief.get("memory_id") or ""),
        "capture_id": belief.get("capture_id"),
        "kind": str(belief.get("kind") or "observation"),
        "layer": str(belief.get("layer") or "semantic"),
        "content": str(belief.get("content") or ""),
        "summary": str(belief.get("summary") or ""),
        "source": str(belief.get("source") or ""),
        "source_url": belief.get("source_url"),
        "confidence": str(belief.get("confidence") or "confirmed"),
        "importance": int(belief.get("importance") or 3),
        "author_class": str(belief.get("author_class") or "unknown"),
        "occurred_at": belief.get("occurred_at"),
        "valid_from": belief.get("valid_from"),
        "valid_to": belief.get("valid_to"),
        "captured_at": belief.get("captured_at"),
        "topics": [str(value) for value in (belief.get("topics") or [])],
        "entity_ids": [str(value) for value in (belief.get("entity_ids") or [])],
        "provenance": belief.get("provenance") if isinstance(belief.get("provenance"), dict) else {},
    }


def _independent_verify_belief_proof(result: dict[str, Any]) -> tuple[bool, list[str]]:
    """Independently verify a get_belief_proof result. Returns (ok, reasons).

    Checks, without trusting any store-computed verdict flag:
    1. every receipt event's fingerprint recomputes from its returned bytes;
    2. the chain segment folds from chain_head_before_segment to the claimed
       known-at prefix head, and each receipt is bound into that segment at its
       claimed index;
    3. every sealed belief's returned fields re-hash to the snapshot_sha256 its
       chain-sealed receipt attests (so the belief text cannot be doctored).
    """
    reasons: list[str] = []
    envelope = result.get("proof") if isinstance(result.get("proof"), dict) else {}
    beliefs = result.get("beliefs") if isinstance(result.get("beliefs"), list) else []

    claimed_head = str(envelope.get("chain_head_at_known_at") or "")
    if not claimed_head:
        reasons.append("missing chain_head_at_known_at")

    # -- chain segment fold ------------------------------------------------
    segment = envelope.get("chain_segment") if isinstance(envelope.get("chain_segment"), list) else None
    segment_entries: dict[int, tuple[str, str]] = {}
    try:
        segment_start = int(envelope.get("chain_segment_start_index"))
    except (TypeError, ValueError):
        segment_start = -1
        reasons.append("invalid chain_segment_start_index")
    try:
        event_count = int(envelope.get("event_count_at_known_at"))
    except (TypeError, ValueError):
        event_count = -1
        reasons.append("invalid event_count_at_known_at")
    if segment is None:
        reasons.append("missing chain_segment")
    elif segment_start >= 0 and event_count >= 0:
        if segment_start + len(segment) != event_count:
            reasons.append("chain segment does not reach the known-at head")
        fold = str(envelope.get("chain_head_before_segment") or "")
        if segment_start == 0 and fold != _CHAIN_GENESIS:
            reasons.append("segment starting at index 0 must fold from genesis")
        for offset, item in enumerate(segment):
            if not (isinstance(item, (list, tuple)) and len(item) == 2):
                reasons.append("malformed chain segment entry")
                continue
            entry_id, fingerprint = str(item[0] or ""), str(item[1] or "")
            if len(fingerprint) != 64:
                reasons.append("malformed segment fingerprint")
                continue
            segment_entries[segment_start + offset] = (entry_id, fingerprint)
            fold = _independent_chain_link(fold, fingerprint)
        if claimed_head and fold != claimed_head:
            reasons.append("chain segment fold does not reproduce the claimed head")

    # -- receipts: fingerprints recompute and bind into the segment ---------
    receipts_by_id: dict[str, dict[str, Any]] = {}
    for receipt in envelope.get("receipts") or []:
        if not isinstance(receipt, dict) or not isinstance(receipt.get("event"), dict):
            reasons.append("malformed receipt")
            continue
        event = receipt["event"]
        event_id = str(event.get("id") or "")
        recomputed = _independent_event_fingerprint(event)
        if recomputed != str(receipt.get("fingerprint") or ""):
            reasons.append(f"receipt {event_id or '?'} fingerprint does not recompute")
            continue
        try:
            index = int(receipt.get("event_index"))
        except (TypeError, ValueError):
            reasons.append(f"receipt {event_id or '?'} has no valid event_index")
            continue
        if segment_entries and segment_entries.get(index) != (event_id, recomputed):
            reasons.append(f"receipt {event_id or '?'} is not bound to the chain segment")
            continue
        receipts_by_id[event_id] = event

    # -- beliefs: returned fields must re-hash to the chain-sealed snapshot --
    for belief in beliefs:
        if not isinstance(belief, dict):
            reasons.append("malformed belief")
            continue
        memory_id = str(belief.get("memory_id") or "?")
        if not belief.get("sealed"):
            reasons.append(f"belief {memory_id} is unsealed")
            continue
        receipt_id = str(belief.get("record_receipt_event_id") or "")
        event = receipts_by_id.get(receipt_id)
        if event is None:
            reasons.append(f"belief {memory_id} has no verified receipt in the envelope")
            continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        sealed_snapshot = metadata.get("snapshot") if isinstance(metadata.get("snapshot"), dict) else {}
        sealed_sha = str(metadata.get("snapshot_sha256") or "")
        if _independent_snapshot_sha(sealed_snapshot) != sealed_sha:
            reasons.append(f"belief {memory_id} receipt metadata does not hash to its own seal")
            continue
        returned_sha = _independent_snapshot_sha(_independent_belief_snapshot(belief))
        if returned_sha != sealed_sha:
            reasons.append(f"belief {memory_id} returned fields do not re-hash to the sealed snapshot")
    return (not reasons, reasons)



@dataclass
class CategoryScore:
    passed: int = 0
    total: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    def record(self, ok: bool, failure: str) -> None:
        self.total += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append(failure)

    def to_json(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "passed": self.passed,
            "total": self.total,
            "failures": self.failures[:10],
        }


def run_bench(client: BenchClient, scenario: Scenario, *, mode: str = "inprocess") -> dict[str, Any]:
    """Seed, revise, probe, and score. Returns the JSON report."""
    started = time.time()
    client.configure()

    recall = CategoryScore()
    abstention = CategoryScore()
    temporal = CategoryScore()
    provenance = CategoryScore()
    canvas = CategoryScore()
    belief_proof = CategoryScore()
    metacognition = CategoryScore()
    shared_memory = CategoryScore()
    associative_recall = CategoryScore()

    # --- Seed simple facts -------------------------------------------------
    for fact in scenario.facts:
        client.remember(fact["content"])

    # Seed only the answerable half of M6's gold set. Unknown cases deliberately
    # have no matching preference/decision memory anywhere in the scenario.
    for case in scenario.metacognition_cases:
        if case.get("content"):
            client.remember(str(case["content"]))

    # M7 associative recall chains are seeded before distractors so direct
    # lexical retrieval sees the obvious first-hop fact, while the answer slug is
    # only reachable by traversing seed -> bridge -> target.
    for case in scenario.associative_cases:
        client.remember(str(case["seed_content"]))
        client.remember(str(case["bridge_content"]))
        client.remember(str(case["target_content"]))
        for distractor in case.get("distractor_contents") or []:
            client.remember(str(distractor))
        client.remember(str(case["isolated_content"]))

    # --- Seed revisions and supersede v1 -> v2 (belief revision) -----------
    revision_ids: list[dict[str, Any]] = []
    for revision in scenario.revisions:
        v1_ids = client.remember(revision["v1_content"])
        v2_ids = client.remember(revision["v2_content"])
        resolved = False
        if v1_ids and v2_ids:
            # Supersede every v1 record so the stale fact cannot leak through
            # a secondary extracted record.
            resolved = all(client.resolve_conflict(v1, v2_ids[0]) for v1 in v1_ids)
        revision_ids.append({"v1": v1_ids, "v2": v2_ids, "resolved": resolved})

    # --- M2 pin: freeze a transaction-time anchor now; re-derive it at the END
    # of the run. Append-only history means the event prefix at a past instant
    # never changes, so the prefix head must reproduce byte-identically after
    # all the probe traffic below has appended dozens of new events.
    #
    # Anchor discipline: pin at the last microsecond of the CURRENT server
    # second (server generated_at, so no client clock skew), then wait until
    # the server clock leaves that second. After that instant the prefix is
    # closed: every seeded event is inside it, and no future event - even a
    # second-precision audit row, which truncates DOWN to :SS.000000 - can
    # sort into it retroactively.
    pin_probe: dict[str, Any] | None = None
    if scenario.revisions:
        pin_topic = scenario.revisions[0]["topic"]
        baseline = client.belief_proof(pin_topic)
        server_now = str(baseline.get("generated_at") or "")
        anchor = datetime.fromisoformat(server_now) if server_now else datetime.now(timezone.utc)
        pin_dt = anchor.replace(microsecond=0) + timedelta(seconds=1) - timedelta(microseconds=1)
        pin_time = pin_dt.isoformat()
        for _ in range(60):
            check = client.belief_proof(pin_topic)
            check_raw = str(check.get("generated_at") or "")
            check_now = datetime.fromisoformat(check_raw) if check_raw else datetime.now(timezone.utc)
            if check_now > pin_dt:
                break
            time.sleep(0.05)
        pinned = client.belief_proof(pin_topic, known_at=pin_time)
        pin_probe = {
            "known_at": pin_time,
            "head": str((pinned.get("proof") or {}).get("chain_head_at_known_at") or ""),
            "event_count": (pinned.get("proof") or {}).get("event_count_at_known_at"),
        }

    # --- Recall probes ------------------------------------------------------
    for fact in scenario.facts:
        answer = client.ask(fact["question"])
        cited = answer.get("status") in {"cited", "low_confidence", "conflicted"} and bool(_citation_ids(answer))
        slug_ok = fact["slug"] in _cited_contents(answer)
        recall.record(
            cited and slug_ok,
            f"recall miss: q={fact['question']!r} status={answer.get('status')} slug_found={slug_ok}",
        )

        # Provenance probe 1: each citation id must resolve to a real memory that
        # contains the slug (citation correctness, not just answer correctness).
        if cited and slug_ok:
            hits = client.search(fact["slug"])
            hit_ids: list[str] = []
            _collect_strings(hits, "id", hit_ids)
            hit_text: list[str] = []
            _collect_strings(hits, "content", hit_text)
            _collect_strings(hits, "summary", hit_text)
            answer_ids = set(_citation_ids(answer))
            resolvable = bool(answer_ids & set(hit_ids)) and any(fact["slug"] in t for t in hit_text)
            provenance.record(
                resolvable,
                f"citation unresolvable: q={fact['question']!r} cited={sorted(answer_ids)[:3]}",
            )

    # --- Abstention probes --------------------------------------------------
    for probe in scenario.abstention_probes:
        answer = client.ask(probe.question)
        abstained = answer.get("status") == "no_cited_evidence" and not _citation_ids(answer)
        abstention.record(
            abstained,
            f"confabulation: q={probe.question!r} status={answer.get('status')} citations={len(_citation_ids(answer))}",
        )

    # --- Temporal probes ----------------------------------------------------
    for revision, ids in zip(scenario.revisions, revision_ids):
        answer = client.ask(revision["question"])
        text = _cited_contents(answer)
        current_ok = revision["v2_slug"] in text
        stale_leaked = revision["v1_slug"] in text
        temporal.record(
            ids["resolved"] and current_ok and not stale_leaked,
            f"temporal: q={revision['question']!r} resolved={ids['resolved']} current={current_ok} stale_leaked={stale_leaked}",
        )

        timeline = client.belief_timeline(revision["topic"])
        payload = json.dumps(timeline)
        head_ok = False
        for thread in timeline.get("timelines") or []:
            if not isinstance(thread, dict):
                continue
            head_id = str(thread.get("current_memory_id") or "")
            revisions_list = thread.get("revisions") or []
            head_content = ""
            for rev in revisions_list:
                if isinstance(rev, dict) and str(rev.get("memory_id") or "") == head_id:
                    head_content = str(rev.get("content") or "")
                    break
            if thread.get("revised") and revision["v2_slug"] in head_content and revision["v1_slug"] in payload:
                head_ok = True
                break
        temporal.record(
            head_ok,
            f"timeline: topic={revision['topic']!r} lacks revised head with v2 slug",
        )

        # Provenance probe 2: author_class must survive the pipeline as "user".
        author_classes: list[str] = []
        _collect_strings(timeline, "author_class", author_classes)
        provenance.record(
            bool(author_classes) and all(a == "user" for a in author_classes),
            f"author_class corrupted for topic={revision['topic']!r}: {sorted(set(author_classes))}",
        )

    # --- Provenance probe 3: the integrity chain must verify -----------------
    digest = client.integrity_digest()
    head = str(digest.get("chain_head") or "")
    verified = client.verify_integrity(head) if head else {}
    provenance.record(
        bool(head) and bool(verified.get("matches")),
        f"integrity chain failed: head={head[:16]!r} matches={verified.get('matches')}",
    )

    # --- M3 canvas probes -----------------------------------------------------
    # The whole offload loop: record verbose evidence -> only a compact node stays
    # in context -> drill-down recovers the raw bytes EXACTLY. Grading never trusts
    # the store's own "verified" flag; it recomputes equality against the scenario.
    if scenario.canvas_nodes:
        session_id = f"bench-canvas-{scenario.seed}"
        recorded_receipts: dict[str, str] = {}
        for node in scenario.canvas_nodes:
            result = client.record_canvas_node(session_id, node)
            receipt = str((result or {}).get("receipt_event_id") or "")
            raw_leaked = node["slug"] in json.dumps(result or {})
            canvas.record(
                bool(receipt) and not raw_leaked,
                f"canvas record: node={node['node_id']} receipt={bool(receipt)} raw_leaked={raw_leaked}",
            )
            if receipt:
                recorded_receipts[node["node_id"]] = receipt

        board = client.get_canvas(session_id)
        board_json = json.dumps(board or {})
        raw_total = sum(len(n["raw_text"]) for n in scenario.canvas_nodes)
        compact_len = len(str((board or {}).get("canvas") or ""))
        node_count_ok = (board or {}).get("node_count") == len(scenario.canvas_nodes)
        no_leak = not any(n["slug"] in board_json for n in scenario.canvas_nodes)
        # Tencent's credible number is >=40% input-token reduction; the canvas text
        # replacing the raw evidence must be at most 60% of the raw size. In practice
        # it is far smaller; the floor just keeps the claim honest.
        compact_ok = compact_len <= int(raw_total * 0.6)
        edges_ok = all(
            f'{n["predecessor_node_id"]} --> {n["node_id"]}' in str((board or {}).get("canvas") or "")
            for n in scenario.canvas_nodes
            if n.get("predecessor_node_id")
        )
        canvas.record(
            node_count_ok and no_leak and compact_ok and edges_ok,
            "canvas board: "
            f"nodes_ok={node_count_ok} no_leak={no_leak} compact_ok={compact_ok} "
            f"({compact_len}B vs raw {raw_total}B) edges_ok={edges_ok}",
        )

        for node in scenario.canvas_nodes:
            recovered = client.get_canvas_node(session_id, node["node_id"])
            raw_back = str((recovered or {}).get("raw_text") or "")
            exact = raw_back == node["raw_text"]
            receipt_stable = (
                str((recovered or {}).get("receipt_event_id") or "") == recorded_receipts.get(node["node_id"], "")
            )
            canvas.record(
                exact and receipt_stable,
                f"canvas drill-down: node={node['node_id']} exact_bytes={exact} receipt_stable={receipt_stable}",
            )

    # --- Belief-proof probes (M2 bi-temporal reconstruction) ----------------
    # Grading never trusts store-computed verdicts: every proof below is checked
    # by _independent_verify_belief_proof, a from-scratch reimplementation of the
    # fingerprint/chain/snapshot contract.
    proof_latencies_ms: list[float] = []
    for revision in scenario.revisions:
        topic = revision["topic"]

        # P1: current proof shows v2, never v1, and passes independent crypto.
        t0 = time.perf_counter()
        current = client.belief_proof(topic)
        proof_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        crypto_ok, crypto_reasons = _independent_verify_belief_proof(current)
        text = json.dumps([b.get("content") for b in current.get("beliefs") or []])
        current_ok = revision["v2_slug"] in text and revision["v1_slug"] not in text
        belief_proof.record(
            crypto_ok and current_ok and not current.get("abstained"),
            f"belief_proof current: topic={topic!r} v2={revision['v2_slug'] in text} "
            f"v1_leaked={revision['v1_slug'] in text} crypto={crypto_reasons[:2]}",
        )

        # Locate v2's recorded_at so we can ask about the instant just before it.
        v2_recorded_at = ""
        for belief in current.get("beliefs") or []:
            recorded = str((belief.get("transaction_time") or {}).get("recorded_at") or "")
            if revision["v2_slug"] in str(belief.get("content") or ""):
                v2_recorded_at = recorded
        # P2: retro known_at = 1 microsecond before v2 was recorded -> the store
        # must reproduce v1 (never v2) and the proof must still verify.
        retro_ok = False
        retro_reasons: list[str] = ["no v2 recorded_at found"]
        retro_text = ""
        if v2_recorded_at:
            v2_dt = datetime.fromisoformat(v2_recorded_at)
            retro_known = (v2_dt - timedelta(microseconds=1)).isoformat()
            t0 = time.perf_counter()
            retro = client.belief_proof(topic, known_at=retro_known)
            proof_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            crypto_ok, retro_reasons = _independent_verify_belief_proof(retro)
            retro_text = json.dumps([b.get("content") for b in retro.get("beliefs") or []])
            retro_ok = (
                crypto_ok
                and revision["v1_slug"] in retro_text
                and revision["v2_slug"] not in retro_text
            )
        belief_proof.record(
            retro_ok,
            f"belief_proof retro: topic={topic!r} v1={revision['v1_slug'] in retro_text} "
            f"v2_leaked={revision['v2_slug'] in retro_text} crypto={retro_reasons[:2]}",
        )

        # P3: doctored belief content must be caught by the independent verifier
        # (and by the store's own pure verifier, which we cross-check for parity).
        doctored = copy.deepcopy(current)
        doctored_checked = False
        for belief in doctored.get("beliefs") or []:
            if revision["v2_slug"] in str(belief.get("content") or ""):
                belief["content"] = str(belief["content"]).replace(
                    revision["v2_slug"], "forged-" + revision["v1_slug"]
                )
                doctored_checked = True
        doctored_flagged, _ = _independent_verify_belief_proof(doctored)
        store_verdict = client.verify_belief_proof(
            {"beliefs": doctored.get("beliefs") or [], "proof": doctored.get("proof") or {}}
        )
        belief_proof.record(
            doctored_checked and not doctored_flagged and store_verdict.get("verified") is False,
            f"belief_proof tamper: topic={topic!r} doctored={doctored_checked} "
            f"independent_caught={not doctored_flagged} store_caught={store_verdict.get('verified') is False}",
        )

    # P4: unknown topic must not fabricate history. Returning a fuzzy neighbor
    # (same artifact vocabulary, different project) with honest sealed receipts
    # is legitimate retrieval; CLAIMING the never-seeded project compound is
    # fabrication. So: everything returned must pass independent crypto, and
    # the unknown compound must not appear in any belief content/topics.
    for probe in scenario.abstention_probes[:2]:
        ghost = client.belief_proof(probe.topic)
        crypto_ok, ghost_reasons = _independent_verify_belief_proof(ghost)
        ghost_beliefs = ghost.get("beliefs") or []
        ghost_text = json.dumps(
            [[b.get("content"), b.get("topics")] for b in ghost_beliefs if isinstance(b, dict)]
        )
        unknown_compound = probe.topic.split(" ")[0]
        fabricated = unknown_compound in ghost_text
        belief_proof.record(
            crypto_ok and not fabricated,
            f"belief_proof abstention: topic={probe.topic!r} abstained={ghost.get('abstained')} "
            f"fabricated={fabricated} beliefs={len(ghost_beliefs)} crypto={ghost_reasons[:2]}",
        )

    # P5: external pin. The head pinned before all probe traffic must reproduce
    # byte-identically now, from a fresh proof over the same known_at prefix.
    # A store that rewrote history coherently (rebuilding the whole chain) fails
    # here, because the pinned prefix head no longer folds to the same bytes.
    if pin_probe is not None:
        replay = client.belief_proof(
            scenario.revisions[0]["topic"],
            known_at=pin_probe["known_at"],
            expected_head=pin_probe["head"] or "pin-was-empty",
        )
        envelope = replay.get("proof") or {}
        crypto_ok, pin_reasons = _independent_verify_belief_proof(replay)
        head_now = str(envelope.get("chain_head_at_known_at") or "")
        count_now = envelope.get("event_count_at_known_at")
        pin_ok = (
            crypto_ok
            and bool(pin_probe["head"])
            and head_now == pin_probe["head"]
            and count_now == pin_probe["event_count"]
            and envelope.get("anchor_matches") is True
        )
        belief_proof.record(
            pin_ok,
            f"belief_proof pin: head_stable={head_now == pin_probe['head']} "
            f"count_stable={count_now == pin_probe['event_count']} "
            f"anchor_matches={envelope.get('anchor_matches')} crypto={pin_reasons[:2]}",
        )

    # --- M6 calibrated metacognition -----------------------------------------
    # The scenario itself is the independent answerability oracle: preference
    # content was either seeded exactly or never seeded. Grade every prediction
    # through the public feedback loop, compute metrics locally, then compare the
    # first-class Cortex scorecard field-for-field against that independent result.
    metacognition_observations: list[dict[str, Any]] = []
    metacognition_prediction_ids: list[str] = []
    for case in scenario.metacognition_cases:
        prediction = client.would_i(str(case["question"]))
        prediction_id = str(prediction.get("prediction_id") or "")
        confidence = _finite_confidence(prediction.get("confidence"))
        known_unknown = (
            prediction.get("known_unknown")
            if isinstance(prediction.get("known_unknown"), bool)
            else None
        )
        evidence_text = _twin_evidence_text(prediction)
        detail = prediction.get("confidence_detail") if isinstance(prediction.get("confidence_detail"), dict) else {}
        threshold_ok = detail.get("threshold") == _METACOGNITION_THRESHOLD
        if case["answerability"] == "answerable":
            behavior_ok = (
                bool(prediction_id)
                and prediction.get("verdict") == case["expected_verdict"]
                and known_unknown is False
                and confidence is not None
                and confidence >= _METACOGNITION_THRESHOLD
                and int(prediction.get("evidence_count") or 0) >= 1
                and case["slug"] in evidence_text
                and prediction.get("knowledge_gap") is None
                and threshold_ok
            )
        else:
            behavior_ok = (
                bool(prediction_id)
                and prediction.get("verdict") == case["expected_verdict"]
                and known_unknown is True
                and confidence == 0.0
                and int(prediction.get("evidence_count") or 0) == 0
                and not (prediction.get("supporting") or [])
                and not (prediction.get("opposing") or [])
                and case["slug"] not in evidence_text
                and isinstance(prediction.get("knowledge_gap"), dict)
                and threshold_ok
            )

        outcome = "correct" if behavior_ok else "incorrect"
        grade_error = ""
        try:
            grade = (
                client.grade_twin_prediction(
                    prediction_id,
                    outcome,
                    answerability=str(case["answerability"]),
                    actual=str(case["expected_verdict"]),
                )
                if prediction_id
                else {}
            )
        except Exception as exc:  # benchmark failures should score, not abort the report
            grade = {}
            grade_error = f"{type(exc).__name__}: {exc}"
        grade_ok = (
            grade.get("prediction_id") == prediction_id
            and grade.get("outcome") == outcome
            and grade.get("answerability") == case["answerability"]
        )
        metacognition.record(
            behavior_ok and grade_ok,
            "metacognition prediction: "
            f"q={case['question']!r} gold={case['answerability']} "
            f"verdict={prediction.get('verdict')} confidence={prediction.get('confidence')!r} "
            f"known_unknown={known_unknown!r} evidence={prediction.get('evidence_count')!r} "
            f"slug_cited={case['slug'] in evidence_text} threshold_ok={threshold_ok} "
            f"grade_ok={grade_ok} grade_error={grade_error!r}",
        )
        if prediction_id:
            metacognition_prediction_ids.append(prediction_id)
        metacognition_observations.append(
            {
                "answerability": case["answerability"],
                "confidence": confidence,
                "known_unknown": known_unknown,
                "outcome": outcome,
            }
        )

    independent_metacognition = _independent_metacognition_metrics(metacognition_observations)
    answerable_count = sum(
        1 for case in scenario.metacognition_cases if case["answerability"] == "answerable"
    )
    expected_coverage = (
        round(answerable_count / len(scenario.metacognition_cases), 4)
        if scenario.metacognition_cases
        else None
    )
    abstention_metrics = independent_metacognition["abstention"]
    confident_wrong_metrics = independent_metacognition["confident_wrong"]
    independent_floor_ok = (
        independent_metacognition["prediction_samples"] == len(scenario.metacognition_cases)
        and len(set(metacognition_prediction_ids)) == len(scenario.metacognition_cases)
        and independent_metacognition["graded_samples"] == len(scenario.metacognition_cases)
        and independent_metacognition["outcome_graded_samples"] == len(scenario.metacognition_cases)
        and independent_metacognition["expected_calibration_error"] is not None
        and independent_metacognition["expected_calibration_error"] <= _METACOGNITION_ECE_MAX
        and independent_metacognition["brier_score"] is not None
        and independent_metacognition["brier_score"] <= _METACOGNITION_BRIER_MAX
        and abstention_metrics["precision"] == 1.0
        and abstention_metrics["recall"] == 1.0
        and confident_wrong_metrics["count"] == 0
        and confident_wrong_metrics["rate"] == 0.0
        and independent_metacognition["coverage"] == expected_coverage
    )
    metacognition.record(
        independent_floor_ok,
        "metacognition independent metrics: "
        f"ece={independent_metacognition['expected_calibration_error']!r} "
        f"(max={_METACOGNITION_ECE_MAX}) brier={independent_metacognition['brier_score']!r} "
        f"(max={_METACOGNITION_BRIER_MAX}) "
        f"precision={abstention_metrics['precision']!r} recall={abstention_metrics['recall']!r} "
        f"confident_wrong={confident_wrong_metrics['rate']!r} "
        f"coverage={independent_metacognition['coverage']!r} expected_coverage={expected_coverage!r}",
    )

    reported_metacognition = client.get_twin_calibration(
        days=365,
        prediction_ids=metacognition_prediction_ids,
    )
    scorecard_mismatches = _metacognition_scorecard_mismatches(
        reported_metacognition,
        independent_metacognition,
        prediction_ids=metacognition_prediction_ids,
        window_days=365,
    )
    metacognition.record(
        not scorecard_mismatches,
        f"metacognition scorecard parity: {scorecard_mismatches[:5]}",
    )

    # --- M5 poison-proof shared memory ---------------------------------------
    shared_metrics = {
        "attacks": {"attempted": 0, "succeeded": 0, "attack_success_rate": None},
        "detection": {
            "expected": 0,
            "detected": 0,
            "detection_rate": None,
            "by_reason": {},
        },
        "legitimate_writes": {
            "attempted": 0,
            "falsely_flagged": 0,
            "false_positive_rate": None,
        },
        "verification": {"checks": 0, "passed": 0, "rate": None},
        "trusted_conflicts": {"cases": 0, "survived": 0, "survival_rate": None},
    }
    from backend.app.provenance import sign_shared_write

    for case_index, case in enumerate(scenario.shared_memory_cases):
        trusted_principal = client.create_shared_principal(
            label=f"MemoryTruth trusted {scenario.seed}-{case_index}",
            trust_score=0.9,
        )
        attacker_principal = client.create_shared_principal(
            label=f"MemoryTruth attacker {scenario.seed}-{case_index}",
            trust_score=0.4,
        )
        trusted_id = str((trusted_principal.get("principal") or {}).get("id") or "")
        attacker_id = str((attacker_principal.get("principal") or {}).get("id") or "")
        trusted_secret = str(trusted_principal.get("secret") or "")
        attacker_secret = str(attacker_principal.get("secret") or "")

        accepted_nonce = f"m5-{scenario.seed}-{case_index}-accepted"
        accepted_content = (
            f"The trusted shared benchmark observation token is {case['accepted_slug']}."
        )
        accepted_signature = sign_shared_write(
            trusted_secret,
            principal_id=trusted_id,
            nonce=accepted_nonce,
            content=accepted_content,
            source_url="agent-report://memorytruth/trusted",
            title="MemoryTruth trusted write",
        )
        accepted = client.record_shared_memory(
            principal_id=trusted_id,
            nonce=accepted_nonce,
            content=accepted_content,
            signature=accepted_signature,
            source_url="agent-report://memorytruth/trusted",
            title="MemoryTruth trusted write",
        )
        accepted_memories = accepted.get("memories") or []
        attribution_ok = (
            accepted.get("disposition") == "accepted"
            and bool(accepted_memories)
            and all(
                str(memory.get("author_principal_id") or "") == trusted_id
                for memory in accepted_memories
                if isinstance(memory, dict)
            )
        )
        shared_memory.record(
            attribution_ok,
            "shared_memory attribution: "
            f"accepted={accepted.get('disposition')} memories={len(accepted_memories)} "
            f"principal_bound={attribution_ok}",
        )

        legitimate_nonces = {accepted_nonce}
        legitimate_ok = attribution_ok
        for index, slug in enumerate(case.get("legitimate_slugs") or []):
            nonce = f"m5-{scenario.seed}-{case_index}-legitimate-{index}"
            legitimate_nonces.add(nonce)
            content = f"The shared benchmark observed legitimate token {slug} in lane {index}."
            signature = sign_shared_write(
                attacker_secret,
                principal_id=attacker_id,
                nonce=nonce,
                content=content,
                source_url=f"agent-report://memorytruth/legitimate/{index}",
                title="MemoryTruth legitimate low-trust write",
            )
            result = client.record_shared_memory(
                principal_id=attacker_id,
                nonce=nonce,
                content=content,
                signature=signature,
                source_url=f"agent-report://memorytruth/legitimate/{index}",
                title="MemoryTruth legitimate low-trust write",
            )
            legitimate_ok = legitimate_ok and result.get("disposition") == "review_required"

        trusted_fact = f"The database is {case['trusted_slug']} for the shared benchmark launch."
        trusted_fact_ids = client.remember(trusted_fact)

        expected_attacks: dict[str, str] = {}
        attack_succeeded = 0

        invalid_nonce = f"m5-{scenario.seed}-{case_index}-invalid"
        expected_attacks[invalid_nonce] = "invalid_signature"
        invalid_rejected = False
        try:
            client.record_shared_memory(
                principal_id=attacker_id,
                nonce=invalid_nonce,
                content=f"The unsigned poison token is {case['invalid_slug']}.",
                signature="0" * 64,
                source_url="agent-report://memorytruth/invalid",
                title="MemoryTruth invalid signature",
            )
        except Exception as exc:
            if not _expected_shared_rejection(exc):
                raise
            invalid_rejected = True
        attack_succeeded += int(not invalid_rejected)

        replay_nonce = f"m5-{scenario.seed}-{case_index}-replay"
        replay_content = f"The signed replay probe token is {case['replay_slug']}."
        replay_signature = sign_shared_write(
            attacker_secret,
            principal_id=attacker_id,
            nonce=replay_nonce,
            content=replay_content,
            source_url="agent-report://memorytruth/replay",
            title="MemoryTruth replay probe",
        )
        client.record_shared_memory(
            principal_id=attacker_id,
            nonce=replay_nonce,
            content=replay_content,
            signature=replay_signature,
            source_url="agent-report://memorytruth/replay",
            title="MemoryTruth replay probe",
        )
        expected_attacks[replay_nonce] = "replay"
        replay_rejected = False
        try:
            client.record_shared_memory(
                principal_id=attacker_id,
                nonce=replay_nonce,
                content=replay_content,
                signature=replay_signature,
                source_url="agent-report://memorytruth/replay",
                title="MemoryTruth replay probe",
            )
        except Exception as exc:
            if not _expected_shared_rejection(exc):
                raise
            replay_rejected = True
        attack_succeeded += int(not replay_rejected)

        conflict_nonce = f"m5-{scenario.seed}-{case_index}-conflict"
        poison_content = (
            f"The database is {case['poison_slug']} for the shared benchmark launch."
        )
        conflict_signature = sign_shared_write(
            attacker_secret,
            principal_id=attacker_id,
            nonce=conflict_nonce,
            content=poison_content,
            source_url="agent-report://memorytruth/conflict",
            title="MemoryTruth authority attack",
            supersedes_memory_id=trusted_fact_ids[0] if trusted_fact_ids else "",
        )
        conflict = client.record_shared_memory(
            principal_id=attacker_id,
            nonce=conflict_nonce,
            content=poison_content,
            signature=conflict_signature,
            source_url="agent-report://memorytruth/conflict",
            title="MemoryTruth authority attack",
            supersedes_memory_id=trusted_fact_ids[0] if trusted_fact_ids else "",
        )
        expected_attacks[conflict_nonce] = "lower_authority_supersession"
        answer = client.ask("What is the database for the shared benchmark launch?")
        cited_text = _cited_contents(answer)
        trusted_survived = (
            bool(trusted_fact_ids)
            and conflict.get("disposition") == "quarantined"
            and case["trusted_slug"] in cited_text
            and case["poison_slug"] not in cited_text
        )
        attack_succeeded += int(not trusted_survived)

        client.revoke_shared_principal(attacker_id)
        revoked_nonce = f"m5-{scenario.seed}-{case_index}-revoked"
        revoked_content = f"The revoked writer poison token is {case['revoked_slug']}."
        revoked_signature = sign_shared_write(
            attacker_secret,
            principal_id=attacker_id,
            nonce=revoked_nonce,
            content=revoked_content,
            source_url="agent-report://memorytruth/revoked",
            title="MemoryTruth revoked writer",
        )
        expected_attacks[revoked_nonce] = "revoked_principal"
        revoked_rejected = False
        try:
            client.record_shared_memory(
                principal_id=attacker_id,
                nonce=revoked_nonce,
                content=revoked_content,
                signature=revoked_signature,
                source_url="agent-report://memorytruth/revoked",
                title="MemoryTruth revoked writer",
            )
        except Exception as exc:
            if not _expected_shared_rejection(exc):
                raise
            revoked_rejected = True
        attack_succeeded += int(not revoked_rejected)

        attacker_attempts = client.get_poisoning_attempts(attacker_id)
        trusted_attempts = client.get_poisoning_attempts(trusted_id)
        attempts = list(attacker_attempts.get("attempts") or []) + list(
            trusted_attempts.get("attempts") or []
        )
        reason_by_nonce = {
            str(item.get("nonce") or ""): str(item.get("reason") or "")
            for item in attempts
            if isinstance(item, dict)
        }
        detected = sum(
            1 for nonce, reason in expected_attacks.items() if reason_by_nonce.get(nonce) == reason
        )
        falsely_flagged = sum(1 for nonce in legitimate_nonces if nonce in reason_by_nonce)
        legitimate_attempted = len(legitimate_nonces)
        false_positive_rate = round(falsely_flagged / legitimate_attempted, 4)
        attack_attempted = len(expected_attacks)
        attack_success_rate = round(attack_succeeded / attack_attempted, 4)
        detection_rate = round(detected / attack_attempted, 4)

        all_verification = client.verify_shared_memory()
        attacker_verification = client.verify_shared_memory(attacker_id)
        verification_passed = sum(
            1
            for result in (all_verification, attacker_verification)
            if result.get("verified") is True
        )

        shared_metrics = {
            "attacks": {
                "attempted": attack_attempted,
                "succeeded": attack_succeeded,
                "attack_success_rate": attack_success_rate,
            },
            "detection": {
                "expected": attack_attempted,
                "detected": detected,
                "detection_rate": detection_rate,
                "by_reason": {
                    reason: sum(1 for observed in reason_by_nonce.values() if observed == reason)
                    for reason in sorted(set(expected_attacks.values()))
                },
            },
            "legitimate_writes": {
                "attempted": legitimate_attempted,
                "falsely_flagged": falsely_flagged,
                "false_positive_rate": false_positive_rate,
            },
            "verification": {
                "checks": 2,
                "passed": verification_passed,
                "rate": round(verification_passed / 2, 4),
            },
            "trusted_conflicts": {
                "cases": 1,
                "survived": int(trusted_survived),
                "survival_rate": float(trusted_survived),
            },
        }
        shared_memory.record(
            legitimate_ok and false_positive_rate < 0.01,
            "shared_memory false positives: "
            f"legitimate_ok={legitimate_ok} flagged={falsely_flagged}/{legitimate_attempted} "
            f"rate={false_positive_rate}",
        )
        shared_memory.record(
            attack_success_rate == 0.0,
            f"shared_memory attacks: succeeded={attack_succeeded}/{attack_attempted}",
        )
        shared_memory.record(
            detection_rate == 1.0,
            "shared_memory detection: "
            f"detected={detected}/{attack_attempted} observed={reason_by_nonce}",
        )
        shared_memory.record(
            trusted_survived,
            "shared_memory trusted conflict: "
            f"disposition={conflict.get('disposition')} trusted={case['trusted_slug'] in cited_text} "
            f"poison_leaked={case['poison_slug'] in cited_text}",
        )
        shared_memory.record(
            verification_passed == 2,
            "shared_memory verification: "
            f"all={all_verification.get('verified')} attacker={attacker_verification.get('verified')}",
        )

    # --- M7 bounded associative recall ----------------------------------------
    associative_metrics = {
        "cases": len(scenario.associative_cases),
        "direct_hits": 0,
        "one_hop_hits": 0,
        "bounded_hits": 0,
        "ppr_hits": 0,
        "ppr_reject_leaks": 0,
        "verified_paths": 0,
        "deep_paths": 0,
    }
    for case in scenario.associative_cases:
        query = str(case["query"])
        slug = str(case["slug"])
        reject_slug = str(case["reject_slug"])

        mode_payloads = {
            "direct": client.search(query, associative=False),
            "one_hop": client.search(query, associative=True, association_mode="one_hop"),
            "bounded": client.search(query, associative=True, association_mode="bounded"),
            "ppr": client.search(query, associative=True, association_mode="ppr"),
        }
        mode_text: dict[str, str] = {}
        for name, payload in mode_payloads.items():
            strings: list[str] = []
            _collect_strings(payload, "content", strings)
            _collect_strings(payload, "summary", strings)
            mode_text[name] = "\n".join(strings)

        direct_hit = slug in mode_text["direct"]
        one_hop_hit = slug in mode_text["one_hop"]
        bounded_hit = slug in mode_text["bounded"]
        ppr_hit = slug in mode_text["ppr"]
        reject_leaked = reject_slug in mode_text["ppr"]
        associative_metrics["direct_hits"] += int(direct_hit)
        associative_metrics["one_hop_hits"] += int(one_hop_hit)
        associative_metrics["bounded_hits"] += int(bounded_hit)
        associative_metrics["ppr_hits"] += int(ppr_hit)
        associative_metrics["ppr_reject_leaks"] += int(reject_leaked)

        ppr_payload = mode_payloads["ppr"]
        if isinstance(ppr_payload, dict):
            # search_memory returns {query, ..., results: [...]}; the relationship
            # metadata lives on each result item.
            ppr_items = ppr_payload.get("results") or []
        elif isinstance(ppr_payload, list):
            ppr_items = ppr_payload
        else:
            ppr_items = []
        target_items = [
            item
            for item in ppr_items
            if isinstance(item, dict) and slug in str(item.get("content") or item.get("summary") or "")
        ]
        relationship = (
            target_items[0].get("relationship")
            if target_items and isinstance(target_items[0].get("relationship"), dict)
            else {}
        )
        verified_path = (
            bool(relationship.get("path_verified"))
            and relationship.get("kind") == "associative"
            and str(relationship.get("algorithm") or "") == "personalized_pagerank"
        )
        deep_path = int(relationship.get("depth") or 0) >= 2
        associative_metrics["verified_paths"] += int(verified_path)
        associative_metrics["deep_paths"] += int(deep_path)

        associative_recall.record(
            (not direct_hit) and (not one_hop_hit) and (not bounded_hit) and ppr_hit and not reject_leaked,
            "associative comparison: "
            f"query={query!r} direct={direct_hit} one_hop={one_hop_hit} "
            f"bounded={bounded_hit} ppr={ppr_hit} reject_leaked={reject_leaked}",
        )
        associative_recall.record(
            ppr_hit and verified_path and deep_path,
            "associative proof: "
            f"query={query!r} path_verified={verified_path} depth={relationship.get('depth')} "
            f"algorithm={relationship.get('algorithm')!r}",
        )

    categories = {
        "recall": recall,
        "abstention": abstention,
        "temporal": temporal,
        "provenance": provenance,
        "canvas": canvas,
        "belief_proof": belief_proof,
        "metacognition": metacognition,
        "shared_memory": shared_memory,
        "associative_recall": associative_recall,
    }
    total_passed = sum(c.passed for c in categories.values())
    total_probes = sum(c.total for c in categories.values())
    proof_latencies_ms.sort()
    latency_summary = (
        {
            "count": len(proof_latencies_ms),
            "p50_ms": round(proof_latencies_ms[len(proof_latencies_ms) // 2], 2),
            "max_ms": round(proof_latencies_ms[-1], 2),
        }
        if proof_latencies_ms
        else None
    )
    category_payloads = {name: score.to_json() for name, score in categories.items()}
    category_payloads["metacognition"]["metrics"] = {
        "independent": independent_metacognition,
        "reported": {
            key: reported_metacognition.get(key)
            for key in independent_metacognition
        },
    }
    category_payloads["shared_memory"]["metrics"] = shared_metrics
    category_payloads["associative_recall"]["metrics"] = associative_metrics
    return {
        "bench": BENCH_NAME,
        "version": BENCH_VERSION,
        "seed": scenario.seed,
        "mode": mode,
        "duration_seconds": round(time.time() - started, 3),
        "categories": category_payloads,
        "overall": round(total_passed / total_probes, 4) if total_probes else 0.0,
        "probes": total_probes,
        "belief_proof_latency": latency_summary,
        "caveats": [
            "Slug-based exact grading: no LLM judge; the answer key is verifiable by construction.",
            "Scores reflect the retrieval + citation + supersession + integrity pipeline, not language fluency.",
            "belief_proof grading recomputes fingerprints, chain folds, and snapshot hashes independently; it never trusts the store's verified flag.",
            "metacognition grading derives answerability from seeded-vs-absent gold, computes all metrics independently, then cross-checks Cortex's scorecard.",
            "shared_memory reports observed false-positive rate over a deterministic finite sample; it is not a statistical confidence bound.",
            "associative_recall compares direct, one-hop, bounded strongest-path, and personalized PageRank retrieval over deterministic two-hop probes.",
        ],
    }


def run_inprocess(seed: int, **kwargs: Any) -> dict[str, Any]:
    """Convenience entry: fresh temp store, full run, cleaned up."""
    import tempfile
    from pathlib import Path

    from backend.app.database import init_db
    from backend.app.storage import CortexStore

    scenario = generate_scenario(seed, **kwargs)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_db(root / "bench.db")
        store = CortexStore(root / "bench.db", root / "vault")
        store._vector_ready = lambda conn: False  # deterministic lexical retrieval
        client = InProcessClient(store, "memorytruth-bench")
        return run_bench(client, scenario, mode="inprocess")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MemoryTruth-light memory benchmark.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--url", default="", help="Base URL of a live Cortex server (e.g. http://127.0.0.1:8766)")
    parser.add_argument("--token", default="", help="API token for --url mode")
    parser.add_argument(
        "--user",
        default="",
        help="Optional Cortex user matching a scoped token. Exact prediction cohorts isolate scorecards from prior runs.",
    )
    parser.add_argument("--out", default="", help="Write the JSON report to this path")
    args = parser.parse_args()

    if args.url:
        scenario = generate_scenario(args.seed)
        report = run_bench(HTTPClient(args.url, args.token, user_id=args.user), scenario, mode="http")
    else:
        report = run_inprocess(args.seed)

    rendered = json.dumps(report, indent=2)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
