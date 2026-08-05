# Cortex Accounts + Per-User Encryption — Architecture (2026-07-04)

Decision record from a research-backed design panel (3 web-research agents, 3 design lenses,
3 judges, 1 synthesis; run wf_77f68e5b-8b5). Base = the "1M-first" design (2 of 3 judges),
with judge-mandated grafts. This is the canonical reference for the accounts/encryption work.

## Research verdicts (cited in the panel run)

### Research recommendation 1

Do not promise either login option now; keep a zero-cost pluggable slot for OpenAI and drop 'Sign in with Claude' entirely. Specifics for Cortex: (1) DROP 'Sign in with Claude' from any roadmap, marketing copy, or UI mock — it does not exist, Anthropic's 2026 actions (Jan server-side OAuth lockdown, Feb credential-use policy, Apr harness cutoff) point away from consumer identity federation, and Cortex's own Anthropic access must stay on Console API keys / WIF regardless. (2) Do NOT promise 'Sign in with ChatGPT' — as of July 2026 it is GA only inside OpenAI's Codex tooling plus an invite/interest pipeline with no public OIDC docs, no self-serve onboarding, no published eligibility, and no guarantee Cortex would be accepted; promising it also invites the wrong user expectation that their ChatGPT subscription pays for Cortex's model calls, which no vendor allows. (3) DESIGN THE PLUGGABLE SLOT the cheap way: make Cortex's auth layer a generic OIDC/OAuth-2.1 provider registry (issuer URL + client credentials + scopes per provider, discovery via .well-known) rather than hard-coded Google/Apple branches. When OpenAI ships 'Sign in with ChatGPT' publicly it will almost certainly be standard OIDC, so adding it becomes a config entry plus a button, not an architecture change; the same slot covers any future Anthropic offering. (4) Optionally register on OpenAI's developer interest form now (free option value, no commitment), and revisit at the next OpenAI DevDay or a GA announcement. Net: pluggable slot yes, promises no, 'Sign in with Claude' out.

---

### Research recommendation 2

For a 2-person team whose brand IS sovereignty, build a thin first-party auth layer in the existing Python backend rather than adopting a managed IdP or a heavyweight OSS IdP — with WorkOS AuthKit as the documented fallback and Ory Kratos as the documented scale-out path. Reasoning: (1) Brand coherence: every managed IdP (Auth0/Clerk/WorkOS/Supabase/Firebase) puts the user identity store on someone else's cloud, which directly contradicts 'local-first personal memory' and blocks users from self-hosting Cortex end-to-end; a hand-built layer makes 'your identity never leaves your machine/our infra' a marketable feature. (2) Scope honesty: Cortex has exactly one first-party client, so it does not need an OIDC provider, SAML, or multi-tenancy — the 'never roll your own auth' warnings target people reimplementing OAuth/OIDC flows, not first-party email+password+session auth, which is a small, OWASP-documented surface. (3) Cost: the build path is $0/MAU forever; if you later want to buy, WorkOS is the only rational managed choice (free to 1M MAU, then $2,500/mo per additional million — vs Auth0 at ~$700/mo for just 10k and $33k+/mo territory at scale, Clerk ~$19k/mo at 1M, Firebase ~$4.4k, Supabase ~$3k). (4) Ops: Keycloak's Java/Infinispan clustering is disproportionate for 2 people; if you ever need standards-compliant OIDC/SSO or B2B features, self-hosted Ory Kratos (Go + Postgres only, headless so your brand owns every screen, proven at Fandom scale) is the sovereignty-compatible upgrade, with Zitadel second. Concrete build spec: argon2id via argon2-cffi (m=19–64 MiB, t=2, p=1 per OWASP) — or zero-dependency hashlib.scrypt at N=2^17, r=8, p=1 if you refuse the dep; opaque server-side session tokens (>=64-bit CSPRNG entropy, __Host- Secure/HttpOnly/SameSite cookies for web, opaque access+refresh with rotation and family-revocation-on-reuse for the macOS app) stored in Postgres — no JWTs, so revocation at 1M users is a single DELETE; mandatory email verification through a transactional-email provider with a disposable-domain blocklist and per-IP/ASN/velocity rate limits from day one; passkeys (via the maintained 'webauthn' Python package) added as an optional second method once email+password is solid — 2026 data says infrastructure is ready but only ~15–20% of users opt in, so never passkey-only. Budget roughly 2–4 focused weeks to build and property-test this; that is the price of keeping the sovereignty story true at both 10k and 1M MAU.

---

### Research recommendation 3

RECOMMENDED ARCHITECTURE FOR CORTEX HOSTED PLANE — server-side envelope encryption with per-user DEKs, E2E-shaped seams, crypto-shredding as the GDPR deletion primitive.

