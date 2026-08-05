from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse

from .config import APP_BRAND, Settings


HOSTED_VECTOR_BACKENDS = {"pgvector", "postgres-pgvector"}
HOSTED_WORKER_MODES = {"external", "hosted", "worker"}
# The hosted runtime decision (roadmap D): sharded SQLite on one box is the sanctioned tier
# for 10k users; the postgres tier is for post-10k multi-instance scale-out.
HOSTED_RUNTIME_TIERS = {"sharded_sqlite", "postgres"}
RESERVED_PUBLIC_HOST_SUFFIXES = (".example", ".invalid", ".localhost", ".local", ".test")
DOCUMENTATION_HOSTS = ("example.com", "example.net", "example.org")


def hosted_readiness_contract(settings: Settings, runtime: dict | None = None) -> dict:
    shard_mode = (settings.shard_mode or "local").strip().lower()
    hosted_mode = shard_mode != "local"
    requires_scoped_tokens = bool(settings.require_scoped_api_tokens)
    global_token_user_switching = "blocked" if hosted_mode or requires_scoped_tokens else "allowed_local_compatibility"
    runtime_tier = (getattr(settings, "hosted_runtime_tier", "") or "sharded_sqlite").strip().lower().replace("-", "_")
    if runtime_tier not in HOSTED_RUNTIME_TIERS:
        runtime_tier = "sharded_sqlite"

    checks = [
        _scoped_token_check(hosted_mode, requires_scoped_tokens),
        _public_base_url_check(hosted_mode, settings.public_base_url),
        _sync_signing_key_check(hosted_mode, settings.sync_signing_key),
        _hosted_database_check(hosted_mode, settings.hosted_database_url, runtime_tier, shard_mode),
        _embedding_provider_check(hosted_mode, settings.embedding_provider),
        _vector_backend_check(hosted_mode, settings.hosted_vector_backend, runtime_tier),
        _runtime_storage_check(hosted_mode, runtime, runtime_tier),
        _worker_check(hosted_mode, settings.worker_mode),
        _worker_queue_check(hosted_mode, settings.worker_mode, runtime),
        _observability_check(hosted_mode, settings.observability_enabled),
        _control_plane_check(hosted_mode, runtime),
    ]

    return {
        "status": "ok" if all(check["status"] == "ok" for check in checks) else "blocked",
        "hosted_mode": hosted_mode,
        "shard_mode": shard_mode,
        "runtime_tier": runtime_tier,
        "require_scoped_api_tokens": requires_scoped_tokens,
        "global_token_user_switching": global_token_user_switching,
        "runtime": runtime or {},
        "checks": checks,
    }


def _scoped_token_check(hosted_mode: bool, requires_scoped_tokens: bool) -> dict:
    if hosted_mode and not requires_scoped_tokens:
        return {
            "name": "scoped_api_tokens_required",
            "status": "blocked",
            "detail": "Set CORTEX_REQUIRE_SCOPED_API_TOKENS=1 before marking hosted shard mode ready.",
        }
    return {
        "name": "scoped_api_tokens_required",
        "status": "ok",
        "detail": "Hosted shard modes require per-user scoped REST tokens before readiness passes.",
    }


def _public_base_url_check(hosted_mode: bool, public_base_url: str) -> dict:
    if not hosted_mode:
        return {
            "name": "public_base_url",
            "status": "ok",
            "detail": "Local mode can use the default loopback URL.",
        }
    try:
        parsed = urlparse(public_base_url or "")
        host = (parsed.hostname or "").rstrip(".").lower()
        _ = parsed.port
    except ValueError:
        parsed = None
        host = ""
    origin_only = (
        parsed is not None
        and parsed.scheme == "https"
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )
    if origin_only and _public_base_url_host_is_safe(host):
        return {
            "name": "public_base_url",
            "status": "ok",
            "detail": "Hosted mode has an HTTPS public API origin.",
        }
    return {
        "name": "public_base_url",
        "status": "blocked",
        "detail": "Set CORTEX_PUBLIC_BASE_URL to a hosted HTTPS API origin with a public routable host and no path, query, fragment, or credentials.",
    }


