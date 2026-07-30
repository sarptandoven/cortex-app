# Publishing the Cortex SDKs

Both packages are release-ready and their build artifacts are validated (a Python wheel builds; the
npm tarball packs; the TypeScript client has a passing test suite — see `sdk/typescript/test/`).
The steps below are the **public publish** — run them with your own PyPI / npm accounts. Nothing
here is automated in CI: `.github/workflows/publish-sdks.yml` exists but is `workflow_dispatch`
ONLY (manual trigger from the Actions tab), and no automation in this repo ever runs an upload
command. Publishing is an intentional, credentialed, founder-gated action.

Bump the version in `python/pyproject.toml` and `typescript/package.json` together before each release.

## Package names (resolved 2026-07-11)

Both names below were verified available (unregistered on PyPI / npm; the npm scope
`@doppl-tech` is unclaimed) at the time of writing — re-check before your first publish in case
that has changed:

| Package | Name | Registry check |
| --- | --- | --- |
| Python | `doppl-cortex-client` | `curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/doppl-cortex-client/json` → expect `404` (available) |
| TypeScript | `@doppl-tech/cortex-client` | `npm view @doppl-tech/cortex-client` → expect `404 Not Found` (available) |

The original placeholders (`cortex-client` on PyPI, `@cortex/client` on npm) were both
**already taken by unrelated parties** — `@cortex` and `@cortex-memory` are both claimed npm
scopes owned by other accounts. `@doppl-tech` is the project's package and signed-release
namespace. Source provenance points to
[`trace-cortex/cortex-app`](https://github.com/trace-cortex/cortex-app), while binary artifacts
are published from `doppl-tech/releases`.

## Python — PyPI (`doppl-cortex-client`)

```bash
cd sdk/python
python3 -m pip install --upgrade build twine
python3 -m build                     # produces dist/*.whl + dist/*.tar.gz
python3 -m twine check dist/*         # validate metadata
# Test first (recommended):
python3 -m twine upload --repository testpypi dist/*
# Then real:
python3 -m twine upload dist/*        # prompts for your PyPI token
```
Verify: `pip install doppl-cortex-client` in a clean venv, then
`python -c "from cortex_client import CortexClient"` (the *import* name stays `cortex_client`
regardless of the PyPI distribution name — same pattern as e.g. `beautifulsoup4` → `bs4`).

## TypeScript — npm (`@doppl-tech/cortex-client`)

```bash
cd sdk/typescript
npm install                # dev-installs typescript, @types/node
npm test                   # compiles + runs the client test suite (node:test) — must be green
npm run build               # emits dist/index.js + dist/index.d.ts (required for publish)
npm pack --dry-run         # confirm dist/ is now included
npm login                  # your npm account (must have publish rights on the @doppl-tech scope)
npm publish --access public
```
Scope note: `@doppl-tech` must be an npm org you (the founder) own or have created — it is
currently unclaimed, so the first `npm login` + `npm publish --access public` under that scope
by an account tied to `doppl-tech` claims it. Verify beforehand with
`npm view @doppl-tech/cortex-client` (expect 404) and after with
`npm install @doppl-tech/cortex-client` in a scratch project.

## Manual publish workflow (`.github/workflows/publish-sdks.yml`)

A `workflow_dispatch`-only GitHub Actions workflow builds and typechecks both SDKs and can
optionally run the actual `twine upload` / `npm publish` steps, gated behind manual inputs and
repository secrets. It never runs on push/PR — only when triggered by hand from the Actions tab
(Actions → "Publish SDKs" → "Run workflow"). Required repository secrets (set these in
Settings → Secrets and variables → Actions before first use):

| Secret | Used for |
| --- | --- |
| `PYPI_API_TOKEN` | `twine upload` to PyPI (real, not TestPyPI) |
| `NPM_TOKEN` | `npm publish` to npm (an automation/publish token scoped to `@doppl-tech`) |

If a secret is missing, the corresponding publish step is skipped (build/typecheck/test steps
still run) rather than failing the whole workflow — see the workflow file for the exact
`if:` conditions.

## After publishing
- Update `docs/EXTERNAL_INTEGRATIONS.md` install lines if the package names changed.
- Tag the release in git.
