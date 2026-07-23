# CMP example envelopes (generated)

These two files are REAL `/v1/context?format=smp` responses captured by `scripts/cmp_demo.py` against the worktree backend (hash embedder, deterministic).

- `example_smp_claude.json` — a `model=claude` pack (legend omitted for size; item content truncated).
- `example_smp_delta_turn2.json` — a session turn-2 envelope whose `working_memory` block carries the delta `{new, evicted, superseded, cursor}`.

Regenerate: `python3 scripts/cmp_demo.py`.