def _public_base_url_host_is_safe(host: str) -> bool:
    if not host:
        return False
    try:
        parsed_ip = ip_address(host)
    except ValueError:
        pass
    else:
        return parsed_ip.is_global

    if "." not in host:
        return False
    if any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in RESERVED_PUBLIC_HOST_SUFFIXES):
        return False
    if any(host == documentation_host or host.endswith(f".{documentation_host}") for documentation_host in DOCUMENTATION_HOSTS):
        return False
    return True


def _sync_signing_key_check(hosted_mode: bool, sync_signing_key: str) -> dict:
    if not hosted_mode or sync_signing_key.strip():
        return {
            "name": "sync_signing_key",
            "status": "ok",
            "detail": "Sync/device receipts can be signed for the current deployment mode.",
        }
    return {
        "name": "sync_signing_key",
        "status": "blocked",
        "detail": "Set CORTEX_SYNC_SIGNING_KEY before enabling hosted sync or multi-device receipts.",
    }


def _hosted_database_check(hosted_mode: bool, hosted_database_url: str, runtime_tier: str, shard_mode: str) -> dict:
    if not hosted_mode:
        return {
            "name": "hosted_database",
            "status": "ok",
            "detail": f"Local mode uses SQLite and the local {APP_BRAND} vault.",
        }
    if runtime_tier == "sharded_sqlite":
        # The sanctioned 10k tier: per-tenant sharded SQLite on one box. The primary store IS
        # the shard set; no external database URL is required or expected.
        if shard_mode in {"user", "bucket"}:
            return {
                "name": "hosted_database",
                "status": "ok",
                "detail": f"Sharded-SQLite tier uses per-tenant SQLite shards (shard_mode={shard_mode}); no external database is required for 10k.",
            }
        return {
            "name": "hosted_database",
            "status": "blocked",
            "detail": "Sharded-SQLite tier needs CORTEX_SHARD_MODE=user or bucket so tenants are isolated into shards.",
        }
    parsed = urlparse(hosted_database_url or "")
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").rstrip(".").lower()
    database_name = (parsed.path or "").strip("/")
    if scheme in {"postgres", "postgresql"} and host and database_name:
        return {
            "name": "hosted_database",
            "status": "ok",
            "detail": "Hosted mode has a Postgres database URL for the primary memory store.",
        }
    return {
        "name": "hosted_database",
        "status": "blocked",
        "detail": "Set CORTEX_HOSTED_DATABASE_URL or DATABASE_URL to a postgres:// or postgresql:// database before marking hosted mode ready.",
    }


def _embedding_provider_check(hosted_mode: bool, embedding_provider: str) -> dict:
    provider = (embedding_provider or "hash").strip().lower()
    if not hosted_mode or provider != "hash":
        return {
            "name": "embedding_provider",
            "status": "ok",
            "detail": f"Embedding provider: {provider or 'hash'}.",
        }
    return {
        "name": "embedding_provider",
        "status": "blocked",
        "detail": "Hosted retrieval needs a real embedding provider; set CORTEX_EMBEDDING_PROVIDER=openai or another production provider.",
    }


def _vector_backend_check(hosted_mode: bool, hosted_vector_backend: str, runtime_tier: str) -> dict:
    backend = (hosted_vector_backend or "").strip().lower()
    if not hosted_mode:
        return {
            "name": "hosted_vector_backend",
            "status": "ok",
            "detail": "Local mode uses SQLite/FTS and optional sqlite-vec.",
        }
    if runtime_tier == "sharded_sqlite":
        if backend in {"", "sqlite-vec", "sqlite_vec"}:
            return {
                "name": "hosted_vector_backend",
                "status": "ok",
                "detail": "Sharded-SQLite tier uses per-shard sqlite-vec retrieval; live evidence is verified by runtime_hosted_storage.",
            }
        return {
            "name": "hosted_vector_backend",
            "status": "blocked",
            "detail": f"Sharded-SQLite tier expects sqlite-vec (or unset), not {backend}; set CORTEX_HOSTED_RUNTIME_TIER=postgres to gate on {backend}.",
        }
    if backend in HOSTED_VECTOR_BACKENDS:
        return {
            "name": "hosted_vector_backend",
            "status": "ok",
            "detail": f"Hosted vector backend configured as {backend}.",
        }
    return {
        "name": "hosted_vector_backend",
        "status": "blocked",
        "detail": "Set CORTEX_HOSTED_VECTOR_BACKEND=pgvector before treating hosted retrieval as 10k-user ready.",
    }


