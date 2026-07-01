"""Connector helpers for account-backed and local source sync."""

from .calendar import CalendarSync, CalendarSyncRecord, fetch_calendar_records
from .github import GitHubSync, GitHubSyncRecord, fetch_github_records
from .jira import JiraSync, JiraSyncRecord, fetch_jira_records
from .linear import LinearSync, LinearSyncRecord, fetch_linear_records
from .notion import NotionSync, NotionSyncRecord, fetch_notion_records
from .obsidian import ObsidianVaultScan, scan_obsidian_vault
from .raindrop import RaindropSync, RaindropSyncRecord, fetch_raindrop_records
from .readwise import ReadwiseSync, ReadwiseSyncRecord, fetch_readwise_records
from .slack import SlackSync, SlackSyncRecord, fetch_slack_records
from .zotero import ZoteroSync, ZoteroSyncRecord, fetch_zotero_records

__all__ = [
    "GitHubSync",
    "GitHubSyncRecord",
    "CalendarSync",
    "CalendarSyncRecord",
    "JiraSync",
    "JiraSyncRecord",
    "LinearSync",
    "LinearSyncRecord",
    "NotionSync",
    "NotionSyncRecord",
    "ObsidianVaultScan",
    "RaindropSync",
    "RaindropSyncRecord",
    "ReadwiseSync",
    "ReadwiseSyncRecord",
    "SlackSync",
    "SlackSyncRecord",
    "ZoteroSync",
    "ZoteroSyncRecord",
    "fetch_github_records",
    "fetch_calendar_records",
    "fetch_jira_records",
    "fetch_linear_records",
    "fetch_notion_records",
    "fetch_raindrop_records",
    "fetch_readwise_records",
    "fetch_slack_records",
    "fetch_zotero_records",
    "scan_obsidian_vault",
]
