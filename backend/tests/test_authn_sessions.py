"""AccountsService behavior tests (design doc sections 1-3 + test list 7.1-7.5,
7.13): password hashing discipline, signup/verify/login, enumeration
resistance, session verify/expiry/revocation, refresh rotation with family
revocation on reuse, single-use TTL'd flows, never-auto-link identity matrix,
unlink-last-method refusal, legacy-user claim guard, and a concurrency smoke.

Everything runs on an injectable FakeClock: no sleeps, fully deterministic.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.app.accounts import SQLiteControlStore
from backend.app import authn as authn_module
from backend.app.authn import (
    ACCESS_TOKEN_PREFIX,
    GENERIC_AUTH_FAILURE,
    REFRESH_TOKEN_PREFIX,
    AccountsService,
    AuthError,
    PasswordEngine,
    RateLimited,
    default_user_id_factory,
    mint_token,
    token_lookup_hash,
    token_verify_hash,
)
from backend.app.sharding import TokenControlIndex

T0 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)

# Cheap-but-real argon2id params for flow tests (default doc params are
# exercised once in PasswordEngineTests.test_default_params_roundtrip).
CHEAP_ARGON2 = dict(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1)
CHEAP_SCRYPT = dict(prefer_argon2=False, scrypt_ln=14)


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Password hashing engine
# ---------------------------------------------------------------------------


class PasswordEngineTests(unittest.TestCase):
    def test_default_params_roundtrip(self) -> None:
        engine = PasswordEngine()
        self.assertEqual(engine.backend, "argon2")
        phc = engine.hash("correct horse battery staple")
        self.assertTrue(phc.startswith("$argon2id$"))
        # Doc params: m=64 MiB, t=3, p=1 — self-described in the PHC string.
        self.assertIn("m=65536", phc)
        self.assertIn("t=3", phc)
        self.assertIn("p=1", phc)
        self.assertTrue(engine.verify(phc, "correct horse battery staple"))
        self.assertFalse(engine.verify(phc, "wrong password"))
        self.assertFalse(engine.needs_rehash(phc))

    def test_scrypt_fallback_mints_phc_style_and_verifies(self) -> None:
        engine = PasswordEngine(prefer_argon2=False, scrypt_ln=14)
        self.assertEqual(engine.backend, "scrypt")
        phc = engine.hash("hunter2hunter2")
        self.assertTrue(phc.startswith("$scrypt$ln=14,r=8,p=1$"))
        self.assertTrue(engine.verify(phc, "hunter2hunter2"))
        self.assertFalse(engine.verify(phc, "hunter2hunter3"))

    def test_scrypt_default_params_match_spec(self) -> None:
        engine = PasswordEngine(prefer_argon2=False)
        self.assertEqual((engine.scrypt_ln, engine.scrypt_r, engine.scrypt_p), (17, 8, 1))
        phc = engine.hash("a strong enough pw")
        self.assertTrue(phc.startswith("$scrypt$ln=17,r=8,p=1$"))
        self.assertTrue(engine.verify(phc, "a strong enough pw"))

    def test_cross_backend_parity(self) -> None:
        """Hashes minted by either backend verify under the other."""
        argon = PasswordEngine(**CHEAP_ARGON2)
        scrypt = PasswordEngine(**CHEAP_SCRYPT)
        argon_phc = argon.hash("shared-password")
        scrypt_phc = scrypt.hash("shared-password")
        # argon2-backed engine verifies scrypt-minted hashes...
        self.assertTrue(argon.verify(scrypt_phc, "shared-password"))
        self.assertFalse(argon.verify(scrypt_phc, "nope"))
        # ...and the scrypt-backed engine verifies argon2-minted hashes
        # (argon2 lib is importable here; without it this returns False).
        self.assertTrue(scrypt.verify(argon_phc, "shared-password"))
        self.assertFalse(scrypt.verify(argon_phc, "nope"))

    def test_needs_rehash_matrix(self) -> None:
        weak_argon = PasswordEngine(**CHEAP_ARGON2)
        strong_argon = PasswordEngine(time_cost=2, memory_cost_kib=16 * 1024, parallelism=1)
        scrypt = PasswordEngine(**CHEAP_SCRYPT)
        weak_phc = weak_argon.hash("pw")
        scrypt_phc = scrypt.hash("pw")
        # Same params: no rehash. Raised params: rehash.
        self.assertFalse(weak_argon.needs_rehash(weak_phc))
        self.assertTrue(strong_argon.needs_rehash(weak_phc))
        # scrypt hash under an argon2 backend: always upgrade.
        self.assertTrue(weak_argon.needs_rehash(scrypt_phc))
        # argon2 hash under a scrypt backend: never downgrade.
        self.assertFalse(scrypt.needs_rehash(weak_phc))
        # scrypt hash with matching params under scrypt backend: no rehash;
        # different params: rehash.
        self.assertFalse(scrypt.needs_rehash(scrypt_phc))
        other_scrypt = PasswordEngine(prefer_argon2=False, scrypt_ln=15)
        self.assertTrue(other_scrypt.needs_rehash(scrypt_phc))

    def test_verify_rejects_garbage(self) -> None:
        engine = PasswordEngine(**CHEAP_ARGON2)
        self.assertFalse(engine.verify("", "pw"))
        self.assertFalse(engine.verify("$unknown$x", "pw"))
        self.assertFalse(engine.verify("$scrypt$mangled", "pw"))
        self.assertFalse(engine.verify("plaintext-not-a-hash", "pw"))

    def test_dummy_verify_always_false(self) -> None:
        engine = PasswordEngine(**CHEAP_ARGON2)
        self.assertFalse(engine.dummy_verify("anything"))
        self.assertFalse(engine.dummy_verify("anything"))  # cached dummy path

    def test_password_work_is_bounded_under_concurrency(self) -> None:
        state_lock = threading.Lock()
        active = 0
        max_active = 0

        class SlowHasher:
            def hash(self, _password: str) -> str:
                nonlocal active, max_active
                with state_lock:
                    active += 1
                    max_active = max(max_active, active)
                try:
                    time.sleep(0.03)
                    return "$argon2id$test"
                finally:
                    with state_lock:
                        active -= 1

        engine = PasswordEngine(**CHEAP_SCRYPT)
        engine._hasher = SlowHasher()
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                engine.hash("bounded-password")
            except BaseException as exc:  # noqa: BLE001 - assert all worker failures
                errors.append(exc)

        with patch.object(
            authn_module,
            "_PASSWORD_WORK_SLOTS",
            threading.BoundedSemaphore(2),
        ):
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(max_active, 2)


# ---------------------------------------------------------------------------
# Token discipline
# ---------------------------------------------------------------------------


class TokenDisciplineTests(unittest.TestCase):
    def test_mint_token_shape(self) -> None:
        for prefix in (ACCESS_TOKEN_PREFIX, REFRESH_TOKEN_PREFIX):
            token = mint_token(prefix)
            self.assertTrue(token.startswith(prefix))
            body = token[len(prefix):]
            self.assertGreaterEqual(len(body), 38)  # 43 urlsafe chars minus stripped -/_
            self.assertLessEqual(len(body), 43)
            self.assertTrue(body.isalnum())

    def test_two_hash_scheme_matches_token_control_index(self) -> None:
        """The cxs_/cxr_ hashes must be byte-identical to the proven
        TokenControlIndex discipline in sharding.py (lookup + salted verify)."""
        with tempfile.TemporaryDirectory() as tmp:
            index = TokenControlIndex(Path(tmp) / "token_index.sqlite")
            token = mint_token(ACCESS_TOKEN_PREFIX)
            self.assertEqual(index._lookup_hash(token), token_lookup_hash(token))
            self.assertEqual(index._token_hash(token, "somesalt"), token_verify_hash(token, "somesalt"))


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


class ServiceTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.deliveries: list[tuple[str, str, str]] = []
        self.tmp = tempfile.TemporaryDirectory(prefix="cortex-authn-test-")
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteControlStore(
            Path(self.tmp.name) / "control" / "accounts.sqlite", clock=self.clock
        )
        self.engine = PasswordEngine(**CHEAP_ARGON2)
        self.service = self.make_service()

    def make_service(self, **overrides) -> AccountsService:
        kwargs = dict(
            password_engine=self.engine,
            clock=self.clock,
            flow_delivery=lambda kind, email, token: self.deliveries.append((kind, email, token)),
        )
        kwargs.update(overrides)
        return AccountsService(self.store, **kwargs)

    def last_delivery(self, kind: str) -> tuple[str, str, str]:
        matches = [item for item in self.deliveries if item[0] == kind]
        self.assertTrue(matches, f"no '{kind}' delivery recorded")
        return matches[-1]

    def signup_and_activate(self, email: str = "alice@example.com", password: str = "pw-123456") -> dict:
        self.service.signup(email, password)
        _kind, _email, token = self.last_delivery("email_verify")
        return self.service.verify_email(token)


class SignupVerifyLoginTests(ServiceTestBase):
    def test_happy_path(self) -> None:
        result = self.service.signup("Alice@Example.com", "pw-123456", display_name="Alice")
        self.assertEqual(result, {"ok": True, "next": "verify_email"})
        account = self.store.get_account_by_email("alice@example.com")
        self.assertIsNotNone(account)
        self.assertEqual(account["status"], "pending_verification")
        self.assertTrue(account["account_id"].startswith("acct_"))
        self.assertTrue(account["user_id"].startswith("u_"))
        self.assertIsNone(account["email_verified_at"])
        # Password identity + credential exist; PHC at rest, never plaintext.
        identity = self.store.get_identity("password", "alice@example.com")
        self.assertEqual(identity["account_id"], account["account_id"])
        credential = self.store.get_password_credential(account["account_id"])
        self.assertTrue(credential["password_hash"].startswith("$argon2id$"))
        self.assertNotIn("pw-123456", credential["password_hash"])

        kind, email, token = self.last_delivery("email_verify")
        self.assertEqual((kind, email), ("email_verify", "alice@example.com"))
        activated = self.service.verify_email(token)
        self.assertEqual(activated["status"], "active")
        self.assertEqual(activated["email_verified_at"], "2026-07-04T12:00:00+00:00")

        session = self.service.login("alice@example.com", "pw-123456")
        self.assertTrue(session["access"].startswith("cxs_"))
        self.assertTrue(session["refresh"].startswith("cxr_"))
        self.assertEqual(session["account"]["account_id"], account["account_id"])
        verified = self.service.verify_session(session["access"])
        self.assertEqual(verified["account"]["account_id"], account["account_id"])
        self.assertEqual(verified["user_id"], account["user_id"])
        self.assertEqual(verified["session_id"], session["session_id"])

    def test_tokens_stored_only_as_two_hash_pairs(self) -> None:
        self.signup_and_activate()
        session = self.service.login("alice@example.com", "pw-123456")
        row = self.store.get_session(session["session_id"])
        for column in ("access_lookup_hash", "access_hash", "refresh_lookup_hash", "refresh_hash"):
            self.assertNotIn(session["access"], str(row[column]))
            self.assertNotIn(session["refresh"], str(row[column]))
        self.assertEqual(row["access_lookup_hash"], token_lookup_hash(session["access"]))
        self.assertEqual(row["refresh_lookup_hash"], token_lookup_hash(session["refresh"]))
        self.assertEqual(
            row["access_hash"], token_verify_hash(session["access"], row["access_salt"])
        )
        self.assertEqual(
            row["refresh_hash"], token_verify_hash(session["refresh"], row["refresh_salt"])
        )

    def test_login_allowed_while_pending_verification(self) -> None:
        # Doc: verification gates connector-credential storage, not basic use.
        self.service.signup("bob@example.com", "pw-123456")
        session = self.service.login("bob@example.com", "pw-123456")
        verified = self.service.verify_session(session["access"])
        self.assertEqual(verified["account"]["status"], "pending_verification")

    def test_signup_input_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.service.signup("not-an-email", "pw-123456")
        with self.assertRaises(ValueError):
            self.service.signup("ok@example.com", "short")

    def test_email_verify_single_use(self) -> None:
        self.service.signup("alice@example.com", "pw-123456")
        _, _, token = self.last_delivery("email_verify")
        self.service.verify_email(token)
        with self.assertRaises(AuthError):
            self.service.verify_email(token)

    def test_email_verify_ttl(self) -> None:
        self.service.signup("alice@example.com", "pw-123456")
        _, _, token = self.last_delivery("email_verify")
        self.clock.advance(31 * 60)  # past the 30-min TTL
        with self.assertRaises(AuthError):
            self.service.verify_email(token)
        account = self.store.get_account_by_email("alice@example.com")
        self.assertEqual(account["status"], "pending_verification")

    def test_email_verify_tampered_secret_leaves_flow_usable(self) -> None:
        self.service.signup("alice@example.com", "pw-123456")
        _, _, token = self.last_delivery("email_verify")
        flow_id, _, secret = token.partition(".")
        with self.assertRaises(AuthError):
            self.service.verify_email(f"{flow_id}.{'x' * len(secret)}")
        with self.assertRaises(AuthError):
            self.service.verify_email("garbage-no-separator")
        # A wrong secret must not consume the flow: the real token still works.
        self.assertEqual(self.service.verify_email(token)["status"], "active")

    def test_rehash_on_login_upgrades_scrypt_and_stale_argon2(self) -> None:
        account = self.signup_and_activate()
        # Simulate a legacy scrypt-fallback credential at rest.
        scrypt_engine = PasswordEngine(**CHEAP_SCRYPT)
        self.store.set_password_credential(
            account["account_id"], scrypt_engine.hash("pw-123456"), "2026-01-01T00:00:00+00:00"
        )
        self.service.login("alice@example.com", "pw-123456")
        upgraded = self.store.get_password_credential(account["account_id"])
        self.assertTrue(upgraded["password_hash"].startswith("$argon2id$"))
        # Now raise the params: next login re-mints again.
        stronger = self.make_service(
            password_engine=PasswordEngine(time_cost=2, memory_cost_kib=16 * 1024, parallelism=1)
        )
        stronger.login("alice@example.com", "pw-123456")
        rehashed = self.store.get_password_credential(account["account_id"])
        self.assertIn("t=2", rehashed["password_hash"])
        self.assertNotEqual(rehashed["password_hash"], upgraded["password_hash"])
        # And logging in still works after both upgrades.
        stronger.login("alice@example.com", "pw-123456")

    def test_deterministic_clock_controls_timestamps(self) -> None:
        self.clock.now = datetime(2026, 7, 4, 15, 30, 0, tzinfo=timezone.utc)
        self.service.signup("clock@example.com", "pw-123456")
        account = self.store.get_account_by_email("clock@example.com")
        self.assertEqual(account["created_at"], "2026-07-04T15:30:00+00:00")


class EnumerationResistanceTests(ServiceTestBase):
    def test_signup_collision_shape_identical_to_success(self) -> None:
        first = self.service.signup("alice@example.com", "pw-123456")
        deliveries_after_first = len(self.deliveries)
        second = self.service.signup("alice@example.com", "another-pw-99")
        self.assertEqual(first, second)  # exact same generic value
        # No second account, no second verification email.
        self.assertEqual(len(self.deliveries), deliveries_after_first)
        accounts = [
            self.store.get_account_by_email("alice@example.com"),
        ]
        self.assertEqual(len([a for a in accounts if a]), 1)
        credential = self.store.get_password_credential(accounts[0]["account_id"])
        self.assertTrue(self.engine.verify(credential["password_hash"], "pw-123456"))
        self.assertFalse(self.engine.verify(credential["password_hash"], "another-pw-99"))

    def test_login_failures_are_uniform(self) -> None:
        self.signup_and_activate()
        with self.assertRaises(AuthError) as unknown_email:
            self.service.login("nobody@example.com", "pw-123456")
        with self.assertRaises(AuthError) as wrong_password:
            self.service.login("alice@example.com", "wrong-password")
        self.assertEqual(str(unknown_email.exception), str(wrong_password.exception))
        self.assertEqual(str(unknown_email.exception), GENERIC_AUTH_FAILURE)
        # Suspended account fails with the same generic error too.
        account = self.store.get_account_by_email("alice@example.com")
        self.store.update_account_fields(account["account_id"], now="2026-07-04T12:01:00+00:00", status="suspended")
        with self.assertRaises(AuthError) as suspended:
            self.service.login("alice@example.com", "pw-123456")
        self.assertEqual(str(suspended.exception), GENERIC_AUTH_FAILURE)

    def test_password_reset_request_generic_for_unknown_email(self) -> None:
        self.signup_and_activate()
        known = self.service.request_password_reset("alice@example.com")
        unknown = self.service.request_password_reset("nobody@example.com")
        malformed = self.service.request_password_reset("not-an-email")
        self.assertEqual(known, unknown)
        self.assertEqual(known, malformed)
        # Only the real account got a delivery.
        resets = [item for item in self.deliveries if item[0] == "password_reset"]
        self.assertEqual(len(resets), 1)
        self.assertEqual(resets[0][1], "alice@example.com")


class RateLimitHookTests(ServiceTestBase):
    def test_limiter_called_and_denial_raises_before_hashing(self) -> None:
        calls: list[tuple[str, str]] = []

        def limiter(action: str, key: str):
            calls.append((action, key))
            return action != "login"

        service = self.make_service(limiter=limiter)
        service.signup("alice@example.com", "pw-123456")
        self.assertIn(("signup", "alice@example.com"), calls)
        with self.assertRaises(RateLimited):
            service.login("Alice@example.com ", "pw-123456")
        self.assertIn(("login", "alice@example.com"), calls)  # normalized key
        service.request_password_reset("alice@example.com")
        self.assertIn(("password_reset", "alice@example.com"), calls)


class SessionLifecycleTests(ServiceTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.account = self.signup_and_activate()

    def test_verify_session_expiry(self) -> None:
        session = self.service.login("alice@example.com", "pw-123456")
        self.clock.advance(59 * 60)
        self.assertIsNotNone(self.service.verify_session(session["access"]))
        self.clock.advance(2 * 60)  # past the 1h access TTL
        self.assertIsNone(self.service.verify_session(session["access"]))
        # Refresh still works inside the idle window and yields a live access.
        renewed = self.service.refresh(session["refresh"])
        self.assertIsNotNone(self.service.verify_session(renewed["access"]))

    def test_verify_session_rejects_garbage_and_unknown(self) -> None:
        self.assertIsNone(self.service.verify_session(""))
        self.assertIsNone(self.service.verify_session("cxa_wrongaudience"))
        self.assertIsNone(self.service.verify_session(mint_token(ACCESS_TOKEN_PREFIX)))

    def test_verify_session_suspended_and_deleted_account(self) -> None:
        session = self.service.login("alice@example.com", "pw-123456")
        self.store.update_account_fields(
            self.account["account_id"], now="2026-07-04T12:01:00+00:00", status="suspended"
        )
        self.assertIsNone(self.service.verify_session(session["access"]))
        with self.assertRaises(AuthError):
            self.service.refresh(session["refresh"])
        self.store.update_account_fields(
            self.account["account_id"], now="2026-07-04T12:02:00+00:00", status="active"
        )
        self.assertIsNotNone(self.service.verify_session(session["access"]))

    def test_logout_revokes_session(self) -> None:
        session = self.service.login("alice@example.com", "pw-123456")
        self.assertTrue(self.service.logout(session["access"]))
        self.assertIsNone(self.service.verify_session(session["access"]))
        with self.assertRaises(AuthError):
            self.service.refresh(session["refresh"])
        with self.assertRaises(AuthError):
            self.service.logout(session["access"])  # already revoked
        row = self.store.get_session(session["session_id"])
        self.assertEqual(row["revoke_reason"], "logout")

    def test_revoke_all(self) -> None:
        s1 = self.service.login("alice@example.com", "pw-123456")
        s2 = self.service.login("alice@example.com", "pw-123456", client="macos")
        count = self.service.revoke_all(self.account["account_id"])
        self.assertEqual(count, 2)
        self.assertIsNone(self.service.verify_session(s1["access"]))
        self.assertIsNone(self.service.verify_session(s2["access"]))

    def test_change_password_revokes_other_sessions(self) -> None:
        keeper = self.service.login("alice@example.com", "pw-123456")
        other = self.service.login("alice@example.com", "pw-123456", client="macos")
        self.service.change_password(
            self.account["account_id"], "pw-123456", "new-pw-7890",
            keep_session_id=keeper["session_id"],
        )
        self.assertIsNotNone(self.service.verify_session(keeper["access"]))
        self.assertIsNone(self.service.verify_session(other["access"]))
        with self.assertRaises(AuthError):
            self.service.login("alice@example.com", "pw-123456")
        self.service.login("alice@example.com", "new-pw-7890")

    def test_change_password_requires_current(self) -> None:
        with self.assertRaises(AuthError):
            self.service.change_password(self.account["account_id"], "wrong", "new-pw-7890")
        with self.assertRaises(AuthError):
            self.service.change_password(self.account["account_id"], None, "new-pw-7890")

    def test_mark_deleted_kills_everything(self) -> None:
        session = self.service.login("alice@example.com", "pw-123456")
        deleted = self.service.mark_deleted(self.account["account_id"])
        self.assertEqual(deleted["status"], "deleted")
        self.assertIsNone(self.service.verify_session(session["access"]))
        with self.assertRaises(AuthError):
            self.service.login("alice@example.com", "pw-123456")

    def test_last_seen_coarsened_to_five_minutes(self) -> None:
        session = self.service.login("alice@example.com", "pw-123456")
        first_seen = self.store.get_session(session["session_id"])["last_seen_at"]
        self.clock.advance(60)
        self.service.verify_session(session["access"])
        self.assertEqual(self.store.get_session(session["session_id"])["last_seen_at"], first_seen)
        self.clock.advance(4 * 60)  # now >= 5 min since mint
        self.service.verify_session(session["access"])
        self.assertNotEqual(self.store.get_session(session["session_id"])["last_seen_at"], first_seen)


class RefreshRotationTests(ServiceTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.account = self.signup_and_activate()

    def test_rotation_issues_new_pair_and_kills_old_tokens(self) -> None:
        pair0 = self.service.login("alice@example.com", "pw-123456")
        pair1 = self.service.refresh(pair0["refresh"])
        self.assertNotEqual(pair0["access"], pair1["access"])
        self.assertNotEqual(pair0["refresh"], pair1["refresh"])
        self.assertEqual(pair0["session_id"], pair1["session_id"])  # same row, rotated
        # Old access + refresh stop working; new pair works.
        self.assertIsNone(self.service.verify_session(pair0["access"]))
        self.assertIsNotNone(self.service.verify_session(pair1["access"]))
        family = self.store.get_session(pair0["session_id"])["refresh_family_id"]
        self.assertTrue(family.startswith("fam_"))

    def test_reuse_of_rotated_token_revokes_whole_family(self) -> None:
        pair0 = self.service.login("alice@example.com", "pw-123456")
        pair1 = self.service.refresh(pair0["refresh"])
        pair2 = self.service.refresh(pair1["refresh"])
        # Attacker replays pair0's refresh token: generic failure...
        with self.assertRaises(AuthError):
            self.service.refresh(pair0["refresh"])
        # ...and the WHOLE family is dead, including the newest legitimate pair.
        row = self.store.get_session(pair2["session_id"])
        self.assertIsNotNone(row["revoked_at"])
        self.assertEqual(row["revoke_reason"], "refresh_reuse")
        self.assertIsNone(self.service.verify_session(pair2["access"]))
        with self.assertRaises(AuthError):
            self.service.refresh(pair2["refresh"])
        reuse_events = self.store.list_audit_events(event="refresh_reuse")
        self.assertEqual(len(reuse_events), 1)

    def test_reuse_of_middle_token_in_chain_revokes_family(self) -> None:
        pair = self.service.login("alice@example.com", "pw-123456")
        chain = [pair]
        for _ in range(3):
            chain.append(self.service.refresh(chain[-1]["refresh"]))
        with self.assertRaises(AuthError):
            self.service.refresh(chain[1]["refresh"])  # middle of the chain
        self.assertIsNone(self.service.verify_session(chain[-1]["access"]))

    def test_unknown_refresh_token_fails_without_collateral(self) -> None:
        live = self.service.login("alice@example.com", "pw-123456")
        with self.assertRaises(AuthError):
            self.service.refresh(mint_token(REFRESH_TOKEN_PREFIX))
        with self.assertRaises(AuthError):
            self.service.refresh("cxs_wrong_prefix")
        # An unknown token must not nuke unrelated live sessions.
        self.assertIsNotNone(self.service.verify_session(live["access"]))

    def test_idle_expiry(self) -> None:
        session = self.service.login("alice@example.com", "pw-123456")
        self.clock.advance(31 * 24 * 60 * 60)  # past 30d idle window
        with self.assertRaises(AuthError):
            self.service.refresh(session["refresh"])
        row = self.store.get_session(session["session_id"])
        self.assertEqual(row["revoke_reason"], "refresh_expired")

    def test_absolute_expiry_caps_sliding_window(self) -> None:
        service = self.make_service(
            access_ttl_seconds=60,
            refresh_idle_ttl_seconds=100,
            refresh_absolute_ttl_seconds=250,
        )
        session = service.login("alice@example.com", "pw-123456")
        # Keep sliding inside the idle window...
        for _ in range(2):
            self.clock.advance(90)
            session = service.refresh(session["refresh"])
        # ...but the absolute 250s ceiling still ends the family.
        self.clock.advance(90)  # t=270 > absolute expiry
        with self.assertRaises(AuthError):
            service.refresh(session["refresh"])


class FlowsPasswordResetTests(ServiceTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.account = self.signup_and_activate()

    def test_reset_confirm_sets_password_and_revokes_sessions(self) -> None:
        session = self.service.login("alice@example.com", "pw-123456")
        self.service.request_password_reset("alice@example.com")
        _, _, token = self.last_delivery("password_reset")
        # Secret is hashed at rest, never plaintext.
        flow_id = token.partition(".")[0]
        flow = self.store.get_flow(flow_id)
        self.assertNotIn(token.partition(".")[2], str(flow["secret_hash"]))
        account = self.service.confirm_password_reset(token, "reset-pw-000")
        self.assertEqual(account["account_id"], self.account["account_id"])
        self.assertIsNone(self.service.verify_session(session["access"]))
        with self.assertRaises(AuthError):
            self.service.login("alice@example.com", "pw-123456")
        self.service.login("alice@example.com", "reset-pw-000")

    def test_reset_token_single_use(self) -> None:
        self.service.request_password_reset("alice@example.com")
        _, _, token = self.last_delivery("password_reset")
        self.service.confirm_password_reset(token, "reset-pw-000")
        with self.assertRaises(AuthError):
            self.service.confirm_password_reset(token, "reset-pw-111")
        self.service.login("alice@example.com", "reset-pw-000")  # first reset stands

    def test_reset_token_ttl(self) -> None:
        self.service.request_password_reset("alice@example.com")
        _, _, token = self.last_delivery("password_reset")
        self.clock.advance(31 * 60)
        with self.assertRaises(AuthError):
            self.service.confirm_password_reset(token, "reset-pw-000")
        self.service.login("alice@example.com", "pw-123456")  # unchanged

    def test_reset_token_not_valid_as_email_verify(self) -> None:
        self.service.request_password_reset("alice@example.com")
        _, _, token = self.last_delivery("password_reset")
        with self.assertRaises(AuthError):
            self.service.verify_email(token)  # kind mismatch


class NeverAutoLinkTests(ServiceTestBase):
    def test_verified_email_match_returns_link_required_not_silent_link(self) -> None:
        account = self.signup_and_activate()
        result = self.service.find_or_challenge_identity(
            "google", "sub-1", email="alice@example.com", email_verified=True
        )
        self.assertEqual(result["action"], "link_required")
        self.assertIn("flow_id", result)
        self.assertIn("challenge", result)
        # NOTHING was linked and no account was created.
        self.assertIsNone(self.store.get_identity("google", "sub-1"))
        self.assertEqual(len(self.store.list_identities(account["account_id"])), 1)  # password only

    def test_unverified_email_match_same_generic_challenge_no_link(self) -> None:
        self.signup_and_activate()
        verified = self.service.find_or_challenge_identity(
            "google", "sub-v", email="alice@example.com", email_verified=True
        )
        unverified = self.service.find_or_challenge_identity(
            "google", "sub-u", email="alice@example.com", email_verified=False
        )
        # Identical shape either way: no verified-status oracle.
        self.assertEqual(set(verified.keys()), set(unverified.keys()))
        self.assertEqual(unverified["action"], "link_required")
        self.assertIsNone(self.store.get_identity("google", "sub-u"))

    def test_unknown_identity_with_verified_email_signs_up_active(self) -> None:
        result = self.service.find_or_challenge_identity(
            "google", "sub-new", email="fresh@example.com", email_verified=True, display_name="Fresh"
        )
        self.assertEqual(result["action"], "signup")
        account = result["account"]
        self.assertEqual(account["status"], "active")
        self.assertEqual(account["primary_email"], "fresh@example.com")
        self.assertIsNotNone(account["email_verified_at"])
        self.assertTrue(account["user_id"].startswith("u_"))
        self.assertIsNotNone(self.store.get_identity("google", "sub-new"))
        # Session minting for OIDC login works through the same discipline.
        session = self.service.mint_session(account["account_id"], client="web")
        self.assertIsNotNone(self.service.verify_session(session["access"]))

    def test_unknown_identity_with_unverified_email_never_trusts_it(self) -> None:
        result = self.service.find_or_challenge_identity(
            "github", "9999", email="sketchy@example.com", email_verified=False
        )
        self.assertEqual(result["action"], "signup")
        account = result["account"]
        self.assertEqual(account["status"], "pending_verification")
        self.assertIsNone(account["primary_email"])  # unverified email never primary
        self.assertIsNone(account["email_verified_at"])
        identity = self.store.get_identity("github", "9999")
        self.assertEqual(identity["email_verified"], 0)

    def test_known_identity_logs_in(self) -> None:
        created = self.service.find_or_challenge_identity(
            "google", "sub-back", email="returning@example.com", email_verified=True
        )
        again = self.service.find_or_challenge_identity(
            "google", "sub-back", email="returning@example.com", email_verified=True
        )
        self.assertEqual(again["action"], "login")
        self.assertEqual(again["account_id"], created["account_id"])
        identity = self.store.get_identity("google", "sub-back")
        self.assertIsNotNone(identity["last_login_at"])

    def test_link_challenge_completes_only_for_the_colliding_account(self) -> None:
        account = self.signup_and_activate()
        other = self.service.find_or_challenge_identity(
            "github", "777", email="other@example.com", email_verified=True
        )["account"]
        challenge = self.service.find_or_challenge_identity(
            "google", "sub-x", email="alice@example.com", email_verified=True
        )
        # Wrong (non-colliding) account: refused, challenge burned fail-closed.
        with self.assertRaises(AuthError):
            self.service.complete_link_challenge(challenge["challenge"], other["account_id"])
        self.assertIsNone(self.store.get_identity("google", "sub-x"))
        # Fresh challenge, correct pre-authenticated account: identity attaches.
        challenge2 = self.service.find_or_challenge_identity(
            "google", "sub-x", email="alice@example.com", email_verified=True
        )
        identity = self.service.complete_link_challenge(challenge2["challenge"], account["account_id"])
        self.assertEqual(identity["account_id"], account["account_id"])
        self.assertEqual(self.store.get_identity("google", "sub-x")["account_id"], account["account_id"])
        # Single-use: the same challenge cannot run twice.
        with self.assertRaises(AuthError):
            self.service.complete_link_challenge(challenge2["challenge"], account["account_id"])
        # And the provider identity now logs straight in.
        result = self.service.find_or_challenge_identity(
            "google", "sub-x", email="alice@example.com", email_verified=True
        )
        self.assertEqual(result["action"], "login")

    def test_link_identity_refuses_duplicate_subject(self) -> None:
        first = self.service.find_or_challenge_identity(
            "google", "sub-dup", email="one@example.com", email_verified=True
        )["account"]
        second = self.service.find_or_challenge_identity(
            "github", "1234", email="two@example.com", email_verified=True
        )["account"]
        with self.assertRaises(AuthError):
            self.service.link_identity(second["account_id"], "google", "sub-dup")
        self.assertEqual(
            self.store.get_identity("google", "sub-dup")["account_id"], first["account_id"]
        )


class UnlinkIdentityTests(ServiceTestBase):
    def test_refuses_removing_last_method_without_password(self) -> None:
        account = self.service.find_or_challenge_identity(
            "google", "sub-only", email="solo@example.com", email_verified=True
        )["account"]
        identity = self.store.get_identity("google", "sub-only")
        with self.assertRaises(AuthError):
            self.service.unlink_identity(account["account_id"], identity["identity_id"])
        self.assertIsNotNone(self.store.get_identity("google", "sub-only"))
        # After setting a first password (no current password exists), the
        # unlink becomes legal.
        self.service.change_password(account["account_id"], None, "first-pw-000")
        self.assertTrue(self.service.unlink_identity(account["account_id"], identity["identity_id"]))
        self.assertIsNone(self.store.get_identity("google", "sub-only"))
        # Password login still works via primary_email + credential.
        self.service.login("solo@example.com", "first-pw-000")

    def test_unlink_one_of_two_identities_allowed(self) -> None:
        account = self.service.find_or_challenge_identity(
            "google", "sub-a", email="multi@example.com", email_verified=True
        )["account"]
        self.service.link_identity(account["account_id"], "github", "55", email_verified=True)
        github_identity = self.store.get_identity("github", "55")
        self.assertTrue(self.service.unlink_identity(account["account_id"], github_identity["identity_id"]))
        self.assertEqual(len(self.store.list_identities(account["account_id"])), 1)

    def test_unlink_unknown_or_foreign_identity_refused(self) -> None:
        account = self.signup_and_activate()
        stranger = self.service.find_or_challenge_identity(
            "google", "sub-s", email="stranger@example.com", email_verified=True
        )["account"]
        foreign = self.store.get_identity("google", "sub-s")
        with self.assertRaises(AuthError):
            self.service.unlink_identity(account["account_id"], foreign["identity_id"])
        with self.assertRaises(AuthError):
            self.service.unlink_identity(account["account_id"], "idn_missing")
        self.assertEqual(self.store.get_identity("google", "sub-s")["account_id"], stranger["account_id"])


class ClaimAccountTests(ServiceTestBase):
    def test_claim_binds_account_to_existing_user_id(self) -> None:
        account = self.service.claim_account_for_user(
            "u_legacy0000000000000000001", "legacy@example.com", "legacy-pw-1"
        )
        self.assertEqual(account["user_id"], "u_legacy0000000000000000001")
        self.assertEqual(account["status"], "pending_verification")
        # Verification email went out; password login works immediately.
        self.assertEqual(self.last_delivery("email_verify")[1], "legacy@example.com")
        session = self.service.login("legacy@example.com", "legacy-pw-1")
        self.assertEqual(session["account"]["user_id"], "u_legacy0000000000000000001")

    def test_claim_guard_refuses_second_account_for_same_user(self) -> None:
        self.service.claim_account_for_user("u_legacy2", "legacy2@example.com", "legacy-pw-2")
        with self.assertRaises(AuthError):
            self.service.claim_account_for_user("u_legacy2", "different@example.com", "legacy-pw-3")

    def test_claim_refuses_taken_email_and_blank_user(self) -> None:
        self.signup_and_activate()
        with self.assertRaises(AuthError):
            self.service.claim_account_for_user("u_legacy3", "alice@example.com", "legacy-pw-3")
        with self.assertRaises(ValueError):
            self.service.claim_account_for_user("", "blank@example.com", "legacy-pw-4")

    def test_claim_with_preverified_email_is_active_with_no_flow(self) -> None:
        before = len(self.deliveries)
        account = self.service.claim_account_for_user(
            "u_legacy4", "trusted@example.com", "legacy-pw-5", email_verified=True
        )
        self.assertEqual(account["status"], "active")
        self.assertIsNotNone(account["email_verified_at"])
        self.assertEqual(len(self.deliveries), before)

    def test_default_user_id_factory_shape(self) -> None:
        user_id = default_user_id_factory()
        self.assertTrue(user_id.startswith("u_"))
        self.assertEqual(len(user_id), 2 + 26)


class ConcurrencySmokeTests(ServiceTestBase):
    def test_concurrent_logins_all_mint_valid_distinct_sessions(self) -> None:
        self.signup_and_activate()
        results: list[dict] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(8)
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            try:
                session = self.service.login("alice@example.com", "pw-123456")
                with lock:
                    results.append(session)
            except Exception as exc:  # pragma: no cover - failure reporting
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(len({session["access"] for session in results}), 8)
        self.assertEqual(len({session["session_id"] for session in results}), 8)
        for session in results:
            self.assertIsNotNone(self.service.verify_session(session["access"]))


if __name__ == "__main__":
    unittest.main()
