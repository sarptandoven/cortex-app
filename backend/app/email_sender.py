"""Transactional email delivery for the Cortex accounts plane.

The hosted auth flows (email verification, password reset, invite/claim) mint
single-use tokens and hand them to a delivery hook. In self-hosting / beta the
hook is the log sink (``LogEmailSender``): tokens are printed and captured in an
in-memory outbox. For a PUBLIC launch those links must actually reach an inbox,
so this module adds an SMTP sender that shares one interface with the log sink.

Design constraints (see the task brief):

- Standard library only: ``smtplib`` + ``ssl`` + ``email.message``. No new deps.
- Plaintext bodies only. Deliverability is better and there is no rendering
  surface; HTML is a deferred nicety, not a launch blocker.
- HOSTED-PLANE ONLY. This module is imported lazily by ``main.py`` (which already
  gates on ``settings.auth_enabled``); the stdlib-only standalone server must
  never import it at module scope.
- A send failure must NEVER crash signup/login. ``SmtpEmailSender.send`` catches
  transport errors and raises the defined :class:`EmailSendError`; the caller
  (``AuthRuntime._deliver_flow``) logs it and keeps the outbox entry so nothing
  is lost.
"""

from __future__ import annotations

import logging
import smtplib
try:
    import ssl
except ModuleNotFoundError:  # App Store build ships without _ssl (TLS/HTTPS not used offline)
    ssl = None  # type: ignore[assignment]
from collections import deque
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import quote

from .config import APP_BRAND

_logger = logging.getLogger("cortex.auth.email")

# Default SMTP submission timeout (seconds). Kept short so a wedged relay can
# never stall a signup/login request; the caller falls back on failure.
_DEFAULT_TIMEOUT = 15.0

# The browser front-door paths (backend/app/webauth.py). email_verify and
# password_reset link to a real GET page that consumes ``?token=``. account_claim
# has no dedicated page, so we link to /account/login and put the code in the
# body text (see render_auth_email).
_VERIFY_PATH = "/account/verify"
_RESET_PATH = "/account/reset"
_LOGIN_PATH = "/account/login"


class EmailSendError(Exception):
    """Raised by :meth:`SmtpEmailSender.send` when delivery fails.

    Surfacing a defined exception (instead of a raw smtplib/ssl/socket
    traceback) lets the caller catch precisely and decide to fall back.
    """


class EmailSender(Protocol):
    """The delivery interface. ``LogEmailSender`` and ``SmtpEmailSender`` both
    implement it so ``AuthRuntime`` can hold one without caring which it is."""

    def send(self, to_addr: str, subject: str, body: str) -> None:
        ...


def _build_link(app_url: str, path: str, *, query_key: str, value: str) -> str:
    """Compose ``<app_url><path>?<query_key>=<urlencoded value>``.

    ``app_url`` is settings.public_app_url (or public_base_url); we strip a
    trailing slash so we never emit a double slash. The token/code is
    percent-encoded so tokens containing URL-reserved bytes survive intact.
    """
    base = (app_url or "").rstrip("/")
    return f"{base}{path}?{query_key}={quote(value, safe='')}"


