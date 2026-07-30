# Cortex Support

## Choose the Right Channel

- **Reproducible bug or installation problem:** open a GitHub issue using the
  bug-report template.
- **Feature or design proposal:** open a feature request after checking
  [ROADMAP.md](ROADMAP.md).
- **Security or private-data concern:** follow [SECURITY.md](SECURITY.md); do
  not post it publicly.
- **Conduct concern:** follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Before Filing a Bug

1. Confirm you are testing the latest release or current `main`.
2. Check [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
3. Run the relevant health check:

   ```bash
   make check
   ```

4. For a running app, create a sanitized support bundle from Connections &
   Privacy or:

   ```bash
   .venv/bin/python scripts/export_support_bundle.py --mode live
   ```

Support bundles are designed to omit raw memory, but review the generated file
before sharing it. Never attach a vault, connector credential, API token, or
private export to a public issue.

## Useful Bug Details

Include the Cortex version/build, macOS version and architecture, installation
method, exact steps, expected behavior, actual behavior, and the smallest
sanitized log or diagnostic excerpt that demonstrates the problem.

Cortex is currently a beta. Community support is best-effort and has no
guaranteed response time.
