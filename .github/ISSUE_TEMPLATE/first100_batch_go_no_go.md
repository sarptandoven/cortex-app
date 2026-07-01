---
name: First-100 batch go/no-go
about: Decide whether a named first-100 tester batch can be invited
title: "[first-100 go/no-go] batch "
labels: first-100, release
assignees: ""
---

## Batch

- Batch number:
- Planned invite count:
- Tester cohort source:
- Stop/go decision owner:
- Decision: go/no-go

## Build

- PR or commit:
- Version:
- Build:
- DMG checksum:
- ZIP checksum:
- Download location:
- Previous rollback build retained: yes/no

## Required Support Packet

- Support channel:
- Primary support owner:
- Backup support owner:
- Incident engineer:
- Case log location:
- Support artifact storage:
- Business hours and timezone:
- Deletion request contact:
- Build version, build number, hash:
- Known limitations sent to testers:

## Automated Gates

Paste command status only. Do not paste private memory content.

- Backend tests passed:
- Retrieval eval passed:
- Adaptation eval passed:
- macOS build passed:
- Codesign verify passed:
- Distribution site check passed:
- Update manifest validation passed:
- Strict ops readiness with package artifacts passed:
- First-100 live smoke passed against launched app:
- GitHub CI green:

## Human Clean-Profile QA

- Clean-profile QA packet location:
- DMG downloaded from planned tester URL:
- DMG checksum matched:
- Installed on clean macOS 13+ profile:
- If unnotarized, Control-click > Open worked and was disclosed:
- First-run onboarding completed without developer docs:
- Obsidian/local notes or MCP connected:
- Sync created Review items:
- Review approval worked:
- Ask returned cited answer:
- Backup worked:
- Content-free support bundle export worked:
- Manual update over previous beta preserved memory folder:
- Manual rollback to previous beta preserved memory folder:
- Strict launch gate passed with support packet and clean-profile QA packet:

## No-Go Checks

Mark any true item as a no-go until resolved:

- Support owner not assigned:
- Clean-profile install not done:
- Hosted artifact hash differs from verified checksum:
- App cannot launch from Applications:
- Gatekeeper blocks beyond documented Control-click > Open path:
- Backup/export/delete/support-bundle/update/rollback risks user data:
- Invite copy implies notarization, automatic updates, hosted sync, cloud backup, or production support:
- Open SEV 0 issue:
- Repeated unresolved SEV 1 issue on current build:

## Decision Notes

- Known limitations sent to testers:
- Invite copy reviewed:
- Privacy/support consent text reviewed:
- Final decision notes:
