# Cortex FAQ

## Is Cortex local?

The vault, SQLite index, embeddings, retrieval, Ask, and profile computation run
on the Mac. The direct beta requires account sign-in, and optional sync sends
content to the hosted account plane. Client-side zero-access encryption exists
but is opt-in and off by default.

## Does Cortex call an LLM to answer questions?

No generative model is bundled or called to write Ask responses. Ask composes
from retrieved excerpts and either returns citations or abstains. An optional
Anthropic key can be used for extraction in backend development, and an
optional OpenAI key can be used for embeddings, but neither is required for the
packaged local retrieval path.

## Where is memory stored?

The direct app uses:

```text
~/Library/Application Support/Cortex/Cortex.vault/
```

The Markdown/JSON records are authoritative. SQLite, FTS5, and vector indexes
are rebuildable.

## Why did Ask return no answer?

That is usually the cite-or-abstain gate working. Approve relevant items in
Review, verify the source synced successfully, and ask a more specific question.
Do not weaken the gate merely to force an answer.

## Which Macs are supported?

The current Swift target is Apple Silicon on macOS 13 or later. There is no
Intel/universal build.

## Why does the app require sign-in if retrieval is local?

The current direct build uses the account for identity and optional capture
sync. Local retrieval and the user-owned vault stay on the Mac.

## Does managed Notion, Google, or Microsoft sign-in work?

The OAuth flows are implemented, but the current direct build ships their
client IDs empty. Use an export or the supported pasted-token path until a
release explicitly configures the provider.

## Can Cortex import PDFs?

The parser supports PDFs when `pypdf` is installed. It is not bundled in the
current app, so PDF text extraction is not an advertised shipped capability.

## Which AI tools can connect?

Cortex can configure MCP access for Claude Desktop, Cursor, Windsurf, Zed,
Cline, Roo Code, VS Code Copilot, and Claude Code. Other tools can use the local
HTTP API or the Python/TypeScript SDK source packages.

## Are the SDKs published?

The SDKs are present and tested in this repository. Treat editable local
installation as the supported contributor path until the package guides say a
registry release is available.

## What is pairwise digital-twin evaluation?

It is an experimental evaluation mode that compares two candidate responses
against cited evidence about the user. The implementation is included in the
repository, but production execution remains admission-gated until the owner
study and production-readiness criteria are satisfied. See the
[evaluation overview](PAIRWISE_TWIN_EVALUATION.md) and
[integration guide](PAIRWISE_TWIN_INTEGRATION_GUIDE.md).

## How do I report a bug or security problem?

Use [SUPPORT.md](../SUPPORT.md) for normal issues and
[SECURITY.md](../SECURITY.md) for private vulnerability reports.