def render_auth_email(
    kind: str,
    email: str,
    token: str,
    *,
    app_url: str,
    product: str = APP_BRAND,
) -> tuple[str, str]:
    """Render an auth email as ``(subject, plaintext_body)``.

    Pure function (no I/O) so it is trivial to unit-test. Three templates:

    - ``email_verify``  -> link to /account/verify?token=...
    - ``password_reset`` -> link to /account/reset?token=...
    - ``account_claim``  -> link to /account/login (no dedicated claim page)
      with the claim code shown in the body.

    Any unknown kind renders a safe generic notice rather than raising, so a new
    FLOW_KIND can never turn into a 500 on the auth path.
    """
    signoff = (
        f"If you did not request this, you can safely ignore this email.\n\n"
        f"— The {product} team"
    )
    if kind == "email_verify":
        link = _build_link(app_url, _VERIFY_PATH, query_key="token", value=token)
        subject = f"Verify your {product} email"
        body = (
            f"Welcome to {product}.\n\n"
            f"Confirm this email address to activate your account:\n\n"
            f"    {link}\n\n"
            f"This link expires shortly and can only be used once.\n\n"
            f"{signoff}\n"
        )
        return subject, body
    if kind == "password_reset":
        link = _build_link(app_url, _RESET_PATH, query_key="token", value=token)
        subject = f"Reset your {product} password"
        body = (
            f"We received a request to reset the password for your {product} account.\n\n"
            f"Choose a new password here:\n\n"
            f"    {link}\n\n"
            f"This link expires shortly and can only be used once. Your password "
            f"will not change until you complete this step.\n\n"
            f"{signoff}\n"
        )
        return subject, body
    if kind == "account_claim":
        # No dedicated claim page in the web front-door; send them to sign-in and
        # give them the code to paste. The token here is the "flow_id.secret"
        # claim code, not a URL query token.
        link = (app_url or "").rstrip("/") + _LOGIN_PATH
        subject = f"Claim your {product} account"
        body = (
            f"An account has been created for you on {product}.\n\n"
            f"Sign in to finish setting it up:\n\n"
            f"    {link}\n\n"
            f"Use this one-time claim code when prompted:\n\n"
            f"    {token}\n\n"
            f"This code expires shortly and can only be used once.\n\n"
            f"{signoff}\n"
        )
        return subject, body
    # Unknown kind: never raise on the auth path. Emit a neutral notice that
    # still carries the token so an operator can act on it if needed.
    subject = f"A {product} account notification"
    body = (
        f"Your {product} account has a pending action ({kind}).\n\n"
        f"Reference code:\n\n    {token}\n\n"
        f"{signoff}\n"
    )
    return subject, body


class LogEmailSender:
    """The self-hosting / beta default: append to a bounded outbox and log.

    This preserves the pre-SMTP behavior. ``AuthRuntime`` keeps its own
    ``self.outbox`` (tests + beta rely on it) and passes it in here so there is a
    single shared record; if none is provided we keep our own.
    """

    def __init__(self, outbox: deque[dict[str, str]] | None = None) -> None:
        self.outbox: deque[dict[str, str]] = outbox if outbox is not None else deque(maxlen=50)

    def send(self, to_addr: str, subject: str, body: str) -> None:
        self.outbox.append({"email": to_addr, "subject": subject, "body": body})
        message = f"[cortex-auth] email to {to_addr}: {subject}"
        print(message, flush=True)
        _logger.info(message)


class SmtpEmailSender:
    """Deliver via SMTP using stdlib ``smtplib`` + ``ssl``.

    Two transport modes:

    - ``use_ssl=True``  -> implicit TLS from the first byte (SMTPS, usually :465).
    - ``use_starttls=True`` (default) -> plaintext connect then upgrade with
      STARTTLS (submission, usually :587).

    ``send`` catches every transport-layer error and re-raises it as
    :class:`EmailSendError`, so callers can fall back without seeing a raw
    traceback and signup/login never 500s on a mail outage.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        username: str = "",
        password: str = "",
        from_addr: str = "",
        use_starttls: bool = True,
        use_ssl: bool = False,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        # Fall back to the SMTP username as the envelope/from address when a
        # dedicated from address is not configured.
        self.from_addr = from_addr or username
        self.use_ssl = bool(use_ssl)
        # Implicit TLS and STARTTLS are mutually exclusive; implicit TLS wins.
        self.use_starttls = bool(use_starttls) and not self.use_ssl
        self.timeout = float(timeout)

    def _build_message(self, to_addr: str, subject: str, body: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.from_addr
        message["To"] = to_addr
        message["Subject"] = subject
        message.set_content(body)
        return message

    def send(self, to_addr: str, subject: str, body: str) -> None:
        if ssl is None:
            raise EmailSendError("TLS is unavailable in this build; SMTP email cannot be sent.")
        message = self._build_message(to_addr, subject, body)
        context = ssl.create_default_context()
        try:
            if self.use_ssl:
                with smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout, context=context
                ) as client:
                    self._authenticate_and_send(client, message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
                    client.ehlo()
                    if self.use_starttls:
                        client.starttls(context=context)
                        client.ehlo()
                    self._authenticate_and_send(client, message)
        except EmailSendError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
            _logger.error("SMTP delivery to %s failed: %s", to_addr, exc)
            raise EmailSendError(str(exc)) from exc

    def _authenticate_and_send(self, client: smtplib.SMTP, message: EmailMessage) -> None:
        if self.username:
            client.login(self.username, self.password)
        client.send_message(message)
