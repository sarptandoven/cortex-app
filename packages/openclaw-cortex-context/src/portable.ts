import { createHash, createPublicKey, verify as verifyEd25519 } from "node:crypto";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface PortableMemoryRecord {
  id?: unknown;
  content?: unknown;
  summary?: unknown;
  topics?: unknown;
  source?: unknown;
  source_url?: unknown;
  status?: unknown;
  superseded_by?: unknown;
  trust_score?: unknown;
  author_class?: unknown;
  user_id?: unknown;
  [key: string]: unknown;
}

export interface PortableMemoryPayload {
  user_id: string;
  captures: unknown[];
  memories: PortableMemoryRecord[];
  tasks: unknown[];
  entities: unknown[];
  edges: unknown[];
  imports: unknown[];
  [key: string]: unknown;
}

export interface PortableMemoryVerification {
  verified: boolean;
  importable: boolean;
  protocolVersion: number | null;
  signingKeyId: string | null;
  expectedSigningKeyId: string | null;
  payload: PortableMemoryPayload | null;
  checks: {
    protocol: boolean;
    payload: boolean;
    payloadObject: boolean;
    counts: boolean;
    tenant: boolean;
    proof: boolean;
    publicKey: boolean;
    signature: boolean;
    signer: boolean;
  };
  error: string | null;
}

const OUTER_VERSION = "v3";
const PROTOCOL_NAME = "cortex-portable-memory";
const PROTOCOL_VERSION = 2;
const SIGNATURE_DOMAIN = "cortex-portable-memory-signature-v2";
const PROOF_DOMAIN = "cortex-portable-memory-integrity-proof-v2";
const PAYLOAD_ENCODING = "base64url-json-utf8";
const CHAIN_ALGORITHM = "sha256-colon-fold-v1";
const PROOF_FORMAT = "cortex-integrity-fingerprint-chain-v2";
const CHAIN_VERSION = "v1";
const CHAIN_GENESIS = "cortex:integrity:v1:genesis";
const MAX_PAYLOAD_BYTES = 128 * 1024 * 1024;
const MAX_FINGERPRINTS = 2_000_000;
const MAX_MEMORY_RECORDS = 100_000;
const MAX_MEMORY_CONTENT_BYTES = 100 * 1024 * 1024;
const MAX_JSON_NODES = 2_000_000;
const MAX_JSON_DEPTH = 128;
const MAX_SAFE_UINT = Number.MAX_SAFE_INTEGER;
const COLLECTIONS = ["captures", "memories", "tasks", "entities", "edges", "imports"] as const;
const HEX_64 = /^[0-9a-f]{64}$/;
const BASE64URL = /^[A-Za-z0-9_-]+$/;
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

function assertExactKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
  label: string,
): void {
  const allowed = new Set([...required, ...optional]);
  if (
    required.some((key) => !Object.prototype.hasOwnProperty.call(value, key)) ||
    Object.keys(value).some((key) => !allowed.has(key))
  ) {
    throw new Error(`${label} fields are malformed`);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function sha256(value: Uint8Array | string): string {
  return createHash("sha256").update(value).digest("hex");
}

function strictBase64Url(value: unknown, maxBytes: number, label: string): Buffer {
  if (typeof value !== "string" || !value || value.includes("=") || !BASE64URL.test(value)) {
    throw new Error(`${label} must be canonical unpadded base64url`);
  }
  const decoded = Buffer.from(value, "base64url");
  if (decoded.length > maxBytes || decoded.toString("base64url") !== value) {
    throw new Error(`${label} is not canonical base64url`);
  }
  return decoded;
}

function uint(value: unknown, label: string, maximum = MAX_SAFE_UINT): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0 || (value as number) > maximum) {
    throw new Error(`${label} must be a bounded non-negative integer`);
  }
  return value as number;
}

function lowerHex(value: unknown, label: string): string {
  if (typeof value !== "string" || !HEX_64.test(value)) {
    throw new Error(`${label} must be 64 lowercase hexadecimal characters`);
  }
  return value;
}

function assertWellFormedUnicode(value: string, label: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) throw new Error(`${label} contains invalid Unicode`);
      index += 1;
      continue;
    }
    if (code >= 0xdc00 && code <= 0xdfff) throw new Error(`${label} contains invalid Unicode`);
  }
}

