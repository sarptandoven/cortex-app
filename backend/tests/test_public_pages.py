"""Public + operator HTML surface served by main.py: the branded landing, Terms, Privacy, Download,
favicon, and the admin dashboard.

Two properties matter for "live and perfect for users":
1. Every link the account front-door emits (/terms, /privacy, /download, /favicon.svg) actually
   resolves — no 404s in the signup/home journey.
2. Every HTML page sends its OWN route-scoped CSP (never the strict `default-src 'none'` global that
   Caddy applies as a set-default), so the inline <style>/external JS actually render — and the
   admin page carries NO inline on* handlers (script-src 'self' would block them).
"""
from __future__ import annotations

import os
import re
import unittest

os.environ.setdefault("CORTEX_API_KEY", "test-token")

from fastapi.testclient import TestClient

from backend.app import main as main_module

STRICT_GLOBAL_CSP = "default-src 'none'; form-action 'self'"


class PublicPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main_module.app)

    def _csp(self, path: str) -> str:
        return self.client.get(path).headers.get("content-security-policy", "")

    def test_public_content_pages_render_with_own_relaxed_csp(self) -> None:
        for path in ("/", "/terms", "/privacy", "/download"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertTrue(r.headers["content-type"].startswith("text/html"), path)
            self.assertIn("<!doctype html>", r.text.lower(), path)
            csp = r.headers.get("content-security-policy", "")
            # Its own CSP, not the strict global — so the inline <style> renders.
            self.assertNotEqual(csp, STRICT_GLOBAL_CSP, path)
            self.assertIn("style-src 'self' 'unsafe-inline'", csp, path)
            self.assertIn(">Doppl</span>", r.text, path)  # on-brand shell

    def test_account_page_links_all_resolve(self) -> None:
        # The signup page links /terms + /privacy; the home page links /download; every page's
        # <link rel=icon> is /favicon.svg. None may 404.
        for path in ("/terms", "/privacy", "/download", "/favicon.svg"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_legal_pages_have_real_content(self) -> None:
        terms = self.client.get("/terms").text
        self.assertIn("Terms of Service", terms)
        self.assertIn("16 years", terms)  # age gate matches the signup ToS checkbox
        privacy = self.client.get("/privacy").text
        self.assertIn("Privacy Policy", privacy)
        self.assertIn("crypto-shred", privacy)  # reflects the real deletion behavior

    def test_download_points_at_live_site_not_a_404_repo(self) -> None:
        # Review finding: the download button linked to the private github.com/doppl-tech/cortex-app
        # (a hard 404). It must point at the live product site instead.
        html = self.client.get("/download").text
        self.assertIn("https://trydoppl.com", html)
        self.assertNotIn("doppl-tech/cortex-app", html)

    def test_capture_page_is_branded_doppl(self) -> None:
        # The bookmarklet capture page must read "Doppl", not stale "Cortex".
        html = self.client.get("/capture").text
        self.assertIn("Save to Doppl", html)
        self.assertNotIn("Save to Cortex", html)

    def test_favicon_is_svg(self) -> None:
        r = self.client.get("/favicon.svg")
        self.assertEqual(r.status_code, 200)
        self.assertIn("image/svg+xml", r.headers["content-type"])
        self.assertTrue(r.text.lstrip().startswith("<svg"))

    def test_landing_is_branded_not_local_api_blurb(self) -> None:
        text = self.client.get("/").text
        self.assertNotIn("Cortex Local API", text)  # the old unstyled dev blurb is gone
        self.assertIn("/download", text)
        self.assertIn("/terms", text)

    # ---- admin dashboard CSP (was blocked by the strict global CSP) ----

    def test_admin_page_has_own_csp_and_no_inline_handlers(self) -> None:
        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 200)
        csp = r.headers.get("content-security-policy", "")
        self.assertNotEqual(csp, STRICT_GLOBAL_CSP)
        self.assertIn("script-src 'self'", csp)          # external JS allowed
        self.assertIn("style-src 'self' 'unsafe-inline'", csp)
        self.assertIn("connect-src 'self'", csp)          # fetch to /v1/admin/*
        # No inline on* handlers (script-src 'self' would block them) — JS is external + wired.
        self.assertIsNone(re.search(r"\son(click|input|load|change)=", r.text))
        self.assertIn('<script src="/admin/app.js">', r.text)

    def test_admin_app_js_served_and_wires_events(self) -> None:
        r = self.client.get("/admin/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r.headers["content-type"])
        for hook in ("enter-btn", "refresh-btn", "logout-btn", "addEventListener"):
            self.assertIn(hook, r.text, hook)

    def test_capture_page_renders_styled(self) -> None:
        # The bookmarklet capture page (inline <style> + native form) must not be strict-CSP'd.
        self.assertNotEqual(self._csp("/capture"), STRICT_GLOBAL_CSP)

    def test_no_server_side_500s(self) -> None:
        for path in ("/", "/terms", "/privacy", "/download", "/favicon.svg", "/admin", "/admin/app.js", "/capture"):
            self.assertLess(self.client.get(path).status_code, 500, path)


if __name__ == "__main__":
    unittest.main()
