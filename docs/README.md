# Cortex Documentation

This index separates the current product contract from operator runbooks,
forward-looking designs, and experimental feature branches. It was last
audited against the checked-out source on 2026-07-30. It does not claim that
unmerged branch content is already in `main`.

## Status Rules

- **Current** documents describe behavior implemented in `main`.
- **Operations** documents describe how the current macOS beta is built,
  verified, distributed, and supported.
- **Design** documents preserve intended architecture or migration paths. They
  are not launch promises unless the code and current-product docs agree.
- **Experimental** documents describe work outside `main`.

When documents disagree, use this order of authority:

1. executable tests and release manifests;
2. current API and application code;
3. current and operations documents;
4. design documents.

## Current Product

| Area | Documentation |
|---|---|
| Architecture and code map | [ARCHITECTURE.md](ARCHITECTURE.md) · [CODE_MAP.md](CODE_MAP.md) |
| API lifecycle and naming | [API_LIFECYCLE.md](API_LIFECYCLE.md) · [NAMING_AND_OWNERSHIP.md](NAMING_AND_OWNERSHIP.md) |
| Product loop and onboarding | [SIMPLE_PRODUCT_LOOP.md](SIMPLE_PRODUCT_LOOP.md) · [FIRST_RUN_ONBOARDING.md](FIRST_RUN_ONBOARDING.md) |
| Local vault | [LOCAL_VAULT_FORMAT.md](LOCAL_VAULT_FORMAT.md) |
| Context protocol | [CMP_PROTOCOL.md](CMP_PROTOCOL.md) · [PORTABLE_MEMORY_PROTOCOL_V2.md](PORTABLE_MEMORY_PROTOCOL_V2.md) |
| Source ingestion and extension | [SOURCE_IMPORTS.md](SOURCE_IMPORTS.md) · [ADDING_A_CONNECTOR.md](ADDING_A_CONNECTOR.md) · [CAPTURE_SURFACES.md](CAPTURE_SURFACES.md) |
| MCP and external tools | [MCP_INTEGRATIONS.md](MCP_INTEGRATIONS.md) · [EXTERNAL_INTEGRATIONS.md](EXTERNAL_INTEGRATIONS.md) |
| Connector readiness | [CONNECTOR_COVERAGE_READINESS.md](CONNECTOR_COVERAGE_READINESS.md) |
| Trust and privacy | [TRUST_CONTROLS.md](TRUST_CONTROLS.md) · [SECURITY_REVIEW.md](SECURITY_REVIEW.md) |
| Benchmarks | [BENCHMARKS.md](BENCHMARKS.md) |
| Open-source readiness | [OPEN_SOURCE_READINESS.md](OPEN_SOURCE_READINESS.md) |
| FAQ and troubleshooting | [FAQ.md](FAQ.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| Optional encrypted sync | [E2EE_SYNC_DESIGN.md](E2EE_SYNC_DESIGN.md) · [CXE1_WIRE_FORMAT.md](CXE1_WIRE_FORMAT.md) |

## Release and Operations

| Area | Documentation |
|---|---|
| Install and updates | [INSTALLER_AND_UPDATES.md](INSTALLER_AND_UPDATES.md) |
| Distribution | [DISTRIBUTION.md](DISTRIBUTION.md) |
| Apple release paths | [APPLE_RELEASE.md](APPLE_RELEASE.md) |
| Operational readiness | [OPERATIONAL_READINESS.md](OPERATIONAL_READINESS.md) |
| Production readiness | [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) |

The canonical source repository is
[`trace-cortex/cortex-app`](https://github.com/trace-cortex/cortex-app). Current
signed binaries are published through
[`doppl-tech/releases`](https://github.com/doppl-tech/releases/releases); the
committed [`site/downloads/latest.json`](../site/downloads/latest.json) is the
source of truth for the downloadable build and its signing state.

## Design and Migration Documents

These preserve future paths and should not be read as proof that a feature is
already enabled:

- [ACCOUNTS_ENCRYPTION_DESIGN.md](ACCOUNTS_ENCRYPTION_DESIGN.md)
- [PHASE2_SYNC_DESIGN.md](PHASE2_SYNC_DESIGN.md)
- [NATIVE_VAULT_PLAN.md](NATIVE_VAULT_PLAN.md)
- [MEMORY_BACKEND_BLUEPRINT.md](MEMORY_BACKEND_BLUEPRINT.md)
- [SQLITE_VEC_BACKEND_PLAN.md](SQLITE_VEC_BACKEND_PLAN.md)
- [DATA_INGESTION_SURVEY.md](DATA_INGESTION_SURVEY.md)

## Experimental Work

Pairwise digital-twin evaluation is implemented and documented on
`feat/pairwise-twin-eval`; it is not part of the current `main` release:

- [Pairwise evaluation overview](https://github.com/trace-cortex/cortex-app/blob/feat/pairwise-twin-eval/docs/PAIRWISE_TWIN_EVALUATION.md)
- [Pairwise integration guide](https://github.com/trace-cortex/cortex-app/blob/feat/pairwise-twin-eval/docs/PAIRWISE_TWIN_INTEGRATION_GUIDE.md)

The feature remains experimental until its owner study and production-admission
criteria are satisfied.
