from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


VECTOR_DIMENSIONS = 384
VECTOR_MODEL = "cortex-hash-v1"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"

# Local, CPU-only, no-API-key neural embeddings (MinishLab model2vec, MIT).
# potion-base-8M emits 256-dim vectors natively (NOT 384) — so it is not
# drop-in compatible with the existing 384-dim sqlite-vec index. We surface
# that honestly via embedding_status().index_compatible rather than padding or
# truncating into the index. See NOTE at bottom of file re: making this default.
DEFAULT_MODEL2VEC_MODEL = "minishlab/potion-base-8M"
MODEL2VEC_DIMENSIONS = 256

_MODEL2VEC_MODEL: Any = None
_MODEL2VEC_MODEL_KEY: str | None = None
_MODEL2VEC_LOCK = threading.Lock()


@dataclass(frozen=True)
class EmbeddingResult:
    vector: list[float]
    model: str
    provider: str
    dimensions: int


def configured_embedding_provider() -> str:
    provider = os.environ.get("CORTEX_EMBEDDING_PROVIDER", "hash").strip().lower()
    return provider if provider in {"hash", "openai", "model2vec"} else "hash"


def configured_embedding_model() -> str:
    provider = configured_embedding_provider()
    if provider == "openai":
        return os.environ.get("CORTEX_EMBEDDING_MODEL", DEFAULT_OPENAI_EMBEDDING_MODEL).strip() or DEFAULT_OPENAI_EMBEDDING_MODEL
    if provider == "model2vec":
        return os.environ.get("CORTEX_EMBEDDING_MODEL", DEFAULT_MODEL2VEC_MODEL).strip() or DEFAULT_MODEL2VEC_MODEL
    return VECTOR_MODEL


def configured_embedding_dimensions(default: int = VECTOR_DIMENSIONS) -> int:
    raw = os.environ.get("CORTEX_EMBEDDING_DIMENSIONS", "").strip()
    if not raw:
        # model2vec emits a fixed native dimension (256 for potion-base-8M);
        # honour that as the default so status/index_compatible are truthful.
        if configured_embedding_provider() == "model2vec":
            return MODEL2VEC_DIMENSIONS
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def embedding_status(schema_dimensions: int | None = None) -> dict[str, Any]:
    """Report the active embedding provider and how it lines up with the vector index.

    `schema_dimensions` is the ACTUAL dimension of this store's `memory_vec` index (from
    `vec_index_meta`). Pass it so `schema_dimensions`/`index_compatible` reflect reality —
    the index is reconciled to the model's native dimension at store construction
    (see CortexStore._ensure_vector_index), so a model2vec store's real index is 256, not
    the 384 build-time constant. When omitted (callers without DB access), fall back to
    VECTOR_DIMENSIONS so behaviour is unchanged."""
    provider = configured_embedding_provider()
    dimensions = configured_embedding_dimensions()
    schema = int(schema_dimensions) if schema_dimensions else VECTOR_DIMENSIONS
    return {
        "provider": provider,
        "model": configured_embedding_model(),
        "dimensions": dimensions,
        "schema_dimensions": schema,
        "index_compatible": dimensions == schema,
        "network_required": provider == "openai",
        "strict": _strict_embeddings(),
    }


def embed_text(text: str, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
    return embed_text_result(text, dimensions=dimensions).vector


def embed_text_result(text: str, dimensions: int = VECTOR_DIMENSIONS) -> EmbeddingResult:
    resolved_dimensions = configured_embedding_dimensions(dimensions)
    provider = configured_embedding_provider()
    if provider == "openai":
        try:
            return _openai_embedding(text, resolved_dimensions)
        except Exception:
            # In strict mode a provider failure is fatal; otherwise degrade to the deterministic
            # hash fallback so retrieval never hard-fails offline.
            if _strict_embeddings():
                raise
    elif provider == "model2vec":
        try:
            return _model2vec_embedding(text, resolved_dimensions)
        except Exception:
            # Real local model unavailable (deps/model absent) or failed to encode: fall back to
            # hash so a machine without the bundled model still works. Strict mode surfaces it.
            if _strict_embeddings():
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


def _strict_embeddings() -> bool:
    return os.environ.get("CORTEX_EMBEDDING_STRICT", "").strip().lower() in {"1", "true", "yes"}


def _load_model2vec_model() -> Any:
    """Lazily load the on-device Model2Vec static model once and cache it.

    Guarded so the shipping backend runs with or without the bundled model: an ImportError (the
    model2vec dep isn't on the bundled PYTHONPATH) or a load failure propagates to the caller,
    which then degrades to the deterministic hash embedding. Loading a static model is CPU-only,
    needs no API key, and (with a local path or a cached model) touches no network.
    """
    global _MODEL2VEC_MODEL, _MODEL2VEC_MODEL_KEY
    # Prefer an explicit bundled/local model directory; otherwise use the configured model id.
    local_path = os.environ.get("CORTEX_MODEL2VEC_PATH", "").strip()
    key = local_path or configured_embedding_model()
    cached = _MODEL2VEC_MODEL
    if cached is not None and _MODEL2VEC_MODEL_KEY == key:
        return cached
    with _MODEL2VEC_LOCK:
        if _MODEL2VEC_MODEL is not None and _MODEL2VEC_MODEL_KEY == key:
            return _MODEL2VEC_MODEL
        from model2vec import StaticModel  # noqa: PLC0415 — guarded, optional bundled dependency

        model = StaticModel.from_pretrained(key)
        _MODEL2VEC_MODEL = model
        _MODEL2VEC_MODEL_KEY = key
        return model


def _model2vec_embedding(text: str, dimensions: int) -> EmbeddingResult:
    model = _load_model2vec_model()
    raw = model.encode([text])[0]
    vector = [float(value) for value in (raw.tolist() if hasattr(raw, "tolist") else raw)]
    if not vector:
        raise ValueError("model2vec produced an empty embedding")
    # Report the model's NATIVE dimension truthfully (potion-base-8M = 256), not the requested
    # default — the storage layer sizes/rebuilds the vector index from embedding_status().
    return EmbeddingResult(
        vector=vector,
        model=configured_embedding_model(),
        provider="model2vec",
        dimensions=len(vector),
    )


def embedding_json(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def embedding_source_text(*parts: str | None) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def embedding_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
