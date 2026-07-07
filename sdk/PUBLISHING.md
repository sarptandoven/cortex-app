# Publishing the Cortex SDKs

Both packages are release-ready and their build artifacts are validated (a Python wheel builds; the
npm tarball packs). The steps below are the **public publish** — run them with your own PyPI / npm
accounts. Nothing here is automated in CI (publishing is an intentional, credentialed action).

Bump the version in `python/pyproject.toml` and `typescript/package.json` together before each release.

## Python — PyPI (`cortex-client`)

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
Verify: `pip install cortex-client` in a clean venv, then `python -c "from cortex_client import CortexClient"`.

Note: the name `cortex-client` must be available on PyPI (or you own it). If taken, rename in
`pyproject.toml` (e.g. `doppl-cortex-client`) and update the README.

## TypeScript — npm (`@cortex/client`)

```bash
cd sdk/typescript
npm install                # dev-installs typescript
npm run build              # emits dist/index.js + dist/index.d.ts (required for publish)
npm pack --dry-run         # confirm dist/ is now included
npm login                  # your npm account
npm publish --access public
```
Scope note: `@cortex` must be an npm org you own. If not, either publish under a scope you control
(e.g. `@doppl/cortex-client`) or unscoped (`cortex-client-js`) — change `name` in `package.json`
(and drop `publishConfig.access` if unscoped). Verify: `npm view @cortex/client` and
`npm install @cortex/client` in a scratch project.

## After publishing
- Update `docs/EXTERNAL_INTEGRATIONS.md` install lines if the package names changed.
- Tag the release in git.
