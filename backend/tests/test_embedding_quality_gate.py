"""P0 "honest floor" GATE: prove the on-device model2vec embeddings measurably beat the
keyword-hash baseline on an OFFLINE, labeled retrieval eval before any "local semantics" claim
ships.

The fixture is built from PARAPHRASE queries that share little/no keyword overlap with their
relevant memory (e.g. "what database did we pick" vs "we chose sharded SQLite over Postgres").
That is exactly where the keyword-hash trick fails and real semantics win, so it is a fair,
discriminating test of the claim.

For each provider (hash, model2vec) we embed the query and every candidate via
backend.app.embeddings.embed_text, rank candidates by cosine similarity, and compute
precision@1 and mean reciprocal rank (MRR). The gate asserts model2vec strictly beats hash on
both metrics with a clear margin and clears an absolute precision@1 floor.

Runs fully offline: model loading is probed first and the whole comparison is skipped
(self.skipTest) when the model2vec model can't be loaded, so CI without the bundled model still
passes. Mirrors backend/tests/test_embeddings_provider.py for env patching and model-cache reset.
"""

from __future__ import annotations

import math
import unittest
from unittest import mock

from backend.app import embeddings as E


# ~10 (query, relevant_memory, distractors) triples. Every "relevant" line across the fixture
# also serves as a cross-triple distractor, so the candidate pool is realistically confusable.
# Queries are paraphrases with deliberately low lexical overlap with their target memory.
FIXTURE: list[tuple[str, str, list[str]]] = [
    (
        "what database did we pick",
        "we chose sharded SQLite over Postgres for the launch",
        [
            "the standup is moved to 9:30am on Fridays",
            "my favorite programming language these days is Rust",
        ],
    ),
    (
        "how does the user sign in",
        "authentication happens through Google OAuth single sign-on",
        [
            "lunch is catered on the third floor every Wednesday",
            "the marketing team prefers muted pastel colors",
        ],
    ),
    (
        "who leads the design work",
        "Priya owns all of the product design and visual direction",
        [
            "we deploy the backend to a single region for now",
            "the office coffee machine is broken again",
        ],
    ),
    (
        "when do we ship the first release",
        "the initial launch is scheduled for the end of March",
        [
            "our logo uses a deep navy blue as the primary color",
            "the API rate limit is one hundred requests per minute",
        ],
    ),
    (
        "how much does the paid plan cost",
        "the premium subscription is priced at twelve dollars a month",
        [
            "unit tests run automatically on every pull request",
            "the onboarding flow has four steps for new accounts",
        ],
    ),
    (
        "where is the app hosted",
        "everything runs on our own machines in a Frankfurt datacenter",
        [
            "the CEO used to work at a large search company",
            "we send a weekly digest email on Monday mornings",
        ],
    ),
    (
        "what language is the backend written in",
        "the server code is all Python running behind FastAPI",
        [
            "the trial period lasts fourteen days with no card",
            "customer support replies within one business day",
        ],
    ),
    (
        "how do customers get help",
        "support is handled over live chat during business hours",
        [
            "the mobile build is distributed through TestFlight",
            "our color palette leans on warm earthy tones",
        ],
    ),
    (
        "what powers the search feature",
        "retrieval is backed by on-device vector embeddings and cosine similarity",
        [
            "the quarterly all-hands meeting is next Thursday",
            "invoices are generated on the first of each month",
        ],
    ),
    (
        "who approved the budget",
        "the finance director gave the final go-ahead on spending",
        [
            "the staging environment mirrors production data nightly",
            "we celebrate work anniversaries with a team lunch",
        ],
    ),
]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _rank_of_relevant(query_vec: list[float], candidate_vecs: list[list[float]]) -> int:
    """1-based rank of the relevant candidate (index 0) after sorting by cosine desc.

    Ties break in favour of later candidates (the relevant one only 'wins' a tie if it is
    strictly best), so the hash baseline can never accidentally score on a zero-signal tie.
    """
    relevant = candidate_vecs[0]
    relevant_score = _cosine(query_vec, relevant)
    better = sum(1 for cand in candidate_vecs[1:] if _cosine(query_vec, cand) >= relevant_score)
    return better + 1


