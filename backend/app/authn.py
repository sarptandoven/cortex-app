"""First-party auth logic for the hosted plane (framework-free).

Implements sections 1-3 of docs/ACCOUNTS_ENCRYPTION_DESIGN.md over the
`ControlStore` contract in backend/app/accounts.py:

- Password hashing: argon2id via argon2-cffi (m=64 MiB, t=3, p=1; PHC
  self-describing strings, rehash-on-login when params rise) with an
  automatic pure-stdlib scrypt fallback (N=2^17, r=8, p=1, PHC-style
  ``$scrypt$...``) when argon2 is unimportable. Hashes minted by either
  backend verify under the argon2-capable path; scrypt hashes verify under
  both.
- Opaque tokens minted with the exact scoped-token discipline from
  sharding.TokenControlIndex: ``cxs_``/``cxr_`` + token_urlsafe(32) with
  ``-``/``_`` stripped and capped at 43 chars; storage is the same two-hash
  scheme — deterministic ``sha256('cxlookup:'+token)`` index key plus a
  per-row salted ``sha256(salt+':'+token)`` verified with compare_digest.
  No JWTs anywhere; revocation is one indexed UPDATE.
- Refresh rotation with family revocation on reuse; enumeration-resistant
  generic responses; never-silent-auto-link OIDC identity primitives;
  single-use hashed, TTL'd email-verify / password-reset / link-challenge
  flows (emailed token = ``<flow_id>.<secret>`` so lookup stays O(1) by PK
  while the secret is salted-hashed at rest).

No fastapi, no cryptography imports: HTTP wiring happens later in main.py.
Everything time-dependent runs off an injectable clock; rate limiting is an
injectable callable hook (wired to backend/app/ratelimit.py later).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .accounts import ControlStore, iso_utc, utc_now

try:  # argon2-cffi is a hosted-plane dependency; stdlib scrypt is the fallback.
    from argon2 import PasswordHasher as _Argon2PasswordHasher
    from argon2 import exceptions as _argon2_exceptions

    _ARGON2_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via prefer_argon2=False parity tests
    _Argon2PasswordHasher = None
    _argon2_exceptions = None
    _ARGON2_AVAILABLE = False


GENERIC_AUTH_FAILURE = "invalid credentials or token"

ACCESS_TOKEN_PREFIX = "cxs_"
REFRESH_TOKEN_PREFIX = "cxr_"

# Lifetimes (doc section 3): access ~1h, refresh 30d idle / 90d absolute;
# emailed single-use flows 30 min. All injectable on AccountsService.
DEFAULT_ACCESS_TTL_SECONDS = 60 * 60
DEFAULT_REFRESH_IDLE_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_REFRESH_ABSOLUTE_TTL_SECONDS = 90 * 24 * 60 * 60
DEFAULT_FLOW_TTL_SECONDS = 30 * 60
LAST_SEEN_COARSENING_SECONDS = 300  # write last_seen_at at most once per 5 min

MIN_PASSWORD_LENGTH = 8


class AuthError(Exception):
    """Generic auth failure. The message is deliberately uniform for every
    credential/token failure mode so responses never form an enumeration or
    state oracle."""

    def __init__(self, message: str = GENERIC_AUTH_FAILURE) -> None:
        super().__init__(message)


class RateLimited(AuthError):
    def __init__(self) -> None:
        super().__init__("rate limited")


# Argon2id uses 64 MiB per operation. Hosted deployments accept concurrent auth
# requests, so an unbounded thread-pool burst can exhaust the service memory limit
# before per-identifier rate limits help. Bound all password work in this process;
# multi-worker deployments multiply this small cap rather than the request burst.
def _password_work_limit() -> int:
    try:
        configured = int(os.environ.get("CORTEX_PASSWORD_HASH_CONCURRENCY", "2") or "2")
    except ValueError:
        configured = 2
    return max(1, min(configured, 8))


_PASSWORD_WORK_SLOTS = threading.BoundedSemaphore(_password_work_limit())


@contextmanager
def _password_work_slot():
    if not _PASSWORD_WORK_SLOTS.acquire(timeout=5):
        raise RateLimited()
    try:
        yield
    finally:
        _PASSWORD_WORK_SLOTS.release()


# --------------------------------------------------------------------------
# Token discipline (mirrors sharding.TokenControlIndex exactly)
# --------------------------------------------------------------------------

def mint_token(prefix: str) -> str:
    # Same expression as sharding.StoreRegistry.create_api_token/create_mcp_token.
    return prefix + secrets.token_urlsafe(32).replace("-", "").replace("_", "")[:43]


def token_lookup_hash(token: str) -> str:
    # Deterministic (unsalted) index key for O(1) lookup; safe because tokens
    # are high-entropy CSPRNG strings. Identical to TokenControlIndex._lookup_hash.
    return hashlib.sha256(f"cxlookup:{token}".encode("utf-8")).hexdigest()


def token_verify_hash(token: str, salt: str) -> str:
    # Per-row salted hash compared in constant time. Identical to
    # TokenControlIndex._token_hash.
    return hashlib.sha256(f"{salt}:{token}".encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(13)}"


def default_user_id_factory() -> str:
    # 'u_' + token_hex(13): the immutable, non-PII shard-routing key shape.
    return _new_id("u")


# --------------------------------------------------------------------------
# Password hashing engine
# --------------------------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(encoded: str) -> bytes:
    return base64.b64decode(encoded + "=" * (-len(encoded) % 4))


class PasswordEngine:
    """argon2id-first password hashing with a pure-stdlib scrypt fallback.

    - argon2id defaults per the design doc: m=64 MiB, t=3, p=1 (above the
      OWASP floor m=19 MiB, t=2, p=1). PHC strings are self-describing, so
      `needs_rehash` detects outdated params for rehash-on-login.
    - scrypt fallback (used automatically when argon2-cffi is unimportable,
      or when constructed with prefer_argon2=False): hashlib.scrypt N=2^17,
      r=8, p=1, encoded as ``$scrypt$ln=17,r=8,p=1$<b64salt>$<b64key>``.
    - Verification dispatches on the PHC prefix, so hashes minted by either
      backend verify wherever the required primitive is available (scrypt is
      always available; argon2 hashes verify whenever the library imports).
    """

    def __init__(
        self,
        *,
        prefer_argon2: bool = True,
        time_cost: int = 3,
        memory_cost_kib: int = 64 * 1024,
        parallelism: int = 1,
        scrypt_ln: int = 17,
        scrypt_r: int = 8,
        scrypt_p: int = 1,
    ) -> None:
        self.scrypt_ln = int(scrypt_ln)
        self.scrypt_r = int(scrypt_r)
        self.scrypt_p = int(scrypt_p)
        self._hasher = None
        if prefer_argon2 and _ARGON2_AVAILABLE:
            self._hasher = _Argon2PasswordHasher(
                time_cost=int(time_cost),
                memory_cost=int(memory_cost_kib),
                parallelism=int(parallelism),
                hash_len=32,
                salt_len=16,
            )
        self.backend = "argon2" if self._hasher is not None else "scrypt"
        # Verifier for argon2 PHC strings even when this engine mints scrypt:
        # params come from the PHC string itself, so a default hasher suffices.
        self._argon2_verifier = self._hasher
        if self._argon2_verifier is None and _ARGON2_AVAILABLE:
            self._argon2_verifier = _Argon2PasswordHasher()
        self._dummy_phc: Optional[str] = None

    # -- minting -----------------------------------------------------------
    def hash(self, password: str) -> str:
        with _password_work_slot():
            if self._hasher is not None:
                return self._hasher.hash(password)
            return self._scrypt_hash(password)

    def _scrypt_hash(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        derived = self._scrypt_derive(password, salt, self.scrypt_ln, self.scrypt_r, self.scrypt_p)
        return (
            f"$scrypt$ln={self.scrypt_ln},r={self.scrypt_r},p={self.scrypt_p}"
            f"${_b64(salt)}${_b64(derived)}"
        )

    @staticmethod
    def _scrypt_derive(password: str, salt: bytes, ln: int, r: int, p: int) -> bytes:
        n = 1 << ln
        # scrypt needs 128*r*N bytes; hashlib's default maxmem (~32 MiB) is too
        # small for N=2^17, so pass an explicit generous cap.
        maxmem = 128 * r * n * 2 + (1 << 20)
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=maxmem, dklen=32
        )

    # -- verification ------------------------------------------------------
    def verify(self, phc: str, password: str) -> bool:
        if not phc or not isinstance(phc, str):
            return False
        with _password_work_slot():
            if phc.startswith("$argon2"):
                if self._argon2_verifier is None:
                    return False
                try:
                    return bool(self._argon2_verifier.verify(phc, password))
                except (
                    _argon2_exceptions.VerificationError,
                    _argon2_exceptions.InvalidHashError,
                    ValueError,
                ):
                    return False
            if phc.startswith("$scrypt$"):
                parsed = self._parse_scrypt(phc)
                if parsed is None:
                    return False
                ln, r, p, salt, expected = parsed
                try:
                    derived = self._scrypt_derive(password, salt, ln, r, p)
                except (ValueError, MemoryError):
                    return False
                return hmac.compare_digest(derived, expected)
            return False

    @staticmethod
    def _parse_scrypt(phc: str) -> tuple[int, int, int, bytes, bytes] | None:
        parts = phc.split("$")
        # ['', 'scrypt', 'ln=17,r=8,p=1', '<salt>', '<key>']
        if len(parts) != 5 or parts[1] != "scrypt":
            return None
        params: dict[str, int] = {}
        try:
            for pair in parts[2].split(","):
                key, _, value = pair.partition("=")
                params[key.strip()] = int(value)
            salt = _b64decode(parts[3])
            expected = _b64decode(parts[4])
        except (ValueError, KeyError):
            return None
        if not {"ln", "r", "p"} <= set(params):
            return None
        return params["ln"], params["r"], params["p"], salt, expected

    def needs_rehash(self, phc: str) -> bool:
        """True when a successfully verified hash should be re-minted with the
        engine's current backend/params (rehash-on-login)."""
        if self.backend == "argon2":
            if phc.startswith("$scrypt$"):
                return True  # upgrade fallback hashes to argon2id
            try:
                return bool(self._hasher.check_needs_rehash(phc))
            except (_argon2_exceptions.InvalidHashError, ValueError):
                return True
        # scrypt backend: never downgrade an argon2 hash to scrypt.
        if phc.startswith("$argon2"):
            return False
        parsed = self._parse_scrypt(phc)
        if parsed is None:
            return True
        ln, r, p, _salt, _expected = parsed
        return (ln, r, p) != (self.scrypt_ln, self.scrypt_r, self.scrypt_p)

    def dummy_verify(self, password: str) -> bool:
        """Burn a real verification against a throwaway hash so 'account not
        found' and 'wrong password' take comparable time (login timing-oracle
        flattening). Always returns False."""
        if self._dummy_phc is None:
            self._dummy_phc = self.hash(secrets.token_urlsafe(24))
        result = self.verify(self._dummy_phc, password)
        return result and False


