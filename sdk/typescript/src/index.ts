/**
 * @doppl-tech/cortex-client — a small, dependency-free client for the Cortex local memory server.
 *
 * Uses the built-in `fetch` (Node 18+ or any modern browser), so there are no runtime
 * dependencies. Cortex serves the same tool catalog it exposes over MCP as plain HTTP,
 * so any function-calling app can drive it from one tool definition.
 *
 * - Default base URL: http://127.0.0.1:8766 (local loopback — Cortex is local-first).
 * - Auth: `Authorization: Bearer <token>`, optional `X-Cortex-User` header.
 */

/** Formats the tool catalog can be projected to via {@link CortexClient.toolsSchema}. */
export type ToolSchemaFormat = "openai" | "anthropic" | "openapi" | "mcp";

/** An OpenAI Chat Completions / Responses function-calling tool. */
export interface OpenAITool {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

/** An Anthropic Messages API tool. */
export interface AnthropicTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

/** Request body for `POST /v1/tools/call`. */
export interface ToolCallRequest {
  name: string;
  arguments: Record<string, unknown>;
}

/** Response envelope from `POST /v1/tools/call`. */
export interface ToolCallResponse<T = unknown> {
  tool: string;
  result: T;
}

/** Options accepted by {@link CortexClient.context}. */
export interface ContextOptions {
  /** Optional intent hint. */
  intent?: "answer" | "act" | "draft" | "plan" | "recall" | null;
  /** Approximate token budget for the assembled pack. Default 2000. */
  tokenBudget?: number;
  /** Which tool/agent you are (e.g. "cursor", "claude", "agent"). Default "agent". */
  surface?: string;
  /** Response projection. The SMP form is designed for portable agent memory. */
  format?: "json" | "markdown" | "smp";
  /** Optional target-model profile used for pack adaptation. */
  model?: string;
  /**
   * Multi-turn session id used for delta context packs. With `pin: true`, this
   * must be an `asess_...` id returned by the `start_agent_session` MCP tool.
   */
  sessionId?: string;
  /** Persist an immutable, content-addressed copy of the assembled pack. */
  pin?: boolean;
  /** Optional hard memory-sector filter; use this for corpus isolation. */
  sector?: string;
  /**
   * Optional entity-ranking hint. This is not an access-control or isolation
   * boundary; use `sector` for isolation.
   */
  project?: string;
  /** Optional ISO 8601 historical cutoff. */
  asOf?: string;
}

/** Constructor options for {@link CortexClient}. */
export interface CortexClientOptions {
  /** Base URL of the Cortex server. Default `http://127.0.0.1:8766`. */
  baseUrl?: string;
  /** Bearer token. Empty is only accepted by an unauthenticated local dev server. */
  token?: string;
  /** Optional user id sent as the `X-Cortex-User` header. */
  user?: string | null;
  /** Per-request timeout in milliseconds. Default 30000. */
  timeoutMs?: number;
  /** Override the fetch implementation (for testing or custom transports). */
  fetch?: typeof fetch;
}

const DEFAULT_BASE_URL = "http://127.0.0.1:8766";
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_SAME_ORIGIN_REDIRECTS = 3;
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);

/**
 * Raised when the Cortex server returns a non-2xx response or is unreachable.
 *
 * `status` is 0 when the request never reached the server (e.g. a network error or
 * timeout). `detail` is the server's `{"detail": ...}` payload (a string or an object)
 * when present, otherwise the raw response text or transport error message.
 */
export class CortexError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(`Cortex request failed (HTTP ${status}): ${stringifyDetail(detail)}`);
    this.name = "CortexError";
    this.status = status;
    this.detail = detail;
    // Restore prototype chain for downlevel targets.
    Object.setPrototypeOf(this, CortexError.prototype);
  }
}

function stringifyDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

/**
 * Client for the Cortex local memory HTTP API.
 *
 * @example
 * ```ts
 * const cortex = new CortexClient({ token: "cxa_..." });
 * const answer = await cortex.ask("What database do we use?");
 * const hits = await cortex.search("release checklist", 5);
 * ```
 */
