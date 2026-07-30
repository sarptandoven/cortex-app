#!/usr/bin/env python3
"""Run the common search -> cited Ask -> bounded context workflow."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from cortex_client import CortexClient


def show(label: str, value: Any) -> None:
    print(f"\n## {label}")
    print(json.dumps(value, indent=2, sort_keys=True))


def main() -> None:
    task = " ".join(sys.argv[1:]).strip() or "Draft a release checklist from prior decisions"
    client = CortexClient(
        base_url=os.environ.get("CORTEX_BASE_URL", "http://127.0.0.1:8766"),
        token=os.environ.get("CORTEX_API_KEY", ""),
    )
    show("Search", client.search(task, top_k=5))
    show("Cited answer or abstention", client.ask(task, top_k=8))
    show(
        "Task context",
        client.context(task, intent="draft", token_budget=1200, surface="example"),
    )


if __name__ == "__main__":
    main()
