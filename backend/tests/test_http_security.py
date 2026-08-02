from __future__ import annotations

from email.message import Message
from io import BytesIO
import unittest
from urllib.error import HTTPError
from urllib.request import Request

from backend.app.http_security import SameOriginRedirectHandler, _origin


class SameOriginRedirectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = SameOriginRedirectHandler()
        self.headers = Message()
        self.fp = BytesIO()

    def redirect(self, source: str, target: str) -> Request | None:
        request = Request(
            source,
            headers={"Authorization": "Bearer secret", "Accept": "application/json"},
        )
        return self.handler.redirect_request(
            request,
            self.fp,
            302,
            "Found",
            self.headers,
            target,
        )

    def test_allows_same_origin_absolute_and_relative_redirects(self) -> None:
        absolute = self.redirect(
            "https://api.example.com/v1/items",
            "https://api.example.com/v2/items",
        )
        relative = self.redirect("https://api.example.com/v1/items", "/v2/items")

        self.assertEqual(absolute.full_url, "https://api.example.com/v2/items")
        self.assertEqual(relative.full_url, "https://api.example.com/v2/items")
        self.assertEqual(absolute.get_header("Authorization"), "Bearer secret")

    def test_treats_default_and_explicit_ports_as_the_same_origin(self) -> None:
        redirected = self.redirect(
            "https://api.example.com/v1/items",
            "https://api.example.com:443/v2/items",
        )
        self.assertEqual(redirected.full_url, "https://api.example.com:443/v2/items")

    def test_blocks_cross_host_port_and_scheme_redirects(self) -> None:
        for target in (
            "https://attacker.example/steal",
            "https://api.example.com:8443/steal",
            "http://api.example.com/steal",
            "https://user:pass@api.example.com/steal",
        ):
            with self.subTest(target=target), self.assertRaisesRegex(
                HTTPError,
                "cross-origin redirect blocked",
            ):
                self.redirect("https://api.example.com/v1/items", target)

    def test_rejects_malformed_or_credentialed_initial_origins(self) -> None:
        self.assertIsNone(_origin("file:///tmp/private"))
        self.assertIsNone(_origin("https://user:pass@example.com/data"))
        self.assertIsNone(_origin("https://example.com:invalid/data"))


if __name__ == "__main__":
    unittest.main()
