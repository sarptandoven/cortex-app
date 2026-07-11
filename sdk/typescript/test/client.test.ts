/**
 * Tests for CortexClient against a STUBBED fetch (no real network / server). Node's built-in
 * test runner (`node:test`) + `node:assert/strict` — no test framework dependency, matching the
 * SDK's own "dependency-free" posture. Compiled to JS by `npm run test:build`, then run with
 * `node --test dist-test/test/*.test.js` (see package.json's `test` script).
 *
 * These pin the client's HTTP contract against main.py's REAL hosted routes (GET
 * /v1/tools/schema?format=..., POST /v1/tools/call, POST /v1/context, GET /v1/search,
 * GET /v1/ask) and standalone_server.py's identical local routes, so a regression in either
 * the client or a server-side rename shows up here instead of in a live integration.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { CortexClient, CortexError } from "../src/index.js";

interface RecordedRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string | undefined;
}

/** A minimal fetch stub: records every call and returns a queued response (FIFO). */
function makeFetchStub(responses: Array<{ status: number; body: unknown; ok?: boolean }>) {
  const calls: RecordedRequest[] = [];
  let index = 0;
  const fetchStub = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const headers: Record<string, string> = {};
    if (init?.headers) {
      for (const [key, value] of Object.entries(init.headers as Record<string, string>)) {
        headers[key] = value;
      }
    }
    calls.push({
      url,
      method: init?.method ?? "GET",
      headers,
      body: typeof init?.body === "string" ? init.body : undefined,
    });
    const queued = responses[Math.min(index, responses.length - 1)];
    index += 1;
    const status = queued.status;
    const ok = queued.ok ?? (status >= 200 && status < 300);
    const text = typeof queued.body === "string" ? queued.body : JSON.stringify(queued.body);
    return {
      ok,
      status,
      statusText: ok ? "OK" : "Error",
      text: async () => text,
    } as unknown as Response;
  }) as typeof fetch;
  return { fetchStub, calls };
}

// -- request construction: paths, headers, auth -------------------------------------------

test("toolsSchema issues GET /v1/tools/schema?format=<fmt> with bearer auth and unwraps {schema}", async () => {
  const { fetchStub, calls } = makeFetchStub([
    { status: 200, body: { schema: [{ type: "function", function: { name: "search_memory" } }] } },
  ]);
  const client = new CortexClient({ baseUrl: "https://api.signindoppl.com", token: "cxm_test_token", fetch: fetchStub });

  const schema = await client.toolsSchema("anthropic");

  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].url, "https://api.signindoppl.com/v1/tools/schema?format=anthropic");
  assert.equal(calls[0].headers["Authorization"], "Bearer cxm_test_token");
  assert.equal(calls[0].headers["Accept"], "application/json");
  assert.ok(!("Content-Type" in calls[0].headers), "GET requests must not send Content-Type");
  assert.deepEqual(schema, [{ type: "function", function: { name: "search_memory" } }]);
});

test("toolsSchema defaults format to openai when omitted", async () => {
  const { fetchStub, calls } = makeFetchStub([{ status: 200, body: { schema: [] } }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await client.toolsSchema();

  assert.equal(calls[0].url, "http://127.0.0.1:8766/v1/tools/schema?format=openai");
});

test("openaiTools / anthropicTools request the matching format", async () => {
  const { fetchStub, calls } = makeFetchStub([
    { status: 200, body: { schema: [] } },
    { status: 200, body: { schema: [] } },
  ]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await client.openaiTools();
  await client.anthropicTools();

  assert.match(calls[0].url, /format=openai$/);
  assert.match(calls[1].url, /format=anthropic$/);
});

test("callTool issues POST /v1/tools/call with {name, arguments} body and unwraps {result}", async () => {
  const { fetchStub, calls } = makeFetchStub([
    { status: 200, body: { tool: "search_memory", result: { results: [{ id: "m_1" }] } } },
  ]);
  const client = new CortexClient({ baseUrl: "https://api.signindoppl.com", token: "cxm_test_token", fetch: fetchStub });

  const result = await client.callTool("search_memory", { query: "release checklist" });

  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].url, "https://api.signindoppl.com/v1/tools/call");
  assert.equal(calls[0].headers["Authorization"], "Bearer cxm_test_token");
  assert.equal(calls[0].headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].body ?? "{}"), { name: "search_memory", arguments: { query: "release checklist" } });
  assert.deepEqual(result, { results: [{ id: "m_1" }] });
});