(1) KEY HIERARCHY. One root KEK: in a cloud KMS if the hosted plane runs on AWS/GCP (bind unwraps with encryption-context/AAD = user_id); if no KMS yet, a 32-byte KEK in the deployment secret manager/env — acceptable interim, documented upgrade path to KMS. Per user: one random 32-byte master DEK generated at signup, wrapped by the KEK (pyca `cryptography` AESGCM with AAD=user_id||key_version, or aes_key_wrap; AESSIV is a good misuse-resistant wrap if OpenSSL allows). Store wrapped DEKs in a small key table (user_id, wrapped_dek, kek_version, dek_version, created_at). Derive purpose-scoped subkeys via HKDF(master_dek, info="credentials"|"content"|"vault"|"backup") so domains are cryptographically separated but erasure/rotation stays one key per user. Cipher for bulk data: AES-256-GCM (AES-NI-fast, in the sanctioned `cryptography` package; note XChaCha20-Poly1305 — used by Standard Notes/Ente — is NOT in pyca, only PyNaCl). Enforce nonce discipline: random 96-bit nonces with a per-key message budget well under 2^32, bumping dek_version when exceeded; prepend (dek_version, nonce) to every ciphertext blob.

(2) ORDER OF OPERATIONS — worst offender first. Phase 1: connector credentials (backend/app/models.py token/client_secret fields, backend/source_accounts/): encrypt every credential blob with the user's "credentials" subkey; the existing credential_ref indirection makes this a contained change. Phase 2: per-user vault dirs and backups: encrypt file contents with the "vault"/"backup" subkeys (streaming AES-GCM in chunks with a chunk counter in AAD). Phase 3: shard content: application-level encryption of user-content columns (bodies, titles, memories, extracted text, and stored embedding vectors — embedding-inversion attacks make vectors sensitive) inside the 16 bucket-shared SQLite shards, since SQLCipher is one-key-per-file and cannot do per-user within a shared shard. Keep routing/ID/timestamp columns plaintext for query planning.

(3) THE SEARCH-INDEX COMPROMISE, STATED PLAINLY. FTS5 and any server-side vector index must operate on plaintext, so index structures on disk will contain derived plaintext tokens/vectors. Handle this with layering: canonical content is per-user encrypted (crypto-shreddable, including in backups); the derived FTS/vector index sits on a volume-encrypted disk (protects against media theft) and index rows are conventionally deleted on erasure — they are re-derivable data, not the record of truth. Do not pretend searchable encryption or homomorphic vector search closes this gap; at Cortex's scale they are impractical and leak patterns anyway.

(4) ROTATION. KEK rotation = re-wrap the wrapped-DEK table under the new KEK version (cheap, minutes) — do NOT rely on KMS auto-rotation, which only versions the key and never re-wraps existing DEKs. Schedule KEK re-wrap yearly-ish per NIST SP 800-57 cryptoperiods; DEK rotation is lazy (new dek_version for new writes, background re-encrypt) and rarely needed because DEKs are per-user.

(5) GDPR DELETION = CRYPTO-SHREDDING. Erasure request → destroy the user's wrapped-DEK row (and KMS grant if per-user KMS keys are later adopted) → shard rows, vault files, and every historical backup become permanently unreadable in one step; then conventionally delete plaintext-derived index rows and write a tombstone (backend/deletion_tombstones already exists).

(6) E2E TRADEOFF — HONEST CALL. Full client-held keys (Standard Notes/Bitwarden/Ente/Anytype model) would make Cortex zero-knowledge but kills the product core: server-side embedding generation, semantic retrieval, MCP context assembly, and any LLM processing of user content become impossible; search moves entirely client-side. Cortex IS a server-side retrieval/context product, so choose server-side keys now and say so honestly in the privacy posture ("encrypted at rest with per-user keys; Cortex servers can read your data to serve you"). Preserve an E2E seam for later: because the hierarchy already wraps a per-user DEK, a future "zero-access mode" tier can let the user's client-derived key wrap their DEK instead of the server KEK (Bitwarden's protected-symmetric-key topology) — costing background sync/indexing while the user is offline — without re-architecting storage.

---

# FINAL ARCHITECTURE + PHASE-1 BUILD PLAN

All line anchors verified against the tree (sharding.py:290/534/543/546/746/757/819/884/901; main.py:209/323/342/365/1852; vault.py:306/344; config.py:30/41; backend/app/ratelimit.py exists; backend/app/crypto.py and accounts.py do not yet exist — they are new modules). Synthesis below: base architecture = the "1M-first" design (chosen by 2 of 3 judges, scores 9/9/8), with three judge-mandated grafts: (a) never-silent-auto-link from the sovereignty design, (b) the parallel key-wrap topology (`user_key_wraps`) + dynamic `oidc_providers` table, (c) centralized CSRF in the session dependency + 5-minute-coarsened `last_seen`.

# CORTEX ACCOUNTS + PER-USER ENCRYPTION — FINAL ARCHITECTURE & PHASE-1 BUILD PLAN

## 0. Decision summary

**Build first-party auth (no managed IdP), 1M-first shape run at 10k as a config-downgraded strict subset.** Two things downgrade by configuration, never by code: the control store (Postgres → the existing control-plane SQLite) and the KEK holder (cloud KMS → a local 32-byte secret file/env). Everything else — schema, token formats, endpoint surface, ciphertext format, key hierarchy, crypto-shred semantics — is identical at 10k and 1M. `user_id` stays the immutable, non-PII shard-routing key (`ShardRouter` hashes it into 16 buckets); identity gets its own `account_id` that points at it. The local-first macOS app and stdlib-only `standalone_server.py` need **no account, ever**, and import none of this.