def _runtime_storage_check(hosted_mode: bool, runtime: dict | None, runtime_tier: str) -> dict:
    if not hosted_mode:
        return {
            "name": "runtime_hosted_storage",
            "status": "ok",
            "detail": "Local mode is expected to use SQLite/FTS and optional sqlite-vec.",
        }
    storage = (runtime or {}).get("storage") if isinstance(runtime, dict) else None
    if not isinstance(storage, dict):
        return {
            "name": "runtime_hosted_storage",
            "status": "blocked",
            "detail": "Hosted readiness needs runtime storage evidence from the active store.",
        }
    database_backend = str(storage.get("database_backend") or "").strip().lower()
    vector_backend = str(storage.get("vector_backend") or "").strip().lower()
    database_live = bool(storage.get("database_live"))
    vector_live = bool(storage.get("vector_live") or storage.get("vector_available"))
    if runtime_tier == "sharded_sqlite":
        if database_backend == "sqlite" and database_live and vector_backend == "sqlite-vec" and vector_live:
            return {
                "name": "runtime_hosted_storage",
                "status": "ok",
                "detail": "Runtime storage reports live sharded-SQLite primary storage and sqlite-vec retrieval (the sanctioned 10k tier).",
            }
        return {
            "name": "runtime_hosted_storage",
            "status": "blocked",
            "detail": (
                "Sharded-SQLite tier needs live SQLite storage with sqlite-vec retrieval "
                f"(database_backend={database_backend or 'unknown'}, database_live={database_live}, "
                f"vector_backend={vector_backend or 'unknown'}, vector_live={vector_live})."
            ),
        }
    if database_backend == "postgres" and database_live and vector_backend in HOSTED_VECTOR_BACKENDS and vector_live:
        return {
            "name": "runtime_hosted_storage",
            "status": "ok",
            "detail": "Runtime storage reports live Postgres primary storage and pgvector retrieval.",
        }
    return {
        "name": "runtime_hosted_storage",
        "status": "blocked",
        "detail": (
            "Hosted mode is configured, but runtime storage is not a live Postgres/pgvector backend "
            f"(database_backend={database_backend or 'unknown'}, database_live={database_live}, "
            f"vector_backend={vector_backend or 'unknown'}, vector_live={vector_live})."
        ),
    }


def _worker_check(hosted_mode: bool, worker_mode: str) -> dict:
    mode = (worker_mode or "inline").strip().lower()
    if not hosted_mode or mode in HOSTED_WORKER_MODES:
        return {
            "name": "background_workers",
            "status": "ok",
            "detail": f"Worker mode: {mode or 'inline'}.",
        }
    return {
        "name": "background_workers",
        "status": "blocked",
        "detail": "Set CORTEX_WORKER_MODE=external and run background workers before hosted rollout.",
    }


