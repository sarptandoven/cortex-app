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

Run: python3 -m backend.bench.memorytruth --seed 7
     python3 -m backend.bench.memorytruth --url http://127.0.0.1:8766 --token <api-key>
"""

from __future__ import annotations

import argparse
import json
import random
import string
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

BENCH_NAME = "memorytruth-light"
BENCH_VERSION = 1

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

    def to_json(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "facts": self.facts,
            "revisions": self.revisions,
            "abstention_probes": [vars(p) for p in self.abstention_probes],
        }


def generate_scenario(seed: int, *, recall_n: int = 8, abstain_n: int = 6, temporal_n: int = 4) -> Scenario:
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
    return scenario


# --------------------------------------------------------------------------
# Clients: the same scenario drives an in-process store or a live HTTP server.
# --------------------------------------------------------------------------

class BenchClient(Protocol):
    def configure(self) -> None: ...
    def remember(self, content: str) -> list[str]: ...
    def ask(self, question: str) -> dict[str, Any]: ...
    def search(self, query: str) -> Any: ...
    def resolve_conflict(self, stale_id: str, current_id: str) -> bool: ...
    def belief_timeline(self, topic: str) -> dict[str, Any]: ...
    def integrity_digest(self) -> dict[str, Any]: ...
    def verify_integrity(self, expected_head: str) -> dict[str, Any]: ...


class InProcessClient:
    """Drives the real MCP tool dispatch (mcp_tools.call_tool) against a CortexStore."""

    def __init__(self, store: Any, user_id: str) -> None:
        self.store = store
        self.user_id = user_id

    def _tool(self, name: str, args: dict[str, Any]) -> Any:
        from backend.app import mcp_tools

        return mcp_tools.call_tool(self.store, self.user_id, name, args)

    def configure(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False})

    def remember(self, content: str) -> list[str]:
        result = self._tool("remember_this", {"content": content, "source": "note"})
        return [str(m.get("id") or "") for m in (result.get("memories") or []) if m.get("id")]

    def ask(self, question: str) -> dict[str, Any]:
        return self._tool("ask_memory", {"query": question})

    def search(self, query: str) -> Any:
        return self._tool("search_memory", {"query": query, "top_k": 8})

    def resolve_conflict(self, stale_id: str, current_id: str) -> bool:
        return bool(self.store.resolve_conflict(self.user_id, stale_id=stale_id, current_id=current_id))

    def belief_timeline(self, topic: str) -> dict[str, Any]:
        return self._tool("get_belief_timeline", {"topic": topic})

    def integrity_digest(self) -> dict[str, Any]:
        return self._tool("get_memory_integrity", {})

    def verify_integrity(self, expected_head: str) -> dict[str, Any]:
        return self._tool("verify_memory_integrity", {"expected_head": expected_head})


class HTTPClient:
    """Drives a live Cortex standalone/FastAPI server over /v1 - the whole pipeline."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        from urllib import request as _request

        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = _request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        with _request.urlopen(req, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _tool(self, name: str, args: dict[str, Any]) -> Any:
        wrapped = self._request("POST", "/v1/tools/call", {"name": name, "arguments": args})
        return wrapped.get("result") if isinstance(wrapped, dict) and "result" in wrapped else wrapped

    def configure(self) -> None:
        self._request("PUT", "/v1/settings", {"review_new_captures": False})

    def remember(self, content: str) -> list[str]:
        result = self._tool("remember_this", {"content": content, "source": "note"})
        return [str(m.get("id") or "") for m in (result.get("memories") or []) if m.get("id")]

    def ask(self, question: str) -> dict[str, Any]:
        return self._tool("ask_memory", {"query": question})

    def search(self, query: str) -> Any:
        return self._tool("search_memory", {"query": query, "top_k": 8})

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

    # --- Seed simple facts -------------------------------------------------
    for fact in scenario.facts:
        client.remember(fact["content"])

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

    categories = {
        "recall": recall,
        "abstention": abstention,
        "temporal": temporal,
        "provenance": provenance,
    }
    total_passed = sum(c.passed for c in categories.values())
    total_probes = sum(c.total for c in categories.values())
    return {
        "bench": BENCH_NAME,
        "version": BENCH_VERSION,
        "seed": scenario.seed,
        "mode": mode,
        "duration_seconds": round(time.time() - started, 3),
        "categories": {name: score.to_json() for name, score in categories.items()},
        "overall": round(total_passed / total_probes, 4) if total_probes else 0.0,
        "probes": total_probes,
        "caveats": [
            "Slug-based exact grading: no LLM judge; the answer key is verifiable by construction.",
            "Scores reflect the retrieval + citation + supersession + integrity pipeline, not language fluency.",
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
    parser.add_argument("--out", default="", help="Write the JSON report to this path")
    args = parser.parse_args()

    if args.url:
        scenario = generate_scenario(args.seed)
        report = run_bench(HTTPClient(args.url, args.token), scenario, mode="http")
    else:
        report = run_inprocess(args.seed)

    rendered = json.dumps(report, indent=2)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
