# Cortex Memory for Obsidian

This package is the in-repo Cortex Obsidian plugin. It builds on the vendored Obsidian MIT-licensed Plugin API package at `../obsidian-api` and talks to the local Cortex backend.

It does not vendor, fork, or embed the proprietary Obsidian desktop app.

## Commands

- `Cortex: Sync this vault`
- `Cortex: Check Review`
- `Cortex: Prepare action brief from current note`

## Local Development

```bash
cd packages/obsidian-cortex-plugin
npm install
npm run typecheck
npm run build
```

The packaged Cortex Mac app installs and configures this bridge automatically when a user connects a real Obsidian vault. The manual copy flow is only for local plugin development.

From the repo root, build and package the installable plugin zip:

```bash
scripts/package_obsidian_plugin.sh
```

Copy `manifest.json` and `main.js` into:

```text
<vault>/.obsidian/plugins/cortex-memory/
```

Then enable the plugin in Obsidian community plugin settings.

## Settings

- Cortex endpoint defaults to `http://127.0.0.1:8766`.
- Cortex API token is required when the local app started the backend with authentication.
- Auto-sync is off by default in development. The packaged Cortex app enables startup sync when it installs the bridge into a connected vault.

Vault sync sends the selected vault path to the local Cortex backend. `Prepare action brief` reads the current note title and first useful line to form a local query. Cortex performs scanning, extraction, review gating, retrieval, and MCP output on the user's Mac.
