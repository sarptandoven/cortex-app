"""Connector helpers for account-backed and local source sync."""

from .github import GitHubSync, GitHubSyncRecord, fetch_github_records
from .obsidian import ObsidianVaultScan, scan_obsidian_vault
from .slack import SlackSync, SlackSyncRecord, fetch_slack_records

__all__ = [
    "GitHubSync",
    "GitHubSyncRecord",
    "ObsidianVaultScan",
    "SlackSync",
    "SlackSyncRecord",
    "fetch_github_records",
    "fetch_slack_records",
    "scan_obsidian_vault",
]
