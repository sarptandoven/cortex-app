# Upstream

This package vendors the official Obsidian Plugin API type definitions so the Cortex Obsidian bridge can build against an in-repo package.

- Upstream repository: `https://github.com/obsidianmd/obsidian-api`
- Vendored commit: `28a6b8607927249cb549ab3053f5f36f9287aab8`
- License: MIT, preserved in `LICENSE.md`

This is not the Obsidian desktop app source. Cortex uses this API package to build `packages/obsidian-cortex-plugin`, while the Mac app and backend keep memory sync, extraction, review, retrieval, and MCP output inside Cortex.
