# Open-Source Readiness Review

Last reviewed: 2026-07-30

Review posture: independent, reject by default

Review baseline: source commit `8eedd5c` on the local
`docs/repo-refresh` branch. The corrections described below are the review
delta from that baseline; this document must be updated with a new baseline
before it is reused as evidence for a later release.

This is an evidence-based engineering review of the checked-out source tree,
not a security certification. It used local source and local test tooling only;
it did not require repository administration, hosted secrets, or live
connector accounts.

## Verdict

**Accept for open-source development and external code review. Reject for an
unqualified production-hosted launch.**

The repository now has credible local setup, tests, examples, security
boundaries, and operator documentation. Production approval is still blocked
by unverified external controls and by architectural work that should not be
hidden behind documentation claims.

## What Was Corrected

- Cross-origin redirects can no longer carry first-party connector, OAuth,
  model-provider, delivery, OIDC, or SDK bearer credentials to another origin.
- Webhook delivery connects to the exact public address that passed validation,
  retaining the original hostname for HTTP and TLS verification.
- All vault mutations now participate in the same inter-process freeze used by
  backup, restore, and account deletion. Regression tests cover aggregate
  multi-process writes, ordinary record writes, and direct Markdown pruning.
- ZIP imports enforce both member-count and aggregate uncompressed-byte
  budgets, including nested archives.
- Password hashing concurrency and in-process rate-limiter cardinality are
  bounded.
- Hosted readiness fails closed when credential encryption enforcement or its
  runtime keyring is absent, the complete credential scan is truncated or
  unreadable, or any plaintext credential remains.
- Large synchronous exports use a conservative Unicode-aware storage preflight,
  default to a 25 MB source-data cap, and fail with a bounded `413` response
  instead of materializing an unbounded corpus.
- Backup snapshots coordinate with vault writers and publish atomically.
- Public signup fails closed until the operator records legal approval; password
  and OAuth signup consent is explicit and OAuth consent is bound into the
  single-use state.
- `cxa_` HTTP API tokens work on the REST-style `/v1/tools/*` surface while
  `/mcp` remains restricted to `cxm_` MCP tokens.
- `POST /v1/context` is typed and bounded. OpenAPI operation IDs are stable
  method/path identifiers, unique in tests, and protected routes publish a
  bearer security scheme.
- Mac mini setup uses a dedicated Python 3.12 virtual environment.
- Examples include an idempotent, loopback-only synthetic seed.
- Privacy and terms copy no longer says all memory content is encrypted when
  the implementation currently guarantees encryption only for hosted
  connector credentials.
- SDK/plugin licenses and token examples now agree with repository policy.

## Priority Findings

| Priority | Finding | Current disposition |
|---|---|---|
| P0 | Multi-process vault read-modify-write lost data | Fixed and regression-tested |
| P0 | Public privacy copy overstated encryption scope | Corrected; legal approval still required |
| P1 | Credential headers could cross origins on redirects | Fixed across backend and SDK transports |
| P1 | Hosted credential encryption could fail open | Readiness now requires complete runtime scan evidence and zero remaining plaintext records |
| P1 | ZIP and export paths could exhaust memory | ZIP fixed; synchronous export bounded; streaming/background export remains future work |
| P1 | Argon2 bursts could exhaust process memory | Per-process concurrency bounded; deployment worker count still needs capacity planning |
| P1 | Backup could race with vault writes | Fixed with coordinated locks and atomic archive publication |
| P1 | OAuth could create an account without explicit current legal consent | Fixed with signup gating and consent-bound single-use state |
| P1 | Webhook DNS could change after SSRF validation | Fixed by pinning the validated address through the connection |
| P1 | Existing memory-count quota is check-before-write, raceable, and batch/queue blind; stored bytes are unbounded | Open production blocker; move reservation/enforcement into the storage transaction |
| P1 | Export preflight and serialization do not share one database snapshot; portable proof re-exports | Open consistency and memory-safety blocker |
| P1 | Signup consent is not stored with policy version/hash and acceptance time | Open legal-audit blocker |
| P1 | `backend/app/storage.py` is an oversized subsystem with broad responsibilities | Open maintainability risk; split only with characterization tests |
| P2 | Python runtime dependencies allow version ranges | Open reproducibility risk; audit now covers the runtime manifest |
| P2 | Several GitHub Actions use mutable major-version tags | Open supply-chain hardening item; pin reviewed commit SHAs |

## Evidence Required Before Production Approval

1. Replace route-level memory quota checks with atomic storage-transaction
   reservation/enforcement based on actual new active IDs; cover concurrent
   captures, multi-memory extraction, sync batches, idempotent upserts, and
   queued jobs. Add an enforced stored-byte quota as part of the same control.
2. Run the credential migration and retain release evidence that hosted
   readiness observed a complete, readable scan with zero plaintext records.
3. Put export estimate, filtering, imports, stats, payload, and portable proof
   on one database snapshot; derive the proof from the already-built payload,
   then replace large in-memory exports with streaming or background jobs.
4. Persist the accepted Terms/Privacy version, policy hash, age assertion, and
   acceptance timestamp on the account and in the audit event.
5. Characterize and split the storage/API god modules without changing their
   contracts.
6. Lock packaged runtime dependencies and pin third-party CI actions to reviewed
   commit SHAs.
7. Have counsel approve legal entity, jurisdiction, terms, and privacy copy.
8. Exercise OAuth, email delivery, domains, and every supported connector with
   release-owned accounts.
9. Complete an encrypted backup/restore drill with separately escrowed keys.
10. Verify signed artifacts, notarization, update metadata, and rollback on the
   actual release channel.
11. Run load/soak tests using the intended worker count, storage topology, and
    realistic corpus sizes.

## Review Standard

Production approval requires evidence, not configuration-shaped objects or
optimistic prose. A test that simulates a keyring is useful for contract
coverage but is not proof that a deployment holds the right key. Likewise,
synthetic retrieval benchmarks guard regressions but do not establish product
quality for a diverse user population.
