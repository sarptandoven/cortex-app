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
- Reproducible benchmark documentation with metrics, commands, and explicit
  limitations.
- CI gates for the Python and TypeScript SDKs and the OpenClaw context plugin.

### Changed

- Repository documentation now distinguishes implemented capabilities from
  release-configured capabilities.
- SDK and plugin metadata points to `trace-cortex/cortex-app`.
- Hosted deployment, Apple release, account, offline, update, and legal
  documentation now matches the behavior in code.

### Security

- Hosted runtimes now reject local path imports, file-backed connectors, and
  untrusted connector origins at both HTTP and storage execution boundaries.
- Hosted capture rejects secrets and content in query strings.
- Deployment backups are encrypted with `age`, exclude environment secrets,
  use restrictive filesystem permissions, and fail closed without a recipient.
- Public deployment examples default to verified email and disabled automatic
  account verification.

## [0.2.0] - 2026-07-24

### Added

- Developer ID signed and Apple-notarized direct-download beta.
- Local cited memory retrieval, Review, Ask, MCP integrations, connector
  contracts, local vault recovery, and support diagnostics.
- Python and TypeScript SDK source packages.

### Security

- Scoped MCP capabilities, default redaction, audit trails, and tamper-evident
  memory history.

[Unreleased]: https://github.com/trace-cortex/cortex-app/compare/v0.2.0-51...HEAD
[0.2.0]: https://github.com/trace-cortex/cortex-app/releases/tag/v0.2.0-51
