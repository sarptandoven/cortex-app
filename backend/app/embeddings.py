from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .http_security import open_same_origin


VECTOR_DIMENSIONS = 384
VECTOR_MODEL = "cortex-hash-v1"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"

# Local, CPU-only, no-API-key neural embeddings (MinishLab model2vec, MIT).
# potion-base-8M emits 256-dim vectors natively (NOT 384) — so it is not
# drop-in compatible with the existing 384-dim sqlite-vec index. We surface
# that honestly via embedding_status().index_compatible rather than padding or
# truncating into the index. As of the model2vec-by-default change, configured_embedding_provider()
# prefers model2vec WHENEVER the bundled model exists on disk (model2vec_available), degrading to
# hash when it does not — so no new asset is bundled and hash-only machines/CI stay byte-identical.
DEFAULT_MODEL2VEC_MODEL = "minishlab/potion-base-8M"
MODEL2VEC_DIMENSIONS = 256

_MODEL2VEC_MODEL: Any = None
_MODEL2VEC_MODEL_KEY: str | None = None
_MODEL2VEC_LOCK = threading.Lock()

# Process-level failure latch: when model2vec is CONFIGURED but a live embed actually
# fails to load/encode and silently degrades to the deterministic hash fallback, we must
# NOT keep reporting provider=model2vec as if semantic search still works. The latch is set
# by the ACTUAL embed outcome (see embed_text_result) and drives effective_embedding_provider()
# so /ready, search diagnostics, and the vector-write gate all report the truth. It resets the
# moment a live model2vec embed succeeds again, so a transient failure self-heals.
_MODEL2VEC_LOAD_FAILED = False
_MODEL2VEC_FAILURE_LOGGED = False


def _note_model2vec_failure(exc: BaseException) -> None:
    global _MODEL2VEC_LOAD_FAILED, _MODEL2VEC_FAILURE_LOGGED
    _MODEL2VEC_LOAD_FAILED = True
    if not _MODEL2VEC_FAILURE_LOGGED:
        _MODEL2VEC_FAILURE_LOGGED = True
        logging.error(
            "embedding_model_load_failed: configured=model2vec but live embed fell back to hash — %s",
            exc,
        )


def _note_model2vec_success() -> None:
    global _MODEL2VEC_LOAD_FAILED, _MODEL2VEC_FAILURE_LOGGED
    if _MODEL2VEC_LOAD_FAILED:
        logging.info("embedding_model_recovered: model2vec live embed succeeded after a prior fallback")
    _MODEL2VEC_LOAD_FAILED = False
    _MODEL2VEC_FAILURE_LOGGED = False


def effective_embedding_provider() -> str:
    """The provider that is ACTUALLY producing vectors right now.

    Returns the configured provider, except that a model2vec-configured store whose live embed
    has degraded to the hash fallback (latch set) reports 'hash' — so status/diagnostics/gates
    reflect reality instead of the requested config. This is the single source of truth for
    'is this real semantics or keyword-hash noise'."""
    provider = configured_embedding_provider()
    if provider == "model2vec" and _MODEL2VEC_LOAD_FAILED:
        return "hash"
    return provider


def embedding_provider_degraded() -> bool:
    """True when model2vec was requested but the live embed is really emitting hash."""
    return configured_embedding_provider() == "model2vec" and _MODEL2VEC_LOAD_FAILED


def warmup_embedding_provider() -> None:
    """Front-load the model load so the health surface is correct before the first query.

    Runs one probe embed; for a model2vec store this triggers the model load (and sets the
    failure latch if it can't load) up front, rather than leaving /ready reporting a healthy
    model2vec until the first real query trips the fallback. Cheap no-op for hash stores."""
    if configured_embedding_provider() != "model2vec":
        return
    try:
        embed_text_result("probe")
    except Exception:
        # Non-strict: the latch is already set inside embed_text_result on failure; strict mode
        # will have re-raised, which we swallow here so bootstrap warmup never crashes the store.
        pass


@dataclass(frozen=True)
class EmbeddingResult:
    vector: list[float]
    model: str
    provider: str
    dimensions: int


_BUNDLED_MODEL2VEC_PROBE: tuple[bool, str] | None = None


def _looks_like_model2vec_dir(path: Path) -> bool:
    """A directory is a usable local model2vec model when it holds the sentinel config.json that
    save_pretrained() writes (mirrors the launcher's bundled-model check). Cheap, no import."""
    try:
        return path.is_dir() and (path / "config.json").is_file()
    except OSError:
        return False


def _bundled_model2vec_dir() -> str:
    """Path of the model2vec model bundled alongside the backend package, if present.

    In the shipped app the model sits at Resources/model2vec, next to the backend package — relative
    to this module that is <package-parent>/model2vec. The probe is process-cached (the bundle layout
    is fixed for an install); returns "" when no bundled model exists so we degrade to hash rather
    than trigger a network download. An explicit CORTEX_MODEL2VEC_PATH is handled separately/live."""
    global _BUNDLED_MODEL2VEC_PROBE
    if _BUNDLED_MODEL2VEC_PROBE is None:
        candidate = Path(__file__).resolve().parent.parent.parent / "model2vec"
        _BUNDLED_MODEL2VEC_PROBE = (_looks_like_model2vec_dir(candidate), str(candidate))
    exists, path = _BUNDLED_MODEL2VEC_PROBE
    return path if exists else ""


