from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


INSECURE_DEV_API_KEY = "dev-local-key"

# Product brand shown in USER-VISIBLE backend strings (connector names, source-health messages,
# sync status, trust/capability errors, vault page titles, MCP resource/tool prose). The macOS app
# passes its own display name via CORTEX_APP_BRAND at backend launch: the direct-download build sends
# "Cortex", the sandboxed Mac App Store build sends "Doppl". Default is "Cortex" so existing tests and
# the DMG build are unchanged; only when the env is set to "Doppl" do user-visible strings rebrand.
# App Store Guideline 4: the sandboxed build must never render the other brand's name on screen.
APP_BRAND = (os.environ.get("CORTEX_APP_BRAND") or "Cortex").strip() or "Cortex"

# TLS trust for the BUNDLED interpreter. The app ships CPython inside Cortex.app; unlike system
# Python it has no CA store and does not read the macOS keychain, so EVERY outbound HTTPS call
# (GitHub device flow, Notion/Linear/Readwise connects, OAuth starts, connector syncs) died with
# SSL: CERTIFICATE_VERIFY_FAILED on user machines. certifi ships in the bundled runtime deps —
# point OpenSSL at its cacert.pem when nothing else configured it. Runs at config import time so
# it lands before any ssl context is created, for every entrypoint (standalone server, worker,
# MCP stdio bridge). Hosted/dev deployments with real cert stores are untouched (env respected).
if not os.environ.get("SSL_CERT_FILE") or not os.path.isfile(os.environ.get("SSL_CERT_FILE", "")):
    try:
        import certifi  # bundled with the runtime deps; absent in minimal dev venvs is fine

        _cacert = certifi.where()
        if os.path.isfile(_cacert):
            os.environ.setdefault("SSL_CERT_FILE", _cacert)
            os.environ.setdefault("REQUESTS_CA_BUNDLE", _cacert)
    except Exception:
        pass