Rationale (research-grounded): every managed IdP puts the identity store on someone else's cloud, contradicting the sovereignty brand and blocking end-to-end self-hosting; Cortex has exactly one first-party client, so it needs first-party session auth (small, OWASP-documented surface), not an OIDC *provider*. Cost: $0/MAU forever vs Auth0 ~$700/mo at just 10k. Documented fallbacks, never defaults: WorkOS AuthKit (free to 1M MAU, $2,500/mo per additional million) if maintenance proves too costly; self-hosted Ory Kratos (Go+Postgres, Fandom-scale) if requirements grow to SSO/SAML.

---

## 1. Auth architecture + provider mix

### Components (hosted FastAPI plane only)
- `backend/app/accounts.py` — `AccountsService` over a `ControlStore` interface (SQLiteControlStore now at `<shard_root>/control/accounts.sqlite`, sibling of `token_index.sqlite`; `PostgresControlStore` later via the already-reserved `hosted_database_url`, config.py:30). Schema written in the portable TEXT/INTEGER/BLOB SQL subset both engines accept. Contract tests written now that the future Postgres impl must pass.
- `backend/app/authn.py` — argon2id via `argon2-cffi` (m=64 MiB, t=3, p=1; OWASP floor m=19 MiB t=2 p=1), PHC-format self-describing hashes, rehash-on-login when params rise. Documented zero-dep fallback: `hashlib.scrypt` N=2^17, r=8, p=1.
- `backend/app/oidc_registry.py` — generic OIDC/OAuth2.1 provider registry: per-provider issuer URL, client_id, client_secret (encrypted at rest under the service KEK), scopes; discovery via `/.well-known/openid-configuration`; PKCE S256 always; nonce always; full id_token verification (iss/aud/exp/nonce/JWKS signature). GitHub gets a dedicated non-OIDC adapter (its own state+PKCE handling; subject = immutable numeric id from `/user`; email from `/user/emails` filtered to verified+primary — unverified GitHub emails never create or link accounts).
- `backend/app/keyring.py` — envelope encryption (section 4).
- `EmailSender` interface — console/log sink for dev and self-hosters (`CORTEX_AUTH_EMAIL_MODE=log`), SMTP relay config for prod; never an email-vendor SDK. Disposable-domain blocklist + per-IP/per-identifier velocity limits reusing `backend/app/ratelimit.py`, applied **before** any argon2 work, plus a global concurrency semaphore on hash operations (login-flood DoS defense).

Deps added to `backend/requirements.txt` ONLY (hosted plane): `argon2-cffi>=25`, `cryptography>=42`, `httpx`. Never in the stdlib runtime requirements; `standalone_server.py` under `python3 -S` never imports any new module.

### Provider mix (ship order)
1. **Email + password** — primary, always-on, works fully self-hosted. Mandatory email verification before connector-credential storage/sync (read/write of own memories allowed pre-verification).
2. **Google** — plain OIDC registry entry (issuer `https://accounts.google.com`, scopes `openid email profile`); trust email only when `email_verified=true` in the id_token. Only id_token claims consumed — a Google outage breaks only the Google button, never existing sessions or password login.
3. **GitHub** — the dedicated adapter above.
4. **Passkeys** — phase 2+, optional second method via the maintained `webauthn` package; never passkey-only (2026 adoption ~15–20% of users on supporting platforms).

### Honest verdict on AI-vendor login (per research, non-negotiable)
- **"Sign in with Claude": DROPPED entirely** from roadmap, marketing, and UI. It does not exist as of July 2026 — Anthropic's docs list only API keys and workload identity federation, and its 2026 posture (Jan server-side consumer-OAuth lockdown, Feb credential-use policy restricting consumer OAuth to Claude Code/claude.ai, Apr third-party harness enforcement) points away from consumer identity federation. Cortex's own Anthropic access stays on Console API keys regardless.
- **"Sign in with ChatGPT": NOT promised, NOT built.** As of July 2026 it is GA only inside OpenAI's own Codex surfaces; no public OIDC docs, no self-serve onboarding, no eligibility guarantee. Promising it also seeds the false expectation that a user's ChatGPT subscription pays for Cortex's model calls — no vendor allows this (identity-only even where it exists).
- **The pluggable slot ships anyway, at zero cost:** a disabled `'openai'` row in the `oidc_providers` table. `GET /v1/auth/providers` returns enabled rows so clients render login buttons dynamically — when/if OpenAI ships public standard OIDC, launch = flip one DB row + a button, zero client redeploy, zero architecture change. Optionally register on OpenAI's developer interest form now (free option value). No AI-vendor button appears in any UI mock until GA.

---

## 2. Control-plane schema (exact SQL, portable subset)

Existing `users` + `scoped_token_index` tables in `token_index.sqlite` are UNCHANGED except one additive column (migration section 6). All new tables in `<shard_root>/control/accounts.sqlite`, created via the same idempotent `ALTER`/`CREATE IF NOT EXISTS`-in-`_connect` pattern proven at sharding.py:522–541.

