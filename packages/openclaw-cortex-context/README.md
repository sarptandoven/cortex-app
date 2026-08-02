# `@doppl-tech/openclaw-context`

Reference OpenClaw `contextEngine` adapter for [Cortex](../../README.md). It has two modes:

- **Live mode:** recalls a cited, token-budgeted context pack from the local Cortex server before every OpenClaw model run. Optional user-turn capture goes through Cortex's normal queued review pipeline.
- **Bundle mode:** runs without a Cortex server. It independently verifies a published portable-memory v2 bundle, pins the Ed25519 signer, and performs bounded local recall over the signed memories.

The adapter passes the existing OpenClaw transcript through unchanged and delegates compaction back to the OpenClaw runtime. Cortex only contributes a bounded `systemPromptAddition`.

## Requirements

- Node.js 22.19 or newer, matching OpenClaw 2026.6.11.
- OpenClaw 2026.6.11 or newer.
- Live mode: Cortex listening on `http://127.0.0.1:8766` and a scoped API token.
- Bundle mode: a Cortex portable bundle plus its trusted `signature.key_id`.

## Build and install locally

```bash
cd packages/openclaw-cortex-context
npm install
npm test
openclaw plugins install -l "$PWD"
```

Then select the registered engine id `cortex-context` in `~/.openclaw/openclaw.json` and restart the OpenClaw gateway.

## Real OpenClaw release gate

Run the packaged-artifact and Gateway gate before publishing:

```bash
npm run release:gate
```

The gate uses a temporary `HOME`, XDG directories, and profile; it scrubs inherited OpenClaw state/config overrides and asserts both the active config and plugin install remain inside that profile. It packs the npm archive, installs it through OpenClaw 2026.6.11, uninstalls and reinstalls it, and validates the exclusive `contextEngine` registration and plugin schema. Direct lifecycle probes against the installed artifact cover live and bundle modes plus strict tamper rejection. A separate isolated loopback Gateway boot proves real runtime loading and health; it does not run a provider-backed model turn. If the active Node is older than 22.19, the script uses an isolated `npx node@22.19.0` runtime; set `OPENCLAW_NODE` or pass `--node` to pin an existing runtime. The harness currently supports macOS and Linux.

## Live Cortex mode

```json5
{
  plugins: {
    slots: {
      contextEngine: "cortex-context",
    },
    entries: {
      "cortex-context": {
        enabled: true,
        config: {
          mode: "live",
          baseUrl: "http://127.0.0.1:8766",
          token: "cxa_your_scoped_token",
          tokenBudget: 2000,
          captureMode: "off",
          failOpen: true,
        },
      },
    },
  },
}
```

Use a token with `read` scope for recall. Set `captureMode: "user"` only when the token also has `write` scope and you want OpenClaw user turns queued into Cortex. Capture is off by default and uses a deterministic idempotency key per session and message.

The token can be supplied through `CORTEX_API_TOKEN`; `CORTEX_BASE_URL` and `CORTEX_USER_ID` are also supported. The adapter refuses non-loopback base URLs unless `allowRemote: true` is explicitly configured.

## Offline signed-bundle mode

Export a bundle from Cortex:

```bash
curl -H "Authorization: Bearer $CORTEX_TOKEN" \
  http://127.0.0.1:8766/v1/export/bundle > cortex-memory.json
jq -r '.signature.key_id' cortex-memory.json
```

Configure OpenClaw with that key id pinned:

```json5
{
  plugins: {
    slots: {
      contextEngine: "cortex-context",
    },
    entries: {
      "cortex-context": {
        enabled: true,
        config: {
          mode: "bundle",
          bundlePath: "/absolute/path/to/cortex-memory.json",
          expectedSigningKeyId: "64-lowercase-hex-characters",
          maxMemories: 12,
        },
      },
    },
  },
}
```

The file is reloaded when its size or modification time changes. Every reload verifies:

1. The exact signed payload bytes and SHA-256 digest.
2. Required record counts and tenant binding.
3. The ordered continuity-chain commitment and proof digest.
4. The Ed25519 public-key fingerprint and signature.
5. The configured signer pin.

A self-signed but unpinned bundle is rejected by default. Obtain the expected signer id through an authenticated channel. `allowUnpinnedBundle: true` exists for inspection and local experiments, but it does not authenticate who produced the bundle.

## Failure behavior

`failOpen` defaults to `true`: if live Cortex is unavailable or an offline bundle fails verification, OpenClaw continues with its original messages and no injected Cortex context. Set `failOpen: false` when missing verified memory must stop the run.

Recalled content is wrapped with an explicit instruction that it is evidence, not executable instructions. Memory ids and source references remain visible for citation.
Bundle-mode context also exposes the verified signer id, source-tenant SHA-256 binding, continuity-chain head, and authoritative payload SHA-256 so the receiving agent can retain bundle-level provenance instead of seeing detached facts. The raw verified source tenant remains available through the verifier API but is not interpolated into the model prompt.

## Portable verifier API

The independent TypeScript protocol verifier is also exported:

```ts
import { verifyPortableMemoryBundle } from "@doppl-tech/openclaw-context/portable";

const result = verifyPortableMemoryBundle(bundle, expectedSigningKeyId);
if (!result.verified) throw new Error(result.error ?? "verification failed");
console.log(result.payload?.memories);
console.log(result.sourceUserId, result.chainHead, result.payloadSha256);
```

See [`docs/PORTABLE_MEMORY_PROTOCOL_V2.md`](../../docs/PORTABLE_MEMORY_PROTOCOL_V2.md) for the byte-level protocol and committed cross-language test vector.
