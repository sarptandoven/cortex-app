from __future__ import annotations

import hashlib
import json
import math
import re


VECTOR_DIMENSIONS = 384
VECTOR_MODEL = "cortex-hash-v1"


def embed_text(text: str, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
    """Small deterministic embedding fallback for offline/vector plumbing tests.

    This is not a replacement for semantic embeddings. It lets the sqlite-vec
    backend index and query locally before a real embedding provider is wired in.
    """
    vector = [0.0] * dimensions
    tokens = re.findall(r"[a-z0-9][a-z0-9_\-]{1,}", text.lower())
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [round(value / norm, 6) for value in vector]


def embedding_json(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def embedding_source_text(*parts: str | None) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def embedding_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
