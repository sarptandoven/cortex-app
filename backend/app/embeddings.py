from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


VECTOR_DIMENSIONS = 384
VECTOR_MODEL = "cortex-hash-v1"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass(frozen=True)
class EmbeddingResult:
    vector: list[float]
    model: str
    provider: str
    dimensions: int


def configured_embedding_provider() -> str:
    provider = os.environ.get("CORTEX_EMBEDDING_PROVIDER", "hash").strip().lower()
    return provider if provider in {"hash", "openai"} else "hash"


def configured_embedding_model() -> str:
    if configured_embedding_provider() == "openai":
        return os.environ.get("CORTEX_EMBEDDING_MODEL", DEFAULT_OPENAI_EMBEDDING_MODEL).strip() or DEFAULT_OPENAI_EMBEDDING_MODEL
    return VECTOR_MODEL


def configured_embedding_dimensions(default: int = VECTOR_DIMENSIONS) -> int:
    raw = os.environ.get("CORTEX_EMBEDDING_DIMENSIONS", "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def embedding_status() -> dict[str, Any]:
    provider = configured_embedding_provider()
    dimensions = configured_embedding_dimensions()
    return {
        "provider": provider,
        "model": configured_embedding_model(),
        "dimensions": dimensions,
        "schema_dimensions": VECTOR_DIMENSIONS,
        "index_compatible": dimensions == VECTOR_DIMENSIONS,
        "network_required": provider == "openai",
        "strict": _strict_openai_embeddings(),
    }


def embed_text(text: str, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
    return embed_text_result(text, dimensions=dimensions).vector


def embed_text_result(text: str, dimensions: int = VECTOR_DIMENSIONS) -> EmbeddingResult:
    resolved_dimensions = configured_embedding_dimensions(dimensions)
    if configured_embedding_provider() == "openai":
        try:
            return _openai_embedding(text, resolved_dimensions)
        except Exception:
            if _strict_openai_embeddings():
                raise
    return EmbeddingResult(
        vector=hash_embed_text(text, dimensions=resolved_dimensions),
        model=VECTOR_MODEL,
        provider="hash",
        dimensions=resolved_dimensions,
    )


def hash_embed_text(text: str, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
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


def _openai_embedding(text: str, dimensions: int) -> EmbeddingResult:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for OpenAI embeddings")
    model = configured_embedding_model()
    payload: dict[str, Any] = {
        "input": text,
        "model": model,
        "encoding_format": "float",
    }
    if model.startswith("text-embedding-3"):
        payload["dimensions"] = dimensions
    request = urllib.request.Request(
        os.environ.get("CORTEX_OPENAI_EMBEDDINGS_URL", "https://api.openai.com/v1/embeddings"),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_openai_timeout_seconds()) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI embeddings request failed: HTTP {exc.code}: {detail}") from exc
    vector = body["data"][0]["embedding"]
    if len(vector) != dimensions:
        raise ValueError(f"OpenAI embedding dimensions mismatch: expected {dimensions}, got {len(vector)}")
    return EmbeddingResult(
        vector=[float(value) for value in vector],
        model=str(body.get("model") or model),
        provider="openai",
        dimensions=dimensions,
    )


def _openai_timeout_seconds() -> float:
    try:
        return max(1.0, float(os.environ.get("CORTEX_EMBEDDING_TIMEOUT_SECONDS", "10")))
    except ValueError:
        return 10.0


def _strict_openai_embeddings() -> bool:
    return os.environ.get("CORTEX_EMBEDDING_STRICT", "").strip().lower() in {"1", "true", "yes"}


def embedding_json(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def embedding_source_text(*parts: str | None) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def embedding_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
