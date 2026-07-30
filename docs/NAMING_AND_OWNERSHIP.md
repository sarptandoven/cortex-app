# Naming and Ownership

Status: **Current**

Last reviewed: 2026-07-30

This repository contains historical names from more than one distribution
surface. Use the following map instead of inventing another namespace.

| Name | Meaning | Where it is canonical |
|---|---|---|
| Cortex | Product and open-source project | UI, docs, Python modules, `CORTEX_*` configuration |
| `trace-cortex/cortex-app` | Canonical source repository | Source links, issues, contribution docs |
| `doppl-tech/releases` | Current signed binary release repository | Update and direct-download metadata |
| `api.signindoppl.com` | Current hosted API/auth origin | Hosted examples and deployment configuration |
| `doppl-cortex-client` | Python SDK distribution name | Python package metadata and install commands |
| `@doppl-tech/cortex-client` | Planned TypeScript SDK distribution name | npm package metadata and install commands |
| `@doppl-tech/openclaw-context` | Planned OpenClaw context-engine adapter distribution name | npm package metadata and OpenClaw install docs |
| `@doppl/cortex-obsidian-plugin` | Private, unpublished historical workspace package name | Obsidian plugin build metadata only |
| `com.cortex.doppl` | Existing signed macOS bundle identifier | Apple signing, updates, and release manifests |

The `doppl-tech` package namespace, binary-release repository, hosted domain,
and bundle ID are distribution identifiers; they do not rename the open-source
project. The planned public npm packages use `@doppl-tech` because `@cortex`
belongs to an unrelated registry account. Registry availability is not
ownership: the release operator must create or verify control of the
`@doppl-tech` npm organization before publishing. The private Obsidian package
keeps its historical name until it has a public release plan; if published, it
should migrate to the same verified public scope.

New environment variables, user-facing feature names, and protocol fields
should use `Cortex` unless a package registry or release surface requires one
of the distribution identifiers above. Do not casually change the macOS bundle
ID: signed-app identity, Keychain access, updates, and upgrades depend on it.

## Token Prefixes

Prefixes communicate credential type but are not authorization by themselves:

| Prefix | Credential |
|---|---|
| `cxa_` | Scoped Cortex HTTP API token |
| `cxm_` | Scoped MCP token |
| `cxs_` | Hosted account access token |
| `cxr_` | Hosted account refresh token |

Examples must use the prefix matching the surface. Never use a real token in
documentation, tests, issue reports, or support bundles.

## Ownership Boundaries

- Maintainers of this repository own source compatibility and review.
- Release operators own signing identities, notarization, hosted secrets,
  domains, email delivery, and recovery drills.
- Legal owners must approve terms, privacy language, entity names, and
  jurisdiction. Source review cannot substitute for that approval.
- Connector providers own their upstream APIs. A connector is not production
  ready until its live contract has been exercised with a release-owned test
  account.
