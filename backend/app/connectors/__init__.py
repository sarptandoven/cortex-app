"""Connector helpers for account-backed and local source sync."""

from .calendar import CalendarSync, CalendarSyncRecord, fetch_calendar_records
from .github import GitHubRepositoryDiscovery, GitHubSync, GitHubSyncRecord, discover_github_repositories, fetch_github_records
from .gmail import GmailSync, GmailSyncRecord, fetch_gmail_records
from .google_drive import GoogleDriveSync, GoogleDriveSyncRecord, fetch_google_drive_records
from .jira import JiraSync, JiraSyncRecord, fetch_jira_records
from .linear import LinearSync, LinearSyncRecord, fetch_linear_records
from .notion import NotionSync, NotionSyncRecord, fetch_notion_records
from .obsidian import ObsidianVaultScan, scan_obsidian_vault
from .outlook import OutlookSync, OutlookSyncRecord, fetch_outlook_records
from .raindrop import RaindropSync, RaindropSyncRecord, fetch_raindrop_records
from .readwise import ReadwiseSync, ReadwiseSyncRecord, fetch_readwise_records
from .slack import SlackChannelDiscovery, SlackSync, SlackSyncRecord, discover_slack_channels, fetch_slack_records
from .zotero import ZoteroSync, ZoteroSyncRecord, fetch_zotero_records

__all__ = [
    "GitHubSync",
    "GitHubRepositoryDiscovery",
    "GitHubSyncRecord",
    "CalendarSync",
    "CalendarSyncRecord",
    "JiraSync",
    "JiraSyncRecord",
    "GmailSync",
    "GmailSyncRecord",
    "GoogleDriveSync",
    "GoogleDriveSyncRecord",
    "LinearSync",
    "LinearSyncRecord",
    "NotionSync",
    "NotionSyncRecord",
    "ObsidianVaultScan",
    "OutlookSync",
    "OutlookSyncRecord",
    "RaindropSync",
    "RaindropSyncRecord",
    "ReadwiseSync",
    "ReadwiseSyncRecord",
    "SlackSync",
    "SlackChannelDiscovery",
    "SlackSyncRecord",
    "ZoteroSync",
    "ZoteroSyncRecord",
    "discover_github_repositories",
    "discover_slack_channels",
    "fetch_github_records",
    "fetch_calendar_records",
    "fetch_gmail_records",
    "fetch_google_drive_records",
    "fetch_jira_records",
    "fetch_linear_records",
    "fetch_notion_records",
    "fetch_outlook_records",
    "fetch_raindrop_records",
    "fetch_readwise_records",
    "fetch_slack_records",
    "fetch_zotero_records",
    "scan_obsidian_vault",
]