function validateJsonModel(root: unknown): void {
  const stack: Array<{ value: unknown; depth: number }> = [{ value: root, depth: 0 }];
  let nodes = 0;
  while (stack.length > 0) {
    const entry = stack.pop()!;
    nodes += 1;
    if (nodes > MAX_JSON_NODES) throw new Error("payload has too many JSON nodes");
    if (entry.depth > MAX_JSON_DEPTH) throw new Error("payload is nested too deeply");
    const value = entry.value;
    if (value === null || typeof value === "boolean") continue;
    if (typeof value === "string") {
      assertWellFormedUnicode(value, "payload string");
      continue;
    }
    if (typeof value === "number") {
      if (!Number.isFinite(value)) throw new Error("payload numbers must be finite");
      if (Number.isInteger(value) && !Number.isSafeInteger(value)) {
        throw new Error("payload integers must fit the interoperable JSON range");
      }
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) stack.push({ value: item, depth: entry.depth + 1 });
      continue;
    }
    if (isRecord(value)) {
      for (const [key, item] of Object.entries(value)) {
        assertWellFormedUnicode(key, "payload object key");
        stack.push({ value: item, depth: entry.depth + 1 });
      }
      continue;
    }
    throw new Error("payload contains a non-JSON value");
  }
}

function deepJsonEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (typeof left === "number" && typeof right === "number") return left === right;
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
    return left.every((item, index) => deepJsonEqual(item, right[index]));
  }
  if (isRecord(left) || isRecord(right)) {
    if (!isRecord(left) || !isRecord(right)) return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    if (!deepJsonEqual(leftKeys, rightKeys)) return false;
    return leftKeys.every((key) => deepJsonEqual(left[key], right[key]));
  }
  return false;
}

function assertNoDuplicateJsonKeys(text: string): void {
  let index = 0;
  const whitespace = /\s/;
  const skipWhitespace = () => {
    while (index < text.length && whitespace.test(text[index]!)) index += 1;
  };
  const parseString = (): string => {
    if (text[index] !== '"') throw new Error("expected JSON string");
    const start = index;
    index += 1;
    while (index < text.length) {
      const code = text.charCodeAt(index);
      if (code < 0x20) throw new Error("unescaped control character in JSON string");
      if (text[index] === '"') {
        index += 1;
        return JSON.parse(text.slice(start, index)) as string;
      }
      if (text[index] === "\\") {
        index += 1;
        const escape = text[index];
        if (!escape || !'"\\/bfnrtu'.includes(escape)) throw new Error("invalid JSON escape");
        if (escape === "u") {
          const hex = text.slice(index + 1, index + 5);
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) throw new Error("invalid JSON unicode escape");
          index += 4;
        }
      }
      index += 1;
    }
    throw new Error("unterminated JSON string");
  };
  const parseValue = (depth: number): void => {
    if (depth > MAX_JSON_DEPTH) throw new Error("payload is nested too deeply");
    skipWhitespace();
    const current = text[index];
    if (current === "{") {
      index += 1;
      skipWhitespace();
      const keys = new Set<string>();
      if (text[index] === "}") {
        index += 1;
        return;
      }
      while (index < text.length) {
        skipWhitespace();
        const key = parseString();
        if (keys.has(key)) throw new Error(`duplicate JSON key: ${key}`);
        keys.add(key);
        skipWhitespace();
        if (text[index] !== ":") throw new Error("expected colon after JSON key");
        index += 1;
        parseValue(depth + 1);
        skipWhitespace();
        if (text[index] === "}") {
          index += 1;
          return;
        }
        if (text[index] !== ",") throw new Error("expected comma in JSON object");
        index += 1;
      }
      throw new Error("unterminated JSON object");
    }
    if (current === "[") {
      index += 1;
      skipWhitespace();
      if (text[index] === "]") {
        index += 1;
        return;
      }
      while (index < text.length) {
        parseValue(depth + 1);
        skipWhitespace();
        if (text[index] === "]") {
          index += 1;
          return;
        }
        if (text[index] !== ",") throw new Error("expected comma in JSON array");
        index += 1;
      }
      throw new Error("unterminated JSON array");
    }
    if (current === '"') {
      parseString();
      return;
    }
    const remaining = text.slice(index);
    const token = remaining.match(/^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/)?.[0];
    if (!token) throw new Error("invalid JSON value");
    index += token.length;
  };
  parseValue(0);
  skipWhitespace();
  if (index !== text.length) throw new Error("trailing data after JSON value");
}

