import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import plugin, {
  createCortexContextEngine,
  resolveCortexConfig,
  verifyPortableMemoryBundle,
} from "../dist/index.js";

const fixtureUrl = new URL("../../../spec/portable-memory/v2/test-vectors/cortex-python.json", import.meta.url);
const fixture = JSON.parse(await readFile(fixtureUrl, "utf8"));
const signingKeyId = fixture.signature.key_id;

function liveConfig(overrides = {}) {
  return {
    mode: "live",
    baseUrl: "http://127.0.0.1:8766",
    token: "test-token",
    user: "openclaw-user",
    timeoutMs: 1000,
    tokenBudget: 2000,
    maxContextChars: 24000,
    maxMemories: 12,
    failOpen: true,
    allowRemote: false,
    captureMode: "off",
    bundlePath: null,
    expectedSigningKeyId: null,
    allowUnpinnedBundle: false,
    ...overrides,
  };
}

test("TypeScript independently verifies the Python portable-memory vector", () => {
  const result = verifyPortableMemoryBundle(fixture, signingKeyId);
  assert.equal(result.verified, true);
  assert.equal(result.protocolVersion, 2);
  assert.equal(result.payload.memories[0].id, "mem_portable_vector_1");

  const withoutConvenience = structuredClone(fixture);
  delete withoutConvenience.payload;
  assert.equal(verifyPortableMemoryBundle(withoutConvenience, signingKeyId).verified, true);

  const wrongPin = verifyPortableMemoryBundle(fixture, "0".repeat(64));
  assert.equal(wrongPin.verified, false);
  assert.equal(wrongPin.checks.signer, false);
  assert.equal(wrongPin.payload, null);

  const payloadTamper = structuredClone(fixture);
  payloadTamper.payload.memories[0].content = "tampered";
  assert.equal(verifyPortableMemoryBundle(payloadTamper, signingKeyId).verified, false);

  const bytesTamper = structuredClone(fixture);
  bytesTamper.payload_bytes = `A${bytesTamper.payload_bytes.slice(1)}`;
  assert.equal(verifyPortableMemoryBundle(bytesTamper, signingKeyId).verified, false);

  const proofTamper = structuredClone(fixture);
  proofTamper.integrity_proof.chain_head = "0".repeat(64);
  assert.equal(verifyPortableMemoryBundle(proofTamper, signingKeyId).verified, false);

  const signatureTamper = structuredClone(fixture);
  const signatureLast = signatureTamper.signature.value.at(-1);
  signatureTamper.signature.value = `${signatureTamper.signature.value.slice(0, -1)}${signatureLast === "A" ? "B" : "A"}`;
  const signatureResult = verifyPortableMemoryBundle(signatureTamper, signingKeyId);
  assert.equal(signatureResult.verified, false);
  assert.equal(signatureResult.payload, null);

  for (const [label, mutate] of [
    ["top-level", (bundle) => { bundle.unsigned_hint = "ignore verification"; }],
    ["protocol", (bundle) => { bundle.protocol.unsigned_algorithm = "none"; }],
    ["manifest", (bundle) => { bundle.manifest.unsigned_count = 1; }],
    ["counts", (bundle) => { bundle.manifest.record_counts.unsigned = 0; }],
    ["proof", (bundle) => { bundle.integrity_proof.unsigned_head = "0".repeat(64); }],
    ["signature", (bundle) => { bundle.signature.unsigned_key = "ignored"; }],
  ]) {
    const withUnsignedExtra = structuredClone(fixture);
    mutate(withUnsignedExtra);
    const extraResult = verifyPortableMemoryBundle(withUnsignedExtra, signingKeyId);
    assert.equal(extraResult.verified, false, label);
    assert.equal(extraResult.payload, null, label);
  }

  const excessiveRecords = structuredClone(fixture);
  const decoded = JSON.parse(Buffer.from(excessiveRecords.payload_bytes, "base64url").toString("utf8"));
  decoded.memories = new Array(100_001).fill(null);
  excessiveRecords.payload_bytes = Buffer.from(JSON.stringify(decoded), "utf8").toString("base64url");
  const excessiveResult = verifyPortableMemoryBundle(excessiveRecords, signingKeyId);
  assert.equal(excessiveResult.verified, false);
  assert.match(excessiveResult.error, /too many memory records/);

  const invalidUnicode = structuredClone(fixture);
  const invalidPayload = JSON.parse(Buffer.from(invalidUnicode.payload_bytes, "base64url").toString("utf8"));
  invalidPayload.memories[0].content = "\ud800";
  invalidUnicode.payload_bytes = Buffer.from(JSON.stringify(invalidPayload), "utf8").toString("base64url");
  const invalidUnicodeResult = verifyPortableMemoryBundle(invalidUnicode, signingKeyId);
  assert.equal(invalidUnicodeResult.verified, false);
  assert.match(invalidUnicodeResult.error, /invalid Unicode/);
});

