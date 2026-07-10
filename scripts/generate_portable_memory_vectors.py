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


VECTOR_DIR = ROOT / "spec" / "portable-memory" / "v2" / "test-vectors"
OUTPUT = VECTOR_DIR / "cortex-python.json"
INTEROP_OUTPUT = VECTOR_DIR / "cortex-python-n3.json"


def _signed_bundle(payload: dict, proof: dict) -> dict:
    payload_bytes = CortexStore._canonical_export_bytes(payload)
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
            "chain_head": proof["chain_head"],
            "event_count": proof["event_count"],
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
    proof = {
        "chain_version": CortexStore.INTEGRITY_CHAIN_VERSION,
        "genesis": CortexStore.INTEGRITY_CHAIN_GENESIS,
        "event_count": 0,
        "event_fingerprints": [],
        "chain_head": CortexStore.INTEGRITY_CHAIN_GENESIS,
    }
    return _signed_bundle(payload, proof)


def build_interop_vector() -> dict:
    user_id = "portable-vector-user"

    def memory(index: int, content: str, summary: str, topics: list[str]) -> dict:
        return {
            "id": f"mem_portable_interop_{index}",
            "user_id": user_id,
            "kind": "decision" if index == 1 else "observation",
            "layer": "decision" if index == 1 else "event",
            "content": content,
            "summary": summary,
            "source": "note",
            "source_url": f"note://portable-interop/{index}",
            "confidence": "confirmed",
            "importance": 5,
            "status": "active",
            "topics": ["Café", "launch", *topics],
            "trust_score": 1.0,
            "author_class": "user",
            "author_principal_id": user_id,
            "provenance": {
                "fixture": True,
                "receipt_id": f"evt_portable_interop_{index}",
                "source_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
            "entity_ids": [],
            "occurrences": 1,
            "captured_at": f"2026-07-10T00:0{index}:00+00:00",
            "recorded_at": f"2026-07-10T00:0{index}:00+00:00",
            "updated_at": f"2026-07-10T00:0{index}:00+00:00",
            "raw_excerpt": content,
            "occurred_at": None,
            "valid_from": None,
            "valid_to": None,
            "superseded_by": None,
            "superseded_at": None,
            "sector": "work",
            "source_type": "note",
        }

    memories = [
        memory(
            1,
            "Café launch database decision: use PostgreSQL.",
            "Café launch uses PostgreSQL.",
            ["PostgreSQL", "database"],
        ),
        memory(
            2,
            "Café launch date is October 14.",
            "Café launch is scheduled for October 14.",
            ["October 14", "schedule"],
        ),
        memory(
            3,
            "Elena owns the Café launch rollout.",
            "Café launch rollout owner is Elena.",
            ["Elena", "owner"],
        ),
    ]
    payload = {
        "exported_at": "2026-07-10T00:00:00+00:00",
        "user_id": user_id,
        "stats": {"quality_score": 1.0, "memory_count": len(memories)},
        "imports": [],
        "captures": [],
        "memories": memories,
        "tasks": [],
        "entities": [],
        "edges": [],
    }
    fingerprints = [hashlib.sha256(f"portable-interop-event-{index}".encode()).hexdigest() for index in range(1, 4)]
    chain_head = CortexStore.INTEGRITY_CHAIN_GENESIS
    for fingerprint in fingerprints:
        chain_head = hashlib.sha256(f"{chain_head}:{fingerprint}".encode("utf-8")).hexdigest()
    proof = {
        "chain_version": CortexStore.INTEGRITY_CHAIN_VERSION,
        "genesis": CortexStore.INTEGRITY_CHAIN_GENESIS,
        "event_count": len(fingerprints),
        "event_fingerprints": fingerprints,
        "chain_head": chain_head,
    }
    return _signed_bundle(payload, proof)


def render_vector() -> str:
    return json.dumps(build_vector(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_interop_vector() -> str:
    return json.dumps(build_interop_vector(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    check = sys.argv[1:] == ["--check"]
    if sys.argv[1:] and not check:
        raise SystemExit("usage: generate_portable_memory_vectors.py [--check]")
    rendered = {OUTPUT: render_vector(), INTEROP_OUTPUT: render_interop_vector()}
    if check:
        stale = [path for path, content in rendered.items() if not path.exists() or path.read_text(encoding="utf-8") != content]
        if stale:
            raise SystemExit(f"portable-memory vector is stale: {', '.join(str(path) for path in stale)}")
        for path in rendered:
            print(f"verified {path}")
        return
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in rendered.items():
        path.write_text(content, encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