function parsePayload(bundle: Record<string, unknown>): {
  payload: PortableMemoryPayload;
  bytes: Buffer;
  payloadObjectMatches: boolean;
} {
  const bytes = strictBase64Url(bundle.payload_bytes, MAX_PAYLOAD_BYTES, "payload_bytes");
  if (bytes.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf]))) {
    throw new Error("payload bytes must not contain a UTF-8 BOM");
  }
  const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  assertNoDuplicateJsonKeys(text);
  const parsed = JSON.parse(text) as unknown;
  if (!isRecord(parsed)) throw new Error("payload bytes must contain a JSON object");
  validateJsonModel(parsed);
  for (const name of COLLECTIONS) {
    if (!Array.isArray(parsed[name])) throw new Error(`payload.${name} must be an array`);
  }
  const memories = parsed.memories as unknown[];
  if (memories.length > MAX_MEMORY_RECORDS) {
    throw new Error("payload contains too many memory records");
  }
  let memoryContentBytes = 0;
  for (const memory of memories) {
    if (!isRecord(memory)) continue;
    if (typeof memory.content === "string") {
      memoryContentBytes += Buffer.byteLength(memory.content, "utf8");
      if (memoryContentBytes > MAX_MEMORY_CONTENT_BYTES) {
        throw new Error("payload memory content exceeds the verification limit");
      }
    }
  }
  if (typeof parsed.user_id !== "string" || !parsed.user_id) {
    throw new Error("payload.user_id must be a non-empty string");
  }
  const convenience = bundle.payload;
  let payloadObjectMatches = true;
  if (convenience !== undefined) {
    if (!isRecord(convenience)) throw new Error("convenience payload must be an object");
    const comparable = { ...convenience };
    delete comparable.exported_at;
    payloadObjectMatches = deepJsonEqual(comparable, parsed);
    if (!payloadObjectMatches) throw new Error("convenience payload does not match payload bytes");
  }
  return {
    payload: parsed as unknown as PortableMemoryPayload,
    bytes,
    payloadObjectMatches,
  };
}

function integrityLink(previous: string, fingerprint: string): string {
  return sha256(`${previous}:${fingerprint}`);
}

function proofBytes(proof: Record<string, unknown>): Buffer {
  assertExactKeys(
    proof,
    ["chain_version", "genesis", "event_count", "event_fingerprints", "chain_head"],
    [],
    "integrity proof",
  );
  if (proof.chain_version !== CHAIN_VERSION || proof.genesis !== CHAIN_GENESIS) {
    throw new Error("unsupported integrity chain");
  }
  const eventCount = uint(proof.event_count, "proof.event_count");
  const chainHead = proof.chain_head;
  if (
    typeof chainHead !== "string" ||
    (eventCount === 0 ? chainHead !== CHAIN_GENESIS : !HEX_64.test(chainHead))
  ) {
    throw new Error("proof.chain_head is malformed");
  }
  if (!Array.isArray(proof.event_fingerprints) || proof.event_fingerprints.length !== eventCount) {
    throw new Error("proof fingerprint count mismatch");
  }
  if (eventCount > MAX_FINGERPRINTS) throw new Error("proof contains too many fingerprints");
  const lines = [
    PROOF_DOMAIN,
    `chain_algorithm=${CHAIN_ALGORITHM}`,
    `chain_version=${CHAIN_VERSION}`,
    `genesis=${CHAIN_GENESIS}`,
    `event_count=${eventCount}`,
  ];
  for (const value of proof.event_fingerprints) {
    lines.push(`fingerprint=${lowerHex(value, "proof fingerprint")}`);
  }
  lines.push(`chain_head=${chainHead}`);
  return Buffer.from(lines.join("\n"), "ascii");
}

