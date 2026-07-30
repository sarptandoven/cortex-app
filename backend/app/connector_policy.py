"""Shared outbound-origin policy for credential-bearing hosted connectors."""

from __future__ import annotations

from urllib.parse import urlsplit


HOSTED_CONNECTOR_ORIGINS: dict[str, tuple[str, ...]] = {
    "github": ("https://api.github.com",),
    "gmail": ("https://gmail.googleapis.com/gmail/v1",),
    "google-drive": ("https://www.googleapis.com/drive/v3",),
    "outlook": ("https://graph.microsoft.com/v1.0",),
    "slack": ("https://slack.com/api",),
    "readwise": ("https://readwise.io/api/v2",),
    "raindrop": ("https://api.raindrop.io/rest/v1",),
    "linear": ("https://api.linear.app/graphql",),
    "notion": ("https://api.notion.com/v1",),
}

HOSTED_OAUTH_TOKEN_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "gmail": ("https://oauth2.googleapis.com/token",),
    "google-drive": ("https://oauth2.googleapis.com/token",),
    "notion": ("https://api.notion.com/v1/oauth/token",),
    "outlook": ("https://login.microsoftonline.com/common/oauth2/v2.0/token",),
}


def is_official_connector_origin(source: str, value: str) -> bool:
    candidate = str(value or "").strip().rstrip("/")
    return candidate in HOSTED_CONNECTOR_ORIGINS.get(source, ())


def is_official_oauth_token_endpoint(source: str, value: str) -> bool:
    candidate = str(value or "").strip().rstrip("/")
    return candidate in HOSTED_OAUTH_TOKEN_ENDPOINTS.get(source, ())


def is_atlassian_cloud_origin(value: str) -> bool:
    parsed = urlsplit(str(value or "").strip())
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and hostname.endswith(".atlassian.net")
        and hostname != ".atlassian.net"
    )