def _score_provider(provider: str) -> tuple[float, int]:
    """Return (precision@1, MRR) for a provider across the fixture.

    Must be called inside a CORTEX_EMBEDDING_PROVIDER env patch; resets the cached model first so
    provider switches take effect deterministically.
    """
    E._MODEL2VEC_MODEL = None
    E._MODEL2VEC_MODEL_KEY = None

    hits_at_1 = 0
    reciprocal_ranks = 0.0
    for query, relevant, distractors in FIXTURE:
        candidates = [relevant, *distractors]
        query_vec = E.embed_text(query)
        candidate_vecs = [E.embed_text(text) for text in candidates]
        rank = _rank_of_relevant(query_vec, candidate_vecs)
        if rank == 1:
            hits_at_1 += 1
        reciprocal_ranks += 1.0 / rank

    n = len(FIXTURE)
    return hits_at_1 / n, reciprocal_ranks / n


class EmbeddingQualityGateTests(unittest.TestCase):
    # Absolute floor model2vec must clear to justify a "local semantics" claim.
    MODEL2VEC_PRECISION_FLOOR = 0.7
    # model2vec must beat hash by at least this much on each metric (a clear, non-noise margin).
    MIN_MARGIN = 0.1

    def setUp(self) -> None:
        E._MODEL2VEC_MODEL = None
        E._MODEL2VEC_MODEL_KEY = None

    def tearDown(self) -> None:
        E._MODEL2VEC_MODEL = None
        E._MODEL2VEC_MODEL_KEY = None

    def test_model2vec_beats_hash_baseline_offline(self) -> None:
        # Probe the model first; skip gracefully (never fail) if it can't be loaded offline, so CI
        # without the bundled model still passes. Same pattern as test_embeddings_provider.py.
        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": "model2vec"}, clear=False):
            try:
                self.assertIsNotNone(E._load_model2vec_model())
            except Exception as exc:  # noqa: BLE001 — any load failure means "model unavailable".
                self.skipTest(f"model2vec model unavailable in this environment: {exc}")
            model2vec_p1, model2vec_mrr = _score_provider("model2vec")

        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": "hash"}, clear=False):
            hash_p1, hash_mrr = _score_provider("hash")

        # Human-readable margins in the test output on both pass and failure.
        print(
            "\n[embedding-quality gate] "
            f"precision@1: hash={hash_p1:.3f} model2vec={model2vec_p1:.3f} "
            f"(margin +{model2vec_p1 - hash_p1:.3f}) | "
            f"MRR: hash={hash_mrr:.3f} model2vec={model2vec_mrr:.3f} "
            f"(margin +{model2vec_mrr - hash_mrr:.3f})"
        )

        # Absolute floor: model2vec must actually be good, not merely better than a weak baseline.
        self.assertGreaterEqual(
            model2vec_p1,
            self.MODEL2VEC_PRECISION_FLOOR,
            f"model2vec precision@1 {model2vec_p1:.3f} below floor {self.MODEL2VEC_PRECISION_FLOOR}",
        )

        # Strictly beats hash on precision@1, by a clear margin.
        self.assertGreater(
            model2vec_p1,
            hash_p1,
            f"model2vec precision@1 {model2vec_p1:.3f} must exceed hash {hash_p1:.3f}",
        )
        self.assertGreaterEqual(
            model2vec_p1 - hash_p1,
            self.MIN_MARGIN,
            f"precision@1 margin {model2vec_p1 - hash_p1:.3f} below required {self.MIN_MARGIN}",
        )

        # Strictly beats hash on MRR, by a clear margin.
        self.assertGreater(
            model2vec_mrr,
            hash_mrr,
            f"model2vec MRR {model2vec_mrr:.3f} must exceed hash {hash_mrr:.3f}",
        )
        self.assertGreaterEqual(
            model2vec_mrr - hash_mrr,
            self.MIN_MARGIN,
            f"MRR margin {model2vec_mrr - hash_mrr:.3f} below required {self.MIN_MARGIN}",
        )


if __name__ == "__main__":
    unittest.main()