```sql
CREATE TABLE accounts (
  account_id        TEXT PRIMARY KEY,            -- 'acct_' + hex26
  user_id           TEXT NOT NULL UNIQUE,        -- FK -> users.user_id: IMMUTABLE shard-routing key ('u_'+token_hex(13)); never derived from email
  primary_email     TEXT,                        -- normalized lowercase; NULL for unclaimed legacy users
  email_verified_at TEXT,
  display_name      TEXT NOT NULL DEFAULT '',
  status            TEXT NOT NULL DEFAULT 'pending_verification'
                    CHECK (status IN ('pending_verification','active','suspended','deleted')),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_accounts_email ON accounts(primary_email) WHERE primary_email IS NOT NULL;

CREATE TABLE account_identities (
  identity_id       TEXT PRIMARY KEY,            -- 'idn_' + hex
  account_id        TEXT NOT NULL REFERENCES accounts(account_id),
  provider          TEXT NOT NULL,               -- 'password' | 'google' | 'github' | 'openai' (reserved)
  provider_subject  TEXT NOT NULL,               -- OIDC sub / GitHub numeric id / normalized email for password. NEVER email for OAuth.
  email             TEXT,
  email_verified    INTEGER NOT NULL DEFAULT 0,
  profile_json      TEXT NOT NULL DEFAULT '{}',
  created_at        TEXT NOT NULL,
  last_login_at     TEXT,
  UNIQUE (provider, provider_subject)
);
CREATE INDEX idx_identities_account ON account_identities(account_id);

CREATE TABLE account_password_credentials (
  account_id        TEXT PRIMARY KEY REFERENCES accounts(account_id),
  password_hash     TEXT NOT NULL,               -- PHC format ($argon2id$...), params self-describing
  updated_at        TEXT NOT NULL
);

CREATE TABLE auth_sessions (
  session_id             TEXT PRIMARY KEY,       -- 'sess_' + hex
  account_id             TEXT NOT NULL,
  user_id                TEXT NOT NULL,          -- denormalized: one-lookup request auth
  client                 TEXT NOT NULL,          -- 'web' | 'macos' | 'cli'
  access_lookup_hash     TEXT NOT NULL,          -- sha256('cxlookup:'+token), deterministic O(1) index key
  access_salt            TEXT NOT NULL,
  access_hash            TEXT NOT NULL,          -- sha256(salt:token), compare_digest verify
  access_expires_at      TEXT NOT NULL,
  refresh_family_id      TEXT NOT NULL,
  refresh_lookup_hash    TEXT NOT NULL,
  refresh_salt           TEXT NOT NULL,
  refresh_hash           TEXT NOT NULL,
  refresh_expires_at     TEXT NOT NULL,          -- absolute: 90d from login
  refresh_idle_expires_at TEXT NOT NULL,         -- 30d sliding
  created_ip             TEXT,
  user_agent             TEXT,
  created_at             TEXT NOT NULL,
  last_seen_at           TEXT,                   -- written at most once per 5 min (graft: single-writer protection)
  revoked_at             TEXT,
  revoke_reason          TEXT
);
CREATE INDEX idx_sessions_access  ON auth_sessions(access_lookup_hash)  WHERE revoked_at IS NULL;
CREATE INDEX idx_sessions_refresh ON auth_sessions(refresh_lookup_hash) WHERE revoked_at IS NULL;
CREATE INDEX idx_sessions_account ON auth_sessions(account_id, revoked_at);

CREATE TABLE refresh_token_history (              -- powers reuse-detection family revocation
  lookup_hash  TEXT PRIMARY KEY,
  family_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  rotated_at   TEXT NOT NULL
);
CREATE INDEX idx_rth_family ON refresh_token_history(family_id);

CREATE TABLE auth_flows (                         -- single-use, TTL-swept; OAuth state, app-login polls, emailed tokens, claim codes, link challenges
  flow_id      TEXT PRIMARY KEY,                  -- = OAuth state where applicable
  kind         TEXT NOT NULL,                     -- 'oidc' | 'app_login' | 'email_verify' | 'password_reset' | 'account_claim' | 'link_challenge'
  provider     TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',        -- pkce_verifier / nonce / redirect / account_id / pending identity claims
  secret_hash  TEXT,                              -- salted hash of poll secrets & emailed single-use tokens (never plaintext at rest)
  created_at   TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  consumed_at  TEXT
);

CREATE TABLE user_keys (
  user_id      TEXT PRIMARY KEY,                  -- FK -> users.user_id
  dek_version  INTEGER NOT NULL DEFAULT 1,
  write_count  INTEGER NOT NULL DEFAULT 0,        -- enforced nonce budget, bump dek_version at 2^28
  created_at   TEXT NOT NULL,
  rotated_at   TEXT,
  destroyed_at TEXT                                -- crypto-shred tombstone
);

CREATE TABLE user_key_wraps (                     -- GRAFT (judge 2): parallel wraps of the SAME master DEK
  user_id      TEXT NOT NULL,
  wrap_type    TEXT NOT NULL,                     -- 'service' | 'passphrase' | 'recovery'
  kek_id       TEXT NOT NULL,                     -- 'local:v1' | 'kms:<arn>#<ver>' | 'user'
  wrapped_dek  BLOB NOT NULL,
  wrap_alg     TEXT NOT NULL DEFAULT 'A256GCM',
  kdf_params_json TEXT,                           -- argon2id params for passphrase wraps
  created_at   TEXT NOT NULL,
  PRIMARY KEY (user_id, wrap_type)
);

CREATE TABLE oidc_providers (                     -- GRAFT (judge 2): THE pluggable AI-vendor slot; seedable from env for self-hosters
  provider          TEXT PRIMARY KEY,             -- 'google' | 'github' | 'openai' (enabled=0 today)
  kind              TEXT NOT NULL,                -- 'oidc' | 'github'
  issuer            TEXT,
  client_id         TEXT,
  client_secret_enc BLOB,                         -- encrypted under service KEK, never plaintext
  scopes            TEXT,
  display_name      TEXT,
  enabled           INTEGER NOT NULL DEFAULT 0,
  button_order      INTEGER NOT NULL DEFAULT 100,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE TABLE audit_auth_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id   TEXT,
  event        TEXT NOT NULL,                     -- login_ok/login_fail/refresh_reuse/link/unlink/token_minted/session_revoked/shred/...
  identity_id  TEXT,
  ip           TEXT,
  user_agent   TEXT,
  detail_json  TEXT NOT NULL DEFAULT '{}',
  created_at   TEXT NOT NULL
);
```

