"""Tests for the accounts-plane email sender (backend/app/email_sender.py) and
its wiring into AuthRuntime.

Three layers:

- render_auth_email: each FLOW_KIND produces a subject + a plaintext body with
  the correct https link, the token url-encoded, and the right /account path;
  an unknown kind is handled safely (no raise).
- SmtpEmailSender: smtplib.SMTP / SMTP_SSL are monkeypatched with a fake that
  captures (host, port, starttls, login args, sendmail from/to/message). We
  assert a verify email reaches the right recipient with the link in the body,
  and that a transport exception is caught and surfaced as EmailSendError (not a
  raw traceback).
- AuthRuntime integration: reuse AuthEnabledTestCase's harness but flip
  auth_email_mode="smtp" and inject a fake sender through the module-level
  _email_sender_factory seam. A signup in smtp mode calls the sender exactly
  once with the verification recipient; a misconfigured smtp mode (no host)
  degrades to log mode without raising and still populates the outbox.
"""

from __future__ import annotations

import os
import smtplib
import unittest
from dataclasses import replace
from urllib.parse import quote

os.environ.setdefault("CORTEX_API_KEY", "test-token")

from backend.app import main as main_module
from backend.app.email_sender import (
    EmailSendError,
    LogEmailSender,
    SmtpEmailSender,
    render_auth_email,
)
from backend.tests.test_auth_http import PASSWORD, AuthEnabledTestCase

APP_URL = "https://app.cortex.example"


class RenderAuthEmailTests(unittest.TestCase):
    def test_email_verify_links_to_verify_path_with_encoded_token(self) -> None:
        token = "flw_abc.tok en/with+chars"
        subject, body = render_auth_email("email_verify", "u@example.com", token, app_url=APP_URL)
        self.assertIn("verify", subject.lower())
        expected = f"{APP_URL}/account/verify?token={quote(token, safe='')}"
        self.assertIn(expected, body)
        self.assertIn("https://", body)
        # raw (un-encoded) token must not leak the spaces/slashes into the URL
        self.assertNotIn("tok en/with+chars", body)
        self.assertIn("ignore this email", body.lower())
        self.assertIn("Cortex", body)

    def test_password_reset_links_to_reset_path(self) -> None:
        token = "reset-token-123"
        subject, body = render_auth_email("password_reset", "u@example.com", token, app_url=APP_URL)
        self.assertIn("reset", subject.lower())
        self.assertIn(f"{APP_URL}/account/reset?token={quote(token, safe='')}", body)
        self.assertIn("ignore this email", body.lower())

    def test_account_claim_links_to_login_and_includes_code(self) -> None:
        code = "flw_xyz.secret-code"
        subject, body = render_auth_email("account_claim", "u@example.com", code, app_url=APP_URL)
        self.assertIn("claim", subject.lower())
        self.assertIn(f"{APP_URL}/account/login", body)
        # the claim code itself is shown for pasting
        self.assertIn(code, body)

    def test_trailing_slash_in_app_url_does_not_double(self) -> None:
        _subject, body = render_auth_email(
            "email_verify", "u@example.com", "tok", app_url="https://app.cortex.example/"
        )
        self.assertIn("https://app.cortex.example/account/verify?token=tok", body)
        self.assertNotIn("//account", body.replace("https://", ""))

    def test_custom_product_name(self) -> None:
        subject, body = render_auth_email(
            "email_verify", "u@example.com", "tok", app_url=APP_URL, product="Doppl"
        )
        self.assertIn("Doppl", subject)
        self.assertIn("Doppl", body)

    def test_unknown_kind_is_handled_safely(self) -> None:
        # Must NOT raise; renders a neutral notice that still carries the token.
        subject, body = render_auth_email("mystery_flow", "u@example.com", "tok-42", app_url=APP_URL)
        self.assertTrue(subject)
        self.assertIn("tok-42", body)


