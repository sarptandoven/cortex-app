from __future__ import annotations

import socket
import unittest

from backend.app.connectors import calendar


def _fake_getaddrinfo(mapping: dict[str, list[str]]):
    """Return a getaddrinfo stand-in that resolves hosts from ``mapping`` only."""

    def _resolver(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(f"unexpected host in test: {host!r}")
        infos = []
        for addr in mapping[host]:
            family = socket.AF_INET6 if ":" in addr else socket.AF_INET
            infos.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, port or 0)))
        return infos

    return _resolver


class CalendarSsrfGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._real_getaddrinfo = calendar.socket.getaddrinfo
        self._resolved_hosts: list[str] = []

    def tearDown(self) -> None:
        calendar.socket.getaddrinfo = self._real_getaddrinfo

    def _install_resolver(self, mapping: dict[str, list[str]]) -> None:
        base = _fake_getaddrinfo(mapping)

        def _tracking(host, port, *args, **kwargs):
            self._resolved_hosts.append(host)
            return base(host, port, *args, **kwargs)

        calendar.socket.getaddrinfo = _tracking

    def _fetch(self, url: str) -> None:
        # request_text is None so the real (guarded) network path is exercised;
        # the guard must reject before any socket connection is attempted.
        calendar._fetch_feed(url, None)

    def test_rejects_link_local_metadata_endpoint(self) -> None:
        self._install_resolver({"169.254.169.254": ["169.254.169.254"]})
        with self.assertRaises(ValueError):
            self._fetch("http://169.254.169.254/latest/meta-data/iam/security-credentials/")

    def test_rejects_loopback(self) -> None:
        self._install_resolver({"127.0.0.1": ["127.0.0.1"]})
        with self.assertRaises(ValueError):
            self._fetch("http://127.0.0.1/")

    def test_rejects_private(self) -> None:
        self._install_resolver({"10.0.0.5": ["10.0.0.5"]})
        with self.assertRaises(ValueError):
            self._fetch("http://10.0.0.5/")

    def test_rejects_ipv6_loopback(self) -> None:
        self._install_resolver({"::1": ["::1"]})
        with self.assertRaises(ValueError):
            self._fetch("http://[::1]/")

    def test_rejects_hostname_resolving_to_private(self) -> None:
        # A public-looking hostname that resolves to an internal address (DNS rebinding).
        self._install_resolver({"evil.example.com": ["10.1.2.3"]})
        with self.assertRaises(ValueError):
            self._fetch("http://evil.example.com/feed.ics")

    def test_rejects_if_any_resolved_address_is_private(self) -> None:
        # Mixed answer: one public, one private. Must reject on the private one.
        self._install_resolver({"mixed.example.com": ["93.184.216.34", "10.0.0.9"]})
        with self.assertRaises(ValueError):
            self._fetch("http://mixed.example.com/feed.ics")

    def test_public_url_passes_the_validator(self) -> None:
        # A normal public host resolves to a public address and must NOT be rejected
        # by the SSRF guard. We stop before the actual network read via request_text.
        self._install_resolver({"calendar.example.com": ["93.184.216.34"]})
        # The guard itself must not raise for a public address.
        calendar._guard_feed_url("https://calendar.example.com/feed.ics")
        self.assertIn("calendar.example.com", self._resolved_hosts)

    def test_public_url_end_to_end_with_stub_reader(self) -> None:
        # With a request_text override the guard should still allow a public URL through
        # and return the fetched content unchanged.
        self._install_resolver({"calendar.example.com": ["93.184.216.34"]})
        sentinel = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
        result = calendar._fetch_feed(
            "https://calendar.example.com/feed.ics",
            lambda url: sentinel,
        )
        self.assertEqual(result, sentinel)


if __name__ == "__main__":
    unittest.main()