Additive migration in `token_index.sqlite` (same try/except `OperationalError` pattern as sharding.py:534):
```sql
ALTER TABLE scoped_token_index ADD COLUMN account_id TEXT;  -- NULL for legacy operator-minted rows
```

**Linking rules (graft, judge 1 — never-silent-auto-link):** login resolves `(provider, provider_subject)` → account. If no identity match, **never auto-link at unauthenticated callback time** — even on a verified-email match with a verified account, return a generic `link_required` challenge (`auth_flows kind='link_challenge'` holding the pending provider claims); the user must authenticate with the existing method, then the identity attaches. Signup collision on `primary_email` returns the **same generic challenge flow** (kills the email-enumeration oracle). Unlinking the last identity is refused unless `account_password_credentials` exists. Both this and the GitHub verified-primary-email requirement are pinned by regression tests (release gate).

---

## 3. Endpoints, flows, and mapping onto existing machinery

### Token taxonomy (prefix dispatch; audiences never blur)
| Token | Prefix | Audience | Storage/verify |
|---|---|---|---|
| Interactive session access | `cxs_` + `token_urlsafe(32)` | humans: dashboard + account surface + token self-service | `auth_sessions`, two-hash scheme identical to `TokenControlIndex._token_hash`/`_lookup_hash` (sharding.py:543–551) |
| Refresh | `cxr_` + `token_urlsafe(32)` | macOS app / web renew | same row; rotated EVERY use; reuse ⇒ revoke whole `refresh_family_id` |
| API personal access token | `cxa_` (existing, unchanged) | data plane, scoped via `_required_api_scope` (main.py:209) | `scoped_token_index`, untouched |
| MCP token | `cxm_` (existing, unchanged) | MCP only | untouched; `mcp_auth()` (main.py:342) stays `cxm_`-only — sessions can never drive MCP |
| Global `CORTEX_API_KEY` | — | operator/admin only | `admin_auth()` (main.py:365) unchanged; still cannot impersonate sharded users |

No JWTs anywhere (OWASP: session state belongs server-side; revocation at 1M = one indexed UPDATE). Lifetimes: access 30 min web / 60 min app; refresh 30d idle / 90d absolute. Session regeneration on privilege change; password change revokes all other sessions. Step-up re-auth (password or recovery code) required for account deletion/shred and destructive-scope token minting.

### `auth()` wiring (main.py:323)
One additive branch: after the global-key check, before the scoped-token path, `cxs_` prefix dispatches to `AccountsService.authenticate_session()` → returns the account's `user_id` with full user scopes but **never admin**. Session auth joins `accounts.status` AND `users.status` so suspension is enforced on every request. **CSRF enforced centrally in this dependency (graft, judge 3):** cookie-authenticated state-changing requests require the custom `X-Cortex-Client` header (plus `__Host-cortex_session` cookie: Secure, HttpOnly, SameSite=Lax) — no new endpoint can ship without the check. `last_seen_at` written at most once per 5 min per session.