def resolve_model2vec_path() -> str:
    """The local model2vec directory to load from. An explicit CORTEX_MODEL2VEC_PATH wins (checked
    live, honoured even if it turns out stale so _load_model2vec_model's fallback path is unchanged);
    otherwise the bundled model next to the backend package (if present). "" means no local dir — the
    loader then falls back to the configured model id (network) only if model2vec was requested."""
    explicit = os.environ.get("CORTEX_MODEL2VEC_PATH", "").strip()
    if explicit:
        return explicit
    return _bundled_model2vec_dir()


def model2vec_available() -> bool:
    """True when a bundled/configured model2vec model actually exists on disk (config.json present).

    Drives the DEFAULT provider selection (see configured_embedding_provider): semantic retrieval is
    preferred WHENEVER the model is present, but a machine without it degrades to the deterministic
    hash embedder with no network fetch. Bundle-size neutral — probes an already-bundled asset."""
    path = resolve_model2vec_path()
    return bool(path) and _looks_like_model2vec_dir(Path(path).expanduser())


def configured_embedding_provider() -> str:
    raw = os.environ.get("CORTEX_EMBEDDING_PROVIDER", "").strip().lower()
    if raw in {"hash", "openai", "model2vec"}:
        return raw
    # No explicit override: prefer on-device semantic retrieval WHEN the bundled model2vec model is
    # actually present on disk; otherwise degrade to the deterministic hash embedder. CI (no bundled
    # model) stays on hash and byte-identical, while the shipped app (model bundled) defaults to real
    # semantics with no env var. CORTEX_EMBEDDING_PROVIDER remains an explicit escape hatch.
    return "model2vec" if model2vec_available() else "hash"


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
    configured = configured_embedding_provider()
    provider = effective_embedding_provider()
    degraded = provider != configured
    dimensions = configured_embedding_dimensions()
    schema = int(schema_dimensions) if schema_dimensions else VECTOR_DIMENSIONS
    return {
        # provider is the EFFECTIVE provider (what's actually producing vectors); when model2vec
        # was requested but the live model can't load, this honestly reports 'hash', not model2vec.
        "provider": provider,
        "configured_provider": configured,
        "model": configured_embedding_model() if not degraded else VECTOR_MODEL,
        "dimensions": dimensions,
        "schema_dimensions": schema,
        "index_compatible": dimensions == schema,
        "network_required": provider == "openai",
        "strict": _strict_embeddings(),
        "degraded": degraded,
        "degraded_reason": "embedding_model_load_failed" if degraded else None,
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
            result = _model2vec_embedding(text, resolved_dimensions)
        except Exception as exc:
            # Real local model unavailable (deps/model absent) or failed to encode: fall back to
            # hash so a machine without the bundled model still works. Strict mode surfaces it.
            # Latch the failure so status/diagnostics/gates stop reporting provider=model2vec —
            # a silent degrade to hash must not masquerade as working semantic search.
            _note_model2vec_failure(exc)
            if _strict_embeddings():
                raise
        else:
            # Live embed succeeded: clear any prior degrade latch so a transient failure self-heals.
            _note_model2vec_success()
            return result
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
        with open_same_origin(request, timeout=_openai_timeout_seconds()) as response:
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
    # Prefer an explicit/bundled local model directory (resolve_model2vec_path also finds the model
    # bundled next to the package, so the default provider selection loads offline); otherwise use
    # the configured model id.
    local_path = resolve_model2vec_path()
    key = local_path or configured_embedding_model()
    cached = _MODEL2VEC_MODEL
    if cached is not None and _MODEL2VEC_MODEL_KEY == key:
        return cached
    with _MODEL2VEC_LOCK:
        if _MODEL2VEC_MODEL is not None and _MODEL2VEC_MODEL_KEY == key:
            return _MODEL2VEC_MODEL
        from model2vec import StaticModel  # noqa: PLC0415 — guarded, optional bundled dependency

        model_path = Path(local_path).expanduser() if local_path else None
        if model_path is not None and model_path.is_dir():
            # The MAS build bundles the complete model, so load it directly from its
            # three local data files. StaticModel.from_pretrained imports the Hugging
            # Face network stack even for a local path; that unnecessarily pulled an
            # AWS-LC/OpenSSL binary into our loopback-only worker and made offline
            # semantics depend on ssl. Direct construction keeps the exact same
            # Model2Vec inference math with no network or crypto runtime at all.
            from safetensors import safe_open  # noqa: PLC0415
            from tokenizers import Tokenizer  # noqa: PLC0415

            tensor_file = safe_open(model_path / "model.safetensors", framework="numpy")
            embeddings = tensor_file.get_tensor("embeddings")
            try:
                weights = tensor_file.get_tensor("weights")
            except Exception:
                weights = None
            try:
                mapping = tensor_file.get_tensor("mapping")
            except Exception:
                mapping = None
            tokenizer = Tokenizer.from_file(str(model_path / "tokenizer.json"))
            config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
            model = StaticModel(
                vectors=embeddings,
                tokenizer=tokenizer,
                weights=weights,
                token_mapping=mapping,
                config=config,
            )
        else:
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