function assertProtocol(bundle: Record<string, unknown>): {
  protocol: Record<string, unknown>;
  manifest: Record<string, unknown>;
  proof: Record<string, unknown>;
  signature: Record<string, unknown>;
} {
  assertExactKeys(
    bundle,
    ["cortex_bundle_version", "protocol", "manifest", "payload_bytes", "integrity_proof", "signature"],
    ["payload", "how_to_verify"],
    "bundle",
  );
  if (bundle.cortex_bundle_version !== OUTER_VERSION) throw new Error("unsupported outer bundle version");
  const protocol = bundle.protocol;
  const manifest = bundle.manifest;
  const proof = bundle.integrity_proof;
  const signature = bundle.signature;
  if (!isRecord(protocol) || !isRecord(manifest) || !isRecord(proof) || !isRecord(signature)) {
    throw new Error("bundle requires protocol, manifest, integrity_proof, and signature objects");
  }
  assertExactKeys(
    protocol,
    [
      "name",
      "version",
      "signature_format",
      "signature_algorithm",
      "key_id_algorithm",
      "payload_encoding",
      "payload_digest_algorithm",
      "proof_format",
      "proof_digest_algorithm",
      "chain_algorithm",
    ],
    ["capabilities"],
    "protocol",
  );
  if (
    protocol.capabilities !== undefined &&
    (!Array.isArray(protocol.capabilities) ||
      protocol.capabilities.some((value) => typeof value !== "string") ||
      new Set(protocol.capabilities).size !== protocol.capabilities.length)
  ) {
    throw new Error("protocol capabilities are malformed");
  }
  assertExactKeys(
    manifest,
    [
      "user_id",
      "chain_version",
      "chain_head",
      "event_count",
      "payload_sha256",
      "payload_bytes",
      "proof_sha256",
      "signing_key_id",
      "record_counts",
    ],
    ["generated_at"],
    "manifest",
  );
  assertExactKeys(signature, ["algorithm", "key_id", "public_key", "value"], [], "signature");
  const expected: Record<string, unknown> = {
    name: PROTOCOL_NAME,
    version: PROTOCOL_VERSION,
    signature_format: SIGNATURE_DOMAIN,
    signature_algorithm: "ed25519",
    key_id_algorithm: "sha256-raw-ed25519-public-key",
    payload_encoding: PAYLOAD_ENCODING,
    payload_digest_algorithm: "sha256",
    proof_format: PROOF_FORMAT,
    proof_digest_algorithm: "sha256",
    chain_algorithm: CHAIN_ALGORITHM,
  };
  for (const [key, value] of Object.entries(expected)) {
    if (protocol[key] !== value) throw new Error(`unsupported protocol field ${key}`);
  }
  return { protocol, manifest, proof, signature };
}

function signatureBytes(fields: ReturnType<typeof assertProtocol>): Buffer {
  const { manifest, signature } = fields;
  const userId = manifest.user_id;
  if (typeof userId !== "string" || !userId || userId.length > 500) {
    throw new Error("manifest.user_id is malformed");
  }
  if (manifest.chain_version !== CHAIN_VERSION) throw new Error("unsupported chain version");
  const eventCount = uint(manifest.event_count, "manifest.event_count");
  const chainHead = manifest.chain_head;
  if (
    typeof chainHead !== "string" ||
    (eventCount === 0 ? chainHead !== CHAIN_GENESIS : !HEX_64.test(chainHead))
  ) {
    throw new Error("manifest.chain_head is malformed");
  }
  const payloadBytes = uint(manifest.payload_bytes, "manifest.payload_bytes", MAX_PAYLOAD_BYTES);
  const signingKeyId = lowerHex(signature.key_id, "signature.key_id");
  if (manifest.signing_key_id !== signingKeyId) throw new Error("signing key ids do not match");
  if (!isRecord(manifest.record_counts)) throw new Error("manifest.record_counts is malformed");
  const recordCounts = manifest.record_counts;
  assertExactKeys(recordCounts, COLLECTIONS, [], "manifest.record_counts");
  const counts = Object.fromEntries(
    COLLECTIONS.map((name) => [name, uint(recordCounts[name], `record count ${name}`)]),
  ) as Record<(typeof COLLECTIONS)[number], number>;
  const lines = [
    SIGNATURE_DOMAIN,
    `outer_bundle_version=${OUTER_VERSION}`,
    `protocol_name=${PROTOCOL_NAME}`,
    `protocol_version=${PROTOCOL_VERSION}`,
    "signature_algorithm=ed25519",
    "key_id_algorithm=sha256-raw-ed25519-public-key",
    `payload_encoding=${PAYLOAD_ENCODING}`,
    "payload_digest_algorithm=sha256",
    `proof_format=${PROOF_FORMAT}`,
    "proof_digest_algorithm=sha256",
    `chain_algorithm=${CHAIN_ALGORITHM}`,
    `source_user_id_sha256=${sha256(Buffer.from(userId, "utf8"))}`,
    `chain_version=${CHAIN_VERSION}`,
    `chain_head=${chainHead}`,
    `event_count=${eventCount}`,
    `payload_sha256=${lowerHex(manifest.payload_sha256, "manifest.payload_sha256")}`,
    `payload_bytes=${payloadBytes}`,
    `signing_key_id=${signingKeyId}`,
  ];
  for (const name of COLLECTIONS) lines.push(`record_count.${name}=${counts[name]}`);
  lines.push(`proof_sha256=${lowerHex(manifest.proof_sha256, "manifest.proof_sha256")}`);
  return Buffer.from(lines.join("\n"), "ascii");
}

