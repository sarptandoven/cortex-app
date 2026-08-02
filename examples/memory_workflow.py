#!/usr/bin/env python3
"""Run the common search -> cited Ask -> bounded context workflow."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from cortex_client import CortexClient


def show(label: str, value: Any) -> None:
    print(f"\n## {label}")
    print(json.dumps(value, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run factual retrieval and task-context assembly against Cortex."
    )
    parser.add_argument(
        "--query",
        default="When does Project Atlas ship?",
        help="Factual question used for Search and cited Ask.",
    )
    parser.add_argument(
        "--task",
        default="Draft a concise Project Atlas release update.",
        help="Work-shaped instruction used to assemble a bounded Context pack.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = CortexClient(
        base_url=os.environ.get("CORTEX_BASE_URL", "http://127.0.0.1:8766"),
        token=os.environ.get("CORTEX_API_KEY", ""),
    )
    show("Search", client.search(args.query, top_k=5))
    show("Cited answer or abstention", client.ask(args.query, top_k=8))
    show(
        "Task context",
        client.context(args.task, intent="draft", token_budget=1200, surface="example"),
    )


if __name__ == "__main__":
    main()
