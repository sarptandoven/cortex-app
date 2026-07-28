# Contributing to Cortex

Thanks for helping improve Cortex. The project is a local-first macOS app with a Python backend and
several independently testable integrations. Contributions should preserve user control, citations,
and the boundary between local memory and connected AI tools.

## Choose a contribution

- Start with an unassigned [`good first issue`](https://github.com/trace-cortex/cortex-app/labels/good%20first%20issue)
  or [`help wanted`](https://github.com/trace-cortex/cortex-app/labels/help%20wanted) issue.
- Comment on the issue before beginning substantial work so scope and ownership are clear.
- Keep one pull request focused on one issue.

Do not include personal memory, production vaults, credentials, access tokens, or private chat
exports in issues, fixtures, screenshots, logs, or pull requests. Use synthetic examples.

## Component map

| Area | Location | Primary validation |
|---|---|---|
| Backend and retrieval | `backend/`, `scripts/` | `python3 -m pytest backend/tests -q` |
| macOS app | `macos/` | `./macos/build.sh` |
| Python SDK | `sdk/python/` | `python3 -m pytest sdk/python/tests -q` |
| TypeScript SDK | `sdk/typescript/` | `npm test && npm run typecheck && npm run build` |
| Browser extension | `extension/` | Follow `extension/PUBLISHING.md` |
| Obsidian plugin | `packages/obsidian-cortex-plugin/` | `scripts/check_obsidian_plugin.sh` |
| OpenClaw adapter | `packages/openclaw-cortex-context/` | `npm run typecheck && npm test` |
| Product and protocol docs | `README.md`, `SETUP.md`, `docs/` | `python3 scripts/check_docs_current.py` |

Python 3.11 or newer is required for backend work. The native SwiftUI app requires macOS; backend,
SDK, documentation, and many integration contributions can be developed cross-platform.

## Development workflow

1. Fork the repository and create a descriptive branch from `main`.
2. Follow [`SETUP.md`](SETUP.md) for product setup or work inside the relevant component directory.
3. Add or update tests for behavior changes.
4. Run the smallest relevant checks from the table above, then any broader checks the issue names.
5. Open a pull request that links the issue and lists the commands you ran.

For backend changes, install the test dependencies and run:

```bash
python3 -m pip install -r backend/requirements.txt
python3 -m pip install pytest
python3 -m pytest backend/tests -q
```

For native app changes on macOS, run:

```bash
./macos/build.sh
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
```

## Pull-request expectations

- Explain the user or developer problem and why the chosen scope is sufficient.
- Link the issue with `Closes #...` when the pull request fully resolves it.
- List validation commands and their results.
- Call out changes to local data, permissions, imports, migrations, network access, or AI-tool
  sharing boundaries.
- Include before/after screenshots for visible SwiftUI changes, using synthetic data only.
- Update documentation when behavior or public interfaces change.

Maintainers may ask to narrow a pull request so it remains reviewable and independently testable.
