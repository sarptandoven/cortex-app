#!/usr/bin/env python3
"""cortex-decrypt — the open-source, offline decryptor for Cortex's encrypted data.

Cortex encrypts your hosted data at rest with a per-user key using the self-describing
**CXE1** envelope (spec: docs/CXE1_WIRE_FORMAT.md). This standalone tool decrypts any
CXE1 blob given the key material *you own* — with zero dependency on Cortex's servers,
codebase, or continued existence. It is the teeth behind "a memory of you that you own":
if Cortex disappears tomorrow, your exported encrypted data is still readable with this
~200-line script and your key.

It intentionally re-implements the format from the published spec rather than importing
`backend.app.keyring`, so it stays correct even if the app is gone. A round-trip test
(`backend/tests/test_cortex_decrypt.py`) pins byte-for-byte agreement with the live
keyring, so the two can never silently drift.

Key hierarchy (see docs/CXE1_WIRE_FORMAT.md):

    KEK (root, 32 bytes)  --wraps-->  per-user DEK (32 bytes)  --HKDF-->  purpose subkey

To decrypt a blob you need either:
  * the raw 32-byte DEK (``--dek``), or
  * the KEK (``--kek`` / ``--kek-file`` / ``CORTEX_KEK``) **and** the user's wrapped DEK
    (``--wrapped-dek``, from a ``user_key_wraps`` export) so this tool can unwrap it first.

Plus the ``--user`` id and ``--purpose`` (both are bound into the AES-GCM AAD, so a wrong
value fails authentication rather than returning garbage).

Examples
--------
Decrypt a credentials blob using the KEK + the wrapped DEK exported from your keyring:

    export CORTEX_KEK="$(cat kek.b64)"
    python3 scripts/cortex_decrypt.py \
        --user alice --purpose credentials --dek-version 1 \
        --wrapped-dek "$(cat alice.wrap.b64)" \
        --in secret.cxe1 --out secret.json

Or read the whole thing straight from a keyring SQLite export (no manual wrap handling):

    python3 scripts/cortex_decrypt.py from-keyring \
        --keyring keyring.sqlite --kek-file kek.b64 \
        --user alice --purpose credentials --in secret.cxe1
"""
from __future__ import annotations

import argparse
import base64
import os
import struct
import sys
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# --- Format constants (frozen; must match docs/CXE1_WIRE_FORMAT.md + keyring.py) -------
CXE1_MAGIC = b"CXE1"
HKDF_INFO_PREFIX = b"cortex:v1:"
DEK_LEN = 32
WRAP_NONCE_LEN = 12
DATA_NONCE_LEN = 12
GCM_TAG_LEN = 16


class DecryptError(Exception):
    """Any failure to parse or authenticate a CXE1 blob or a wrapped DEK."""


def is_cxe1(blob: bytes) -> bool:
    """True if ``blob`` carries the CXE1 envelope; False means legacy plaintext."""
    return bytes(blob[:4]) == CXE1_MAGIC


def wrap_aad(user_id: str, kek_id: str, dek_version: int) -> bytes:
    return f"{user_id}|{kek_id}|{dek_version}".encode("utf-8")


def data_aad(user_id: str, purpose: str, dek_version: int) -> bytes:
    return f"{user_id}|{purpose}|{dek_version}".encode("utf-8")


def parse_envelope(blob: bytes) -> tuple[str, int, bytes, bytes]:
    """Parse a CXE1 blob into (kek_id, dek_version, nonce, ciphertext+tag).

    Layout: ``b"CXE1" | kek_id_len(1) | kek_id | dek_version(4, big-endian) | nonce(12) | ct+tag``.
    """
    raw = bytes(blob)
    if not is_cxe1(raw):
        raise DecryptError("not a CXE1 blob (missing magic); it may be legacy plaintext")
    if len(raw) < 5:
        raise DecryptError("truncated CXE1 header")
    kek_id_len = raw[4]
    off = 5
    if len(raw) < off + kek_id_len + 4 + DATA_NONCE_LEN + GCM_TAG_LEN:
        raise DecryptError("truncated CXE1 blob")
    kek_id = raw[off : off + kek_id_len].decode("utf-8")
    off += kek_id_len
    (dek_version,) = struct.unpack(">I", raw[off : off + 4])
    off += 4
    nonce = raw[off : off + DATA_NONCE_LEN]
    off += DATA_NONCE_LEN
    ciphertext = raw[off:]
    return kek_id, dek_version, nonce, ciphertext


