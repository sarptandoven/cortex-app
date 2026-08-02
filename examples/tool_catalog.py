#!/usr/bin/env python3
"""Inspect the OpenAI-compatible Cortex tool catalog."""

from __future__ import annotations

import json
import os

from cortex_client import CortexClient


def main() -> None:
    client = CortexClient(
        base_url=os.environ.get("CORTEX_BASE_URL", "http://127.0.0.1:8766"),
        token=os.environ.get("CORTEX_API_KEY", ""),
    )
    tools = client.openai_tools()
    summary = [
        {
            "name": tool["function"]["name"],
            "description": tool["function"].get("description", ""),
        }
        for tool in tools
    ]
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