export class CortexClient {
  readonly baseUrl: string;
  readonly token: string;
  readonly user: string | null;
  readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: CortexClientOptions = {}) {
    const candidateBaseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
    let parsedBaseUrl: URL;
    try {
      parsedBaseUrl = new URL(candidateBaseUrl);
    } catch {
      throw new CortexError(0, "Cortex baseUrl must be an absolute HTTP(S) URL");
    }
    if (
      !["http:", "https:"].includes(parsedBaseUrl.protocol) ||
      parsedBaseUrl.username ||
      parsedBaseUrl.password ||
      parsedBaseUrl.search ||
      parsedBaseUrl.hash
    ) {
      throw new CortexError(
        0,
        "Cortex baseUrl must be an absolute HTTP(S) URL without credentials, query, or fragment",
      );
    }
    this.baseUrl = parsedBaseUrl.toString().replace(/\/+$/, "");
    this.token = options.token ?? "";
    this.user = options.user ?? null;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const resolvedFetch = options.fetch ?? globalThis.fetch;
    if (typeof resolvedFetch !== "function") {
      throw new Error(
        "No fetch implementation available. Use Node 18+, a browser, or pass `fetch` in options.",
      );
    }
    this.fetchImpl = resolvedFetch;
  }

  // -- transport -----------------------------------------------------------------------

  private headers(jsonBody: boolean): Record<string, string> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    if (this.user) headers["X-Cortex-User"] = this.user;
    if (jsonBody) headers["Content-Type"] = "application/json";
    return headers;
  }

  private buildUrl(path: string, params?: Record<string, unknown>): string {
    let url = this.baseUrl + path;
    if (params) {
      const search = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) {
          search.append(key, String(value));
        }
      }
      const qs = search.toString();
      if (qs) url = `${url}?${qs}`;
    }
    return url;
  }

  private async request<T = unknown>(
    method: string,
    path: string,
    options: { params?: Record<string, unknown>; body?: Record<string, unknown> } = {},
  ): Promise<T> {
    const url = this.buildUrl(path, options.params);
    const hasBody = options.body !== undefined;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    let response: Response;
    let currentUrl = url;
    let redirectCount = 0;
    try {
      for (;;) {
        response = await this.fetchImpl(currentUrl, {
          method,
          headers: this.headers(hasBody),
          body: hasBody ? JSON.stringify(options.body) : undefined,
          // Keep redirect handling inside this client. That lets us prove that a
          // bearer token is only replayed to the same origin.
          redirect: "manual",
          signal: controller.signal,
        });
        if (!REDIRECT_STATUSES.has(response.status)) break;

        if (method !== "GET" && method !== "HEAD") {
          throw new CortexError(
            response.status,
            `Redirect blocked for credential-bearing ${method} request`,
          );
        }
        const location = response.headers.get("location");
        if (!location) {
          throw new CortexError(response.status, "Redirect response did not include a Location header");
        }
        if (redirectCount >= MAX_SAME_ORIGIN_REDIRECTS) {
          throw new CortexError(response.status, "Too many Cortex API redirects (maximum 3)");
        }

        let redirectedUrl: URL;
        try {
          redirectedUrl = new URL(location, currentUrl);
        } catch {
          throw new CortexError(response.status, "Redirect response included an invalid Location URL");
        }
        if (redirectedUrl.origin !== new URL(currentUrl).origin) {
          throw new CortexError(
            response.status,
            "Cross-origin redirect blocked for credential-bearing request",
          );
        }
        currentUrl = redirectedUrl.toString();
        redirectCount += 1;
      }
      const text = await response.text();
      const parsed = parseMaybeJson(text);
      if (!response.ok) {
        throw new CortexError(response.status, errorDetail(parsed, text, response.statusText));
      }
      return parsed as T;
    } catch (err) {
      if (err instanceof CortexError) throw err;
      const reason = err instanceof Error ? err.message : String(err);
      throw new CortexError(0, `Could not reach Cortex at ${this.baseUrl}: ${reason}`);
    } finally {
      clearTimeout(timer);
    }
  }

  // -- tool catalog --------------------------------------------------------------------

  /**
   * Fetch the tool catalog projected to a function-calling format.
   *
   * @param fmt One of `"openai"`, `"anthropic"`, `"openapi"`, `"mcp"`. Default `"openai"`.
   * @returns The schema as returned by the server (the `{schema: ...}` envelope is
   *   unwrapped for you): a list for openai/anthropic/mcp, an object for openapi.
   */
  async toolsSchema<T = unknown>(fmt: ToolSchemaFormat = "openai"): Promise<T> {
    const response = await this.request<{ schema?: T } | T>("GET", "/v1/tools/schema", {
      params: { format: fmt },
    });
    if (response && typeof response === "object" && "schema" in response) {
      return (response as { schema: T }).schema;
    }
    return response as T;
  }

  /**
   * Return the OpenAI function-calling `tools` array (convenience).
   * Drop straight into `chat.completions.create({ tools })`, then route tool calls
   * back through {@link CortexClient.callTool}.
   */
  async openaiTools(): Promise<OpenAITool[]> {
    return this.toolsSchema<OpenAITool[]>("openai");
  }

  /** Return the Anthropic Messages API `tools` array (convenience). */
  async anthropicTools(): Promise<AnthropicTool[]> {
    return this.toolsSchema<AnthropicTool[]>("anthropic");
  }

  /**
   * Invoke a Cortex tool by name and return its result.
   *
   * Uses `POST /v1/tools/call`. The server responds with `{tool, result}`; this method
   * returns `result` directly.
   */
  async callTool<T = unknown>(
    name: string,
    args: Record<string, unknown> = {},
  ): Promise<T> {
    const body: ToolCallRequest = { name, arguments: args };
    const response = await this.request<ToolCallResponse<T> | T>("POST", "/v1/tools/call", {
      body: body as unknown as Record<string, unknown>,
    });
    if (response && typeof response === "object" && "result" in response) {
      return (response as ToolCallResponse<T>).result;
    }
    return response as T;
  }

  // -- high-value endpoints ------------------------------------------------------------

  /**
   * Build a token-budgeted, cited working-context pack for a task (`POST /v1/context`).
   * Call this first before doing work for the user.
   */
  async context<T = unknown>(task: string, options: ContextOptions = {}): Promise<T> {
    const body: Record<string, unknown> = {
      task,
      intent: options.intent ?? null,
      token_budget: options.tokenBudget ?? 2000,
      surface: options.surface ?? "agent",
    };
    if (options.format !== undefined) body.format = options.format;
    if (options.model !== undefined) body.model = options.model;
    if (options.sessionId !== undefined) body.session_id = options.sessionId;
    if (options.pin !== undefined) body.pin = options.pin;
    if (options.sector !== undefined) body.sector = options.sector;
    if (options.project !== undefined) body.project = options.project;
    if (options.asOf !== undefined) body.as_of = options.asOf;
    return this.request<T>("POST", "/v1/context", {
      body,
    });
  }

  /**
   * Search Cortex memory (`GET /v1/search`). `topK` maps to the server's `limit`.
   */
  async search<T = unknown>(query: string, topK = 8): Promise<T> {
    return this.request<T>("GET", "/v1/search", { params: { query, limit: topK } });
  }

  /**
   * Ask a question against memory and get a cited answer or explicit abstention
   * (`GET /v1/ask`). `topK` maps to the server's `limit`.
   */
  async ask<T = unknown>(query: string, topK = 8): Promise<T> {
    return this.request<T>("GET", "/v1/ask", { params: { query, limit: topK } });
  }
}

function parseMaybeJson(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    // Some endpoints (context-pack, markdown formats) return text, not JSON.
    return text;
  }
}

function errorDetail(parsed: unknown, rawText: string, statusText: string): unknown {
  if (parsed && typeof parsed === "object" && "detail" in parsed) {
    return (parsed as { detail: unknown }).detail;
  }
  if (parsed !== null && parsed !== undefined) return parsed;
  return rawText || statusText;
}

export default CortexClient;
