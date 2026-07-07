"""Unit tests for the Cortex Python SDK.

These tests DO NOT hit the network. They monkeypatch ``urllib.request.urlopen`` (the
one call the SDK makes) so we can assert the exact URL, method, headers, and JSON body
the client builds, and verify parsing of canned responses plus error raising.

Run:  python3 -m pytest sdk/python/tests/test_client.py
"""

from __future__ import annotations

import io
import json
import os
import sys
from urllib.error import HTTPError, URLError

import pytest

# Make ``cortex_client`` importable without installing the package.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cortex_client import CortexClient, CortexError  # noqa: E402
from cortex_client import client as client_module  # noqa: E402


class _FakeResponse:
    """Minimal stand-in for the object urlopen returns (a context manager with .read)."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


class _Recorder:
    """Captures the Request the SDK passes to urlopen and returns a canned response."""

    def __init__(self, response_body: bytes = b"{}") -> None:
        self.response_body = response_body
        self.request = None
        self.timeout = None

    def __call__(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        return _FakeResponse(self.response_body)


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(client_module, "urlopen", rec)
    return rec


def _headers(request) -> dict:
    # urllib lowercases header keys stored on the Request; normalize for assertions.
    return {k.lower(): v for k, v in request.header_items()}


# -- auth / header construction --------------------------------------------------------

def test_headers_include_bearer_and_user(recorder):
    client = CortexClient(token="tok_abc", user="user_42")
    client.search("hello")
    headers = _headers(recorder.request)
    assert headers["authorization"] == "Bearer tok_abc"
    assert headers["x-cortex-user"] == "user_42"


def test_no_user_header_when_user_unset(recorder):
    client = CortexClient(token="tok_abc")
    client.search("hello")
    headers = _headers(recorder.request)
    assert "x-cortex-user" not in headers
    assert headers["authorization"] == "Bearer tok_abc"


def test_no_auth_header_when_token_empty(recorder):
    client = CortexClient()
    client.search("hello")
    headers = _headers(recorder.request)
    assert "authorization" not in headers


def test_timeout_is_passed_through(recorder):
    client = CortexClient(token="t", timeout=12.5)
    client.search("hi")
    assert recorder.timeout == 12.5


def test_base_url_trailing_slash_stripped(recorder):
    client = CortexClient(base_url="http://127.0.0.1:8766/", token="t")
    client.search("hi")
    assert recorder.request.full_url.startswith("http://127.0.0.1:8766/v1/search")


# -- search ----------------------------------------------------------------------------

def test_search_url_method_and_topk(recorder):
    client = CortexClient(token="t")
    client.search("release checklist", top_k=5)
    req = recorder.request
    assert req.get_method() == "GET"
    assert req.data is None
    assert req.full_url == "http://127.0.0.1:8766/v1/search?query=release+checklist&limit=5"


def test_search_parses_canned_response(monkeypatch):
    payload = {"query": "q", "results": [{"id": "m1", "content": "hi"}]}
    rec = _Recorder(json.dumps(payload).encode("utf-8"))
    monkeypatch.setattr(client_module, "urlopen", rec)
    client = CortexClient(token="t")
    result = client.search("q")
    assert result == payload


# -- ask -------------------------------------------------------------------------------

def test_ask_url_and_topk(recorder):
    client = CortexClient(token="t")
    client.ask("what db?", top_k=3)
    req = recorder.request
    assert req.get_method() == "GET"
    assert req.full_url == "http://127.0.0.1:8766/v1/ask?query=what+db%3F&limit=3"


# -- context ---------------------------------------------------------------------------

def test_context_posts_json_body(recorder):
    client = CortexClient(token="t")
    client.context("draft the release notes", intent="draft", token_budget=1500, surface="cursor")
    req = recorder.request
    assert req.get_method() == "POST"
    assert req.full_url == "http://127.0.0.1:8766/v1/context"
    assert _headers(req)["content-type"] == "application/json"
    body = json.loads(req.data.decode("utf-8"))
    assert body == {
        "task": "draft the release notes",
        "intent": "draft",
        "token_budget": 1500,
        "surface": "cursor",
    }


def test_context_defaults(recorder):
    client = CortexClient(token="t")
    client.context("do a thing")
    body = json.loads(recorder.request.data.decode("utf-8"))
    assert body["intent"] is None
    assert body["token_budget"] == 2000
    assert body["surface"] == "agent"


# -- call_tool -------------------------------------------------------------------------

def test_call_tool_body_and_unwrap(monkeypatch):
    canned = {"tool": "search_memory", "result": {"results": [1, 2, 3]}}
    rec = _Recorder(json.dumps(canned).encode("utf-8"))
    monkeypatch.setattr(client_module, "urlopen", rec)
    client = CortexClient(token="t")
    result = client.call_tool("search_memory", {"query": "x", "top_k": 2})
    # URL / method / body
    assert rec.request.get_method() == "POST"
    assert rec.request.full_url == "http://127.0.0.1:8766/v1/tools/call"
    body = json.loads(rec.request.data.decode("utf-8"))
    assert body == {"name": "search_memory", "arguments": {"query": "x", "top_k": 2}}
    # unwraps the {"tool", "result"} envelope
    assert result == {"results": [1, 2, 3]}


def test_call_tool_defaults_arguments_to_empty(recorder):
    client = CortexClient(token="t")
    client.call_tool("list_capabilities")
    body = json.loads(recorder.request.data.decode("utf-8"))
    assert body == {"name": "list_capabilities", "arguments": {}}


# -- tools_schema / openai_tools -------------------------------------------------------

def test_tools_schema_default_format_and_unwrap(monkeypatch):
    schema = [{"type": "function", "function": {"name": "get_context"}}]
    rec = _Recorder(json.dumps({"schema": schema}).encode("utf-8"))
    monkeypatch.setattr(client_module, "urlopen", rec)
    client = CortexClient(token="t")
    result = client.tools_schema()
    assert rec.request.get_method() == "GET"
    assert rec.request.full_url == "http://127.0.0.1:8766/v1/tools/schema?format=openai"
    assert result == schema  # {"schema": ...} envelope unwrapped


def test_openai_tools_uses_openai_format(monkeypatch):
    rec = _Recorder(json.dumps({"schema": []}).encode("utf-8"))
    monkeypatch.setattr(client_module, "urlopen", rec)
    client = CortexClient(token="t")
    client.openai_tools()
    assert "format=openai" in rec.request.full_url


def test_anthropic_tools_uses_anthropic_format(monkeypatch):
    rec = _Recorder(json.dumps({"schema": []}).encode("utf-8"))
    monkeypatch.setattr(client_module, "urlopen", rec)
    client = CortexClient(token="t")
    client.anthropic_tools()
    assert "format=anthropic" in rec.request.full_url


# -- error handling --------------------------------------------------------------------

def _raise_http_error(status: int, detail):
    body = json.dumps({"detail": detail}).encode("utf-8")

    def _fake(request, timeout=None):
        raise HTTPError(
            url=request.full_url,
            code=status,
            msg="error",
            hdrs=None,
            fp=io.BytesIO(body),
        )

    return _fake


def test_403_raises_cortex_error_with_detail(monkeypatch):
    monkeypatch.setattr(
        client_module,
        "urlopen",
        _raise_http_error(403, "Cortex API token requires write scope"),
    )
    client = CortexClient(token="t")
    with pytest.raises(CortexError) as excinfo:
        client.call_tool("remember_this", {"content": "x"})
    assert excinfo.value.status == 403
    assert excinfo.value.detail == "Cortex API token requires write scope"


def test_401_raises_cortex_error(monkeypatch):
    monkeypatch.setattr(
        client_module,
        "urlopen",
        _raise_http_error(401, "Missing or invalid Cortex API token"),
    )
    client = CortexClient(token="")
    with pytest.raises(CortexError) as excinfo:
        client.search("q")
    assert excinfo.value.status == 401
    assert excinfo.value.detail == "Missing or invalid Cortex API token"


def test_error_detail_can_be_object(monkeypatch):
    detail = {"status": "needs_configuration", "hosted_readiness": {"status": "error"}}
    monkeypatch.setattr(client_module, "urlopen", _raise_http_error(503, detail))
    client = CortexClient(token="t")
    with pytest.raises(CortexError) as excinfo:
        client.ask("q")
    assert excinfo.value.status == 503
    assert excinfo.value.detail == detail


def test_transport_error_raises_cortex_error_status_zero(monkeypatch):
    def _fake(request, timeout=None):
        raise URLError("Connection refused")

    monkeypatch.setattr(client_module, "urlopen", _fake)
    client = CortexClient(token="t")
    with pytest.raises(CortexError) as excinfo:
        client.search("q")
    assert excinfo.value.status == 0
    assert "Connection refused" in str(excinfo.value.detail)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