def unwrap_dek(kek: bytes, user_id: str, dek_version: int, kek_id: str, wrapped_dek: bytes) -> bytes:
    """Recover the 32-byte per-user DEK from its KEK-wrapped form.

    Wrapped layout: ``nonce(12) | AES-256-GCM(kek).encrypt(nonce, dek, aad=user|kek_id|version)``.
    """
    wrapped = bytes(wrapped_dek)
    if len(wrapped) < WRAP_NONCE_LEN + GCM_TAG_LEN:
        raise DecryptError("wrapped DEK is truncated")
    nonce, ciphertext = wrapped[:WRAP_NONCE_LEN], wrapped[WRAP_NONCE_LEN:]
    try:
        dek = AESGCM(kek).decrypt(nonce, ciphertext, wrap_aad(user_id, kek_id, dek_version))
    except InvalidTag as exc:
        raise DecryptError(
            "wrapped DEK failed authentication — wrong KEK, user, or dek_version"
        ) from exc
    if len(dek) != DEK_LEN:
        raise DecryptError(f"unwrapped DEK is {len(dek)} bytes, expected {DEK_LEN}")
    return dek


def purpose_key(dek: bytes, purpose: str) -> bytes:
    """Derive the AES-256 subkey for a purpose: HKDF-SHA256(dek, info='cortex:v1:'+purpose)."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=DEK_LEN,
        salt=None,
        info=HKDF_INFO_PREFIX + purpose.encode("ascii"),
    ).derive(bytes(dek))


def decrypt_blob(
    blob: bytes,
    *,
    user_id: str,
    purpose: str,
    dek: Optional[bytes] = None,
    kek: Optional[bytes] = None,
    wrapped_dek: Optional[bytes] = None,
) -> bytes:
    """Decrypt a CXE1 blob. Supply either the raw ``dek`` OR (``kek`` + ``wrapped_dek``).

    Legacy plaintext (no CXE1 magic) is returned unchanged, matching the keyring's
    lazy read-migrate behaviour.
    """
    raw = bytes(blob)
    if not is_cxe1(raw):
        return raw
    kek_id, dek_version, nonce, ciphertext = parse_envelope(raw)
    if dek is None:
        if kek is None or wrapped_dek is None:
            raise DecryptError("need either --dek, or both --kek and --wrapped-dek")
        dek = unwrap_dek(kek, user_id, dek_version, kek_id, wrapped_dek)
    key = purpose_key(dek, purpose)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, data_aad(user_id, purpose, dek_version))
    except InvalidTag as exc:
        raise DecryptError(
            "blob failed authentication — wrong user, purpose, or key (or corrupted ciphertext)"
        ) from exc


# --- KEK / input helpers --------------------------------------------------------------

def load_kek(kek_b64: Optional[str], kek_file: Optional[str]) -> bytes:
    """Load the 32-byte KEK from a base64 string, a file (raw 32B or base64 text), or
    the ``CORTEX_KEK`` env var — matching backend/app/keyring.LocalKekProvider exactly."""
    if not kek_b64:
        kek_b64 = os.environ.get("CORTEX_KEK") or ""
    if kek_file:
        data = open(kek_file, "rb").read()
        if len(data) == DEK_LEN:
            return data
        kek_b64 = data.decode("utf-8").strip()
    kek_b64 = (kek_b64 or "").strip()
    if not kek_b64:
        raise DecryptError("no KEK provided (use --kek / --kek-file / CORTEX_KEK)")
    kek = base64.b64decode(kek_b64, validate=True)
    if len(kek) != DEK_LEN:
        raise DecryptError(f"KEK must decode to {DEK_LEN} bytes, got {len(kek)}")
    return kek


def _read_blob(path: str) -> bytes:
    data = sys.stdin.buffer.read() if path == "-" else open(path, "rb").read()
    # Accept either a raw CXE1 blob or its base64 text form.
    if is_cxe1(data):
        return data
    try:
        decoded = base64.b64decode(data.strip(), validate=True)
        if is_cxe1(decoded):
            return decoded
    except Exception:
        pass
    return data


def _emit(plaintext: bytes, out: Optional[str]) -> None:
    if out and out != "-":
        with open(out, "wb") as fh:
            fh.write(plaintext)
    else:
        sys.stdout.buffer.write(plaintext)


def _cmd_decrypt(args: argparse.Namespace) -> int:
    blob = _read_blob(args.infile)
    dek = base64.b64decode(args.dek, validate=True) if args.dek else None
    kek = None
    wrapped = None
    if dek is None:
        kek = load_kek(args.kek, args.kek_file)
        wrapped = base64.b64decode(args.wrapped_dek, validate=True) if args.wrapped_dek else None
    plaintext = decrypt_blob(
        blob, user_id=args.user, purpose=args.purpose, dek=dek, kek=kek, wrapped_dek=wrapped
    )
    _emit(plaintext, args.out)
    return 0


def _cmd_from_keyring(args: argparse.Namespace) -> int:
    """Convenience: pull the user's wrapped DEK straight out of a keyring.sqlite export."""
    import sqlite3

    blob = _read_blob(args.infile)
    _kek_id, dek_version, _nonce, _ct = parse_envelope(blob)
    conn = sqlite3.connect(args.keyring)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT kek_id, wrapped_dek FROM user_key_wraps "
            "WHERE user_id = ? AND dek_version = ? AND wrap_type = 'service'",
            (args.user, dek_version),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise DecryptError(
            f"no service wrap in {args.keyring} for user {args.user!r} dek_version {dek_version}"
        )
    kek = load_kek(args.kek, args.kek_file)
    plaintext = decrypt_blob(
        blob, user_id=args.user, purpose=args.purpose, kek=kek, wrapped_dek=bytes(row["wrapped_dek"])
    )
    _emit(plaintext, args.out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cortex-decrypt",
        description="Offline decryptor for Cortex CXE1-encrypted data (docs/CXE1_WIRE_FORMAT.md).",
    )
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--user", required=True, help="user_id the blob was encrypted for")
        p.add_argument("--purpose", required=True, help="purpose domain (credentials|content|vault|backup)")
        p.add_argument("--in", dest="infile", default="-", help="CXE1 blob file (raw or base64), or - for stdin")
        p.add_argument("--out", default="-", help="output file, or - for stdout")
        p.add_argument("--kek", help="KEK as base64 (or set CORTEX_KEK)")
        p.add_argument("--kek-file", help="KEK file (32 raw bytes or base64 text)")

    d = sub.add_parser("decrypt", help="decrypt one CXE1 blob (default command)")
    add_common(d)
    d.add_argument("--dek", help="raw per-user DEK as base64 (skips KEK unwrap)")
    d.add_argument("--wrapped-dek", help="the user's KEK-wrapped DEK as base64 (from user_key_wraps)")
    d.add_argument("--dek-version", type=int, default=None, help="(informational; read from the blob)")
    d.set_defaults(func=_cmd_decrypt)

    k = sub.add_parser("from-keyring", help="decrypt using a keyring.sqlite export + the KEK")
    add_common(k)
    k.add_argument("--keyring", required=True, help="path to the exported keyring.sqlite")
    k.set_defaults(func=_cmd_from_keyring)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args))
    except DecryptError as exc:
        print(f"cortex-decrypt: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
