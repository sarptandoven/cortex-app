# Troubleshooting

## `make setup` says Python 3.12 is missing

The macOS system `python3` may be 3.9 and is not supported by this repository.
Install Python 3.12 and point setup at it:

```bash
make setup PYTHON=/absolute/path/to/python3.12
```

Verify without creating a virtual environment:

```bash
PYTHON_BIN=/absolute/path/to/python3.12 ./scripts/bootstrap_dev.sh --check
```

## `make run` cannot import FastAPI or uvicorn

Run `make setup` first. `make run` intentionally uses `.venv/bin/python` instead
of silently falling back to the system interpreter.

## Port 8766 is already in use

The packaged app may already be running. Either quit Cortex or use a different
development port:

```bash
CORTEX_PORT=8877 make run
```

Keep clients pointed at the same base URL.

## The app opens but the full local engine is unavailable

The default `./macos/build.sh` output is a development/UI build without the
bundled Python interpreter or Model2Vec weights. A release-like build requires
the Python 3.12 framework and `CORTEX_BUNDLE_PYTHON=1`; follow
[APPLE_RELEASE.md](APPLE_RELEASE.md). Do not distribute an ad-hoc development
build as if it were the notarized release.

## First launch is blocked by sign-in

The direct build sets `CortexRequireAccount=true`. Confirm the Mac can reach the
configured hosted API and that system time is correct. The sample-notes preview
is temporary and does not disable the requirement for later launches.

## Ask returns an abstention or empty result

1. Confirm the source shows a successful sync.
2. Approve relevant pending items in Review.
3. Ask a specific question using terms present in the source.
4. Check storage/retrieval health in Connections & Privacy.

An abstention is expected when Cortex cannot attach relevant citations.

## Semantic retrieval reports the hash fallback

Development builds may not contain the Model2Vec package or weights. Release
packaging enforces the real model. Use `scripts/check_vector_runtime.py` against
the packaged app before making semantic-retrieval claims.

## An MCP client cannot connect

- Confirm Cortex is running and `/health` responds on loopback.
- Reinstall the integration from Connections & Privacy.
- Restart the client after its config changes.
- Check that the token is scoped for the requested operation.
- Never paste the token into a public issue.

See [MCP_INTEGRATIONS.md](MCP_INTEGRATIONS.md) for client-specific paths.

## A connector sign-in option is missing

GitHub device flow is configured. Google, Microsoft, and Notion managed OAuth
flows require client IDs that are empty in the current direct build. Use the
documented token/export fallback; see
[CONNECTOR_COVERAGE_READINESS.md](CONNECTOR_COVERAGE_READINESS.md).

## I need help without sharing memory

Generate a sanitized support bundle from Connections & Privacy or:

```bash
.venv/bin/python scripts/export_support_bundle.py --mode live
```

Review it before sharing. Never attach the vault, raw exports, or credentials.
See [SUPPORT.md](../SUPPORT.md).
