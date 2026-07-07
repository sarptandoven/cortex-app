#!/usr/bin/env python3
"""Delivery / push CI gate (Phase 8).

Deterministic, stdlib-only, in-process. Proves the egress path is safe:
  - SSRF guard rejects internal targets and http public targets, accepts https public;
  - deliver_webhook honors the guard and reports success/failure from an injectable sender;
  - a sector-scoped brief never leaks another sector's memory and every packed item is cited
    (over-share guardrail);
  - the payload never includes the identity/persona layer.

Exits 0 when all hard checks pass, 1 otherwise. `python3 scripts/delivery_eval.py`.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.database import init_db  # noqa: E402
from backend.app.delivery import build_delivery_payload, deliver_webhook, is_safe_webhook_url  # noqa: E402
from backend.app.extractor import extract_context  # noqa: E402
from backend.app.storage import CortexStore  # noqa: E402

USER = "delivery-eval-user"


def _seed(store: CortexStore) -> None:
    rows = [
        ("alpha", "Decided to adopt sqlite-vec for Project Alpha vector search.", "cortex-source://notes#alpha1"),
        ("alpha", "Project Alpha ships the beta on the first of next month.", "cortex-source://notes#alpha2"),
        ("beta", "Project Beta uses a separate billing provider and must stay isolated.", "cortex-source://notes#beta1"),
        ("beta", "Beta's launch is gated on a security review.", "cortex-source://notes#beta2"),
    ]
    for sector, content, url in rows:
        extracted = extract_context(content, "notes")
        extracted["sector"] = sector
        store.save_capture(
            user_id=USER,
            content=content,
            source="notes",
            source_url=url,
            title=None,
            extracted=extracted,
            cite_capture_provenance=True,
            auto_approve=True,
        )


def _iter_pack_items(pack: dict) -> list[dict]:
    items: list[dict] = []
    for layer in (pack.get("layers") or []):
        if isinstance(layer, dict):
            items.extend(item for item in (layer.get("items") or []) if isinstance(item, dict))
    return items


def _identity_present(pack: dict) -> bool:
    return any(
        isinstance(layer, dict) and layer.get("layer") == "identity" and layer.get("items")
        for layer in (pack.get("layers") or [])
    )


def run_delivery_eval(db_path: Path, vault_path: Path | None = None) -> dict:
    init_db(db_path)
    store = CortexStore(db_path, vault_path)
    _seed(store)
    checks: list[dict] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # 1) SSRF guard.
    ssrf_cases = [
        ("http://127.0.0.1/hook", False),
        ("https://127.0.0.1/hook", False),
        ("http://10.0.0.5/hook", False),
        ("https://169.254.169.254/latest", False),
        ("http://8.8.8.8/hook", False),        # public but not https
        ("https://8.8.8.8/hook", True),        # public https (numeric -> no DNS)
        ("ftp://example.com/x", False),
        ("https://user:pass@8.8.8.8/x", False),
    ]
    for url, expected in ssrf_cases:
        ok, _ = is_safe_webhook_url(url)
        record(f"ssrf:{url}", ok == expected, f"expected {expected} got {ok}")

    # 2) deliver_webhook honors the guard + reports sender status (injected, no network).
    sent: list[str] = []

    def ok_sender(url, body, headers):
        sent.append(url)
        return 200

    def fail_sender(url, body, headers):
        sent.append(url)
        return 500

    good = deliver_webhook("https://8.8.8.8/hook", {"x": 1}, request_fn=ok_sender)
    record("deliver:success", good.get("ok") is True and good.get("status") == 200)
    bad = deliver_webhook("https://8.8.8.8/hook", {"x": 1}, request_fn=fail_sender)
    record("deliver:server_error", bad.get("ok") is False and bad.get("status") == 500)
    blocked = deliver_webhook("http://127.0.0.1/hook", {"x": 1}, request_fn=ok_sender)
    record("deliver:ssrf_blocked_no_send", blocked.get("ok") is False and "127.0.0.1/hook" not in sent[-1:] and len([u for u in sent if "127.0.0.1" in u]) == 0)

    # 3) Over-share guardrail: a sector-scoped brief never leaks another sector + is cited-only.
    payload = build_delivery_payload(store, USER, task="what is the plan", sector="alpha", token_budget=1500)
    items = _iter_pack_items(payload["pack"])
    no_leak = all(str(item.get("sector") or "") in {"alpha", ""} for item in items)
    record("overshare:no_cross_sector", no_leak, f"{[item.get('sector') for item in items]}")
    all_cited = all(item.get("source_url") for item in items)
    record("overshare:cited_only", all_cited)
    record("overshare:identity_omitted", not _identity_present(payload["pack"]))

    failures = [c for c in checks if not c["ok"]]
    return {
        "ssrf_ok": all(c["ok"] for c in checks if c["check"].startswith("ssrf:")),
        "deliver_ok": all(c["ok"] for c in checks if c["check"].startswith("deliver:")),
        "overshare_ok": all(c["ok"] for c in checks if c["check"].startswith("overshare:")),
        "counts": {"total": len(checks), "failures": len(failures)},
        "checks": checks,
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        summary = run_delivery_eval(Path(tmp) / "cortex.db", Path(tmp) / "vault")
    print(json.dumps(summary, indent=2))
    return 1 if summary["counts"]["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
