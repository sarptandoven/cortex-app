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

- Git and Make
- Python 3.12
- Node.js 18+ only for TypeScript SDK work; Node.js 22 for Obsidian/OpenClaw
- Xcode command line tools only for macOS app work

```bash
git clone https://github.com/trace-cortex/cortex-app.git
cd cortex-app
make doctor
make demo
```

`make demo` runs `make setup` automatically when `.venv` is missing, then
proves capture → retrieval → cited Ask against disposable synthetic data.
`make setup` creates `.venv` and fails with an actionable message if the chosen
interpreter is not Python 3.12. It installs the exact versions in
`requirements-dev.lock` and verifies every downloaded artifact hash;
dependency updates should refresh that file
intentionally. Use an explicit interpreter when necessary:

```bash
make setup PYTHON=/absolute/path/to/python3.12
```

Start the local FastAPI development API:

```bash
make run
```

This uses `backend/data/Cortex.vault/`, not the installed application's
personal vault. If port 8766 is occupied, the command prints an available port
and the matching `CORTEX_BASE_URL` export.

For the macOS app:

```bash
./macos/build.sh
open macos/build/Cortex.app
```

The default app build is a development bundle without the release interpreter
or embedding model. See [SETUP.md](SETUP.md) and
[docs/APPLE_RELEASE.md](docs/APPLE_RELEASE.md) for release-like builds.

## Good First Contributions

You can contribute without an account or a personal Cortex vault. Useful,
contained starting points include:

- reproduce an installation failure and improve the diagnostic or
  troubleshooting step;
- add a synthetic import fixture and a parser regression test;
- make one runnable example easier to understand without tying it to a model
  provider;
- add an adversarial retrieval or privacy case to an existing evaluation
  harness; or
- fix a broken, ambiguous, or stale documentation path.

If your change adds a source, follow [Adding a Cortex
Connector](docs/ADDING_A_CONNECTOR.md). For unfamiliar areas, the
[contributor code map](docs/CODE_MAP.md) links product surfaces to their first
implementation file and focused test.

For documentation-only work, `make docs-check` is the relevant local gate.
For code changes, open or reference an issue first when public behavior,
storage, security, or an API contract would change. If you are unsure whether
an idea fits, a focused feature request with a concrete use case is welcome.

## What to Test

Run the smallest relevant set while developing, then the pre-PR gate. The full
backend suite currently takes roughly ten minutes on a laptop; it is not the
first command a new contributor needs to run.

```bash
make check
make test
```

Choose additional checks by the files you changed:

```bash
# Documentation only
make docs-check

# Connectors
make connector-check

# Packaged standalone runtime
make runtime-check

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

`make check` is the required fast pre-PR gate and includes the public examples
against both FastAPI and the packaged standalone runtime. Run `make test` for a
broad backend change; an SDK-only change may use its focused suite plus
`make check`.

### Refreshing the Python lock

`backend/requirements.txt` contains the direct runtime requirements.
`requirements-dev.lock` freezes their complete Python 3.12 dependency graph
plus pytest for reproducible contributor and CI installs.
`backend/requirements.lock` freezes the hosted/API runtime, while
`backend/runtime-requirements.lock` freezes the dependency-light runtime
bundled into the macOS app. All three locks include PyPI artifact hashes.

Regenerate both locks in an isolated temporary environment, then review every
version and hash change:

```bash
make lock-python
make setup
make check
```

`make lock-python` pins its temporary lock compiler too, so generated output is
stable across contributor machines. Do not refresh locks as an unrelated side
effect of another change.

## Engineering Expectations

- Follow `.editorconfig`: UTF-8, LF endings, final newlines, four-space Python
  and Swift indentation, and two-space JSON/YAML/TypeScript indentation.
- Use type hints on new Python public boundaries and descriptive names over
  abbreviations. Cortex does not yet impose a repository-wide autoformatter;
  avoid unrelated mechanical reformatting.
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
