# Obsidian Integration Package

## Direction

Cortex should treat Obsidian as the first-class local notes surface, but it should not vendor or fork the proprietary Obsidian desktop app.

The in-repo package path is:

- keep the current backend vault connector in `backend/app/connectors/obsidian.py`;
- keep the in-tree Cortex Obsidian plugin package in `packages/obsidian-cortex-plugin/`;
- vendor the official MIT-licensed Obsidian Plugin API type definitions in `packages/obsidian-api/`;
- build the Cortex plugin against that in-repo API package;
- bundle the built plugin files into the Cortex Mac app and install them into a real Obsidian vault after the user grants folder access;
- keep Obsidian app installation separate from Cortex;
- keep Cortex memory storage, extraction, review, retrieval, and MCP output inside Cortex.

## Why

Obsidian's useful product primitives for Cortex are the local Markdown vault, links, tags, metadata, local-first ownership model, and plugin API. Those can be used without embedding the Obsidian app source.

The official Obsidian releases repository does not contain the application source code. The official `obsidianmd/obsidian-api` repository contains Plugin API type definitions under MIT license. That makes the plugin API the correct vendored/build target, not the closed desktop app.

## Package Shape

The in-tree package lives under:

```text
packages/obsidian-cortex-plugin/
```

The vendored Obsidian API package lives under:

```text
packages/obsidian-api/
```

It should provide:

- a minimal Obsidian plugin manifest;
- TypeScript code using the Obsidian Plugin API;
- local connection settings for Cortex, written automatically by the packaged Mac app when possible;
- explicit user vault selection before Cortex scans local notes;
- commands for syncing the current vault and opening Cortex Review;
- no hidden background collection outside the selected vault.

The package should call Cortex local APIs instead of reimplementing memory logic:

- `POST /v1/connectors/obsidian/sync`
- `GET /v1/sources/readiness`
- `GET /v1/action-brief`
- `GET /v1/ask`

## Verification

Run:

```bash
scripts/check_obsidian_plugin.sh
```

The script installs package-local npm dependencies when needed, typechecks the TypeScript plugin, and builds `packages/obsidian-cortex-plugin/main.js`.

To produce an installable plugin zip:

```bash
scripts/package_obsidian_plugin.sh
```

The zip contains only `manifest.json`, `main.js`, and `versions.json`.

## Boundaries

Do not:

- clone or vendor the proprietary Obsidian desktop app source;
- present Obsidian as bundled with Cortex;
- depend on Obsidian Sync or Publish private APIs;
- make the plugin the only way to use Cortex notes;
- bypass Cortex Review before memory becomes trusted context.

Do:

- keep folder-based vault sync working without the plugin;
- use the plugin only for better user ergonomics and in-vault interaction;
- install/configure the plugin from the Mac app when `<vault>/.obsidian` exists;
- include license notices for vendored MIT API definitions;
- keep all extracted memory locally stored by Cortex unless the user explicitly enables hosted sync later.