def _worker_queue_check(hosted_mode: bool, worker_mode: str, runtime: dict | None) -> dict:
    mode = (worker_mode or "inline").strip().lower()
    if not hosted_mode:
        return {
            "name": "background_worker_queue",
            "status": "ok",
            "detail": "Local mode can monitor queue health through /v1/jobs/health.",
        }
    if mode not in HOSTED_WORKER_MODES:
        return {
            "name": "background_worker_queue",
            "status": "blocked",
            "detail": "Hosted queue health is blocked until CORTEX_WORKER_MODE=external and workers are running.",
        }
    queue = (runtime or {}).get("worker_queue") if isinstance(runtime, dict) else None
    if not isinstance(queue, dict):
        return {
            "name": "background_worker_queue",
            "status": "blocked",
            "detail": "Hosted readiness needs runtime queue-health evidence from ready scoped-token users.",
        }
    queue_status = str(queue.get("status") or "blocked").lower()
    counts = queue.get("counts") if isinstance(queue.get("counts"), dict) else {}
    ready_user_count = int(queue.get("ready_user_count") or 0)
    queued = int(counts.get("queued") or 0)
    running = int(counts.get("running") or 0)
    failed = int(counts.get("failed") or 0)
    stale_running = int(queue.get("stale_running_count") or 0)
    oldest_queued_age_seconds = queue.get("oldest_queued_age_seconds")
    truncated = bool(queue.get("truncated"))
    ready_user_limit = int(queue.get("ready_user_limit") or 0)
    total_ready_user_count = int(queue.get("total_ready_user_count") or ready_user_count)
    if truncated:
        return {
            "name": "background_worker_queue",
            "status": "blocked",
            "detail": (
                "Hosted worker queue health is incomplete "
                f"({ready_user_count} of {total_ready_user_count} ready user(s) checked; "
                f"limit {ready_user_limit or 'unknown'}). Readiness needs complete queue-health evidence "
                "or an aggregate worker queue backend before hosted rollout."
            ),
        }
    if queue_status != "ok" or queued or running or failed or stale_running or ready_user_count <= 0:
        return {
            "name": "background_worker_queue",
            "status": "blocked",
            "detail": (
                f"Hosted worker queue is not healthy ({ready_user_count} ready user(s), status {queue_status}, "
                f"{queued} queued job(s), {running} running job(s), {failed} failed job(s), "
                f"{stale_running} stale running job(s), oldest queued age {oldest_queued_age_seconds})."
            ),
        }
    return {
        "name": "background_worker_queue",
        "status": "ok",
        "detail": f"Hosted worker queue has runtime health evidence for {ready_user_count} ready user(s); status {queue_status}.",
    }


def _observability_check(hosted_mode: bool, observability_enabled: bool) -> dict:
    if not hosted_mode or observability_enabled:
        return {
            "name": "observability",
            "status": "ok",
            "detail": "Operational health can be monitored for the current deployment mode.",
        }
    return {
        "name": "observability",
        "status": "blocked",
        "detail": "Set CORTEX_OBSERVABILITY_ENABLED=1 after wiring logs, metrics, and job-failure alerts.",
    }


def _control_plane_check(hosted_mode: bool, runtime: dict | None) -> dict:
    if not hosted_mode:
        return {
            "name": "control_plane_scoped_tokens",
            "status": "ok",
            "detail": "Local mode can run without hosted scoped-token control-plane evidence.",
        }
    control = (runtime or {}).get("control_plane") if isinstance(runtime, dict) else None
    if not isinstance(control, dict):
        return {
            "name": "control_plane_scoped_tokens",
            "status": "blocked",
            "detail": "Hosted readiness needs runtime control-plane evidence from the scoped token index.",
        }
    api_tokens = int(control.get("active_api_tokens") or 0)
    mcp_tokens = int(control.get("active_mcp_tokens") or 0)
    users = int(control.get("active_users") or 0)
    ready_users = int(control.get("active_ready_users") or 0)
    if ready_users > 0:
        return {
            "name": "control_plane_scoped_tokens",
            "status": "ok",
            "detail": f"Scoped control plane has {ready_users} active user(s) with both API and MCP tokens ({api_tokens} API token(s), {mcp_tokens} MCP token(s), {users} active user(s) total).",
        }
    return {
        "name": "control_plane_scoped_tokens",
        "status": "blocked",
        "detail": "Create at least one hosted user with both an active scoped API token and an active scoped MCP token before marking hosted readiness ok.",
    }
