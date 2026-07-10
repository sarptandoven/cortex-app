"""Full-pipeline proof: MemoryTruth-light against a LIVE standalone server.

This is the "entire pipeline of data from start to finish" check. Where
test_memorytruth_bench.py drives an in-process store, this module boots the
REAL standalone server (backend.app.standalone_server) on a loopback port and
runs the same benchmark over HTTP. One green run proves, in a single pass:

  HTTP auth (bearer token) -> /v1/tools/call dispatch -> mcp_tools.call_tool
  -> extractor -> storage (SQLite) -> vault (Markdown) -> retrieval
  -> the cite-or-abstain gate -> supersession -> belief timelines
  -> the integrity chain -> JSON back over the wire

with the exact same graded floors as the in-process run. If any layer bends
the data (drops author_class, breaks citations, loses a revision), a category
score falls below its floor and the failure names the probe.

The server is booted in-process on a daemon thread with a temp DB/vault and a
test API key, mirroring how test_standalone_server.py isolates module state.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

MODULE_TMP = tempfile.TemporaryDirectory()
_PRIOR_ENV: dict[str, str | None] = {}


def _set_env(key: str, value: str) -> None:
    _PRIOR_ENV[key] = os.environ.get(key)
    os.environ[key] = value


# Environment must be pinned BEFORE importing the server module: it reads
# settings at import time (module-level `settings = load_settings()`).
_set_env("CORTEX_DB_PATH", str(Path(MODULE_TMP.name) / "pipeline.sqlite"))
_set_env("CORTEX_VAULT_PATH", str(Path(MODULE_TMP.name) / "pipeline.vault"))
_set_env("CORTEX_API_KEY", "memorytruth-pipeline-token")

from backend.app import standalone_server  # noqa: E402  (env first)
from backend.bench.memorytruth import HTTPClient, generate_scenario, run_bench  # noqa: E402


def tearDownModule() -> None:
    for key, value in _PRIOR_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    MODULE_TMP.cleanup()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class MemoryTruthLivePipelineTests(unittest.TestCase):
    """Boot the real server once; run the bench over real HTTP."""

    server: "standalone_server.ThreadingHTTPServer"
    port: int

    @classmethod
    def setUpClass(cls) -> None:
        # The bench's temporal probes drive /v1/memory/conflicts/resolve and its
        # store writes go through the exact handler stack the packaged app ships.
        cls.port = _free_port()
        cls.server = standalone_server.ThreadingHTTPServer(
            ("127.0.0.1", cls.port), standalone_server.CortexRequestHandler
        )
        thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_live_server_holds_every_floor(self) -> None:
        client = HTTPClient(
            f"http://127.0.0.1:{self.port}",
            "memorytruth-pipeline-token",
            timeout=60.0,
        )
        report = run_bench(client, generate_scenario(7), mode="http")
        self.assertEqual(report["mode"], "http")
        for category, payload in report["categories"].items():
            self.assertEqual(
                payload["score"],
                1.0,
                msg=f"live-pipeline {category} broke its floor: {payload['failures']}",
            )
        self.assertEqual(report["overall"], 1.0)

    def test_http_and_inprocess_agree(self) -> None:
        """The transport must not change the verdict: the same scenario scored
        over HTTP and in-process (fresh store) produces the same category scores.
        This pins server dispatch as a faithful proxy of the storage API."""
        from backend.app.database import init_db
        from backend.app.storage import CortexStore
        from backend.bench.memorytruth import InProcessClient

        scenario = generate_scenario(21)
        http_report = run_bench(
            HTTPClient(f"http://127.0.0.1:{self.port}", "memorytruth-pipeline-token", timeout=60.0),
            scenario,
            mode="http",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_db(root / "bench.db")
            store = CortexStore(root / "bench.db", root / "vault")
            store._vector_ready = lambda conn: False
            local_report = run_bench(InProcessClient(store, "bench-user"), scenario, mode="inprocess")

        http_scores = {name: payload["score"] for name, payload in http_report["categories"].items()}
        local_scores = {name: payload["score"] for name, payload in local_report["categories"].items()}
        self.assertEqual(http_scores, local_scores)
        self.assertEqual(
            http_report["categories"]["shared_memory"]["metrics"],
            local_report["categories"]["shared_memory"]["metrics"],
        )

    def test_two_consecutive_live_runs_are_cohort_isolated_and_retry_safe(self) -> None:
        """A real token may already have history and may exhaust its initial burst.
        Exact prediction cohorts prevent score contamination; bounded Retry-After
        handling lets the second run honor the server limiter instead of failing."""
        client = HTTPClient(
            f"http://127.0.0.1:{self.port}",
            "memorytruth-pipeline-token",
            timeout=60.0,
        )
        for seed in (99, 1234):
            with self.subTest(seed=seed):
                report = run_bench(client, generate_scenario(seed), mode="http")
                self.assertEqual(report["categories"]["metacognition"]["score"], 1.0)
                independent = report["categories"]["metacognition"]["metrics"]["independent"]
                reported = report["categories"]["metacognition"]["metrics"]["reported"]
                self.assertEqual(reported, independent)
                shared = report["categories"]["shared_memory"]
                self.assertEqual(shared["score"], 1.0)
                self.assertEqual(shared["metrics"]["attacks"]["attack_success_rate"], 0.0)
                self.assertEqual(shared["metrics"]["detection"]["detection_rate"], 1.0)
                self.assertEqual(
                    shared["metrics"]["legitimate_writes"]["false_positive_rate"],
                    0.0,
                )
                self.assertEqual(shared["metrics"]["verification"]["rate"], 1.0)
                self.assertEqual(
                    shared["metrics"]["trusted_conflicts"]["survival_rate"],
                    1.0,
                )


if __name__ == "__main__":
    unittest.main()