@dataclass(frozen=True)
class Settings:
    vault_path: Path
    db_path: Path
    api_key: str
    public_base_url: str
    app_name: str = "Cortex"
    default_user_id: str = "local"
    anthropic_api_key: str = ""
    mcp_api_key: str = ""
    mcp_api_key_scopes: str = ""
    shard_mode: str = "local"
    shard_root: Path | None = None
    shard_count: int = 16
    store_cache_size: int = 512
    rate_limit_per_minute: int = 0
    default_memory_quota: int = 0
    require_scoped_api_tokens: bool = False
    sync_signing_key: str = ""
    hosted_database_url: str = ""
    hosted_vector_backend: str = ""
    worker_mode: str = "inline"
    observability_enabled: bool = False
    embedding_provider: str = "hash"
    # Which MCP tool list scoped tokens are ADVERTISED by default: "core" (curated few) or
    # "full" (legacy complete list). Advertisement only — never affects authorization.
    mcp_tool_surface: str = "core"
    # Streamable-HTTP compatibility for /mcp (ChatGPT deep-research connectors): when on (default),
    # /mcp honors an Accept: text/event-stream request by returning the JSON-RPC response as a
    # single SSE event and returns an Mcp-Session-Id on initialize. JSON clients (Claude Desktop /
    # Cursor stdio bridge) are unaffected — they send Accept: application/json and get the exact
    # same application/json body as before. Set CORTEX_MCP_STREAMABLE=0 to force JSON-only.
    mcp_streamable: bool = True
    # The hosted runtime decision (roadmap D): "sharded_sqlite" is the sanctioned 10k-user
    # tier (sharded SQLite + sqlite-vec on one box); "postgres" gates on Postgres/pgvector
    # for post-10k multi-instance scale-out.
    hosted_runtime_tier: str = "sharded_sqlite"
    # Accounts + first-party auth (docs/ACCOUNTS_ENCRYPTION_DESIGN.md, build step 8).
    # auth_enabled derives true when CORTEX_AUTH_ENABLED is set or any auth/OIDC env is
    # present; with no auth config the hosted plane behaves exactly as before. The KEK
    # itself (CORTEX_KEK / CORTEX_KEK_FILE) is read by keyring.LocalKekProvider directly.
    auth_enabled: bool = False
    accounts_db_path: Path | None = None  # default: <shard_root>/control/accounts.sqlite
    auth_access_ttl_seconds: int = 0  # 0 = authn.py default (1h)
    auth_refresh_idle_ttl_seconds: int = 0  # 0 = authn.py default (30d sliding)
    auth_refresh_absolute_ttl_seconds: int = 0  # 0 = authn.py default (90d absolute)
    auth_email_mode: str = "log"  # "log" (console sink) | "smtp"
    # SMTP delivery (used only when auth_email_mode == "smtp"). Read from
    # CORTEX_SMTP_*. If mode is "smtp" but no host is configured, AuthRuntime
    # logs a warning and degrades to the log sink rather than breaking auth.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_addr: str = ""  # falls back to smtp_username when unset
    smtp_use_starttls: bool = True  # STARTTLS on :587 (submission)
    smtp_use_ssl: bool = False  # implicit TLS on :465 (SMTPS); overrides starttls
    # BETA mode: activate accounts at signup without email verification (no email
    # infrastructure required). Never enable on a public production deployment —
    # unverified emails mean no recovery channel and easy squatting.
    auth_autoverify: bool = False
    # First-run/beta ergonomics: new captures skip the Review inbox and are immediately
    # retrievable (approved at save time) instead of waiting for manual approval. Captures
    # from a connected source still honour that source's per-source review policy, so
    # connector trust is unchanged. Off by default; enable for a low-friction beta.
    auto_approve_captures: bool = False
    auth_rate_limit_per_minute: int = 30  # per (action, identifier/IP) auth limiter
    oidc_google_client_id: str = ""
    oidc_google_client_secret: str = ""
    oidc_github_client_id: str = ""
    oidc_github_client_secret: str = ""
    # Sign in with Apple. For NATIVE macOS/iOS (ASAuthorization) the id_token's audience is the
    # app's bundle id (e.g. com.cortex.doppl); set this to that value. Native verification needs
    # only the client id (audience) — no ES256 client-secret JWT (that is the web code-flow only).
    oidc_apple_client_id: str = ""
    oidc_apple_client_secret: str = ""
    # Public URL browsers hit for OAuth redirects / app-login pages (defaults to
    # public_base_url when unset).
    public_app_url: str = ""
    # Billing (docs/COMPLETE_LAUNCH_INSTRUCTIONS.txt PART 15). DORMANT until
    # configured: a no-billing deployment is byte-identical to today. Provider is
    # "" (off) or "paddle"; billing_enabled derives true only when a provider AND
    # its webhook secret are both set. The webhook route 404s (like other gated
    # features) while disabled. Read from CORTEX_BILLING_PROVIDER /
    # CORTEX_PADDLE_WEBHOOK_SECRET / CORTEX_PADDLE_API_KEY.
    billing_provider: str = ""  # "" | "paddle" | "stripe"
    paddle_webhook_secret: str = ""
    paddle_api_key: str = ""  # optional; reserved for future subscription lookups
    # Stripe billing (second provider alongside Paddle). DORMANT until
    # CORTEX_BILLING_PROVIDER=stripe AND CORTEX_STRIPE_WEBHOOK_SECRET are set.
    # Read from CORTEX_STRIPE_WEBHOOK_SECRET / CORTEX_STRIPE_API_KEY.
    stripe_webhook_secret: str = ""
    stripe_api_key: str = ""  # optional; reserved for future subscription lookups
    # Cloudflare Turnstile on the /account/signup page (bot/DoS protection for
    # public signup; argon2id is CPU-heavy so a flood is a DoS risk). DORMANT
    # until BOTH CORTEX_TURNSTILE_SITEKEY and CORTEX_TURNSTILE_SECRET are set:
    # with neither, the signup page + handler behave exactly as today.
    turnstile_site_key: str = ""
    turnstile_secret: str = ""
    # Per-user memory quota by plan (0 = unlimited). Overrides default_memory_quota
    # when a user's plan is present. JSON map from CORTEX_PLAN_QUOTAS, else the
    # baked-in default below (free vs pro). Don't overbuild — a small dict.
    plan_quotas: dict[str, int] | None = None
    # Encrypted-credentials enforcement (docs/ACCOUNTS_ENCRYPTION_DESIGN.md §4,
    # build step 3). When true AND a cipher is configured, the vault refuses to
    # persist a plaintext credential (always encrypt); if enforcement is on but no
    # cipher reached the vault, the write raises rather than silently writing
    # plaintext. Default off keeps the local/stdlib path byte-identical. Flip to 1
    # only after the backfill gauge (POST /v1/admin/encryption/backfill) hits 0.
    require_encrypted_credentials: bool = False

    @property
    def billing_enabled(self) -> bool:
        """Billing is live only when a provider is chosen AND its webhook secret
        is present. Everything (route registration effect, plan quota lookup) is
        gated on this, so an unconfigured deployment behaves exactly as before."""
        provider = (self.billing_provider or "").strip().lower()
        if provider == "paddle":
            return bool((self.paddle_webhook_secret or "").strip())
        if provider == "stripe":
            return bool((self.stripe_webhook_secret or "").strip())
        return False

    @property
    def turnstile_enabled(self) -> bool:
        """Turnstile is live only when BOTH the site key and secret are set. The
        signup page widget and the server-side siteverify gate are both keyed on
        this, so an unconfigured deployment signs up exactly as before."""
        return bool((self.turnstile_site_key or "").strip()) and bool(
            (self.turnstile_secret or "").strip()
        )

    def quota_for_plan(self, plan: str | None) -> int:
        """Per-user memory quota for a plan (0 = unlimited). Falls back to
        default_memory_quota when the plan is unknown/blank so today's single
        CORTEX_DEFAULT_MEMORY_QUOTA behavior is preserved when billing is off."""
        quotas = self.plan_quotas if self.plan_quotas is not None else DEFAULT_PLAN_QUOTAS
        key = (plan or "").strip().lower()
        if key in quotas:
            return max(0, int(quotas[key]))
        return max(0, int(self.default_memory_quota))


