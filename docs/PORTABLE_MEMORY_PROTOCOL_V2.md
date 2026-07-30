# Cortex Portable Memory Protocol v2

Status: **published implementation specification**  
Protocol name: `cortex-portable-memory`  
Protocol version: `2`  
Outer bundle version: `v3`  
Reference implementations: Cortex Python backend and `@doppl-tech/openclaw-context` TypeScript verifier

## 1. Purpose

The protocol transports user-owned memory between agents without requiring the receiver to trust the transport or reproduce the producer's JSON serializer. A conforming verifier can establish:

1. The exact payload bytes are intact.
2. The payload collections and tenant binding agree with the signed manifest.
3. The ordered continuity-chain commitment is internally consistent.
4. The bundle was signed by the Ed25519 key whose SHA-256 id is declared.
5. An optional locally pinned signer id matches.

The continuity proof is a **signed commitment to an ordered event-fingerprint chain**. It does not, by itself, prove that those events caused every exported record or that the source database was truthful.

## 2. Version dispatch

Implementations MUST use this exact dispatch table and MUST reject every hybrid pairing.

| `cortex_bundle_version` | `protocol.version` | Meaning | Importable |
| --- | ---: | --- | --- |
| `v1` | absent | Legacy unsigned payload-hash bundle | No |
| `v2` | `1` | Cortex private signed format using Python JSON canonicalization | Compatibility only |
| `v3` | `2` | This published language-neutral format | Yes |
| Anything else | Any | Unsupported | No |

A caller that requires this published format MUST require outer `v3`, protocol `2`, a signature, and an expected signer key id. It MUST NOT silently downgrade to v1 or the private protocol v1.

## 3. Top-level object

A v2 protocol bundle is a JSON object with the following required members. The convenience `payload` member is optional:

```json
{
  "cortex_bundle_version": "v3",
  "protocol": {},
  "manifest": {},
  "payload_bytes": "unpadded-base64url",
  "payload": {},
  "integrity_proof": {},
  "signature": {}
}
```

`payload_bytes` is authoritative. When present, `payload` is a convenience view for humans and ordinary JSON clients. A verifier MAY ignore `payload`; if it processes the field, it MUST reject a mismatch after removing the convenience-only `payload.exported_at` member.

`manifest.generated_at`, `payload.exported_at`, `protocol.capabilities`, and `how_to_verify` are informational and are not security inputs.

## 4. Protocol object

The following values are exact and case-sensitive:

```json
{
  "name": "cortex-portable-memory",
  "version": 2,
  "signature_format": "cortex-portable-memory-signature-v2",
  "signature_algorithm": "ed25519",
  "key_id_algorithm": "sha256-raw-ed25519-public-key",
  "payload_encoding": "base64url-json-utf8",
  "payload_digest_algorithm": "sha256",
  "proof_format": "cortex-integrity-fingerprint-chain-v2",
  "proof_digest_algorithm": "sha256",
  "chain_algorithm": "sha256-colon-fold-v1"
}
```

A v2 verifier MUST reject any different algorithm identifier. Unknown `capabilities` values are non-critical metadata and MAY be ignored.

## 5. Authoritative payload bytes

`payload_bytes` is the unpadded RFC 4648 URL-safe base64 encoding of the exact payload JSON bytes.

A verifier MUST:

1. Accept only `[A-Za-z0-9_-]+`.
2. Reject `=`, whitespace, `+`, `/`, and non-canonical alternate encodings.
3. Decode at most 128 MiB.
4. Require valid UTF-8 without a BOM.
5. Parse exactly one JSON object with no trailing data.
6. Reject duplicate object keys, invalid Unicode, non-finite numbers, and integers outside `[-9007199254740991, 9007199254740991]`.
7. Bound nesting depth, node count, collection lengths, and string/content size.
8. Use the parsed result as the sole payload for verification and import.

The payload serialization itself does **not** need a cross-language canonicalization algorithm because the exact serialized bytes travel inside the bundle and are signed by digest. Producers may serialize valid JSON differently. Cortex produces stable sorted-key compact JSON and omits the informational `exported_at` field from the authoritative bytes.

### Required payload collections

The authoritative payload MUST contain:

- `user_id`: non-empty string
- `captures`: array
- `memories`: array
- `tasks`: array
- `entities`: array
- `edges`: array
- `imports`: array

