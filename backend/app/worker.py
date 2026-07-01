from __future__ import annotations

from typing import Any, Iterable

from .extractor import now_iso


def normalize_worker_user_ids(user_ids: Iterable[str] | None, *, default_user_id: str = "local") -> list[str]:
    """Return a stable, de-duplicated list of user ids for a worker pass."""
    values = list(user_ids or [])
    if not values:
        values = [default_user_id]
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        user_id = str(value or "").strip() or default_user_id
        if user_id in seen:
            continue
        normalized.append(user_id)
        seen.add(user_id)
    return normalized


def summarize_failed_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "user_id": job.get("user_id"),
        "job_type": job.get("job_type"),
        "object_type": job.get("object_type"),
        "object_id": job.get("object_id"),
        "attempts": job.get("attempts"),
        "max_attempts": job.get("max_attempts"),
        "last_error": job.get("last_error"),
        "updated_at": job.get("updated_at"),
    }


def run_worker_tick(
    store: Any,
    user_ids: Iterable[str] | None = None,
    *,
    default_user_id: str = "local",
    limit_per_user: int = 10,
    worker_id: str = "cortex-worker",
    failed_job_limit: int = 20,
) -> dict[str, Any]:
    """Run due queued memory jobs for one or more users and return queue health."""
    normalized_user_ids = normalize_worker_user_ids(user_ids, default_user_id=default_user_id)
    limit = max(0, min(int(limit_per_user), 100))
    failed_limit = max(0, min(int(failed_job_limit), 100))
    users: dict[str, dict[str, Any]] = {}
    failed_jobs: list[dict[str, Any]] = []
    processed = 0
    pending = 0
    failed = 0
    source_syncs_scheduled = 0
    source_syncs_skipped = 0

    for user_id in normalized_user_ids:
        result = store.run_due_jobs(user_id, limit=limit, worker_id=worker_id)
        processed += int(result.get("processed") or 0)
        pending += int(result.get("pending") or 0)
        failed += int(result.get("failed") or 0)
        scheduled_source_syncs = result.get("scheduled_source_syncs") if isinstance(result, dict) else None
        if isinstance(scheduled_source_syncs, dict):
            source_syncs_scheduled += int(scheduled_source_syncs.get("scheduled") or 0)
            source_syncs_skipped += len(scheduled_source_syncs.get("skipped") or [])
        users[user_id] = result
        if failed_limit:
            for job in store.list_jobs(user_id, status="failed", limit=failed_limit):
                failed_jobs.append(summarize_failed_job(job))

    failed_jobs.sort(key=lambda job: str(job.get("updated_at") or ""), reverse=True)
    if failed_limit:
        failed_jobs = failed_jobs[:failed_limit]

    return {
        "ran_at": now_iso(),
        "worker_id": worker_id,
        "user_ids": normalized_user_ids,
        "limit_per_user": limit,
        "processed": processed,
        "pending": pending,
        "failed": failed,
        "source_syncs_scheduled": source_syncs_scheduled,
        "source_syncs_skipped": source_syncs_skipped,
        "users": users,
        "failed_jobs": failed_jobs,
    }
