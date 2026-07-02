from __future__ import annotations

import os
import unittest
from dataclasses import replace

os.environ.setdefault("CORTEX_API_KEY", "test-token")

from fastapi import HTTPException

from backend.app import main as main_module


class CaptureQueryTokenFailClosedTests(unittest.TestCase):
    """The capture query-token auth must fail closed whenever auth is actually required,
    matching every other auth path. Previously it fell through to the default user whenever
    no global key was set — even in scoped-token / hosted mode."""

    def _apply(self, **kwargs):
        return replace(main_module.settings, **kwargs)

    def test_local_dev_without_key_allows_default_user(self) -> None:
        original = main_module.settings
        try:
            main_module.settings = self._apply(api_key="", shard_mode="local", require_scoped_api_tokens=False)
            self.assertEqual(main_module._auth_query_token("anything"), main_module.settings.default_user_id)
        finally:
            main_module.settings = original

    def test_scoped_token_mode_rejects_invalid_token(self) -> None:
        original = main_module.settings
        try:
            main_module.settings = self._apply(api_key="", shard_mode="user", require_scoped_api_tokens=True)
            with self.assertRaises(HTTPException) as ctx:
                main_module._auth_query_token("bogus-token")
            self.assertEqual(ctx.exception.status_code, 401)
        finally:
            main_module.settings = original

    def test_non_local_shard_without_key_fails_closed(self) -> None:
        original = main_module.settings
        try:
            main_module.settings = self._apply(api_key="", shard_mode="user", require_scoped_api_tokens=False)
            with self.assertRaises(HTTPException) as ctx:
                main_module._auth_query_token("bogus-token")
            self.assertEqual(ctx.exception.status_code, 401)
        finally:
            main_module.settings = original

    def test_configured_key_rejects_wrong_and_accepts_right(self) -> None:
        original = main_module.settings
        try:
            main_module.settings = self._apply(api_key="the-real-key", shard_mode="local", require_scoped_api_tokens=False)
            with self.assertRaises(HTTPException):
                main_module._auth_query_token("wrong")
            self.assertEqual(main_module._auth_query_token("the-real-key"), main_module.settings.default_user_id)
        finally:
            main_module.settings = original


if __name__ == "__main__":
    unittest.main()