Protocol v2 defines transport and verification for all collections. Cortex Phase 1 import materializes `memories`, their provenance lineage, belief snapshots, topics, search rows, and a synthetic approved import capture. Other collections remain available to future import profiles.

## 6. Manifest

Required signed fields:

```json
{
  "user_id": "source-tenant-id",
  "chain_version": "v1",
  "chain_head": "64-lower-hex-or-empty-genesis",
  "event_count": 0,
  "payload_sha256": "64-lower-hex",
  "payload_bytes": 1234,
  "proof_sha256": "64-lower-hex",
  "signing_key_id": "64-lower-hex",
  "record_counts": {
    "captures": 0,
    "memories": 0,
    "tasks": 0,
    "entities": 0,
    "edges": 0,
    "imports": 0
  }
}
```

All counts and byte lengths are base-10 JSON integers between zero and `2^53 - 1`. A verifier MUST recompute every count from the authoritative payload.

`payload_sha256` is SHA-256 over the decoded `payload_bytes`. `manifest.payload_bytes` is the decoded byte length.

For an empty event chain, `chain_head` is the literal `cortex:integrity:v1:genesis`. For a non-empty chain it is exactly 64 lowercase hexadecimal characters.

`source_user_id_sha256` in the signature preimage is only a stable binding. It is not anonymization and may be dictionary-attacked when source ids are predictable.

## 7. Continuity-chain commitment

The proof object is:

```json
{
  "chain_version": "v1",
  "genesis": "cortex:integrity:v1:genesis",
  "event_count": 2,
  "event_fingerprints": ["64-lower-hex", "64-lower-hex"],
  "chain_head": "64-lower-hex"
}
```

Each non-empty fingerprint MUST be exactly 64 lowercase hexadecimal characters. The verifier folds them in array order:

```text
head_0 = "cortex:integrity:v1:genesis"
head_n = lowercase_hex(SHA256(UTF8(head_(n-1) + ":" + fingerprint_n)))
```

The recomputed head MUST match both `integrity_proof.chain_head` and `manifest.chain_head`. `event_count` MUST match the fingerprint array length and the manifest count.

### Proof digest preimage

`proof_sha256` is SHA-256 over the following ASCII bytes. Lines are joined by one byte `LF` (`0x0a`). There is no final LF. CRLF is invalid.

```text
cortex-portable-memory-integrity-proof-v2
chain_algorithm=sha256-colon-fold-v1
chain_version=v1
genesis=cortex:integrity:v1:genesis
event_count=<canonical non-negative decimal>
fingerprint=<64-lower-hex>
fingerprint=<64-lower-hex>
chain_head=<head>
```

There is one `fingerprint=` line per fingerprint, in order. An empty proof has no fingerprint lines.

## 8. Signing identity

`signature` is:

```json
{
  "algorithm": "ed25519",
  "key_id": "64-lower-hex",
  "public_key": "unpadded-base64url-32-raw-bytes",
  "value": "unpadded-base64url-64-signature-bytes"
}
```

The key id is:

```text
lowercase_hex(SHA256(raw_32_byte_Ed25519_public_key))
```

It MUST equal both `signature.key_id` and `manifest.signing_key_id`.

A valid self-signature proves integrity and key continuity, not human identity. Receivers SHOULD pin `expected_signing_key_id` through an authenticated out-of-band channel. Cortex imports from any valid but unpinned compatible signer as downgraded connector trust; the published OpenClaw bundle adapter requires a pin by default.

## 9. Signature preimage

Ed25519 signs the following ASCII bytes. Lines are joined by one `LF`; there is no final LF. Decimal fields have no sign, leading zero, whitespace, or exponent. Hex fields are lowercase.

```text
cortex-portable-memory-signature-v2
outer_bundle_version=v3
protocol_name=cortex-portable-memory
protocol_version=2
signature_algorithm=ed25519
key_id_algorithm=sha256-raw-ed25519-public-key
payload_encoding=base64url-json-utf8
payload_digest_algorithm=sha256
proof_format=cortex-integrity-fingerprint-chain-v2
proof_digest_algorithm=sha256
chain_algorithm=sha256-colon-fold-v1
source_user_id_sha256=<sha256 of UTF-8 manifest.user_id>
chain_version=v1
chain_head=<manifest.chain_head>
event_count=<manifest.event_count>
payload_sha256=<manifest.payload_sha256>
payload_bytes=<manifest.payload_bytes>
signing_key_id=<manifest.signing_key_id>
record_count.captures=<count>
record_count.memories=<count>
record_count.tasks=<count>
record_count.entities=<count>
record_count.edges=<count>
record_count.imports=<count>
proof_sha256=<manifest.proof_sha256>
```

