#!/usr/bin/env node

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { join, resolve } from "node:path";

function usage() {
  throw new Error(
    "usage: openclaw_context_artifact_probe.mjs <installed-package-root> <bundle-path> <signing-key-id> <temp-dir>",
  );
}

const [, , installedRootArg, bundlePathArg, signingKeyId, tempDirArg] = process.argv;
if (!installedRootArg || !bundlePathArg || !signingKeyId || !tempDirArg) usage();

const installedRoot = resolve(installedRootArg);
const bundlePath = resolve(bundlePathArg);
const tempDir = resolve(tempDirArg);
const moduleUrl = pathToFileURL(join(installedRoot, "dist", "index.js")).href;
const { createCortexContextEngine, verifyPortableMemoryBundle } = await import(moduleUrl);

const bundle = JSON.parse(await readFile(bundlePath, "utf8"));
const verification = verifyPortableMemoryBundle(bundle, signingKeyId);
assert.equal(verification.verified, true);
assert.equal(verification.payload?.memories.length, 3);

function config(overrides = {}) {
  return {
    mode: "bundle",
    baseUrl: "http://127.0.0.1:8766",
    token: "",
    user: null,
    timeoutMs: 2_000,
    tokenBudget: 2_000,
    maxContextChars: 24_000,
    maxMemories: 12,
    failOpen: false,
    allowRemote: false,
    captureMode: "off",
    bundlePath,
    expectedSigningKeyId: signingKeyId,
    allowUnpinnedBundle: false,
    ...overrides,
  };
}

const bundleEngine = createCortexContextEngine(config());
const bundleResult = await bundleEngine.assemble({
  sessionId: "release-gate-bundle",
  messages: [{ role: "user", content: "Summarize the Café launch" }],
});
const bundleAddition = bundleResult.systemPromptAddition ?? "";
const expectedMemories = verification.payload.memories;
for (const memory of expectedMemories) {
  assert.equal(typeof memory.id, "string");
  assert.match(bundleAddition, new RegExp(memory.id));
  assert.equal(typeof memory.source_url, "string");
  assert.match(bundleAddition, new RegExp(memory.source_url.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
}
assert.match(bundleAddition, new RegExp(signingKeyId));
assert.match(bundleAddition, new RegExp(bundle.manifest.chain_head));
assert.match(bundleAddition, new RegExp(bundle.manifest.payload_sha256));
const sourceTenantSha256 = createHash("sha256").update(bundle.manifest.user_id, "utf8").digest("hex");
assert.match(bundleAddition, new RegExp(sourceTenantSha256));
assert.doesNotMatch(bundleAddition, new RegExp(bundle.manifest.user_id));

const tamperedPath = join(tempDir, "tampered-portable-memory.json");
const tampered = structuredClone(bundle);
tampered.payload.memories[0].content = "tampered after signing";
await writeFile(tamperedPath, JSON.stringify(tampered), "utf8");
const strictTamperEngine = createCortexContextEngine(
  config({ bundlePath: tamperedPath, expectedSigningKeyId: signingKeyId, failOpen: false }),
);
await assert.rejects(
  () =>
    strictTamperEngine.assemble({
      sessionId: "release-gate-tamper",
      messages: [{ role: "user", content: "Summarize the Café launch" }],
    }),
  /portable bundle|payload|verification|convenience/i,
);

const requests = [];
const server = createServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = chunks.length > 0 ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : null;
  requests.push({ method: request.method, url: request.url, headers: request.headers, body });
  if (request.method === "POST" && request.url === "/v1/context") {
    response.writeHead(200, { "content-type": "text/markdown" });
    response.end("# Cited Cortex context\n- [mem_live_release] Use PostgreSQL. Source: note://release/live");
    return;
  }
  if (request.method === "POST" && request.url === "/v1/captures?processing=async") {
    response.writeHead(202, { "content-type": "application/json" });
    response.end(JSON.stringify({ capture_id: "queued-for-review", review_status: "pending" }));
    return;
  }
  response.writeHead(404, { "content-type": "text/plain" });
  response.end("not found");
});
await new Promise((resolveListen, rejectListen) => {
  server.once("error", rejectListen);
  server.listen(0, "127.0.0.1", resolveListen);
});

try {
  const address = server.address();
  assert(address && typeof address === "object");
  const liveEngine = createCortexContextEngine(
    config({
      mode: "live",
      baseUrl: `http://127.0.0.1:${address.port}`,
      token: "release-gate-token",
      user: "release-gate-user",
      captureMode: "user",
      bundlePath: null,
      expectedSigningKeyId: null,
    }),
  );
  const liveResult = await liveEngine.assemble({
    sessionId: "release-gate-live",
    messages: [{ role: "user", content: "Which database should the launch use?" }],
  });
  assert.match(liveResult.systemPromptAddition ?? "", /mem_live_release/);
  assert.match(liveResult.systemPromptAddition ?? "", /Use PostgreSQL/);
  const ingest = await liveEngine.ingest({
    sessionId: "release-gate-live",
    sessionKey: "agent:main:release-gate-live",
    message: { role: "user", content: "Remember that the release uses PostgreSQL." },
  });
  assert.deepEqual(ingest, { ingested: true });

  const contextRequest = requests.find((item) => item.url === "/v1/context");
  assert(contextRequest);
  assert.equal(contextRequest.headers.authorization, "Bearer release-gate-token");
  assert.equal(contextRequest.headers["x-cortex-user"], "release-gate-user");
  assert.equal(contextRequest.body.surface, "openclaw");
  assert.equal(contextRequest.body.pin, true);

  const captureRequest = requests.find((item) => item.url === "/v1/captures?processing=async");
  assert(captureRequest);
  assert.equal(captureRequest.body.source, "openclaw");
  assert.match(captureRequest.body.source_url, /^openclaw:\/\/session\//);
  assert.match(captureRequest.body.capture_id_override, /^cap_openclaw_[0-9a-f]{48}$/);
  assert.equal(Object.hasOwn(captureRequest.body, "review_status"), false);
} finally {
  await new Promise((resolveClose, rejectClose) => {
    server.close((error) => (error ? rejectClose(error) : resolveClose()));
  });
}

process.stdout.write(
  `${JSON.stringify({
    artifact_imported: true,
    bundle_memories_recalled: expectedMemories.length,
    bundle_provenance_visible: true,
    tampered_bundle_rejected: true,
    live_context_injected: true,
    capture_queued_through_review_endpoint: true,
  })}\n`,
);
