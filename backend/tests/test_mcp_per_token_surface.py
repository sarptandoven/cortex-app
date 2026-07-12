"""Per-token MCP tool surface + token-mint-with-surface contract.

The hosted /mcp endpoint fronts many remote clients off ONE URL: ChatGPT's connector wants the
`chatgpt` surface (search/fetch by name) while a generic Claude-web / Cursor-over-remote connector
wants `core`/`full`. Instead of a single global CORTEX_MCP_TOOL_SURFACE applied to everyone, a token
now carries its own surface (encoded on the token label tag), and tools/list resolves per token,
falling back to the global default for untagged tokens.

These tests pin:
  1. The label tag encode/decode/strip/normalize helpers.
  2. tools/list honors a per-token surface even when the GLOBAL surface differs.
  3. An untagged token still falls back to the global default (unchanged behavior).
  4. POST /v1/integrations/tokens mints an mcp token bound to the user with an optional surface,
     returning the plaintext token once, and that token drives the chosen surface end to end.
"""
from __future__ import annotations

import contextlib
import dataclasses
import os
import tempfile
import unittest
from pathlib import Path

MODULE_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("CORTEX_DB_PATH", str(Path(MODULE_TMP.name) / "per_token_surface.sqlite"))
os.environ.setdefault("CORTEX_VAULT_PATH", str(Path(MODULE_TMP.name) / "per_token_surface.vault"))
os.environ.setdefault("CORTEX_API_KEY", "test-token")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import main as main_module  # noqa: E402

app = main_module.app
ADMIN = {"Authorization": "Bearer test-token"}


def tearDownModule() -> None:
    MODULE_TMP.cleanup()


@contextlib.contextmanager
def _global_surface(surface: str):
    """Pin the GLOBAL default surface for the duration of the block (frozen-dataclass swap)."""
    original = main_module.settings
    main_module.settings = dataclasses.replace(original, mcp_tool_surface=surface)
    try:
        yield
    finally:
        main_module.settings = original


class SurfaceLabelHelperTests(unittest.TestCase):
    def test_normalize_accepts_known_surfaces_and_full(self) -> None:
        self.assertEqual(main_module._normalize_mcp_surface("chatgpt"), "chatgpt")
        self.assertEqual(main_module._normalize_mcp_surface(" Core "), "core")
        self.assertEqual(main_module._normalize_mcp_surface("full"), "full")

    def test_normalize_rejects_unknown_and_empty(self) -> None:
        self.assertIsNone(main_module._normalize_mcp_surface("bogus"))
        self.assertIsNone(main_module._normalize_mcp_surface(""))
        self.assertIsNone(main_module._normalize_mcp_surface(None))

    def test_tag_roundtrips_through_label(self) -> None:
        tagged = main_module._label_with_surface_tag("ChatGPT connector", "chatgpt")
        self.assertEqual(main_module._surface_from_label(tagged), "chatgpt")
        self.assertEqual(main_module._strip_surface_tag(tagged), "ChatGPT connector")

    def test_tag_is_noop_without_surface(self) -> None:
        self.assertEqual(main_module._label_with_surface_tag("Plain label", None), "Plain label")
        self.assertIsNone(main_module._surface_from_label("Plain label"))

    def test_retag_never_double_tags(self) -> None:
        once = main_module._label_with_surface_tag("My token", "chatgpt")
        twice = main_module._label_with_surface_tag(once, "core")
        self.assertEqual(main_module._surface_from_label(twice), "core")
        self.assertEqual(main_module._strip_surface_tag(twice), "My token")

    def test_tag_stays_within_label_limit(self) -> None:
        tagged = main_module._label_with_surface_tag("x" * 200, "chatgpt")
        self.assertLessEqual(len(tagged), 120)
        self.assertEqual(main_module._surface_from_label(tagged), "chatgpt")


class PerTokenSurfaceHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def _headers(self, token: str, user: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "X-Cortex-User": user}

    def _register_token(self, token: str, user: str, scopes: list[str], label: str) -> None:
        resp = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": token, "label": label, "scopes": scopes},
            headers={**ADMIN, "X-Cortex-User": user},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def _list_tool_names(self, token: str, user: str) -> set[str]:
        resp = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "list", "method": "tools/list"},
            headers=self._headers(token, user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return {t["name"] for t in resp.json()["result"]["tools"]}

    def test_per_token_chatgpt_surface_overrides_global_core(self) -> None:
        # Token label carries the chatgpt surface tag; even with the GLOBAL default on core, this
        # token is advertised the chatgpt surface (search + fetch by name).
        user = "surface-chatgpt-token"
        label = main_module._label_with_surface_tag("chatgpt connector", "chatgpt")
        self._register_token("cxm_per_token_surface_chatgpt01", user, ["read"], label)
        with _global_surface("core"):
            names = self._list_tool_names("cxm_per_token_surface_chatgpt01", user)
        self.assertIn("search", names)
        self.assertIn("fetch", names)

    def test_untagged_token_falls_back_to_global_default(self) -> None:
        # A token minted WITHOUT a surface tag inherits whatever the global default is at request
        # time — proving the per-token path is additive, not a behavior change for existing tokens.
        user = "surface-untagged-token"
        self._register_token("cxm_per_token_surface_untagged1", user, ["read"], "no surface tag")
        with _global_surface("chatgpt"):
            names_chatgpt = self._list_tool_names("cxm_per_token_surface_untagged1", user)
        with _global_surface("core"):
            names_core = self._list_tool_names("cxm_per_token_surface_untagged1", user)
        self.assertIn("search", names_chatgpt)  # follows global chatgpt
        self.assertNotIn("search", names_core)  # follows global core

    def test_per_token_core_overrides_global_chatgpt(self) -> None:
        # The reverse direction: a core-tagged token does NOT get search/fetch even when the global
        # default is chatgpt — the per-token surface wins.
        user = "surface-core-token"
        label = main_module._label_with_surface_tag("cursor connector", "core")
        self._register_token("cxm_per_token_surface_core00001", user, ["read"], label)
        with _global_surface("chatgpt"):
            names = self._list_tool_names("cxm_per_token_surface_core00001", user)
        self.assertNotIn("search", names)
        self.assertNotIn("fetch", names)
        self.assertIn("use_cortex", names)  # core surface staple

    def test_mint_integration_token_with_surface_end_to_end(self) -> None:
        # The signed-in-app path: mint an mcp token bound to the user with surface=chatgpt via the
        # new POST /v1/integrations/tokens; the plaintext token is returned once and drives the
        # chatgpt surface even under a core global default.
        user = "surface-mint-user"
        mint = self.client.post(
            "/v1/integrations/tokens",
            json={"audience": "mcp", "surface": "chatgpt", "label": "ChatGPT via mint"},
            headers={**ADMIN, "X-Cortex-User": user},
        )
        self.assertEqual(mint.status_code, 201, mint.text)
        body = mint.json()
        self.assertEqual(body["audience"], "mcp")
        self.assertEqual(body["surface"], "chatgpt")
        # The human-readable label is returned clean (tag stripped).
        self.assertEqual(body["label"], "ChatGPT via mint")
        self.assertTrue(body["token"].startswith("cxm_"), body["token"])
        with _global_surface("core"):
            names = self._list_tool_names(body["token"], user)
        self.assertIn("search", names)
        self.assertIn("fetch", names)

    def test_mint_integration_token_defaults_to_untagged_mcp(self) -> None:
        # No surface -> untagged token that follows the global default (surface reported as None).
        user = "surface-mint-default"
        mint = self.client.post(
            "/v1/integrations/tokens",
            json={},  # defaults: audience=mcp, scopes=[read], no surface
            headers={**ADMIN, "X-Cortex-User": user},
        )
        self.assertEqual(mint.status_code, 201, mint.text)
        body = mint.json()
        self.assertEqual(body["audience"], "mcp")
        self.assertIsNone(body["surface"])
        with _global_surface("chatgpt"):
            names = self._list_tool_names(body["token"], user)
        self.assertIn("search", names)

    def test_mint_rejects_unknown_audience(self) -> None:
        resp = self.client.post(
            "/v1/integrations/tokens",
            json={"audience": "nonsense"},
            headers={**ADMIN, "X-Cortex-User": "surface-bad-aud"},
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_mint_ignores_unknown_surface_gracefully(self) -> None:
        # An unknown surface is normalized away (None), so the token stays untagged rather than
        # 500ing or persisting a bogus surface.
        user = "surface-mint-bogus"
        mint = self.client.post(
            "/v1/integrations/tokens",
            json={"audience": "mcp", "surface": "totally-made-up"},
            headers={**ADMIN, "X-Cortex-User": user},
        )
        self.assertEqual(mint.status_code, 201, mint.text)
        self.assertIsNone(mint.json()["surface"])


if __name__ == "__main__":
    unittest.main()