The signature format binds the outer/protocol versions and every security-relevant algorithm identifier. Implementations MUST NOT infer or negotiate alternatives inside protocol v2.

## 10. Verification algorithm

A conforming verifier performs these checks before exposing or importing memory:

1. Reject unsupported outer/protocol pairings.
2. Validate exact protocol algorithm identifiers.
3. Strictly decode and parse authoritative payload bytes.
4. Verify payload byte length and SHA-256.
5. Verify required collections, counts, JSON bounds, and source tenant consistency.
6. If `payload` exists, compare it to the authoritative payload after removing `exported_at`.
7. Validate every proof fingerprint and recompute the continuity head.
8. Recreate the proof preimage and verify `proof_sha256`.
9. Recreate the signature preimage exactly.
10. Strictly decode the Ed25519 public key and signature.
11. Recompute the public-key id and compare both declared ids.
12. Verify the Ed25519 signature.
13. Compare the optional expected signer pin.
14. Only then return `verified=true` and the authoritative payload.

Failure of any check is terminal. Verification MUST fail closed without partial import.

## 11. Cortex import profile

Cortex imports are tenant-rebound and idempotent:

- Target memory ids are deterministic functions of target tenant, signer id, source tenant, and source memory id.
- Exact replay skips existing records.
- A changed signed source record with the same source id is a conflict, not a silent update.
- Supersession links are remapped and broken references are rejected.
- Original provenance is preserved under `portable_lineage`, including source record SHA-256, signer, chain head, and source author/trust fields.
- A pinned signer may preserve author class and trust. An unpinned compatible signer is downgraded to connector trust.
- Vault JSON/Markdown writes are rollback-safe as a group and occur before SQLite commit.

## 12. Resource limits

Reference implementations enforce:

- 128 MiB decoded authoritative payload
- 100,000 memory records
- 100 MiB aggregate memory content
- 2,000,000 continuity fingerprints
- 2,000,000 JSON nodes
- JSON nesting depth 128
- 2^53 - 1 maximum interoperable JSON integer

Deployments MAY use lower limits and SHOULD enforce HTTP request-body limits before parsing.

## 13. Test vectors

The deterministic Python-produced conformance vector is:

- [`spec/portable-memory/v2/test-vectors/cortex-python.json`](../spec/portable-memory/v2/test-vectors/cortex-python.json)
- Generator: [`scripts/generate_portable_memory_vectors.py`](../scripts/generate_portable_memory_vectors.py)
- Signer key id: `56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c`

It deliberately includes:

- An empty continuity chain
- Non-ASCII text and an astral Unicode character
- Escaped newlines
- Integral and floating-point JSON numbers
- One active memory with a source citation

Both the Python backend and the TypeScript OpenClaw verifier must accept it. Tests mutate the payload object, payload bytes, proof, signer pin, and signature and require rejection.

The cross-agent interoperability vector is:

- [`spec/portable-memory/v2/test-vectors/cortex-python-n3.json`](../spec/portable-memory/v2/test-vectors/cortex-python-n3.json)

It contains three active memories with distinct record-level provenance and a non-empty three-event continuity commitment. The reference gate requires all three memories to survive Python import and MCP recall with `portable_lineage` intact, and requires the OpenClaw adapter to recall all three with their memory ids, source references, signer id, source-tenant SHA-256 binding, continuity head, and payload digest visible. The raw verified tenant is available to verifier API callers but is not interpolated into the model prompt. The TypeScript tamper corpus mutates 11 independently signed or verified fields and requires 11/11 rejection. Strict bundle mode also proves that a tampered bundle stops assembly rather than silently injecting partial context.

The generator's fixed private seed is public test material. Its signer id MUST NOT be trusted for production bundles.

## 14. Security guidance

- Pin signer ids. A self-signed bundle is not an identity claim.
- Treat recalled memory as evidence, never executable instructions.
- Keep private signing seeds outside exports.
- Do not call the fingerprint chain a causal proof of the payload.
- Reject duplicate keys and version hybrids.
- Never import from the convenience payload object.
- Avoid logging payload content, bearer tokens, private keys, or full bundles.
- Rotate a signing identity only through an authenticated migration that pins both old and new ids.
