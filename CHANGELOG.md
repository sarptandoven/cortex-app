# Changelog

Notable user-facing and contributor-facing changes are recorded here. Cortex is
in beta; compatibility guarantees will tighten as the public API stabilizes.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Reproducible Python 3.12 contributor bootstrap and root `make` commands.
- Open-source contribution, security, support, conduct, roadmap, and issue/PR
  guidance.
- Runnable Python SDK examples for search, memory workflows, multi-agent
  context, and tool discovery.
- Benchmark documentation with deterministic regression metrics, commands, and
  explicit evidence limitations.
- CI gates for the Python and TypeScript SDKs and the OpenClaw context plugin.
- A generated OpenAPI contract test and explicit public API lifecycle policy.
- Deterministic local example data for validating the first-run search flow.
- A one-command disposable `make demo` path and read-only `make doctor`
  prerequisite check.
- A packaged-standalone runtime smoke gate, connector authoring guide, and
  contributor code map.
- Full CMP context options in both SDKs, including SMP/Markdown projections,
  model profiles, session deltas, pinning, project/sector scopes, and `as_of`.

### Changed

- Repository documentation now distinguishes implemented capabilities from
  release-configured capabilities.
- The archived Redis/Voyage/Streamlit prototype now lives under `legacy/`
  instead of competing with current entry points at repository root.
- SDK and plugin metadata points to `trace-cortex/cortex-app`.
- Hosted deployment, Apple release, account, offline, update, and legal
  documentation now matches the behavior in code.
- `POST /v1/context` now returns the same identity fields as the corresponding
  read-scoped `GET` request; exporting identity remains a separate permission.
- Mac mini installation uses a dedicated Python 3.12 virtual environment and
  the current public API hostname.
- Evaluation commands print concise summaries by default and expose full
  per-case diagnostics through `--json`.
- Dependency-free retrieval recognizes bounded owner/DRI and
  launch/release/rollout paraphrases and demotes explicit non-answer templates.
- Vector-index compatibility now fingerprints provider, model/revision, native
  dimensions, local asset identity, and embedding text recipe; same-dimension
  model swaps rebuild and re-embed instead of mixing vector spaces.

### Security

- Hosted runtimes now reject local path imports, file-backed connectors, and
  untrusted connector origins at both HTTP and storage execution boundaries.
- Hosted capture rejects secrets and content in query strings.
- Deployment backups are encrypted with `age`, exclude environment secrets,
  use restrictive filesystem permissions, and fail closed without a recipient.
- Public deployment examples default to verified email and disabled automatic
  account verification.
- Credential-bearing HTTP clients now refuse cross-origin redirects.
- Hosted readiness fails closed unless connector-credential encryption is
  enforced and an active keyring is available.
- Password hashing concurrency and in-process rate-limiter cardinality are
  bounded to reduce memory-exhaustion risk.
- ZIP imports enforce an aggregate uncompressed-byte budget.
- Vault read-modify-write operations and backup snapshots use an inter-process
  lock, preventing silent data loss with multiple workers.
- Synchronous exports reject corpora above a configurable preflight size
  instead of constructing multiple unbounded in-memory copies.

## [0.2.0] - 2026-07-24

Release build `51` was produced from source commit
[`0f7e33ec58264b9694f4219379d7240313d9936b`](https://github.com/trace-cortex/cortex-app/commit/0f7e33ec58264b9694f4219379d7240313d9936b)
on `feat/connect-gate-redesign`, as recorded in the published update manifest.

### Added

- Developer ID signed and Apple-notarized direct-download beta.
- Local cited memory retrieval, Review, Ask, MCP integrations, connector
  contracts, local vault recovery, and support diagnostics.
- Python and TypeScript SDK source packages.

### Security

- Scoped MCP capabilities, default redaction, audit trails, and tamper-evident
  memory history.

[Unreleased]: https://github.com/trace-cortex/cortex-app/compare/0f7e33ec58264b9694f4219379d7240313d9936b...HEAD
[0.2.0]: https://github.com/doppl-tech/releases/releases/tag/v0.2.0-51
