#!/usr/bin/env python3
"""Build separate bounded context for three collaborating agent roles."""

from __future__ import annotations

import json
import os
import sys

from cortex_client import CortexClient


ROLES = {
    "planner": ("plan", 900),
    "researcher": ("recall", 1400),
    "writer": ("draft", 1100),
}


def main() -> None:
    task = " ".join(sys.argv[1:]).strip() or "Prepare the next Cortex beta announcement"
    client = CortexClient(
        base_url=os.environ.get("CORTEX_BASE_URL", "http://127.0.0.1:8766"),
        token=os.environ.get("CORTEX_API_KEY", ""),
    )
    packs = {
        role: client.context(
            f"{role}: {task}",
            intent=intent,
            token_budget=budget,
            surface=f"example-{role}",
        )
        for role, (intent, budget) in ROLES.items()
    }
    print(json.dumps(packs, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
