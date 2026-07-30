# Cortex Benchmarks

Cortex treats evaluation as executable product behavior, not a marketing
number. The repository ships deterministic, offline harnesses for retrieval,
context packing, adaptation, token calibration, routing, and end-to-end agent
tasks. They run on synthetic fixtures with known answers so regressions fail in
CI without sending personal data to an external model.

## Latest reproducible run

Run on 2026-07-30 with Python 3.12 on Apple Silicon. All commands used the
repository's checked-in fixtures and made no model API calls.

| Gate | Cases / checks | Result | Selected metrics |
|---|---:|---|---|
| Retrieval | 163 labeled queries | Pass | top-1 accuracy 1.000; recall@3 1.000; precision@3 0.947 |
| Relevance monotonicity | 160 reordered cases / 41 pairs | Pass | pairwise concordance 1.000; reordered top-1 1.000 |
| Context packing | 10 tasks / 30 checks | Pass | citation coverage 1.000; MRR 1.000; nDCG@k 0.888; no-leak 1.000; budget adherence 1.000 |
| Session replay | 2 sessions / 9 turns | Pass | recall@3 1.000; prefetch hit rate 0.750; delta token savings 28.5%; no resend/leak 1.000 |
| Adaptation | 7 memories / 10 derived rules | Pass | readiness score 100 |
| Token calibration | Claude + Cursor profiles | Pass | mean profile MAPE 0.0158 vs flat-estimator MAPE 0.0743 |

## Reproduce

```bash
make setup
.venv/bin/python scripts/retrieval_eval.py
.venv/bin/python scripts/context_pack_eval.py
.venv/bin/python scripts/adaptation_eval.py
.venv/bin/python scripts/token_calibration_eval.py
```

`make check` runs the fast pre-PR subset. The full CI workflow adds routing,
adapter, delivery, agent-task, connector, docs, security, and packaging gates.

## What these numbers mean

- **Top-1 accuracy / recall** measure whether the known relevant fixture is
  returned at the expected rank.
- **Citation coverage** requires packed evidence to retain provenance.
- **No-leak** fails when a deliberately disallowed cross-project memory appears.
- **Budget adherence** checks the serialized context against its token budget.
- **Session token savings** compares delta delivery with naively resending a
  full context pack every turn.
- **Calibration MAPE** measures the error between a profile's estimate and the
  harness token count.

## Limitations

These are deterministic regression benchmarks, not proof of real-world answer
quality. The fixtures are synthetic, comparatively small, and shaped around
known Cortex behaviors. The token-calibration harness uses simulated reference
counts rather than live vendor tokenizers. Runtime varies by hardware and warm
cache state, so this page does not claim a production latency percentile.

Before making broad quality claims, Cortex still needs:

1. a versioned, consented corpus of real user tasks;
2. blinded human relevance judgments;
3. cold- and warm-cache latency percentiles on release hardware;
4. scale curves across vault sizes and concurrent hosted tenants; and
5. confidence intervals across repeated runs where nondeterminism is enabled.

When results are published, record the commit, fixture version, hardware,
configuration, repetitions, and raw output. Do not compare numbers produced by
different harness versions as if they were the same experiment.