# --------------------------------------------------------------------------
# Auth service
# --------------------------------------------------------------------------

# Generic, enumeration-proof response shapes (fresh copies returned per call).
_SIGNUP_RESULT = {"ok": True, "next": "verify_email"}
_RESET_REQUEST_RESULT = {"ok": True, "next": "check_email"}


class AccountsService:
    """Account/identity/session logic over a ControlStore.

    - `clock` is an injectable zero-arg callable returning an aware datetime.
    - `limiter(action, key)` is the rate-limit hook: return truthy to allow;
      falsy raises RateLimited *before* any password hashing work.
    - `flow_delivery(kind, email, token)` is how emailed single-use tokens
      leave the service (log sink / SMTP wired later); tokens never appear in
      API-shaped return values for enumeration-sensitive calls.
    - `user_id_factory` mints the immutable shard-routing user_id; actual
      shard provisioning is wired at activation time by the HTTP layer.
    """

    def __init__(
        self,
        store: ControlStore,
        *,
        password_engine: Optional[PasswordEngine] = None,
        clock: Callable[[], datetime] = utc_now,
        limiter: Optional[Callable[[str, str], Any]] = None,
        flow_delivery: Optional[Callable[[str, str, str], None]] = None,
        user_id_factory: Callable[[], str] = default_user_id_factory,
        access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
        refresh_idle_ttl_seconds: int = DEFAULT_REFRESH_IDLE_TTL_SECONDS,
        refresh_absolute_ttl_seconds: int = DEFAULT_REFRESH_ABSOLUTE_TTL_SECONDS,
        flow_ttl_seconds: int = DEFAULT_FLOW_TTL_SECONDS,
    ) -> None:
        self.store = store
        self.engine = password_engine or PasswordEngine()
        self._clock = clock
        self._limiter = limiter
        self._flow_delivery = flow_delivery
        self._user_id_factory = user_id_factory
        self.access_ttl_seconds = int(access_ttl_seconds)
        self.refresh_idle_ttl_seconds = int(refresh_idle_ttl_seconds)
        self.refresh_absolute_ttl_seconds = int(refresh_absolute_ttl_seconds)
        self.flow_ttl_seconds = int(flow_ttl_seconds)

    # ------------------------------------------------------------------ time
    def _now(self) -> datetime:
        moment = self._clock()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    def _now_iso(self) -> str:
        return iso_utc(self._now())

    # ------------------------------------------------------------------ hooks
    def _rate_limit(self, action: str, key: str) -> None:
        if self._limiter is None:
            return
        if not self._limiter(action, key):
            raise RateLimited()

    def _deliver(self, kind: str, email: str, token: str) -> None:
        if self._flow_delivery is not None:
            self._flow_delivery(kind, email, token)

    def _audit(self, event: str, **kwargs: Any) -> None:
        self.store.record_audit_event(event, now=self._now_iso(), **kwargs)

    # ------------------------------------------------------------- validation
    @staticmethod
    def _normalize_email(email: str) -> str:
        normalized = (email or "").strip().lower()
        local, _, domain = normalized.partition("@")
        if not local or not domain or "." not in domain:
            raise ValueError("invalid email address")
        return normalized

    @staticmethod
    def _check_password_strength(password: str) -> None:
        if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")

    # ------------------------------------------------------------------ flows
    def _create_secret_flow(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        ttl_seconds: Optional[int] = None,
        provider: Optional[str] = None,
    ) -> tuple[str, str]:
        """Create a single-use flow row and return (flow_id, bearer_token).
        bearer_token = '<flow_id>.<secret>' — flow_id gives an O(1) PK lookup,
        the secret is stored only as a salted hash (never plaintext at rest)."""
        flow_id = _new_id("flw")
        secret = secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        now = self._now()
        self.store.create_flow(
            flow_id=flow_id,
            kind=kind,
            payload=payload,
            secret_hash=f"{salt}${token_verify_hash(secret, salt)}",
            expires_at=iso_utc(now + timedelta(seconds=ttl_seconds or self.flow_ttl_seconds)),
            now=iso_utc(now),
            provider=provider,
        )
        return flow_id, f"{flow_id}.{secret}"

    def _consume_flow_token(self, token: str, expected_kind: str) -> dict[str, Any]:
        """Validate + atomically consume a '<flow_id>.<secret>' bearer token.
        Every failure mode raises the same generic AuthError."""
        flow_id, sep, secret = (token or "").partition(".")
        if not sep or not flow_id or not secret:
            raise AuthError()
        flow = self.store.get_flow(flow_id)
        if flow is None or flow.get("kind") != expected_kind:
            raise AuthError()
        stored = str(flow.get("secret_hash") or "")
        salt, sep2, digest = stored.partition("$")
        if not sep2 or not hmac.compare_digest(token_verify_hash(secret, salt), digest):
            raise AuthError()
        if str(flow.get("expires_at") or "") <= self._now_iso():
            raise AuthError()
        consumed = self.store.consume_flow(flow_id, self._now_iso())
        if consumed is None:  # already consumed: single-use, first caller wins
            raise AuthError()
        return consumed

    @staticmethod
    def _flow_payload(flow: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = json.loads(flow.get("payload_json") or "{}")
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    # ----------------------------------------------------------------- signup
    def signup(self, email: str, password: str, *, display_name: str = "") -> dict[str, Any]:
        """Create a pending_verification account + email-verify flow.

        Enumeration discipline: an already-registered email returns the exact
        same value as success (and the password is hashed either way, so the
        two paths cost comparable time). The verification token leaves only
        through flow_delivery, never in the return value."""
        normalized = self._normalize_email(email)
        self._check_password_strength(password)
        self._rate_limit("signup", normalized)
        password_hash = self.engine.hash(password)  # before collision check: flat timing
        now_iso = self._now_iso()
        account_id = _new_id("acct")
        try:
            account = self.store.create_account(
                account_id=account_id,
                user_id=self._user_id_factory(),
                primary_email=normalized,
                display_name=display_name,
                status="pending_verification",
                email_verified_at=None,
                now=now_iso,
            )
        except Exception:
            # Email (or user_id) collision: identical generic response, no new
            # rows, no second verification email.
            self._audit("signup_conflict")
            return dict(_SIGNUP_RESULT)
        self.store.create_identity(
            identity_id=_new_id("idn"),
            account_id=account_id,
            provider="password",
            provider_subject=normalized,
            email=normalized,
            email_verified=False,
            profile=None,
            now=now_iso,
        )
        self.store.set_password_credential(account_id, password_hash, now_iso)
        self._send_email_verification(account)
        self._audit("signup", account_id=account_id)
        return dict(_SIGNUP_RESULT)

    def _send_email_verification(self, account: dict[str, Any]) -> None:
        email = str(account.get("primary_email") or "")
        if not email:
            return
        _flow_id, token = self._create_secret_flow(
            kind="email_verify",
            payload={"account_id": account["account_id"], "email": email},
        )
        self._deliver("email_verify", email, token)

    def verify_email(self, token: str) -> dict[str, Any]:
        """Single-use, TTL'd activation. pending_verification -> active."""
        flow = self._consume_flow_token(token, "email_verify")
        payload = self._flow_payload(flow)
        account = self.store.get_account(str(payload.get("account_id") or ""))
        if account is None or account["status"] in ("suspended", "deleted"):
            raise AuthError()
        now_iso = self._now_iso()
        new_status = "active" if account["status"] == "pending_verification" else account["status"]
        account = self.store.update_account_fields(
            account["account_id"], now=now_iso, status=new_status, email_verified_at=now_iso
        )
        self._audit("email_verified", account_id=account["account_id"])
        return account

    # ------------------------------------------------------------------ login
    def login(
        self,
        email: str,
        password: str,
        *,
        client: str = "web",
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict[str, Any]:
        """Password login -> {access: cxs_..., refresh: cxr_..., account}.

        Every failure (unknown email, wrong password, suspended/deleted
        account) raises the same AuthError; a dummy hash is verified when the
        account or credential is missing so timing stays flat."""
        try:
            normalized = self._normalize_email(email)
        except ValueError:
            self.engine.dummy_verify(password)
            raise AuthError()
        self._rate_limit("login", normalized)
        account = self.store.get_account_by_email(normalized)
        credential = (
            self.store.get_password_credential(account["account_id"]) if account else None
        )
        if account is None or credential is None:
            self.engine.dummy_verify(password)
            self._audit("login_fail")
            raise AuthError()
        phc = str(credential["password_hash"])
        if not self.engine.verify(phc, password):
            self._audit("login_fail", account_id=account["account_id"])
            raise AuthError()
        if account["status"] in ("suspended", "deleted"):
            self._audit("login_fail", account_id=account["account_id"])
            raise AuthError()
        if self.engine.needs_rehash(phc):
            self.store.set_password_credential(
                account["account_id"], self.engine.hash(password), self._now_iso()
            )
        identity = self.store.get_identity("password", normalized)
        if identity is not None:
            self.store.touch_identity_login(identity["identity_id"], self._now_iso())
        session = self.mint_session(account, client=client, ip=ip, user_agent=user_agent)
        self._audit("login_ok", account_id=account["account_id"])
        return session

    # --------------------------------------------------------------- sessions
    def mint_session(
        self,
        account: dict[str, Any] | str,
        *,
        client: str = "web",
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict[str, Any]:
        """Mint an access+refresh pair for an account (used by password login
        now and by the OIDC/app-login wiring later). Stores only the two-hash
        forms; plaintext tokens exist solely in the return value."""
        if isinstance(account, str):
            resolved = self.store.get_account(account)
        else:
            resolved = account
        if resolved is None or resolved["status"] in ("suspended", "deleted"):
            raise AuthError()
        now = self._now()
        now_iso = iso_utc(now)
        access_token = mint_token(ACCESS_TOKEN_PREFIX)
        refresh_token = mint_token(REFRESH_TOKEN_PREFIX)
        access_salt = secrets.token_hex(16)
        refresh_salt = secrets.token_hex(16)
        session_id = _new_id("sess")
        absolute_expiry = iso_utc(now + timedelta(seconds=self.refresh_absolute_ttl_seconds))
        idle_expiry = min(
            iso_utc(now + timedelta(seconds=self.refresh_idle_ttl_seconds)), absolute_expiry
        )
        self.store.create_session(
            {
                "session_id": session_id,
                "account_id": resolved["account_id"],
                "user_id": resolved["user_id"],
                "client": client,
                "access_lookup_hash": token_lookup_hash(access_token),
                "access_salt": access_salt,
                "access_hash": token_verify_hash(access_token, access_salt),
                "access_expires_at": iso_utc(now + timedelta(seconds=self.access_ttl_seconds)),
                "refresh_family_id": _new_id("fam"),
                "refresh_lookup_hash": token_lookup_hash(refresh_token),
                "refresh_salt": refresh_salt,
                "refresh_hash": token_verify_hash(refresh_token, refresh_salt),
                "refresh_expires_at": absolute_expiry,
                "refresh_idle_expires_at": idle_expiry,
                "created_ip": ip,
                "user_agent": user_agent,
                "created_at": now_iso,
                "last_seen_at": now_iso,
            }
        )
        return {
            "access": access_token,
            "refresh": refresh_token,
            "session_id": session_id,
            "account": resolved,
        }

    def verify_session(self, access_token: str) -> dict[str, Any] | None:
        """One indexed lookup -> {'account', 'session_id', 'user_id', 'client'}
        or None. Checks salted hash, expiry, revocation, and account status
        (suspended/deleted => invalid; pending_verification stays usable —
        verification gates connector credentials, not basic access)."""
        token = (access_token or "").strip()
        if not token.startswith(ACCESS_TOKEN_PREFIX):
            return None
        now = self._now()
        now_iso = iso_utc(now)
        for row in self.store.find_sessions_by_access_lookup(token_lookup_hash(token)):
            if not hmac.compare_digest(
                token_verify_hash(token, str(row["access_salt"])), str(row["access_hash"])
            ):
                continue
            if str(row["access_expires_at"]) <= now_iso:
                return None
            account = self.store.get_account(str(row["account_id"]))
            if account is None or account["status"] in ("suspended", "deleted"):
                return None
            self._coarse_touch_last_seen(row, now)
            return {
                "account": account,
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "client": row["client"],
            }
        return None

    def _coarse_touch_last_seen(self, session_row: dict[str, Any], now: datetime) -> None:
        last_seen = session_row.get("last_seen_at")
        if last_seen:
            try:
                previous = datetime.fromisoformat(str(last_seen))
            except ValueError:
                previous = None
            if previous is not None and previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            if previous is not None and (now - previous).total_seconds() < LAST_SEEN_COARSENING_SECONDS:
                return
        self.store.touch_session_last_seen(str(session_row["session_id"]), iso_utc(now))

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Rotate a refresh token: new access+refresh on the same session row.
        Reuse of an already-rotated token (it lives in refresh_token_history)
        revokes the ENTIRE family and fails generically."""
        token = (refresh_token or "").strip()
        if not token.startswith(REFRESH_TOKEN_PREFIX):
            raise AuthError()
        lookup = token_lookup_hash(token)
        matched: dict[str, Any] | None = None
        for row in self.store.find_sessions_by_refresh_lookup(lookup):
            if hmac.compare_digest(
                token_verify_hash(token, str(row["refresh_salt"])), str(row["refresh_hash"])
            ):
                matched = row
                break
        if matched is None:
            self._revoke_family_on_reuse(lookup)
            raise AuthError()
        now = self._now()
        now_iso = iso_utc(now)
        if str(matched["refresh_expires_at"]) <= now_iso or str(
            matched["refresh_idle_expires_at"]
        ) <= now_iso:
            self.store.revoke_session(
                str(matched["session_id"]), reason="refresh_expired", now=now_iso
            )
            raise AuthError()
        account = self.store.get_account(str(matched["account_id"]))
        if account is None or account["status"] in ("suspended", "deleted"):
            raise AuthError()
        new_access = mint_token(ACCESS_TOKEN_PREFIX)
        new_refresh = mint_token(REFRESH_TOKEN_PREFIX)
        access_salt = secrets.token_hex(16)
        refresh_salt = secrets.token_hex(16)
        idle_expiry = min(
            iso_utc(now + timedelta(seconds=self.refresh_idle_ttl_seconds)),
            str(matched["refresh_expires_at"]),  # sliding window capped at absolute expiry
        )
        rotated = self.store.rotate_session(
            str(matched["session_id"]),
            expected_refresh_lookup_hash=lookup,
            access_lookup_hash=token_lookup_hash(new_access),
            access_salt=access_salt,
            access_hash=token_verify_hash(new_access, access_salt),
            access_expires_at=iso_utc(now + timedelta(seconds=self.access_ttl_seconds)),
            refresh_lookup_hash=token_lookup_hash(new_refresh),
            refresh_salt=refresh_salt,
            refresh_hash=token_verify_hash(new_refresh, refresh_salt),
            refresh_idle_expires_at=idle_expiry,
            now=now_iso,
        )
        if not rotated:
            # Lost a race with a concurrent rotation/revocation of the same
            # token: from this caller's perspective that IS token reuse.
            self._revoke_family_on_reuse(lookup)
            raise AuthError()
        self._audit("refresh_rotated", account_id=account["account_id"])
        return {
            "access": new_access,
            "refresh": new_refresh,
            "session_id": matched["session_id"],
            "account": account,
        }

    def _revoke_family_on_reuse(self, lookup_hash: str) -> None:
        history = self.store.find_refresh_history(lookup_hash)
        if history is None:
            return
        now_iso = self._now_iso()
        revoked = self.store.revoke_family(
            str(history["family_id"]), reason="refresh_reuse", now=now_iso
        )
        self._audit(
            "refresh_reuse",
            detail={"family_id": history["family_id"], "revoked_sessions": revoked},
        )

    def logout(self, access_token: str) -> bool:
        """Revoke the session identified by a live access token."""
        token = (access_token or "").strip()
        if not token.startswith(ACCESS_TOKEN_PREFIX):
            raise AuthError()
        for row in self.store.find_sessions_by_access_lookup(token_lookup_hash(token)):
            if hmac.compare_digest(
                token_verify_hash(token, str(row["access_salt"])), str(row["access_hash"])
            ):
                now_iso = self._now_iso()
                self.store.revoke_session(str(row["session_id"]), reason="logout", now=now_iso)
                self._audit("session_revoked", account_id=row["account_id"], detail={"reason": "logout"})
                return True
        raise AuthError()

    def revoke_all(self, account_id: str, *, reason: str = "revoke_all") -> int:
        count = self.store.revoke_account_sessions(account_id, reason=reason, now=self._now_iso())
        self._audit("sessions_revoked_all", account_id=account_id, detail={"reason": reason, "count": count})
        return count

    # -------------------------------------------------------------- passwords
    def change_password(
        self,
        account_id: str,
        current_password: Optional[str],
        new_password: str,
        *,
        keep_session_id: Optional[str] = None,
    ) -> None:
        """Set a new password. When a credential exists the current password
        must verify; when none exists (OAuth-only account setting its first
        password) current_password must be None. Revokes every other session."""
        self._check_password_strength(new_password)
        account = self.store.get_account(account_id)
        if account is None or account["status"] in ("suspended", "deleted"):
            raise AuthError()
        credential = self.store.get_password_credential(account_id)
        if credential is not None:
            if current_password is None or not self.engine.verify(
                str(credential["password_hash"]), current_password
            ):
                raise AuthError()
        now_iso = self._now_iso()
        self.store.set_password_credential(account_id, self.engine.hash(new_password), now_iso)
        self._ensure_password_identity(account, now_iso)
        self.store.revoke_account_sessions(
            account_id, reason="password_change", now=now_iso, keep_session_id=keep_session_id
        )
        self._audit("password_changed", account_id=account_id)

    def _ensure_password_identity(self, account: dict[str, Any], now_iso: str) -> None:
        email = str(account.get("primary_email") or "")
        if not email:
            return
        if self.store.get_identity("password", email) is not None:
            return
        self.store.create_identity(
            identity_id=_new_id("idn"),
            account_id=account["account_id"],
            provider="password",
            provider_subject=email,
            email=email,
            email_verified=bool(account.get("email_verified_at")),
            profile=None,
            now=now_iso,
        )

    def request_password_reset(self, email: str) -> dict[str, Any]:
        """Always the same generic response; a reset flow is created (and
        delivered out-of-band) only when the account actually exists."""
        try:
            normalized = self._normalize_email(email)
        except ValueError:
            return dict(_RESET_REQUEST_RESULT)
        self._rate_limit("password_reset", normalized)
        account = self.store.get_account_by_email(normalized)
        if account is not None and account["status"] != "deleted":
            _flow_id, token = self._create_secret_flow(
                kind="password_reset",
                payload={"account_id": account["account_id"], "email": normalized},
            )
            self._deliver("password_reset", normalized, token)
            self._audit("password_reset_requested", account_id=account["account_id"])
        return dict(_RESET_REQUEST_RESULT)

    def confirm_password_reset(self, token: str, new_password: str) -> dict[str, Any]:
        """Single-use, TTL'd. Sets the password and revokes ALL sessions."""
        self._check_password_strength(new_password)
        flow = self._consume_flow_token(token, "password_reset")
        payload = self._flow_payload(flow)
        account = self.store.get_account(str(payload.get("account_id") or ""))
        if account is None or account["status"] in ("suspended", "deleted"):
            raise AuthError()
        now_iso = self._now_iso()
        self.store.set_password_credential(
            account["account_id"], self.engine.hash(new_password), now_iso
        )
        self.store.revoke_account_sessions(
            account["account_id"], reason="password_reset", now=now_iso
        )
        self._audit("password_reset", account_id=account["account_id"])
        return account

    # ------------------------------------------------------ OIDC identity core
    def find_or_challenge_identity(
        self,
        provider: str,
        subject: str,
        *,
        email: Optional[str] = None,
        email_verified: bool = False,
        display_name: str = "",
        profile: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Resolve a verified provider assertion (the HTTP OIDC dance happens
        in later wiring) per the never-silent-auto-link rule:

        - (provider, subject) already linked  -> {'action': 'login', ...}
        - email collides with ANY existing account -> {'action': 'link_required',
          'flow_id', 'challenge'} — never a silent link, even on a verified
          email match; identical shape whether the provider email is verified
          or not (no oracle). The user must authenticate with the existing
          method and then complete_link_challenge().
        - otherwise -> {'action': 'signup', ...}: a fresh account; an
          unverified provider email NEVER becomes primary_email.
        """
        provider = (provider or "").strip()
        subject = str(subject or "").strip()
        if not provider or not subject:
            raise AuthError()
        if provider == "password":
            raise ValueError("password identities go through signup/login")
        identity = self.store.get_identity(provider, subject)
        if identity is not None:
            account = self.store.get_account(str(identity["account_id"]))
            if account is None or account["status"] in ("suspended", "deleted"):
                raise AuthError()
            self.store.touch_identity_login(identity["identity_id"], self._now_iso())
            return {"action": "login", "account_id": account["account_id"], "account": account}
        normalized_email: Optional[str] = None
        if email:
            try:
                normalized_email = self._normalize_email(email)
            except ValueError:
                normalized_email = None
        if normalized_email is not None:
            existing = self.store.get_account_by_email(normalized_email)
            if existing is not None:
                flow_id, challenge = self._create_secret_flow(
                    kind="link_challenge",
                    payload={
                        "account_id": existing["account_id"],
                        "provider": provider,
                        "provider_subject": subject,
                        "email": normalized_email,
                        "email_verified": bool(email_verified),
                        "profile": profile or {},
                    },
                    provider=provider,
                )
                self._audit("link_challenge_issued", account_id=existing["account_id"])
                return {"action": "link_required", "flow_id": flow_id, "challenge": challenge}
        # Fresh signup from the provider assertion.
        now_iso = self._now_iso()
        trusted_email = normalized_email if email_verified else None
        account = self.store.create_account(
            account_id=_new_id("acct"),
            user_id=self._user_id_factory(),
            primary_email=trusted_email,
            display_name=display_name,
            status="active" if trusted_email else "pending_verification",
            email_verified_at=now_iso if trusted_email else None,
            now=now_iso,
        )
        self.store.create_identity(
            identity_id=_new_id("idn"),
            account_id=account["account_id"],
            provider=provider,
            provider_subject=subject,
            email=normalized_email,
            email_verified=bool(email_verified),
            profile=profile,
            now=now_iso,
        )
        self._audit("identity_signup", account_id=account["account_id"], detail={"provider": provider})
        return {"action": "signup", "account_id": account["account_id"], "account": account}

    def complete_link_challenge(self, challenge_token: str, authenticated_account_id: str) -> dict[str, Any]:
        """Attach the pending provider identity after the caller has already
        authenticated the user with an existing method. The challenge is bound
        to the colliding account: any other account fails generically."""
        flow = self._consume_flow_token(challenge_token, "link_challenge")
        payload = self._flow_payload(flow)
        if str(payload.get("account_id") or "") != str(authenticated_account_id or ""):
            raise AuthError()
        return self.link_identity(
            authenticated_account_id,
            str(payload.get("provider") or ""),
            str(payload.get("provider_subject") or ""),
            email=payload.get("email"),
            email_verified=bool(payload.get("email_verified")),
            profile=payload.get("profile") or None,
        )

    def link_identity(
        self,
        account_id: str,
        provider: str,
        subject: str,
        *,
        email: Optional[str] = None,
        email_verified: bool = False,
        profile: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Caller pre-authenticates the account (session/step-up handled by
        wiring). Refuses if (provider, subject) is already linked anywhere."""
        account = self.store.get_account(account_id)
        if account is None or account["status"] in ("suspended", "deleted"):
            raise AuthError()
        if not provider or not subject:
            raise AuthError()
        try:
            identity = self.store.create_identity(
                identity_id=_new_id("idn"),
                account_id=account_id,
                provider=provider,
                provider_subject=str(subject),
                email=email,
                email_verified=email_verified,
                profile=profile,
                now=self._now_iso(),
            )
        except Exception:
            raise AuthError()  # already linked (here or on another account)
        self._audit("identity_linked", account_id=account_id, identity_id=identity["identity_id"])
        return identity

    def unlink_identity(self, account_id: str, identity_id: str) -> bool:
        """Refuses to remove the last identity when no password credential
        exists — an account must always keep at least one way in."""
        identities = self.store.list_identities(account_id)
        target = next((row for row in identities if row["identity_id"] == identity_id), None)
        if target is None:
            raise AuthError()
        remaining = [row for row in identities if row["identity_id"] != identity_id]
        has_password = self.store.get_password_credential(account_id) is not None
        if not remaining and not has_password:
            # Doc section 2 linking rule: unlinking the last identity is
            # refused unless account_password_credentials exists (password
            # login runs off primary_email + the credential row, so it keeps
            # working even without a password identity row).
            raise AuthError("cannot remove the last authentication method")
        removed = self.store.delete_identity(account_id, identity_id)
        if removed:
            self._audit("identity_unlinked", account_id=account_id, identity_id=identity_id)
        return removed

    # ------------------------------------------------------ lifecycle / claims
    def mark_deleted(self, account_id: str) -> dict[str, Any]:
        """Mark the account deleted and revoke every session. Crypto-shred and
        data-plane deletion are orchestrated by the wiring layer."""
        account = self.store.get_account(account_id)
        if account is None:
            raise AuthError()
        now_iso = self._now_iso()
        account = self.store.update_account_fields(account_id, now=now_iso, status="deleted")
        self.store.revoke_account_sessions(account_id, reason="account_deleted", now=now_iso)
        self._audit("account_deleted", account_id=account_id)
        return account

    def claim_account_for_user(
        self,
        existing_user_id: str,
        email: str,
        password: Optional[str] = None,
        *,
        display_name: str = "",
        email_verified: bool = False,
    ) -> dict[str, Any]:
        """Legacy-user invite flow bridge: attach a brand-new account to an
        EXISTING provisioned user_id (same shard, same cxa_/cxm_ tokens, new
        login). Guard: a user_id that already has an account cannot be claimed
        again. This is a trusted, operator-driven path, so collisions raise."""
        user_id = str(existing_user_id or "").strip()
        if not user_id:
            raise ValueError("user_id is required")
        normalized = self._normalize_email(email)
        if password is not None:
            self._check_password_strength(password)
        if self.store.get_account_by_user_id(user_id) is not None:
            raise AuthError(f"user '{user_id}' already has an account")
        if self.store.get_account_by_email(normalized) is not None:
            raise AuthError("email is already registered")
        now_iso = self._now_iso()
        account = self.store.create_account(
            account_id=_new_id("acct"),
            user_id=user_id,
            primary_email=normalized,
            display_name=display_name,
            status="active" if email_verified else "pending_verification",
            email_verified_at=now_iso if email_verified else None,
            now=now_iso,
        )
        if password is not None:
            self.store.create_identity(
                identity_id=_new_id("idn"),
                account_id=account["account_id"],
                provider="password",
                provider_subject=normalized,
                email=normalized,
                email_verified=email_verified,
                profile=None,
                now=now_iso,
            )
            self.store.set_password_credential(
                account["account_id"], self.engine.hash(password), now_iso
            )
        if not email_verified:
            self._send_email_verification(account)
        self._audit("account_claimed", account_id=account["account_id"], detail={"user_id": user_id})
        return account


__all__ = [
    "ACCESS_TOKEN_PREFIX",
    "REFRESH_TOKEN_PREFIX",
    "GENERIC_AUTH_FAILURE",
    "DEFAULT_ACCESS_TTL_SECONDS",
    "DEFAULT_REFRESH_IDLE_TTL_SECONDS",
    "DEFAULT_REFRESH_ABSOLUTE_TTL_SECONDS",
    "DEFAULT_FLOW_TTL_SECONDS",
    "MIN_PASSWORD_LENGTH",
    "AccountsService",
    "AuthError",
    "PasswordEngine",
    "RateLimited",
    "default_user_id_factory",
    "mint_token",
    "token_lookup_hash",
    "token_verify_hash",
]