# Baked-in plan → per-user memory quota (0 = unlimited). Overridable via
# CORTEX_PLAN_QUOTAS (JSON map). Kept here so billing.py and main.py share one
# source of truth without a circular import.
DEFAULT_PLAN_QUOTAS: dict[str, int] = {
    "free": 2000,
    "pro": 0,  # unlimited
}


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_plan_quotas() -> dict[str, int] | None:
    """Parse CORTEX_PLAN_QUOTAS (JSON object mapping plan name -> int quota).
    Malformed input falls back to the baked-in defaults so a bad env can never
    break boot. Returns None to mean 'use DEFAULT_PLAN_QUOTAS'."""
    raw = os.environ.get("CORTEX_PLAN_QUOTAS", "").strip()
    if not raw:
        return None
    try:
        import json

        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    quotas: dict[str, int] = {}
    for key, value in parsed.items():
        try:
            quotas[str(key).strip().lower()] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    return quotas or None


def load_settings() -> Settings:
    root = Path(__file__).resolve().parents[1]
    db_env = os.environ.get("CORTEX_DB_PATH")
    vault_env = os.environ.get("CORTEX_VAULT_PATH")
    if vault_env:
        vault_path = Path(vault_env).expanduser()
    elif db_env:
        vault_path = Path(db_env).expanduser().parent
    else:
        vault_path = root / "data" / "Cortex.vault"
    db_path = Path(db_env).expanduser() if db_env else vault_path / "index.sqlite"
    require_scoped_api_tokens = os.environ.get("CORTEX_REQUIRE_SCOPED_API_TOKENS", "").strip().lower() in {"1", "true", "yes", "on"}
    oidc_google_client_id = os.environ.get("CORTEX_OIDC_GOOGLE_CLIENT_ID", "").strip()
    oidc_github_client_id = os.environ.get("CORTEX_OIDC_GITHUB_CLIENT_ID", "").strip()
    oidc_apple_client_id = os.environ.get("CORTEX_OIDC_APPLE_CLIENT_ID", "").strip()
    accounts_db_env = os.environ.get("CORTEX_ACCOUNTS_DB_PATH", "").strip()
    auth_enabled = (
        _truthy_env("CORTEX_AUTH_ENABLED")
        or bool(oidc_google_client_id)
        or bool(oidc_github_client_id)
        or bool(oidc_apple_client_id)
        or bool(accounts_db_env)
    )
    public_base_url = os.environ.get("CORTEX_PUBLIC_BASE_URL", "http://127.0.0.1:8766")
    api_key = os.environ.get("CORTEX_API_KEY", "").strip()
    if api_key == INSECURE_DEV_API_KEY and not _truthy_env("CORTEX_ALLOW_INSECURE_DEV_TOKEN"):
        raise RuntimeError(
            "CORTEX_API_KEY uses the insecure sample token 'dev-local-key'. "
            "Set a long random token, or set CORTEX_ALLOW_INSECURE_DEV_TOKEN=1 only for local development."
        )
    return Settings(
        vault_path=vault_path,
        db_path=db_path,
        api_key=api_key,
        public_base_url=public_base_url,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        mcp_api_key=os.environ.get("CORTEX_MCP_API_KEY", ""),
        mcp_api_key_scopes=os.environ.get("CORTEX_MCP_API_KEY_SCOPES", ""),
        shard_mode=os.environ.get("CORTEX_SHARD_MODE", "local"),
        shard_root=Path(os.environ["CORTEX_SHARD_ROOT"]).expanduser() if os.environ.get("CORTEX_SHARD_ROOT") else None,
        shard_count=max(1, int(os.environ.get("CORTEX_SHARD_COUNT", "16"))),
        store_cache_size=max(1, int(os.environ.get("CORTEX_STORE_CACHE_SIZE", "512") or "512")),
        rate_limit_per_minute=max(0, int(os.environ.get("CORTEX_RATE_LIMIT_PER_MINUTE", "0") or "0")),
        default_memory_quota=max(0, int(os.environ.get("CORTEX_DEFAULT_MEMORY_QUOTA", "0") or "0")),
        require_scoped_api_tokens=require_scoped_api_tokens,
        sync_signing_key=os.environ.get("CORTEX_SYNC_SIGNING_KEY", ""),
        hosted_database_url=os.environ.get("CORTEX_HOSTED_DATABASE_URL", os.environ.get("DATABASE_URL", "")).strip(),
        hosted_vector_backend=os.environ.get("CORTEX_HOSTED_VECTOR_BACKEND", "").strip().lower(),
        worker_mode=os.environ.get("CORTEX_WORKER_MODE", "inline").strip().lower() or "inline",
        observability_enabled=_truthy_env("CORTEX_OBSERVABILITY_ENABLED"),
        embedding_provider=os.environ.get("CORTEX_EMBEDDING_PROVIDER", "hash").strip().lower() or "hash",
        mcp_tool_surface=(os.environ.get("CORTEX_MCP_TOOL_SURFACE", "core").strip().lower() or "core"),
        # Default TRUE: unset -> streamable HTTP compat is on. Only an explicit falsey value disables it.
        mcp_streamable=(os.environ.get("CORTEX_MCP_STREAMABLE", "1").strip().lower() not in {"0", "false", "no", "off"}),
        hosted_runtime_tier=(
            os.environ.get("CORTEX_HOSTED_RUNTIME_TIER", "sharded_sqlite").strip().lower().replace("-", "_")
            or "sharded_sqlite"
        ),
        auth_enabled=auth_enabled,
        accounts_db_path=Path(accounts_db_env).expanduser() if accounts_db_env else None,
        auth_access_ttl_seconds=max(0, int(os.environ.get("CORTEX_AUTH_ACCESS_TTL_SECONDS", "0") or "0")),
        auth_refresh_idle_ttl_seconds=max(0, int(os.environ.get("CORTEX_AUTH_REFRESH_IDLE_TTL_SECONDS", "0") or "0")),
        auth_refresh_absolute_ttl_seconds=max(0, int(os.environ.get("CORTEX_AUTH_REFRESH_ABSOLUTE_TTL_SECONDS", "0") or "0")),
        auth_email_mode=(os.environ.get("CORTEX_AUTH_EMAIL_MODE", "log").strip().lower() or "log"),
        smtp_host=os.environ.get("CORTEX_SMTP_HOST", "").strip(),
        smtp_port=max(1, int(os.environ.get("CORTEX_SMTP_PORT", "587") or "587")),
        smtp_username=os.environ.get("CORTEX_SMTP_USERNAME", "").strip(),
        smtp_password=os.environ.get("CORTEX_SMTP_PASSWORD", ""),
        smtp_from_addr=os.environ.get("CORTEX_SMTP_FROM_ADDR", "").strip(),
        smtp_use_starttls=os.environ.get("CORTEX_SMTP_USE_STARTTLS", "1").strip().lower() in {"1", "true", "yes", "on"},
        smtp_use_ssl=_truthy_env("CORTEX_SMTP_USE_SSL"),
        auth_autoverify=_truthy_env("CORTEX_AUTH_AUTOVERIFY"),
        auto_approve_captures=_truthy_env("CORTEX_AUTO_APPROVE_CAPTURES"),
        auth_rate_limit_per_minute=max(0, int(os.environ.get("CORTEX_AUTH_RATE_LIMIT_PER_MINUTE", "30") or "30")),
        oidc_google_client_id=oidc_google_client_id,
        oidc_google_client_secret=os.environ.get("CORTEX_OIDC_GOOGLE_CLIENT_SECRET", "").strip(),
        oidc_github_client_id=oidc_github_client_id,
        oidc_github_client_secret=os.environ.get("CORTEX_OIDC_GITHUB_CLIENT_SECRET", "").strip(),
        oidc_apple_client_id=oidc_apple_client_id,
        oidc_apple_client_secret=os.environ.get("CORTEX_OIDC_APPLE_CLIENT_SECRET", "").strip(),
        public_app_url=(os.environ.get("CORTEX_PUBLIC_APP_URL", "").strip() or public_base_url),
        billing_provider=os.environ.get("CORTEX_BILLING_PROVIDER", "").strip().lower(),
        paddle_webhook_secret=os.environ.get("CORTEX_PADDLE_WEBHOOK_SECRET", "").strip(),
        paddle_api_key=os.environ.get("CORTEX_PADDLE_API_KEY", "").strip(),
        stripe_webhook_secret=os.environ.get("CORTEX_STRIPE_WEBHOOK_SECRET", "").strip(),
        stripe_api_key=os.environ.get("CORTEX_STRIPE_API_KEY", "").strip(),
        turnstile_site_key=os.environ.get("CORTEX_TURNSTILE_SITEKEY", "").strip(),
        turnstile_secret=os.environ.get("CORTEX_TURNSTILE_SECRET", "").strip(),
        plan_quotas=_load_plan_quotas(),
        require_encrypted_credentials=_truthy_env("CORTEX_REQUIRE_ENCRYPTED_CREDENTIALS"),
    )
