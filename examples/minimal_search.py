#!/usr/bin/env python3
"""Search local Cortex memory and print a short cited result."""

from __future__ import annotations

import argparse
import json
import os

from cortex_client import CortexClient


def _source_label(item: dict) -> str:
    return str(
        item.get("source_url")
        or item.get("source")
        or item.get("memory_id")
        or item.get("id")
        or "source unavailable"
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search Cortex and print concise, source-preserving results."
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="Question or search terms. Defaults to the Project Atlas demo query.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full retrieval payload and diagnostics as JSON.",
    )
    args = parser.parse_args()
    query = " ".join(args.query).strip() or "When does Project Atlas ship?"
    client = CortexClient(
        base_url=os.environ.get("CORTEX_BASE_URL", "http://127.0.0.1:8766"),
        token=os.environ.get("CORTEX_API_KEY", ""),
    )
    payload = client.search(query, top_k=5)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    results = payload.get("results", []) if isinstance(payload, dict) else []
    print(f"Query: {query}")
    if not results:
        print("No cited memory matched. Approve relevant items or try a more specific query.")
        return 0

    print(f"Found {len(results)} cited result{'s' if len(results) != 1 else ''}:")
    for index, item in enumerate(results, start=1):
        content = " ".join(str(item.get("content") or item.get("summary") or "").split())
        if len(content) > 220:
            content = f"{content[:217].rstrip()}…"
        print(f"\n[{index}] {content or '(no excerpt)'}")
        print(f"    Source: {_source_label(item)}")

    retrieval = payload.get("retrieval") if isinstance(payload, dict) else None
    if isinstance(retrieval, dict) and retrieval.get("degraded"):
        print(
            "\nNote: this development server is using its documented keyword-search "
            "fallback. The packaged app bundles on-device semantic embeddings."
        )
        print("Run again with --json to inspect retrieval diagnostics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
