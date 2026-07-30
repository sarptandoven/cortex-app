# Security Policy

Cortex stores highly personal data and exposes it to explicitly authorized
tools. Security and privacy regressions are treated as product failures.

## Reporting a Vulnerability

Please do **not** open a public issue for a suspected vulnerability.

Email **support@trydoppl.com** with the subject `Cortex security report` and
include:

- the affected commit, release, endpoint, or component;
- reproduction steps or a minimal proof of concept;
- expected and observed behavior;
- realistic impact and any prerequisites;
- whether you believe active exploitation is occurring.

Do not send real tokens, private vaults, raw memory, or another person's data.
Use synthetic fixtures and redact identifiers. We aim to acknowledge reports
within five business days, validate severity, and coordinate a fix and
disclosure timeline with the reporter.

## Supported Versions

Security fixes target:

- the current `main` branch;
- the latest direct-download beta release;
- the current published SDK/plugin versions, when any are available.

Older beta artifacts may be asked to update before a report can be reproduced.

## High-Sensitivity Areas

Especially valuable reports include:

- authentication or scope bypasses;
- cross-user or cross-vault access;
- unsafe MCP tool authorization;
- connector-token or Keychain leakage;
- path traversal or unsafe archive extraction;
- support-bundle or log disclosure;
- update/release integrity failures;
- encryption, deletion, backup, or restore failures;
- remote requests that can reach unintended hosts or local resources.

The current threat model and controls are described in
[docs/SECURITY_REVIEW.md](docs/SECURITY_REVIEW.md) and
[docs/TRUST_CONTROLS.md](docs/TRUST_CONTROLS.md).

## Safe Research

Use accounts and data you control, avoid service disruption and automated
high-volume scanning, stop if you encounter another user's information, and
give maintainers reasonable time to remediate before public disclosure.

There is currently no paid bug-bounty program.
