# Cortex — Setup Guide

## 1. Create the GitHub repo

```bash
# Go to github.com/new → create "cortex-brain" (private or public)
# Then generate a Personal Access Token:
# github.com/settings/tokens → New token → repo scope
```

## 2. Install dependencies

```bash
cd cortex/
pip install -r requirements.txt
```

## 3. Set environment variables

```bash
cp .env.example .env
# Edit .env with your actual keys
source .env  # or use direnv / python-dotenv
```

## 4. Start Redis

```bash
docker run -d -p 6379:6379 --name cortex-redis redis/redis-stack
```

## 5. Init repo structure + Redis index

```bash
python github_store.py      # creates folders in your GitHub repo
python redis_store.py       # creates the Redis vector search index
```

## 6. Connect to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cortex": {
      "command": "python",
      "args": ["/absolute/path/to/cortex/mcp_server.py"],
      "env": {
        "ANTHROPIC_API_KEY": "...",
        "GITHUB_TOKEN": "...",
        "CORTEX_REPO": "vamikasinghal/cortex-brain",
        "OPENAI_API_KEY": "...",
        "REDIS_URL": "redis://localhost:6379"
      }
    }
  }
}
```

Restart Claude Desktop. You should see "cortex" in the MCP tools list.

## 7. Set up Claude Project for auto-capture

1. Go to claude.ai → Projects → New Project → "Cortex"
2. Paste the contents of `CORTEX_PROJECT_SYSTEM_PROMPT.txt` as the project instructions
3. Connect the Cortex MCP server to this project

Now **every Claude conversation in this project automatically saves to your second brain.**

## 8. Test it

```
You: I just decided to use GitHub for Cortex instead of Obsidian.
Claude: [saves automatically]
Claude: ✅ Saved to Cortex — 1 decision, 0 questions, 1 summary → github.com/vamikasinghal/cortex-brain
```

---

## Ingesting other sources

### ChatGPT / Gemini (batch export)
```bash
# Download export from chatgpt.com/settings or Google Takeout
# Then run:
python ingest_batch.py --file conversations.json --source chatgpt
```
*(ingest_batch.py — build this in Phase 1)*

### Slack (live, via existing MCP)
Slack MCP is already connected — add a `ingest_slack_channel(channel_id)` tool to mcp_server.py that calls the Slack MCP and feeds results through the pipeline.

### Omi (live webhook)
Configure Omi to POST transcripts to a webhook endpoint. Run a simple FastAPI server alongside mcp_server.py:
```bash
# In mcp_server.py or a separate webhook.py
@app.post("/omi-webhook")
async def omi_webhook(body: dict):
    transcript = body.get("transcript", "")
    extracted = extract_context(transcript, source="omi")
    save_extracted_context(extracted, raw_text=transcript)
```

### Notion (via existing MCP)
Use the Notion MCP to read pages, pipe text through `extract_context()`, push to GitHub.