class _FakeSMTP:
    """Captures everything a real smtplib client would receive. Used for both
    the plaintext-SMTP and implicit-TLS (SMTP_SSL) paths via classmethods that
    remember the last-constructed instance."""

    last: "_FakeSMTP | None" = None
    raise_on_send = False

    def __init__(self, host=None, port=None, timeout=None, context=None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.ehlo_count = 0
        self.starttls_called = False
        self.starttls_context = None
        self.login_args = None
        self.sent_message = None
        type(self).last = self

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def ehlo(self, *args, **kwargs) -> None:
        self.ehlo_count += 1

    def starttls(self, context=None) -> None:
        self.starttls_called = True
        self.starttls_context = context

    def login(self, username, password) -> None:
        self.login_args = (username, password)

    def send_message(self, message) -> None:
        if type(self).raise_on_send:
            raise smtplib.SMTPException("relay refused")
        self.sent_message = message


class _FakeSMTPSSL(_FakeSMTP):
    last: "_FakeSMTPSSL | None" = None


class SmtpEmailSenderTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeSMTP.last = None
        _FakeSMTP.raise_on_send = False
        _FakeSMTPSSL.last = None
        _FakeSMTPSSL.raise_on_send = False
        self._orig_smtp = smtplib.SMTP
        self._orig_smtp_ssl = smtplib.SMTP_SSL
        smtplib.SMTP = _FakeSMTP  # type: ignore[assignment,misc]
        smtplib.SMTP_SSL = _FakeSMTPSSL  # type: ignore[assignment,misc]

    def tearDown(self) -> None:
        smtplib.SMTP = self._orig_smtp  # type: ignore[assignment,misc]
        smtplib.SMTP_SSL = self._orig_smtp_ssl  # type: ignore[assignment,misc]

    def test_starttls_submission_sends_verify_email(self) -> None:
        sender = SmtpEmailSender(
            "smtp.example.com",
            587,
            username="mailer@example.com",
            password="hunter2",
            from_addr="no-reply@cortex.example",
            use_starttls=True,
        )
        subject, body = render_auth_email(
            "email_verify", "recipient@example.com", "tok-abc", app_url=APP_URL
        )
        sender.send("recipient@example.com", subject, body)

        client = _FakeSMTP.last
        assert client is not None
        self.assertEqual(client.host, "smtp.example.com")
        self.assertEqual(client.port, 587)
        self.assertTrue(client.starttls_called)
        self.assertEqual(client.login_args, ("mailer@example.com", "hunter2"))
        self.assertIsNotNone(client.sent_message)
        self.assertEqual(client.sent_message["To"], "recipient@example.com")
        self.assertEqual(client.sent_message["From"], "no-reply@cortex.example")
        self.assertIn("verify", client.sent_message["Subject"].lower())
        payload = client.sent_message.get_content()
        self.assertIn(f"{APP_URL}/account/verify?token=tok-abc", payload)
        # implicit-TLS path was NOT used
        self.assertIsNone(_FakeSMTPSSL.last)

    def test_implicit_ssl_path_uses_smtp_ssl_and_no_starttls(self) -> None:
        sender = SmtpEmailSender(
            "smtp.example.com",
            465,
            username="mailer@example.com",
            password="pw",
            use_ssl=True,
        )
        sender.send("r@example.com", "Subject", "Body")
        ssl_client = _FakeSMTPSSL.last
        assert ssl_client is not None
        self.assertEqual(ssl_client.port, 465)
        self.assertFalse(ssl_client.starttls_called)
        self.assertEqual(ssl_client.login_args, ("mailer@example.com", "pw"))
        self.assertIsNone(_FakeSMTP.last)

    def test_from_addr_falls_back_to_username(self) -> None:
        sender = SmtpEmailSender("smtp.example.com", 587, username="me@example.com")
        sender.send("r@example.com", "S", "B")
        client = _FakeSMTP.last
        assert client is not None
        self.assertEqual(client.sent_message["From"], "me@example.com")

    def test_no_username_skips_login(self) -> None:
        sender = SmtpEmailSender("smtp.example.com", 587, from_addr="no-reply@example.com")
        sender.send("r@example.com", "S", "B")
        client = _FakeSMTP.last
        assert client is not None
        self.assertIsNone(client.login_args)

    def test_send_exception_is_surfaced_as_email_send_error(self) -> None:
        _FakeSMTP.raise_on_send = True
        sender = SmtpEmailSender("smtp.example.com", 587, username="u@example.com", password="p")
        with self.assertRaises(EmailSendError):
            sender.send("r@example.com", "S", "B")

    def test_connect_exception_is_surfaced_as_email_send_error(self) -> None:
        def boom(*args, **kwargs):
            raise OSError("connection refused")

        smtplib.SMTP = boom  # type: ignore[assignment,misc]
        sender = SmtpEmailSender("smtp.example.com", 587)
        with self.assertRaises(EmailSendError):
            sender.send("r@example.com", "S", "B")


class LogEmailSenderTests(unittest.TestCase):
    def test_appends_to_outbox(self) -> None:
        sender = LogEmailSender()
        sender.send("r@example.com", "Hello", "Body text")
        self.assertEqual(len(sender.outbox), 1)
        entry = sender.outbox[0]
        self.assertEqual(entry["email"], "r@example.com")
        self.assertEqual(entry["subject"], "Hello")
        self.assertEqual(entry["body"], "Body text")


class _RecordingSender:
    """A minimal EmailSender injected via the factory seam."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail = False

    def send(self, to_addr: str, subject: str, body: str) -> None:
        self.calls.append((to_addr, subject, body))
        if self.fail:
            raise EmailSendError("boom")


class AuthRuntimeSmtpIntegrationTests(AuthEnabledTestCase):
    """smtp mode with a configured host: a fake sender is injected via
    main_module._email_sender_factory and must be called on signup."""

    def setUp(self) -> None:
        self.recording = _RecordingSender()
        main_module._email_sender_factory = lambda _settings, _outbox: self.recording
        super().setUp()
        # Rebuild in smtp mode now that the factory is installed.
        self.settings = replace(self.settings, auth_email_mode="smtp", smtp_host="smtp.example.com")
        main_module.settings = self.settings
        main_module.init_auth_runtime()
        self.runtime = main_module.auth_runtime
        # self.client from super().setUp() targets main_module.app, which is
        # unchanged by the runtime rebuild — reuse it as-is.

    def tearDown(self) -> None:
        main_module._email_sender_factory = None
        super().tearDown()

    def test_signup_calls_sender_once_with_verification_recipient(self) -> None:
        resp = self.client.post(
            "/v1/auth/signup", json={"email": "new@example.com", "password": PASSWORD}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(self.recording.calls), 1)
        to_addr, subject, body = self.recording.calls[0]
        self.assertEqual(to_addr, "new@example.com")
        self.assertIn("verify", subject.lower())
        self.assertIn("/account/verify?token=", body)
        # outbox still holds the canonical record so nothing is lost
        self.assertTrue(any(item["kind"] == "email_verify" for item in self.runtime.outbox))

    def test_send_failure_does_not_break_signup_and_keeps_outbox(self) -> None:
        self.recording.fail = True
        resp = self.client.post(
            "/v1/auth/signup", json={"email": "flaky@example.com", "password": PASSWORD}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(self.recording.calls), 1)
        # the token survives in the outbox despite the send failure
        self.assertTrue(any(item["kind"] == "email_verify" for item in self.runtime.outbox))


class AuthRuntimeSmtpMisconfigTests(AuthEnabledTestCase):
    """smtp mode WITHOUT a host must degrade to the log sink: no raise on
    signup, and the outbox is still populated with the token."""

    def setUp(self) -> None:
        # No factory installed -> _build_email_sender takes the real branch and
        # sees smtp mode with an empty host.
        main_module._email_sender_factory = None
        super().setUp()
        self.settings = replace(self.settings, auth_email_mode="smtp", smtp_host="")
        main_module.settings = self.settings
        main_module.init_auth_runtime()
        self.runtime = main_module.auth_runtime

    def test_misconfigured_smtp_degrades_to_log(self) -> None:
        # The sender must be a LogEmailSender, not an SmtpEmailSender.
        self.assertIsInstance(self.runtime.email_sender, LogEmailSender)
        resp = self.client.post(
            "/v1/auth/signup", json={"email": "beta@example.com", "password": PASSWORD}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        # outbox has the canonical token record — nothing lost.
        self.assertEqual(self.runtime.outbox[-1]["kind"], "email_verify")
        self.assertEqual(self.runtime.outbox[-1]["email"], "beta@example.com")
        self.assertTrue(self.runtime.outbox[-1]["token"])


if __name__ == "__main__":
    unittest.main()
