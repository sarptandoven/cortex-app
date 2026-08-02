from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.app.config import INSECURE_DEV_API_KEY, load_settings


class ConfigSecurityTests(unittest.TestCase):
    def test_insecure_dev_token_is_rejected_without_explicit_opt_in(self) -> None:
        with patch.dict(os.environ, {"CORTEX_API_KEY": INSECURE_DEV_API_KEY, "CORTEX_ALLOW_INSECURE_DEV_TOKEN": "0"}):
            with self.assertRaises(RuntimeError):
                load_settings()

    def test_insecure_dev_token_can_be_enabled_for_local_dev_scripts(self) -> None:
        with patch.dict(os.environ, {"CORTEX_API_KEY": INSECURE_DEV_API_KEY, "CORTEX_ALLOW_INSECURE_DEV_TOKEN": "1"}):
            settings = load_settings()

        self.assertEqual(settings.api_key, INSECURE_DEV_API_KEY)

    def test_missing_global_token_does_not_create_sample_admin_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings()

        self.assertEqual(settings.api_key, "")

    def test_public_base_url_follows_an_overridden_development_port(self) -> None:
        with patch.dict(os.environ, {"CORTEX_PORT": "57490"}, clear=True):
            settings = load_settings()
        self.assertEqual(settings.public_base_url, "http://127.0.0.1:57490")
        self.assertEqual(settings.public_app_url, "http://127.0.0.1:57490")

    def test_explicit_public_base_url_overrides_the_development_port(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CORTEX_PORT": "57490",
                "CORTEX_PUBLIC_BASE_URL": "https://api.example.invalid",
            },
            clear=True,
        ):
            settings = load_settings()
        self.assertEqual(settings.public_base_url, "https://api.example.invalid")


if __name__ == "__main__":
    unittest.main()