test("callTool defaults arguments to {} when omitted", async () => {
  const { fetchStub, calls } = makeFetchStub([{ status: 200, body: { tool: "list_capabilities", result: {} } }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await client.callTool("list_capabilities");

  assert.deepEqual(JSON.parse(calls[0].body ?? "{}"), { name: "list_capabilities", arguments: {} });
});

test("context issues POST /v1/context with task/intent/token_budget/surface defaults", async () => {
  const { fetchStub, calls } = makeFetchStub([{ status: 200, body: { task: "draft release notes", layers: {} } }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await client.context("draft release notes");

  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].url, "http://127.0.0.1:8766/v1/context");
  assert.deepEqual(JSON.parse(calls[0].body ?? "{}"), {
    task: "draft release notes",
    intent: null,
    token_budget: 2000,
    surface: "agent",
  });
});

test("context forwards intent/tokenBudget/surface overrides", async () => {
  const { fetchStub, calls } = makeFetchStub([{ status: 200, body: {} }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await client.context("plan the launch", { intent: "plan", tokenBudget: 500, surface: "cursor" });

  assert.deepEqual(JSON.parse(calls[0].body ?? "{}"), {
    task: "plan the launch",
    intent: "plan",
    token_budget: 500,
    surface: "cursor",
  });
});

test("search issues GET /v1/search with query + limit params", async () => {
  const { fetchStub, calls } = makeFetchStub([{ status: 200, body: { results: [] } }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await client.search("release checklist", 5);

  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].url, "http://127.0.0.1:8766/v1/search?query=release+checklist&limit=5");
});

test("search defaults topK to 8", async () => {
  const { fetchStub, calls } = makeFetchStub([{ status: 200, body: { results: [] } }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await client.search("anything");

  assert.match(calls[0].url, /limit=8$/);
});

test("ask issues GET /v1/ask with query + limit params", async () => {
  const { fetchStub, calls } = makeFetchStub([{ status: 200, body: { status: "cited", answer: "..." } }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await client.ask("What database do we use?", 3);

  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].url, "http://127.0.0.1:8766/v1/ask?query=What+database+do+we+use%3F&limit=3");
});

test("X-Cortex-User header is sent only when a user is configured", async () => {
  const { fetchStub, calls } = makeFetchStub([
    { status: 200, body: { results: [] } },
    { status: 200, body: { results: [] } },
  ]);
  const withUser = new CortexClient({ token: "t", user: "alice", fetch: fetchStub });
  const withoutUser = new CortexClient({ token: "t", fetch: fetchStub });

  await withUser.search("q");
  await withoutUser.search("q");

  assert.equal(calls[0].headers["X-Cortex-User"], "alice");
  assert.ok(!("X-Cortex-User" in calls[1].headers));
});

test("no Authorization header is sent when token is empty", async () => {
  const { fetchStub, calls } = makeFetchStub([{ status: 200, body: { results: [] } }]);
  const client = new CortexClient({ fetch: fetchStub });

  await client.search("q");

  assert.ok(!("Authorization" in calls[0].headers));
});

test("baseUrl trailing slashes are stripped", async () => {
  const { fetchStub, calls } = makeFetchStub([{ status: 200, body: { results: [] } }]);
  const client = new CortexClient({ baseUrl: "https://api.signindoppl.com///", token: "t", fetch: fetchStub });

  await client.search("q");

  assert.ok(calls[0].url.startsWith("https://api.signindoppl.com/v1/search"));
});

// -- error propagation ----------------------------------------------------------------

test("a non-2xx JSON {detail} response rejects with CortexError carrying status + detail", async () => {
  const { fetchStub } = makeFetchStub([{ status: 403, body: { detail: "Cortex API token requires destructive scope" } }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await assert.rejects(
    () => client.callTool("forget_memory", { id: "m_123" }),
    (err: unknown) => {
      assert.ok(err instanceof CortexError);
      const error: CortexError = err;
      assert.equal(error.status, 403);
      assert.equal(error.detail, "Cortex API token requires destructive scope");
      assert.match(error.message, /HTTP 403/);
      return true;
    },
  );
});

test("a non-2xx response with a non-string detail object propagates the object", async () => {
  const { fetchStub } = makeFetchStub([{ status: 422, body: { detail: { errors: ["name is required"] } } }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await assert.rejects(
    () => client.callTool("", {}),
    (err: unknown) => {
      assert.ok(err instanceof CortexError);
      assert.equal(err.status, 422);
      assert.deepEqual(err.detail, { errors: ["name is required"] });
      return true;
    },
  );
});

test("a non-2xx non-JSON response falls back to raw text as detail", async () => {
  const { fetchStub } = makeFetchStub([{ status: 500, body: "Internal Server Error" }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  await assert.rejects(
    () => client.search("q"),
    (err: unknown) => {
      assert.ok(err instanceof CortexError);
      assert.equal(err.status, 500);
      assert.equal(err.detail, "Internal Server Error");
      return true;
    },
  );
});

test("a transport failure (fetch throws) rejects with CortexError status 0", async () => {
  const throwingFetch = (async () => {
    throw new Error("getaddrinfo ENOTFOUND api.signindoppl.com");
  }) as typeof fetch;
  const client = new CortexClient({ baseUrl: "https://api.signindoppl.com", token: "t", fetch: throwingFetch });

  await assert.rejects(
    () => client.ask("anything"),
    (err: unknown) => {
      assert.ok(err instanceof CortexError);
      assert.equal(err.status, 0);
      assert.match(String(err.detail), /Could not reach Cortex/);
      assert.match(String(err.detail), /ENOTFOUND/);
      return true;
    },
  );
});

test("an empty 200 body resolves to null rather than throwing", async () => {
  const { fetchStub } = makeFetchStub([{ status: 200, body: "" }]);
  const client = new CortexClient({ token: "t", fetch: fetchStub });

  const result = await client.callTool("noop_tool");

  assert.equal(result, null);
});

// -- constructor guards ----------------------------------------------------------------

test("constructing without a fetch implementation and no global fetch throws synchronously", () => {
  const originalFetch = globalThis.fetch;
  // @ts-expect-error -- deliberately simulating an environment without global fetch (e.g. old Node).
  delete globalThis.fetch;
  try {
    assert.throws(() => new CortexClient({ token: "t" }), /No fetch implementation available/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("default export is the same class as the named export", async () => {
  const module_ = await import("../src/index.js");
  assert.equal(module_.default, module_.CortexClient);
});
