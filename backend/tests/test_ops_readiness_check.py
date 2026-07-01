from __future__ import annotations

import copy
import unittest

from scripts.ops_readiness_check import check_support_bundle_contract


def valid_support_bundle() -> dict:
    return {
        "bundle_schema": 1,
        "privacy": {
            "contains_raw_capture_text": False,
            "contains_memory_content": False,
            "contains_context_pack": False,
            "contains_user_files": False,
        },
        "backend": {"features": ["operational-readiness"]},
        "summary": {"status": "ok", "counts": {"captures": 0}},
    }


class OpsReadinessSupportBundleTests(unittest.TestCase):
    def test_support_bundle_contract_accepts_content_free_bundle(self) -> None:
        ok, payload = check_support_bundle_contract(valid_support_bundle())

        self.assertTrue(ok)
        self.assertTrue(payload["content_free"])
        self.assertEqual(payload["bundle_schema"], 1)
        self.assertFalse(payload["contains_raw_capture_text"])

    def test_support_bundle_contract_invokes_content_free_validator(self) -> None:
        bundle = valid_support_bundle()
        bundle["captures"] = [{"raw_text": "private text must not ship in support bundles"}]

        with self.assertRaisesRegex(ValueError, "content-free"):
            check_support_bundle_contract(bundle)

    def test_support_bundle_contract_rejects_oauth_refresh_fields(self) -> None:
        bundle = valid_support_bundle()
        bundle["diagnostics"] = {
            "refresh_token": "oauth_refresh_secret_123",
            "client_id": "oauth_client_id_123",
            "client_secret": "oauth_client_secret_123",
        }

        with self.assertRaisesRegex(ValueError, "content-free"):
            check_support_bundle_contract(bundle)

    def test_support_bundle_contract_requires_ops_readiness_feature(self) -> None:
        bundle = copy.deepcopy(valid_support_bundle())
        bundle["backend"]["features"] = []

        ok, payload = check_support_bundle_contract(bundle)

        self.assertFalse(ok)
        self.assertTrue(payload["content_free"])


if __name__ == "__main__":
    unittest.main()
