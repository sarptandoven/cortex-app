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


if __name__ == "__main__":
    unittest.main()
