# Cortex Setup Guide

Cortex is a local-first macOS beta. You do not need Redis, a GitHub token, Docker, or a hosted account to run the current app.

## Build And Open

```bash
./macos/build.sh
open macos/build/Cortex.app
```

The app starts its bundled local service on `127.0.0.1:8766` and stores user-owned memory files in:

```text
~/Library/Application Support/Cortex/Cortex.vault/
```

## First Run

Use the five-step setup flow:

1. Memory Folder: confirm the local memory folder path and service health.
2. Connect First Source: connect a supported service or direct app integration from Sources.
3. Review Memory: approve at least one useful memory.
4. Ask Cortex: ask a question and inspect cited memory.
5. Trust & Backup: choose trust defaults and create or skip a first backup.

`Finish Later` opens the app without marking setup complete. Setup can be reopened from `Trust > Advanced`.

## Connect Sources

Open `Sources` and connect the services or direct app integrations you want Cortex to use. Start with supported AI tools and work sources such as ChatGPT, Claude, Cursor, Notion, Gmail/email, Slack, Discord, Telegram, Google Keep, Google Chat, Teams, Zoom, Messages, WhatsApp, bookmarks, calendars, contacts, LinkedIn, Twitter/X, docs, notes, and work tools.

Cloud OAuth/API sync is staged by service readiness in this beta. If a service cannot connect directly yet, use `Advanced/Fallback` import for a selected export, folder, or file instead of making that the primary setup path.

## Review And Ask

- `Review` is where pending memories are approved or archived.
- `Ask` searches approved memory and returns cited answers.
- `Ask > AI Handoff` contains fallback copy actions for tools that cannot connect directly yet.

## Trust And Backup

Open `Trust` to manage:

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