### Endpoints (new `/v1/auth` router included from main.py)
- `POST /v1/auth/signup` `{email, password}` → account `pending_verification` + verification mail (rate-limited + blocklist BEFORE hashing).
- `POST /v1/auth/verify-email` `{token}` → activate; **on activation, self-serve provisioning** calls the same internals as `StoreRegistry.provision_user` (sharding.py:819): `register_user` + lazy shard materialization + DEK creation — but does **not** auto-mint `cxa_`/`cxm_`.
- `POST /v1/auth/login` `{email, password}` → `{cxs_, cxr_, account}`; uniform generic 401 on any failure (no enumeration/timing oracle).
- `POST /v1/auth/refresh` (rotate; reuse ⇒ family revocation), `POST /v1/auth/logout` (revoke session), `GET /v1/auth/session` (whoami).
- `POST /v1/auth/password/reset/request` + `/confirm` — single-use hashed tokens in `auth_flows`, 30-min TTL.
- `GET /v1/auth/providers` — enabled `oidc_providers` rows for dynamic login buttons (the AI-vendor slot's UI half).
- `GET /v1/auth/oauth/{provider}/start` → authorize URL; state+nonce+PKCE verifier persisted in `auth_flows` (dedicated table, NOT the connector-flow `remember_oauth_pending` machinery — callback is unauthenticated).
- `GET /v1/auth/oauth/{provider}/callback` → code exchange + full id_token verification → identity match ⇒ login; no match ⇒ `link_required` challenge or fresh signup per section 2 rules.
- `POST /v1/auth/oauth/{provider}/link` (session-authed) / `DELETE .../unlink` (refuse removing last method without a password).
- `POST /v1/auth/app/start` → `{flow_id, browser_url, poll_secret}`; macOS app opens `browser_url` in `ASWebAuthenticationSession`; app polls `POST /v1/auth/app/poll {flow_id, poll_secret}` until it gets the token pair (poll_secret salted-hashed in `auth_flows`; **no token ever rides a redirect URL**). Refresh token → Keychain. Email+password login from the app is a plain POST, no browser.
- `GET/POST/DELETE /v1/auth/tokens` — self-serve mint/list/revoke of the EXISTING `cxa_`/`cxm_` tokens via `create_api_token`/`create_mcp_token` (sharding.py:746/757), backfilling `scoped_token_index.account_id`. Sessions become the factory for PATs; the operator `/v1/admin/users` surface (main.py:1852) remains for support.
- `DELETE /v1/auth/account` — step-up re-auth required; runs crypto-shred (section 4) then the existing conventional deletion flow.

---

## 4. Per-user encryption

**Server-side envelope encryption with per-user DEKs — deliberately NOT client-held E2E**, because the hosted product core (server-side embeddings, semantic retrieval, MCP context assembly) requires plaintext compute; E2E note apps solve this with client-side-only search, which Cortex cannot. Privacy posture copy, verbatim honest: *"encrypted at rest with a per-user key you can own; Cortex servers can read your data to serve you."* Never say "zero-knowledge" or "E2E." Scope: hosted plane only — the local vault stays plaintext Markdown by design (docs/NATIVE_VAULT_PLAN.md, file-over-app); local backup/export encryption is Swift CryptoKit app-side, a separate work item.

### Key hierarchy (`backend/app/keyring.py`)
- **Level 0 — root KEK behind a `KeyProvider` interface**, exactly two impls now: `LocalKekProvider` (32-byte KEK from `CORTEX_KEK` base64 env/secret-file, versioned `local:vN`; never on the data disk, never in any vault.py backup; operator keeps ≥2 offline copies — KEK escrow runbook is a launch gate) and `KmsKekProvider` (AWS/GCP KMS with `EncryptionContext={'user_id': uid, 'purpose': 'cortex-dek'}` binding unwraps to the tenant). KMS is the recommended swap before 100k, but the interface keeps file-KEK a permanently supported self-hosting option — no third-party service can lock users out.
- **Level 1 — per-user master DEK**: 32 random bytes at account activation (lazily for legacy users), wrapped `AESGCM(KEK).encrypt(nonce, dek, aad=user_id||kek_id||dek_version)` — a wrapped DEK replayed into another user's row fails to unwrap. Stored in `user_key_wraps` with `wrap_type='service'`. **Graft (judge 2): parallel wraps of the same DEK** — `'passphrase'` (argon2id-derived, params in `kdf_params_json`) and `'recovery'` (client-generated recovery kit, shown once) are additive rows. "Download your key" exports the user-held wrap + the published wire-format spec; a small open-source offline `cortex-decrypt` tool makes exported encrypted backups readable even if Cortex dies. A future paid **zero-access mode** = DELETE the `'service'` wrap row (Bitwarden protected-symmetric-key topology) — a row operation, not a refactor; honest cost: server-side indexing pauses while the user is offline.
- **Level 2 — purpose subkeys, derived never stored**: `HKDF-SHA256(master_dek, info='cortex:v1:'+purpose)` for `{credentials, content, vault, backup, search-reserved}`.
- **Cipher**: AES-256-GCM from pyca `cryptography` (AES-NI-fast; XChaCha20-Poly1305 is NOT in pyca — no second crypto dep). Random 96-bit nonces with an **enforced** per-(user, dek_version) write budget of 2^28 (tracked in `user_keys.write_count`; exceeding bumps `dek_version`, old versions retained for reads). Unwrapped DEKs live only in a size+TTL-bounded in-process LRU (5-min TTL), purged on shred/suspend, never logged.
- **Ciphertext format, self-describing and storage-agnostic**: `CXE1 || kek_id_ref || dek_version || nonce(12) || ct+tag`, with `AAD = user_id || purpose || dek_version` (blob copied to another user's row or wrong domain fails authentication); JSON-b64 framing for JSON stores, length-prefixed binary for columns/files. Survives every storage migration byte-identical.
- **Rotation**: KEK rotation = incremental, resumable re-wrap pass over `user_key_wraps` (per-row `kek_id` makes mixed states valid mid-flight; minutes even at 1M) — explicitly NOT relying on KMS auto-rotation, which only versions future wraps. Cadence ~yearly per NIST SP 800-57. DEK rotation is lazy via `dek_version`.

### What gets encrypted — worst offender first
- **Phase 1 (this plan): connector credentials.** `write_source_credential`/`read_source_credential` (vault.py:306/344) today persist OAuth tokens/client_secrets (up to 4000-char fields in models.py) as **plaintext JSON** in `credentials.json` — in bucket mode a *shared* file holding many tenants' tokens, a live cross-tenant exposure. Fix: an optional `CipherService` injected into `CortexVault` (constructor arg, default `None`; `StoreRegistry.store_for_user` injects it when `shard_mode != 'local'` and a KEK is configured) encrypts each credential payload with the user's `credentials` subkey; the `credential_ref` indirection in storage.py means zero call-site changes; local/standalone path injects nothing, vault.py stays stdlib-clean. Also encrypt `oidc_providers.client_secret_enc` under the service KEK.
- **Phase 2 (deferred): hosted vault files + backups** — chunked streaming AES-GCM, chunk index in AAD.
- **Phase 3 (deferred): shard content columns** in the 16 bucket-shared SQLite shards (bodies, titles, extracted text, AND embedding vectors — inversion attacks make vectors sensitive); routing ids/timestamps/status stay plaintext for query planning. **SQLCipher explicitly rejected**: one-key-per-file means a bucket shard would share one key across tenants — no per-user shredding, no isolation.

### Stated compromise + deletion
FTS5/sqlite-vec must index plaintext: derived index structures stay plaintext on a volume-encrypted disk, are re-derivable (not record of truth), and are conventionally deleted on erasure — no SSE/homomorphic hand-waving. **Deletion = crypto-shredding** (GDPR Art. 17, EDPB-acknowledged): erasure first zeroizes `user_key_wraps` rows + sets `user_keys.destroyed_at` + audit event → shard ciphertext, credential blobs, and every historical backup become permanently unreadable in one step (this also fixes the current bucket-mode gap where `delete_user_data(include_backups=True)` is refused) → then the existing `delete_user_data` + `deletion_tombstones` flow removes plaintext-derived artifacts. Deletion messaging tracks actual coverage: until phases 2–3 ship, "shredded" applies to credentials only — the claim is scoped, never aspirational.

---

## 5. 10k → 1M swap table

| Concern | 10k (now) | 1M (later) | What changes |
|---|---|---|---|
| Control store | SQLiteControlStore (`accounts.sqlite` + `token_index.sqlite`) | PostgresControlStore via `hosted_database_url` (config.py:30) | Driver behind the day-one `ControlStore` interface; contract tests already pass; offline row-copy of O(users) tables, minutes. Trigger: auth write contention / multi-instance |
| KEK holder | `LocalKekProvider` (secret file/env) | `KmsKekProvider` (EncryptionContext tenant binding); file remains supported for self-hosters | Config flip + incremental per-row re-wrap job; zero data-plane bytes touched. Gate the 100k milestone on it |
| Data plane | sharded_sqlite tier (16 buckets, sanctioned, 54 req/s 0-error load test) | `hosted_runtime_tier='postgres'` (config.py:41, already reserved) | Ciphertext blobs copy byte-identical — encryption is storage-agnostic |
| Session verify | one PK-indexed SQLite read | one PK-indexed Postgres read; optional Redis read-through cache (short TTL, delete-on-revoke) only if measured | Additive, not required |
| Rate limiting | in-proc `ratelimit.py` | Redis or Postgres sliding window shared across instances | Same call sites |
| Email | SMTP relay / log mode | higher-volume relay | Interface unchanged |
| **Never changes** | — | — | Schema; `cxs_/cxr_/cxa_/cxm_` formats + lookup-hash verification; `/v1/auth/*` surface; `oidc_providers` registry; CXE1 format + AAD binding + HKDF domains + `user_key_wraps` topology + user-key export format (the sovereignty contract — eternal); crypto-shred; prefix dispatch; `user_id` as immutable routing key (load-bearing invariant: any future account-merge is an explicit re-encrypt+move migration, never an `UPDATE` of user_id — documented and asserted in code) |

Cost honesty at 1M: this path is $0/MAU; fallbacks priced (WorkOS free→1M then $2,500/mo/M; vs Auth0 $33k+/mo territory, Clerk ~$19k/mo, Firebase ~$4.4k/mo, Supabase ~$3k/mo).

---

## 6. Phase-1 implementation steps (ordered, real paths)

1. **Deps**: add `argon2-cffi>=25`, `cryptography>=42`, `httpx` to `backend/requirements.txt` only. Verify stdlib runtime untouched (`python3 -S` boot of `standalone_server.py`).
2. **`backend/app/keyring.py`** (new): `KeyProvider` + `LocalKekProvider`; DEK mint/wrap/unwrap with AAD binding; HKDF purpose subkeys; `encrypt_blob`/`decrypt_blob` in CXE1 format with legacy-plaintext sniff; enforced nonce budget + `dek_version` bump; bounded DEK LRU; `crypto_shred(user_id)`. `user_keys`/`user_key_wraps` tables.
3. **Credential encryption** (worst offender): optional `CipherService` on `CortexVault` (constructor param, default None), applied in `write_source_credential`/`read_source_credential` (vault.py:306/344) with lazy read-migrate-write-back; hosted `StoreRegistry.store_for_user` injects per-user cipher; `POST /v1/admin/encryption/backfill` sweep + remaining-plaintext gauge in the readiness contract; `CORTEX_REQUIRE_ENCRYPTED_CREDENTIALS=1` flip once the gauge hits zero (bounded downgrade window, alert on post-flip plaintext reads).
4. **`backend/app/accounts.py` + `backend/app/authn.py`** (new): `ControlStore` interface + SQLite impl (+contract test suite the future Postgres impl must pass) with the full section-2 schema; argon2id PHC hashing + rehash-on-login; signup/verify/login/refresh-with-rotation-and-family-revocation/logout/reset; `EmailSender` (log sink default); disposable-domain blocklist + `ratelimit.py` limits + argon2 concurrency semaphore.
5. **`backend/app/oidc_registry.py`** (new): discovery+PKCE+nonce OIDC client with full id_token verification; Google entry; GitHub adapter (own state+PKCE, verified-primary email); disabled `'openai'` row; `oidc_providers` table seeded from env (`CORTEX_OIDC_GOOGLE_CLIENT_ID/SECRET`, `CORTEX_OIDC_GITHUB_...`), secrets encrypted; `auth_flows` state store; app start/poll flow.
6. **Wiring in `backend/app/main.py`**: `cxs_` prefix dispatch in `auth()` (main.py:323) with centralized CSRF (`X-Cortex-Client`) and 5-min `last_seen` coarsening; `/v1/auth` router; self-serve provisioning on activation reusing `provision_user` internals (sharding.py:819) minus auto-minted tokens; crypto-shred step prepended to the deprovision path; `mcp_auth()`/`admin_auth()` untouched.
7. **Migration bits**: `ALTER TABLE scoped_token_index ADD COLUMN account_id TEXT` (pattern sharding.py:534); `POST /v1/admin/users/{user_id}/invite` minting a single-use claim code (`auth_flows kind='account_claim'`) + `POST /v1/auth/claim` linking a new identity to the EXISTING `user_id` (guard: target user has no identities yet) — legacy operator-provisioned users' `cxa_`/`cxm_` tokens keep working indefinitely, account-less; DEK backfill job for existing active users so encryption never blocks a request.
8. **`backend/app/config.py`**: `cortex_kek`, `kms_key_id`, `encryption_enabled`, session/refresh lifetime knobs, `auth_email_mode`, provider env — all via `load_settings` like everything else.
9. **Tests** (section 7), run via `pytest backend/tests`; confirm `macos/build.sh` and the 10k load test still pass unmodified.

Budget: 2–4 focused weeks (research-validated for this scope).

## 7. Test list (release gates in bold)

1. Password hash roundtrip + PHC param upgrade rehash-on-login; scrypt fallback parity.
2. Session mint/verify/expiry/revoke; regenerate on privilege change; password change revokes other sessions.
3. Refresh rotation; **reuse of a rotated token revokes the entire family**.
4. **OIDC callback against a faked provider: state/nonce/PKCE enforced; id_token iss/aud/exp/sig verified; never-auto-link matrix — verified-email match still returns `link_required`, unverified never links, GitHub unverified/non-primary email rejected** (account-takeover gate).
5. Enumeration resistance: signup collision and login failure return generic responses; uniform timing.
6. CSRF: cookie-authed state-changing request without `X-Cortex-Client` is rejected — enforced in the dependency, verified across all account endpoints.
7. Session auth respects `accounts.status` AND `users.status` suspension; `cxs_` can never reach admin or MCP surfaces; existing `cxa_`/`cxm_` auth + tenant-isolation tests still green.
8. App login poll flow: poll_secret hashed at rest, single-use, TTL; no token in any URL.
9. Envelope roundtrip; **cross-user AAD rejection** (blob under user A fails for user B); wrong-domain rejection; CXE1 sniff of legacy plaintext; nonce-budget bump property test.
10. Lazy credential migration + backfill idempotency + remaining-plaintext gauge → enforce-flag refuses plaintext writes.
11. **Crypto-shred: stored credential blobs (including inside a restored backup copy) become unreadable; deletion test asserts BOTH key destruction AND conventional derived-index/tombstone cleanup**.
12. Bucket-mode `credentials.json`: co-tenant blobs mutually unreadable.
13. Claim-invite onto an existing provisioned user: same shard, same tokens, new login.
14. Local mode (`shard_mode='local'`, no auth env): boots and serves with zero new behavior; `standalone_server.py` under `python3 -S` imports none of the new modules.
15. KEK re-wrap job: incremental, resumable, mixed `kek_id` states valid mid-flight.

## 8. Explicitly deferred

- Passkeys (optional second method, post-launch; never passkey-only — ~15–20% opt-in per 2026 FIDO data).
- "Sign in with ChatGPT" activation (disabled registry row only, until public OIDC GA + billing-expectation copy); **"Sign in with Claude" dropped permanently, not deferred**.
- Phase 2 (vault/backup file encryption) and phase 3 (shard content-column + embedding-vector encryption).
- Zero-access mode tier + UI (the `user_key_wraps` seam guarantees it's a row operation later); `cortex-decrypt` offline tool ships with the phase-2 export work.
- `PostgresControlStore` implementation (interface + contract tests only now), Redis session cache, KMS provider activation (interface only), Postgres data-plane port (existing post-10k plan).
- Web dashboard UI, email-vendor selection (log/SMTP interface only), any Swift/local-app work (CryptoKit backup encryption remains a separate app-side item per docs/NATIVE_VAULT_PLAN.md).

**Top risks carried forward** (owned, not hidden): KEK custody = catastrophic-loss point until KMS (escrow runbook + restore drill is a launch gate; one-box compromise still defeats at-rest encryption — threat model says so); crypto-shred claims scoped to actual coverage per phase; account-linking and nonce-discipline correctness are test-gated; control-plane SQLite write pressure from sessions is the first Postgres-swap trigger; first-party auth means the 2-person team owns the security surface — WorkOS/Kratos documented as the priced escape hatches.
