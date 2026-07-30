#!/usr/bin/env python3
"""Search local Cortex memory and print the cited JSON result."""

from __future__ import annotations

import json
import os
import sys

from cortex_client import CortexClient


def main() -> None:
    query = " ".join(sys.argv[1:]).strip() or "When does Project Atlas ship?"
    client = CortexClient(
        base_url=os.environ.get("CORTEX_BASE_URL", "http://127.0.0.1:8766"),
        token=os.environ.get("CORTEX_API_KEY", ""),
    )
    print(json.dumps(client.search(query, top_k=5), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
