from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
import uuid


DUMMY_OPENAI_KEY = "sk-" + ("0" * 24)


def request(base_url: str, token: str, path: str, method: str = "GET", data: dict | None = None, text: bool = False):
    body = None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base_url.rstrip("/") + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    if text:
        return raw.decode("utf-8")
    return json.loads(raw.decode("utf-8"))


def form_request(base_url: str, path: str, data: dict) -> str:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Cortex HTTP smoke and lifecycle checks against a running backend.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--token", default="dev-local-key")
    args = parser.parse_args()
    marker = f"cortex-battle-{uuid.uuid4().hex[:10]}"

    health = request(args.base_url, args.token, "/health")
    assert health["status"] == "ok", health
    assert health["health_contract"] >= 3, health
    assert "reliability-hardening" in health["features"], health
    assert "operational-readiness" in health["features"], health

    settings = request(args.base_url, args.token, "/v1/settings")
    assert settings["review_new_captures"] in {True, False}, settings
    assert settings["allow_pending_in_context"] in {True, False}, settings
    assert settings["allow_agent_reads"] in {True, False}, settings
    assert settings["allow_agent_writes"] in {True, False}, settings
    assert settings["allow_agent_exports"] in {True, False}, settings
    assert settings["redact_sensitive_context"] in {True, False}, settings
    request(
        args.base_url,
        args.token,
        "/v1/settings",
        "PUT",
        {
            "review_new_captures": True,
            "allow_pending_in_context": True,
            "context_pack_limit": 12,
            "allow_agent_reads": True,
            "allow_agent_writes": True,
            "allow_agent_exports": True,
            "redact_sensitive_context": True,
        },
    )

    capture = request(
        args.base_url,
        args.token,
        "/v1/captures",
        "POST",
        {
            "content": (
                f"Battle marker {marker}. "
                "Vamika decided Cortex should keep SQLite for local macOS testing and use Supabase for hosted beta. "
                "Cortex needs a review inbox, markdown export, diagnostics, and MCP tools for ChatGPT and Claude. "
                "The next step is to battle-test backup and search maintenance. "
                f"Do not leak password=supersecret123 or {DUMMY_OPENAI_KEY} in shared AI context."
            ),
            "source": "http-battle-test",
            "title": "HTTP battle test",
        },
    )
    capture_id = capture["capture_id"]
    entity_kinds = {entity["name"]: entity["kind"] for entity in capture["entities"]}
    expected = {
        "Cortex": "project",
        "ChatGPT": "org",
        "Claude": "org",
        "SQLite": "org",
        "Supabase": "org",
        "MCP": "topic",
        "Vamika": "person",
    }
    for name, kind in expected.items():
        assert entity_kinds.get(name) == kind, entity_kinds

    inbox = request(args.base_url, args.token, "/v1/inbox?limit=10")
    assert any(item["id"] == capture_id for item in inbox["results"]), inbox
    pending_loop = request(args.base_url, args.token, "/v1/loop")
    assert pending_loop["primary_action"]["action"] == "review", pending_loop
    assert any(step["key"] == "review" and step["status"] == "current" for step in pending_loop["steps"]), pending_loop

    search_query = urllib.parse.quote(marker)
    assert request(args.base_url, args.token, f"/v1/search?query={search_query}&limit=5")["results"]

    diagnostics = request(args.base_url, args.token, "/v1/diagnostics")
    assert diagnostics["quick_check"] == "ok", diagnostics
    assert diagnostics["vault"]["format"] == "cortex-local-vault", diagnostics
    assert diagnostics["vault"]["record_counts"]["captures"] >= 1, diagnostics

    review = request(args.base_url, args.token, "/v1/review/today")
    assert review["captured_today"] >= 1, review
    assert any(item["id"] == capture_id for item in review["pending"]), review
    assert review["recommended_actions"], review
    assert "# Cortex Context Pack" in review["context_pack"], review["context_pack"][:240]

    context_query = urllib.parse.quote("ChatGPT Claude")
    context_pack = request(args.base_url, args.token, f"/v1/context-pack?query={context_query}&limit=5", text=True)
    assert "# Cortex Context Pack" in context_pack and "Suggested Assistant Instruction" in context_pack, context_pack[:240]
    assert "ChatGPT" in context_pack and "Claude" in context_pack, context_pack[:240]
    reuse = request(args.base_url, args.token, "/v1/loop/reuse", "POST", {"surface": "battle-test", "query": "ChatGPT Claude", "target": "clipboard"})
    assert reuse["recorded"] is True and reuse["product_loop"]["counts"]["reused_today"] >= 1, reuse

    secret_query = urllib.parse.quote("password")
    secret_context_pack = request(args.base_url, args.token, f"/v1/context-pack?query={secret_query}&limit=5", text=True)
    assert "[REDACTED_SECRET]" in secret_context_pack and "[REDACTED_OPENAI_KEY]" in secret_context_pack, secret_context_pack
    assert "supersecret123" not in secret_context_pack and DUMMY_OPENAI_KEY not in secret_context_pack, secret_context_pack

    rebuild = request(args.base_url, args.token, "/v1/maintenance/rebuild-search", "POST")
    assert rebuild["indexed_memories"] >= 1, rebuild

    backup = request(args.base_url, args.token, "/v1/backups", "POST")
    assert backup["size_bytes"] > 0, backup
    assert backup["backup_path"].endswith(".zip"), backup

    reliability = request(args.base_url, args.token, "/v1/reliability/report")
    assert reliability["health_contract"] >= 3, reliability
    assert reliability["latest_backup"]["backup_path"].endswith(".zip"), reliability
    assert any(check["name"] == "sqlite_quick_check" and check["status"] == "ok" for check in reliability["checks"]), reliability

    support = request(args.base_url, args.token, "/v1/support/bundle")
    assert support["bundle_schema"] >= 1, support
    assert support["backend"]["health_contract"] >= 3, support
    assert "operational-readiness" in support["backend"]["features"], support
    assert support["privacy"]["contains_raw_capture_text"] is False, support
    assert support["privacy"]["contains_memory_content"] is False, support
    assert support["privacy"]["contains_context_pack"] is False, support
    support_payload = json.dumps(support)
    assert "supersecret123" not in support_payload and DUMMY_OPENAI_KEY not in support_payload, support_payload[:500]

    repair = request(args.base_url, args.token, "/v1/maintenance/repair-storage", "POST")
    assert repair["backup_path"].endswith(".zip"), repair
    assert repair["after"]["quick_check"] == "ok", repair

    markdown = request(args.base_url, args.token, "/v1/export.md", text=True)
    assert "# Cortex Export" in markdown and "Supabase" in markdown, markdown[:240]
    assert "[REDACTED_SECRET]" in markdown and "supersecret123" not in markdown, markdown[:240]

    tools = request(args.base_url, args.token, "/mcp", "POST", {"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}})
    tool_names = {tool["name"] for tool in tools["result"]["tools"]}
    assert {"remember_this", "search_memory", "get_daily_review", "get_product_loop", "build_context_pack", "get_memory_stats", "get_memory_diagnostics", "get_reliability_report", "get_support_bundle", "repair_memory_storage", "rebuild_index_from_vault", "get_trust_summary", "get_audit_log"}.issubset(tool_names), tool_names

    mcp_pack = request(
        args.base_url,
        args.token,
        "/mcp",
        "POST",
        {"jsonrpc": "2.0", "id": "pack", "method": "tools/call", "params": {"name": "build_context_pack", "arguments": {"query": "Supabase", "limit": 5}}},
    )
    mcp_text = mcp_pack["result"]["content"][0]["text"]
    assert "# Cortex Context Pack" in mcp_text and "Supabase" in mcp_text, mcp_text[:240]

    mcp_loop = request(
        args.base_url,
        args.token,
        "/mcp",
        "POST",
        {"jsonrpc": "2.0", "id": "loop", "method": "tools/call", "params": {"name": "get_product_loop", "arguments": {}}},
    )
    assert "primary_action" in mcp_loop["result"]["content"][0]["text"], mcp_loop

    mcp_reliability = request(
        args.base_url,
        args.token,
        "/mcp",
        "POST",
        {"jsonrpc": "2.0", "id": "reliability", "method": "tools/call", "params": {"name": "get_reliability_report", "arguments": {}}},
    )
    assert "sqlite_quick_check" in mcp_reliability["result"]["content"][0]["text"], mcp_reliability

    mcp_support = request(
        args.base_url,
        args.token,
        "/mcp",
        "POST",
        {"jsonrpc": "2.0", "id": "support", "method": "tools/call", "params": {"name": "get_support_bundle", "arguments": {}}},
    )
    assert "operational-readiness" in mcp_support["result"]["content"][0]["text"], mcp_support

    trust = request(args.base_url, args.token, "/v1/trust/summary")
    assert trust["trust_score"] >= 0 and trust["settings"]["redact_sensitive_context"] is True, trust

    blocked_settings = request(args.base_url, args.token, "/v1/settings", "PUT", {"allow_agent_writes": False})
    assert blocked_settings["allow_agent_writes"] is False, blocked_settings
    blocked_write = request(
        args.base_url,
        args.token,
        "/mcp",
        "POST",
        {"jsonrpc": "2.0", "id": "blocked-write", "method": "tools/call", "params": {"name": "remember_this", "arguments": {"content": "This MCP write should be blocked."}}},
    )
    assert "error" in blocked_write and "writes are disabled" in blocked_write["error"]["message"], blocked_write
    request(args.base_url, args.token, "/v1/settings", "PUT", {"allow_agent_writes": True})

    audit = request(args.base_url, args.token, "/v1/audit-log?limit=20")
    assert any(item["object_type"] == "agent" and item["event_type"] == "tool_call" for item in audit["results"]), audit

    browser_html = form_request(
        args.base_url,
        "/capture",
        {
            "token": args.token,
            "source": "browser-bookmarklet",
            "title": "Browser smoke capture",
            "url": "https://example.com/cortex-smoke",
            "content": "Browser bookmarklet capture should save selected page text into Cortex local memory.",
        },
    )
    assert "saved" in browser_html.lower() and "Save to Cortex" in browser_html, browser_html[:240]
    browser_inbox = request(args.base_url, args.token, "/v1/inbox?limit=20")
    browser_capture_id = next(item["id"] for item in browser_inbox["results"] if item.get("title") == "Browser smoke capture")
    assert request(args.base_url, args.token, f"/v1/captures/{browser_capture_id}/archive", "POST")["archived"]

    strict = request(args.base_url, args.token, "/v1/settings", "PUT", {"allow_pending_in_context": False, "context_pack_limit": 6})
    assert strict["allow_pending_in_context"] is False and strict["context_pack_limit"] == 6, strict
    assert request(args.base_url, args.token, f"/v1/search?query={search_query}&limit=5")["results"] == []

    assert request(args.base_url, args.token, f"/v1/captures/{capture_id}/approve", "POST")["approved"]
    assert request(args.base_url, args.token, f"/v1/search?query={search_query}&limit=5")["results"]
    assert request(args.base_url, args.token, f"/v1/captures/{capture_id}/archive", "POST")["archived"]
    assert request(args.base_url, args.token, f"/v1/search?query={search_query}&limit=5")["results"] == []
    request(
        args.base_url,
        args.token,
        "/v1/settings",
        "PUT",
        {
            "review_new_captures": True,
            "allow_pending_in_context": True,
            "context_pack_limit": 12,
            "allow_agent_reads": True,
            "allow_agent_writes": True,
            "allow_agent_exports": True,
            "redact_sensitive_context": True,
        },
    )
    final_stats = request(args.base_url, args.token, "/v1/stats")
    assert final_stats["pending_captures"] >= 0, final_stats

    print(json.dumps({"status": "ok", "marker": marker, "capture_id": capture_id, "backup_path": backup["backup_path"], "mcp_tools": sorted(tool_names)}, indent=2))


if __name__ == "__main__":
    main()
