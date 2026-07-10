#!/usr/bin/env python3
"""Generate the deterministic cross-language portable-memory v2 test vector."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.storage import CortexStore  # noqa: E402


OUTPUT = ROOT / "spec" / "portable-memory" / "v2" / "test-vectors" / "cortex-python.json"


def build_vector() -> dict:
    payload = {
        "exported_at": "2026-07-10T00:00:00+00:00",
        "user_id": "portable-vector-user",
        "stats": {"quality_score": 1.0, "memory_count": 1},
        "imports": [],
        "captures": [],
        "memories": [
            {
                "id": "mem_portable_vector_1",
                "user_id": "portable-vector-user",
                "kind": "decision",
                "layer": "decision",
                "content": "Café launch decision: use PostgreSQL 🚀.",
                "summary": "Use PostgreSQL for the Café launch.",
                "source": "note",
                "source_url": "note://portable-vector/1",
                "confidence": "confirmed",
                "importance": 5,
                "status": "active",
                "topics": ["Café", "launch", "PostgreSQL"],
                "trust_score": 1.0,
                "author_class": "user",
                "author_principal_id": "portable-vector-user",
                "provenance": {"fixture": True, "escaped": "line one\nline two"},
                "entity_ids": [],
                "occurrences": 1,
                "captured_at": "2026-07-10T00:00:00+00:00",
                "recorded_at": "2026-07-10T00:00:00+00:00",
                "updated_at": "2026-07-10T00:00:00+00:00",
                "raw_excerpt": "Café launch decision",
                "occurred_at": None,
                "valid_from": None,
                "valid_to": None,
                "superseded_by": None,
                "superseded_at": None,
                "sector": "work",
                "source_type": "note",
            }
        ],
        "tasks": [],
        "entities": [],
        "edges": [],
    }
    payload_bytes = CortexStore._canonical_export_bytes(payload)
    proof = {
        "chain_version": CortexStore.INTEGRITY_CHAIN_VERSION,
        "genesis": CortexStore.INTEGRITY_CHAIN_GENESIS,
        "event_count": 0,
        "event_fingerprints": [],
        "chain_head": CortexStore.INTEGRITY_CHAIN_GENESIS,
    }
    proof_sha256 = hashlib.sha256(CortexStore._portable_v2_proof_bytes(proof)).hexdigest()

    private_seed = bytes(range(32))
    private_key = Ed25519PrivateKey.from_private_bytes(private_seed)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = hashlib.sha256(public_key).hexdigest()
    counts = {
        name: len(payload[name])
        for name in ("captures", "memories", "tasks", "entities", "edges", "imports")
    }
    bundle = {
        "cortex_bundle_version": CortexStore.PORTABLE_MEMORY_OUTER_VERSION,
        "protocol": {
            "name": CortexStore.PORTABLE_MEMORY_PROTOCOL,
            "version": CortexStore.PORTABLE_MEMORY_VERSION,
            "signature_format": CortexStore.PORTABLE_MEMORY_SIGNATURE_DOMAIN,
            "signature_algorithm": "ed25519",
            "key_id_algorithm": "sha256-raw-ed25519-public-key",
            "payload_encoding": CortexStore.PORTABLE_MEMORY_PAYLOAD_ENCODING,
            "payload_digest_algorithm": "sha256",
            "proof_format": CortexStore.PORTABLE_MEMORY_PROOF_FORMAT,
            "proof_digest_algorithm": "sha256",
            "chain_algorithm": CortexStore.PORTABLE_MEMORY_CHAIN_ALGORITHM,
            "capabilities": [
                "signed-export",
                "signer-pinning",
                "tenant-rebound-import",
                "belief-timeline-preservation",
            ],
        },
        "manifest": {
            "generated_at": "2026-07-10T00:00:00+00:00",
            "user_id": payload["user_id"],
            "chain_version": CortexStore.INTEGRITY_CHAIN_VERSION,
            "chain_head": CortexStore.INTEGRITY_CHAIN_GENESIS,
            "event_count": 0,
            "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "payload_bytes": len(payload_bytes),
            "proof_sha256": proof_sha256,
            "signing_key_id": key_id,
            "record_counts": counts,
        },
        "payload": payload,
        "payload_bytes": CortexStore._portable_b64encode(payload_bytes),
        "integrity_proof": proof,
    }
    signature = private_key.sign(CortexStore._portable_v2_signature_bytes(bundle))
    bundle["signature"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "public_key": CortexStore._portable_b64encode(public_key),
        "value": CortexStore._portable_b64encode(signature),
    }
    return bundle


def render_vector() -> str:
    return json.dumps(build_vector(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    check = sys.argv[1:] == ["--check"]
    if sys.argv[1:] and not check:
        raise SystemExit("usage: generate_portable_memory_vectors.py [--check]")
    rendered = render_vector()
    if check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"portable-memory vector is stale: {OUTPUT}")
        print(f"verified {OUTPUT}")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
