# E2E-encrypted hosted sync — design, honest constraint, and roadmap

**Goal:** close the one place the ownership pitch breaks today — *"synced capture content
sits plaintext (Doppl-readable) on the hosted store"* — without breaking the retrieval
product for users who don't opt in. This doc records the grounded design, the honest
constraint that makes full content-E2EE a real (not quick) build, and exactly what shipped.

## Ground truth (verified in code, 2026-07-11)

- **Synced content is plaintext at rest.** `POST /v1/sync/ingest` (`main.py`) re-extracts
  and `save_capture` writes `raw_text = content` unencrypted (`storage.py`); the pull feed
  `capture_change_page` returns `raw_text` plaintext. The keyring's `"content"` purpose
  exists but was never wired to captures.
- **A full per-user envelope-encryption system already exists** (`backend/app/keyring.py`,
  task #56): per-user 32-byte DEK, AES-256-GCM, HKDF purpose subkeys, `user_key_wraps`
  keyed by `(user_id, dek_version, wrap_type)`, crypto-shred on delete. **Only the
  `'service'` wrap (server-held KEK) is implemented.**
- **The founder already made the call** (`ACCOUNTS_ENCRYPTION_DESIGN.md` §6, §249, §253):
  server-side keys are deliberate because a hosted retrieval product needs plaintext
  compute; full client-held E2E "kills the product core (server-side embeddings, semantic
  retrieval, MCP context assembly)". **The seam was reserved**: a client-derived key can
  wrap the *same* DEK (Bitwarden protected-symmetric-key topology), "download your key" +
  an offline `cortex-decrypt` tool, and **zero-access mode = DELETE the `'service'` wrap row**
  — "a row operation, not a refactor". Privacy copy stays honest — never say "zero-knowledge".

## The honest constraint (why full content-E2EE is a real build, not a flag)

Encrypting `raw_text` at rest is **not** a one-line change: `raw_text` is read at ~15
diffuse sites in `storage.py`, **including a server-side SQL search** —
`lower(COALESCE(c.raw_text,'')) LIKE ?` (`storage.py:29109`). You cannot `LIKE`-match
ciphertext, so encrypting the column breaks hosted search over raw content regardless of
who holds the key. Therefore **content-at-rest E2EE is inseparable from the "hosted plane
becomes a blind relay, retrieval goes local-only" mode** — which is exactly the founder's
reserved *zero-access mode*, and a genuine multi-slice build.

Crucially, this is **cheap for the shipped product**: the macOS app is local-first (memory
`cortex-local-first-sync-architecture` — the data plane stays LOCAL when signed in; the
account is identity + sync target). The app already retrieves from `127.0.0.1`, not the
hosted plane. So for the core user, a zero-access relay costs nothing — it only blinds
hosted-side retrieval they don't use. The "E2EE kills search" objection is real for a
server-retrieval product and *moot* for this local-first one.

## Roadmap (ordered)

**Slice 0 — SHIPPED this session: provable key ownership.**
The founder's §253 deliverable, and the safest real progress on B — zero risk to live
crypto paths:
- `docs/CXE1_WIRE_FORMAT.md` — the published, frozen wire-format spec for every value
  Cortex encrypts (so the format is auditable and portable, independent of our code).
- `scripts/cortex_decrypt.py` — an open-source, offline decryptor that re-implements the
  spec (does **not** import the app) and recovers any CXE1 blob from the key material you
  own. This is the teeth behind "readable even if Cortex dies."
- `backend/tests/test_cortex_decrypt.py` — pins the tool to byte-for-byte agreement with
  the live keyring (7 tests: KEK-unwrap path, raw-DEK path, AAD isolation for wrong
  user/purpose, legacy-plaintext passthrough, the CLI end-to-end).

**Slice 1 — Encrypt synced content at rest under the per-user DEK (hosted only).**
Wire the hosted `save_capture` write + every `raw_text` reader through
`keyring.encrypt_blob/decrypt_blob(user_id, "content", ...)`, backward-compatible via the
keyring's legacy-plaintext sniff. Requires the raw-content read-site audit + moving the
hosted `raw_text LIKE` search onto derived/tokenized fields (or accepting local-only
search). Upgrades "plaintext on disk" → "encrypted at rest with a per-user key."

**Slice 2 — SHIPPED (opt-in zero-access, client-held key).**
Rather than the DEK-rewrap route, zero-access shipped as a cleaner **additive blind-relay**
(safer than encrypting `raw_text` in place): the macOS client holds its own 256-bit key
(CryptoKit, Keychain, `ThisDeviceOnly`), and when the user turns on zero-access it encrypts
each capture into a `CXEC1` blob (AES-256-GCM, AAD = capture id) before push. The hosted
store keeps only the ciphertext (`captures.encrypted_payload`) — it runs **no extraction**,
persists **no plaintext** (`raw_text = "[encrypted]"`), and cannot search it. The pulling
device decrypts locally. The key never leaves the device; a Crockford-Base32 **recovery
code** (32 bytes + CRC-8) is the only escrow, shown once with a forced "I saved it" gate.
`scripts/cortex_decrypt.py cxec1 --recovery-code …` decrypts it offline — so "we cannot
read your memory, and you can prove it" is structurally true for opt-in users. An
adversarial crypto review gated the ship (fixed: prior-plaintext derivatives are now purged
when a capture is converted to encrypted, so nothing readable survives behind the
ciphertext; `enc_meta` is size-bounded). Files: `macos/Sources/CortexE2EE.swift`,
`CortexPushSync/CortexPullSync/CortexCloudAuth`, backend blind-relay in `storage.py`/
`main.py`/`standalone_server.py`, `models.py`, `docs/CXE1_WIRE_FORMAT.md` §CXEC1.

**Slice 3 — SHIPPED: purge/tombstone propagation.** A local forget/purge now removes the
hosted copy (ciphertext or plaintext) AND reaches every other device. Deletions ride their
own monotonic `sync_tombstones` feed (a DELETE never bumps `captures.rowid`); both servers
expose `GET/POST /v1/sync/deletions`; the push/pull workers propagate + apply them with an
anti-resurrection guard (a tombstoned id is never re-created) and per-item transactions
(a mid-batch failure never leaves the DB claiming a capture the source-of-truth vault lost).
An adversarial data-loss review gated the ship — fixed a HIGH bug where a forgotten memory
was resurrected when its parent capture was re-processed (the deterministic memory id is now
tombstone-checked before re-derivation). Files: `storage.py` (tombstones + feed + apply +
`_save_memory` guard), `main.py`/`standalone_server.py`, `CortexPushSync`/`CortexPullSync`.

## Privacy posture (verbatim, honest — never overclaim)

- **Default (server-side keys):** *"Encrypted at rest with a per-user key you can own;
  Cortex servers can read your data to serve you (embeddings, retrieval, context)."*
  Plus: *"Download your key and decrypt your own data with our open-source tool — even if
  Cortex disappears."* (Slice 0 makes this literally true today.)
- **Zero-access mode (opt-in, after Slice 2):** *"Only your device holds the key; Cortex
  cannot read your synced memory. Retrieval runs locally."* Never the word "zero-knowledge"
  outside this explicitly-entered mode.