export function verifyPortableMemoryBundle(
  value: unknown,
  expectedSigningKeyId?: string | null,
): PortableMemoryVerification {
  const checks = {
    protocol: false,
    payload: false,
    payloadObject: false,
    counts: false,
    tenant: false,
    proof: false,
    publicKey: false,
    signature: false,
    signer: false,
  };
  let payload: PortableMemoryPayload | null = null;
  let signingKeyId: string | null = null;
  const expected = expectedSigningKeyId?.trim().toLowerCase() || null;
  try {
    if (!isRecord(value)) throw new Error("bundle must be a JSON object");
    const fields = assertProtocol(value);
    checks.protocol = true;
    const parsed = parsePayload(value);
    payload = parsed.payload;
    checks.payloadObject = parsed.payloadObjectMatches;
    const manifestPayloadHash = lowerHex(fields.manifest.payload_sha256, "manifest.payload_sha256");
    checks.payload =
      sha256(parsed.bytes) === manifestPayloadHash &&
      fields.manifest.payload_bytes === parsed.bytes.length;

    const counts = fields.manifest.record_counts;
    if (!isRecord(counts)) throw new Error("manifest.record_counts is malformed");
    checks.counts = COLLECTIONS.every(
      (name) => uint(counts[name], `record count ${name}`) === parsed.payload[name].length,
    );
    checks.tenant =
      fields.manifest.user_id === parsed.payload.user_id &&
      parsed.payload.memories.every(
        (memory) =>
          isRecord(memory) &&
          (!memory.user_id || memory.user_id === parsed.payload.user_id),
      );

    const encodedProof = proofBytes(fields.proof);
    const fingerprints = fields.proof.event_fingerprints as string[];
    let head = CHAIN_GENESIS;
    for (const fingerprint of fingerprints) head = integrityLink(head, fingerprint);
    checks.proof =
      head === fields.proof.chain_head &&
      head === fields.manifest.chain_head &&
      fields.manifest.event_count === fields.proof.event_count &&
      sha256(encodedProof) === lowerHex(fields.manifest.proof_sha256, "manifest.proof_sha256");

    signingKeyId = lowerHex(fields.signature.key_id, "signature.key_id");
    const publicKey = strictBase64Url(fields.signature.public_key, 32, "signature.public_key");
    const signature = strictBase64Url(fields.signature.value, 64, "signature.value");
    if (publicKey.length !== 32 || signature.length !== 64) throw new Error("invalid Ed25519 lengths");
    checks.publicKey =
      fields.signature.algorithm === "ed25519" &&
      sha256(publicKey) === signingKeyId &&
      fields.manifest.signing_key_id === signingKeyId;
    checks.signer = expected === null || expected === signingKeyId;
    const key = createPublicKey({
      key: Buffer.concat([ED25519_SPKI_PREFIX, publicKey]),
      format: "der",
      type: "spki",
    });
    checks.signature = verifyEd25519(null, signatureBytes(fields), key, signature);
    const verified = Object.values(checks).every(Boolean);
    return {
      verified,
      importable: verified,
      protocolVersion: PROTOCOL_VERSION,
      signingKeyId,
      expectedSigningKeyId: expected,
      payload: verified ? payload : null,
      checks,
      error: verified ? null : "one or more portable-memory v2 checks failed",
    };
  } catch (error) {
    return {
      verified: false,
      importable: false,
      protocolVersion: null,
      signingKeyId,
      expectedSigningKeyId: expected,
      payload: null,
      checks,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

export const portableMemoryV2 = Object.freeze({
  outerVersion: OUTER_VERSION,
  protocolName: PROTOCOL_NAME,
  protocolVersion: PROTOCOL_VERSION,
  signatureDomain: SIGNATURE_DOMAIN,
  proofDomain: PROOF_DOMAIN,
});
