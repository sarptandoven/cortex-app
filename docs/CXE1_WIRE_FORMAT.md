# CXE1 — Cortex encrypted-blob wire format (v1)

**Status:** stable, published. This is the on-disk/on-wire format for every value Cortex
encrypts at rest with a per-user key. It is **self-describing and storage-agnostic** — a
blob survives every storage migration byte-identical, and can be decrypted by anyone
holding the key material with the open-source `scripts/cortex_decrypt.py` (or any
implementation of this spec), independent of Cortex's servers or codebase.

Publishing this spec is deliberate: it is what makes "a private memory of you that **you
own**" concretely true — your encrypted data is not hostage to our code continuing to
exist. The reference implementation is `backend/app/keyring.py`; the independent decryptor
is `scripts/cortex_decrypt.py`; `backend/tests/test_cortex_decrypt.py` pins the two to
byte-for-byte agreement so they can never silently drift.

## Key hierarchy

```
Level 0   Root KEK — 32 bytes, held by a KeyProvider.
          LocalKekProvider: base64 in CORTEX_KEK, or a file at CORTEX_KEK_FILE
          (32 raw bytes or base64 text). Versioned "local:vN" via CORTEX_KEK_VERSION.
Level 1   Per-user master DEK — 32 random bytes, minted per user, wrapped by the KEK
          and stored in the keyring's own SQLite (user_keys + user_key_wraps).
          NEVER stored in a data-plane shard DB.
Level 2   Purpose subkey — derived, never stored:
          HKDF-SHA256(master_dek, salt=None, info=b"cortex:v1:" + purpose), length 32.
          purpose ∈ { credentials, content, vault, backup }.
```

## DEK wrap (Level 0 → Level 1), stored in `user_key_wraps.wrapped_dek`

```
wrapped_dek := nonce(12) || AES-256-GCM(KEK).seal(nonce, dek, aad = WRAP_AAD)
WRAP_AAD    := utf8(f"{user_id}|{kek_id}|{dek_version}")
```

A wrapped DEK replayed into another user's row or another version fails authentication,
because the AAD binds it to `(user_id, kek_id, dek_version)`.

Parallel wraps of the SAME DEK are allowed via the `wrap_type` column (PRIMARY KEY is
`(user_id, dek_version, wrap_type)`): `service` (server KEK, the only one shipped today);
reserved for later — `passphrase` (argon2id-derived) and `recovery` (client-generated
kit). See `docs/E2EE_SYNC_DESIGN.md` for the client-held-key / zero-access roadmap.

## CXE1 data blob (a single encrypted value)

```
CXE1 := "CXE1"                 4 bytes, magic
      || kek_id_len            1 byte, unsigned (1..255)
      || kek_id                kek_id_len bytes, utf-8 (e.g. "local:v1"); advisory hint
      || dek_version           4 bytes, big-endian unsigned
      || nonce                 12 bytes, random per message
      || ciphertext_and_tag    AES-256-GCM output (plaintext len + 16-byte tag)

subkey   := HKDF-SHA256(dek, salt=None, info=b"cortex:v1:" + ascii(purpose))[:32]
DATA_AAD := utf8(f"{user_id}|{purpose}|{dek_version}")
ciphertext_and_tag := AES-256-GCM(subkey).seal(nonce, plaintext, aad = DATA_AAD)
```

The embedded `kek_id` is an advisory hint; the authoritative KEK for unwrap is the one on
the `user_key_wraps` row selected by the blob's `dek_version`. A value that does not begin
with the `CXE1` magic is treated as **legacy plaintext** and returned unchanged (lazy
read-migrate), so pre-encryption data stays readable.

## Decrypting a blob (what a holder does)

1. Parse the envelope → `kek_id`, `dek_version`, `nonce`, `ciphertext_and_tag`.
2. Obtain the DEK: either you already hold the raw 32-byte DEK, or unwrap it —
   `dek = AES-256-GCM(KEK).open(wrapped[:12], wrapped[12:], aad = f"{user}|{kek_id}|{version}")`.
3. Derive the purpose subkey via HKDF as above.
4. `plaintext = AES-256-GCM(subkey).open(nonce, ciphertext_and_tag, aad = f"{user}|{purpose}|{version}")`.

Any mismatch in `user_id`, `purpose`, `dek_version`, or key material fails GCM
authentication (a hard error), never silent garbage.

## Cryptographic parameters (frozen for v1)

| Parameter        | Value                                   |
|------------------|-----------------------------------------|
| AEAD             | AES-256-GCM                             |
| Nonce            | 12 bytes, CSPRNG, per message           |
| Auth tag         | 16 bytes                                |
| KDF              | HKDF-SHA256, salt=None, 32-byte output  |
| HKDF info        | `b"cortex:v1:" + purpose`               |
| Nonce budget     | 2²⁸ writes per (user, dek_version), then a fresh version is minted |

## Deletion = crypto-shredding

`crypto_shred(user_id)` zero-overwrites and deletes the user's wrap rows and tombstones
`user_keys.destroyed_at`. Every historical CXE1 blob for that user becomes permanently
undecryptable — deletion is a key operation, not a best-effort row sweep.
