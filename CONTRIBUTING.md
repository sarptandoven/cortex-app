# Contributing to Cortex

Thank you for helping make personal AI memory more trustworthy, portable, and
useful. This guide is the shortest path from a fresh clone to a reviewable pull
request.

## Before You Start

- Use an issue for changes that alter public behavior, storage formats, security
  boundaries, or APIs.
- Keep pull requests focused. Separate refactors from behavior changes.
- Never commit real memory, exports, tokens, vaults, support bundles, or user
  identifiers.
- Report vulnerabilities privately through [SECURITY.md](SECURITY.md).

## Development Setup

Requirements:

- Python 3.12
- Xcode command line tools for the macOS app
- Node.js 22 for the Obsidian and OpenClaw packages

```bash
git clone https://github.com/trace-cortex/cortex-app.git
cd cortex-app
make setup
make check
```

`make setup` creates `.venv` and fails with an actionable message if the chosen
interpreter is not Python 3.12. It installs the exact versions in
`requirements-dev.lock`; dependency updates should refresh that file
intentionally. Use an explicit interpreter when necessary:

```bash
make setup PYTHON=/absolute/path/to/python3.12
```

Start the hosted-plane development API:

```bash
make run
```

For the macOS app:

```bash
./macos/build.sh
open macos/build/Cortex.app
```

The default app build is a development bundle without the release interpreter
or embedding model. See [SETUP.md](SETUP.md) and
[docs/APPLE_RELEASE.md](docs/APPLE_RELEASE.md) for release-like builds.

## What to Test

Run the smallest relevant set while developing, then the pre-PR gate:

```bash
make test
make connector-check
make check
```

Additional surfaces:

```bash
# Python SDK
.venv/bin/python -m pytest sdk/python/tests -q

# TypeScript SDK
cd sdk/typescript && npm ci && npm run typecheck && npm test

# Obsidian plugin
scripts/check_obsidian_plugin.sh

# macOS app
./macos/build.sh
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
```

CI is authoritative. If a check is too expensive or platform-specific to run
locally, say exactly what you did run in the pull request.

## Engineering Expectations

- Preserve local-first defaults and cite-or-abstain behavior.
- Treat the vault as authoritative and SQLite/vector indexes as rebuildable.
- Keep local and hosted HTTP surfaces behaviorally aligned.
- Add behavioral tests for bug fixes; avoid tests that only snapshot
  implementation details.
- Keep capability checks at the execution boundary, not only in the UI.
- Use temporary vaults and databases in tests.
- Update the README or current-product docs when public behavior changes.
- Label forward-looking designs as designs rather than shipped behavior.

Architecture and trust boundaries are documented in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/LOCAL_VAULT_FORMAT.md](docs/LOCAL_VAULT_FORMAT.md), and
[docs/TRUST_CONTROLS.md](docs/TRUST_CONTROLS.md).

## Pull Requests

1. Branch from current `main`.
2. Make one coherent change with tests and docs.
3. Run the relevant checks.
4. Complete the pull-request template with evidence and risk notes.
5. Respond to review without force-pushing away useful review history.

By participating, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
