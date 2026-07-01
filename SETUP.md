# Cortex Setup Guide

Cortex is a local-first macOS beta. You do not need Redis, a GitHub token, Docker, or a hosted account to run the current app.

## Build And Open

```bash
./macos/build.sh
open macos/build/Cortex.app
```

The release package starts its bundled local service on `127.0.0.1:8766` and stores user-owned memory files in:

```text
~/Library/Application Support/Cortex/Cortex.vault/
```

## First Run

Use the setup flow:

1. Memory folder: confirm the local memory folder path and service health.
2. Connect/sync: connect Obsidian or a local notes folder as the first memory source, then connect AI tools through MCP when ready.
3. Review: approve useful memory after a source syncs.
4. Ask: ask a question and inspect cited memory after approval.

`Finish Later` opens the app without marking setup complete. Setup can be reopened from Connections & Privacy.

## Connect Sources And AI Tools

Open Connections & Privacy and connect Obsidian/local notes or another supported source plus supported local AI tools through MCP.

Cloud OAuth/API sync is staged by service readiness in this beta and does not appear as a primary setup path until direct sync is ready.

## Review And Ask

- `Review` is where pending memories are approved or archived.
- `Ask` searches approved memory and returns cited answers.
- Connected AI tools can retrieve approved memory through MCP. Ask remains the in-app way to test cited answers.

## Connections & Privacy And Backup

Open Connections & Privacy to manage:

- privacy posture and AI access permissions
- local memory folder backup
- connected AI tools
- source/audit trail
- advanced diagnostics and setup reset

## Local Backend Development

For backend development without opening the app:

```bash
./scripts/dev_backend.sh
```

Run the main verification set:

```bash
python3 -W error::ResourceWarning -m unittest discover backend/tests
python3 scripts/retrieval_eval.py
python3 scripts/adaptation_eval.py
./macos/build.sh
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
python3 scripts/ops_readiness_check.py
```