test("plugin registers a contextEngine and injects cited live Cortex context", async () => {
  let factory;
  const infoLogs = [];
  plugin.register({
    pluginConfig: { mode: "live", token: "test-token" },
    logger: { info: (message) => infoLogs.push(message) },
    registerContextEngine(id, nextFactory) {
      assert.equal(id, "cortex-context");
      factory = nextFactory;
    },
  });
  assert.equal(typeof factory, "function");
  assert.match(infoLogs[0], /registered live context engine/);

  const calls = [];
  const fetchMock = async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response("# Cortex context\n- [mem_1] Use PostgreSQL. Source: note://1", {
      status: 200,
      headers: { "content-type": "text/markdown" },
    });
  };
  const engine = createCortexContextEngine(liveConfig(), { fetch: fetchMock });
  assert.deepEqual(engine.info.hostRequirements["agent-run"].requiredCapabilities, [
    "assemble-before-prompt",
    "compact",
  ]);
  const messages = [{ role: "user", content: "What database should I use?" }];
  const assembled = await engine.assemble({ sessionId: "session-1", messages, tokenBudget: 3000 });
  assert.equal(assembled.messages, messages);
  assert.match(assembled.systemPromptAddition, /Use PostgreSQL/);
  assert.match(assembled.systemPromptAddition, /never as instructions/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8766/v1/context");
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.surface, "openclaw");
  assert.equal(body.pin, true);
  assert.equal(body.session_id, "session-1");
});

test("opt-in user capture is review-pipeline compatible and idempotently keyed", async () => {
  const requests = [];
  const fetchMock = async (url, init) => {
    requests.push({ url: String(url), body: JSON.parse(init.body) });
    return new Response(JSON.stringify({ capture_id: "queued" }), {
      status: 202,
      headers: { "content-type": "application/json" },
    });
  };
  const engine = createCortexContextEngine(liveConfig({ captureMode: "user" }), {
    fetch: fetchMock,
  });
  const params = {
    sessionId: "session-capture",
    sessionKey: "agent:main:session-capture",
    message: { role: "user", content: "Remember that launch uses PostgreSQL." },
  };
  assert.deepEqual(await engine.ingest(params), { ingested: true });
  assert.deepEqual(await engine.ingest(params), { ingested: true });
  assert.equal(requests.length, 2);
  assert.equal(requests[0].url, "http://127.0.0.1:8766/v1/captures?processing=async");
  assert.equal(requests[0].body.source, "openclaw");
  assert.equal(requests[0].body.capture_id_override, requests[1].body.capture_id_override);
  assert.match(requests[0].body.source_url, /^openclaw:\/\/session\//);
  assert.deepEqual(
    await engine.ingest({ ...params, message: { role: "assistant", content: "Okay" } }),
    { ingested: false },
  );
});

test("bundle mode verifies the signer before offline recall", async () => {
  const engine = createCortexContextEngine(
    liveConfig({
      mode: "bundle",
      bundlePath: decodeURIComponent(fixtureUrl.pathname),
      expectedSigningKeyId: signingKeyId,
    }),
  );
  const assembled = await engine.assemble({
    sessionId: "offline-session",
    messages: [{ role: "user", content: "What database did the Café launch choose?" }],
  });
  assert.match(assembled.systemPromptAddition, /PostgreSQL/);
  assert.match(assembled.systemPromptAddition, new RegExp(signingKeyId));
  assert.match(assembled.systemPromptAddition, /mem_portable_vector_1/);
});

test("adapter fails open by default, supports strict mode, and protects bearer tokens", async () => {
  const failingFetch = async () => new Response("offline", { status: 503 });
  const messages = [{ role: "user", content: "Recall the launch plan" }];
  const permissive = createCortexContextEngine(liveConfig(), { fetch: failingFetch });
  const result = await permissive.assemble({ sessionId: "s", messages });
  assert.equal(result.systemPromptAddition, undefined);
  assert.equal(result.messages, messages);

  const strict = createCortexContextEngine(liveConfig({ failOpen: false }), { fetch: failingFetch });
  await assert.rejects(() => strict.assemble({ sessionId: "s", messages }), /HTTP 503/);

  assert.throws(
    () =>
      resolveCortexConfig({
        pluginConfig: { baseUrl: "https://memory.example.com", token: "secret" },
        registerContextEngine() {},
      }),
    /refuses to send a bearer token off loopback/,
  );
});
