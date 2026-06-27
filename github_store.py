"""
cortex/github_store.py
----------------------
Push extracted context to GitHub repo via the REST API.

Repo structure (v2):
  sources/src_*.md            — immutable source manifests
  records/YYYY-MM-DD/mem_*.md — dated atomic claims and decisions
  entities/people/person_*.md — canonical person pages (append-only)
  entities/projects/proj_*.md — canonical project pages
  tasks/open/task_*.md        — open actions and questions
  tasks/resolved/task_*.md    — completed tasks
  knowledge/decisions/        — high-importance curated decisions
  views/                      — generated summaries (person dossiers, project briefs)
  indexes/entity-registry.json — canonical entity ID → name mapping

Every file has YAML front matter with stable IDs, kind, status,
confidence, importance, source_ids, entity_ids, topics.
"""

import base64
import hashlib
import json
import os
import re
import requests
from datetime import datetime
from typing import Optional

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
CORTEX_REPO = os.environ.get("CORTEX_REPO", "")
GITHUB_API = "https://api.github.com"
BRANCH = os.environ.get("CORTEX_BRANCH", "main")


# ── GitHub helpers ─────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_existing_sha(filepath: str) -> Optional[str]:
    url = f"{GITHUB_API}/repos/{CORTEX_REPO}/contents/{filepath}"
    resp = requests.get(url, headers=_headers(), params={"ref": BRANCH})
    if resp.status_code == 200:
        return resp.json().get("sha")
    return None


def _get_existing_content(filepath: str) -> Optional[str]:
    url = f"{GITHUB_API}/repos/{CORTEX_REPO}/contents/{filepath}"
    resp = requests.get(url, headers=_headers(), params={"ref": BRANCH})
    if resp.status_code == 200:
        data = resp.json()
        return base64.b64decode(data["content"]).decode("utf-8")
    return None


def push_file(filepath: str, content: str, commit_message: str) -> bool:
    if not GITHUB_TOKEN or not CORTEX_REPO:
        raise EnvironmentError("GITHUB_TOKEN and CORTEX_REPO must be set")

    url = f"{GITHUB_API}/repos/{CORTEX_REPO}/contents/{filepath}"
    sha = _get_existing_sha(filepath)

    payload = {
        "message": commit_message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, headers=_headers(), json=payload)
    return resp.status_code in (200, 201)


def _slug(text: str, max_len: int = 50) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:max_len]


# ── Front matter builder ───────────────────────────────────────────────────────

def _frontmatter(fields: dict) -> str:
    """Build YAML front matter from a dict."""
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            if v:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{k}: []")
        elif v is None:
            lines.append(f"{k}: null")
        else:
            # Quote strings that contain special chars
            sv = str(v)
            if any(c in sv for c in [":", "#", "[", "]", "{", "}"]):
                lines.append(f'{k}: "{sv}"')
            else:
                lines.append(f"{k}: {sv}")
    lines.append("---")
    return "\n".join(lines)


# ── Main save function ─────────────────────────────────────────────────────────

def save_extracted_context(extracted: dict, raw_text: str = "") -> list[str]:
    """
    Save all extracted context items to the GitHub repo (new v2 structure).

    Returns list of file paths successfully saved.
    """
    source = extracted.get("_source", "unknown")
    source_id = extracted.get("_source_id", "src_unknown")
    timestamp = extracted.get("_timestamp", datetime.now().isoformat())
    date_str = timestamp[:10]
    captured_at = timestamp

    saved = []

    # ── 1. Source manifest ─────────────────────────────────────────────────────
    source_path = f"sources/{source_id}.md"
    source_fm = _frontmatter({
        "id": source_id,
        "kind": "source",
        "source": source,
        "captured_at": captured_at,
        "raw_length": extracted.get("_raw_length", 0),
        "scope": "private",
    })
    source_content = f"""{source_fm}

# Source: {source} · {date_str}

**Captured:** {captured_at}
**From:** `{source}`

## Summary

{extracted.get('summary', '_No summary extracted._')}

## Raw Excerpt

> {raw_text[:400].strip()}{"..." if len(raw_text) > 400 else ""}
"""
    if push_file(source_path, source_content, f"source({source}): {source_id} @ {date_str}"):
        saved.append(source_path)

    # ── 2. Records (claims, decisions, observations) ───────────────────────────
    for record in extracted.get("records", []):
        rid = record.get("id", "mem_unknown")
        kind = record.get("kind", "claim")
        content = record.get("content", "")
        confidence = record.get("confidence", "confirmed")
        importance = record.get("importance", 3)
        entity_ids = record.get("entity_ids", [])
        topics = record.get("topics", [])
        occurred_at = record.get("occurred_at")

        if not content.strip():
            continue

        filepath = f"records/{date_str}/{rid}.md"
        fm = _frontmatter({
            "id": rid,
            "kind": kind,
            "status": "active",
            "occurred_at": occurred_at or captured_at,
            "captured_at": captured_at,
            "source_ids": [source_id],
            "entity_ids": entity_ids,
            "topics": topics,
            "confidence": confidence,
            "importance": importance,
            "scope": "private",
        })

        header = "Decision" if kind == "decision" else kind.title()
        doc = f"""{fm}

# {header}: {content[:80]}{"..." if len(content) > 80 else ""}

{content}

**Source:** `{source}` · {date_str}
**Confidence:** {confidence} · **Importance:** {importance}/5
"""
        if push_file(filepath, doc, f"record({kind}): {rid}"):
            saved.append(filepath)

        # Also copy high-importance decisions to knowledge/decisions/
        if kind == "decision" and importance >= 4:
            knowledge_path = f"knowledge/decisions/{rid}.md"
            if push_file(knowledge_path, doc, f"knowledge(decision): {rid}"):
                saved.append(knowledge_path)

    # ── 3. Tasks (actions + questions) ────────────────────────────────────────
    for task in extracted.get("tasks", []):
        tid = task.get("id", "task_unknown")
        kind = task.get("kind", "action")
        content = task.get("content", "")
        importance = task.get("importance", 3)
        entity_ids = task.get("entity_ids", [])
        topics = task.get("topics", [])
        status = task.get("status", "open")

        if not content.strip():
            continue

        filepath = f"tasks/{status}/{tid}.md"
        fm = _frontmatter({
            "id": tid,
            "kind": kind,
            "status": status,
            "captured_at": captured_at,
            "source_ids": [source_id],
            "entity_ids": entity_ids,
            "topics": topics,
            "importance": importance,
            "scope": "private",
        })

        icon = "❓" if kind == "question" else "☐"
        doc = f"""{fm}

# {icon} {content}

**Kind:** {kind} · **Status:** {status} · **Importance:** {importance}/5
**Source:** `{source}` · {date_str}
"""
        if push_file(filepath, doc, f"task({kind}): {tid}"):
            saved.append(filepath)

        # File questions as GitHub Issues too
        if kind == "question":
            open_github_issue(
                title=f"❓ {content[:100]}",
                body=f"**Source:** {source}\n**Captured:** {captured_at}\n\n> Auto-filed by Cortex",
                labels=["open-question", "cortex"]
            )

    # ── 4. Entities (people, projects, orgs) ──────────────────────────────────
    for entity in extracted.get("entities", []):
        eid = entity.get("id", "")
        ekind = entity.get("kind", "person")
        name = entity.get("name", "")
        context_str = entity.get("context", "")
        aliases = entity.get("aliases", [])

        if not eid or not name:
            continue

        folder = f"entities/{ekind}s"  # people, projects, orgs
        filepath = f"{folder}/{eid}.md"

        # Append to existing entity page, or create new
        existing = _get_existing_content(filepath)
        if existing:
            new_entry = f"\n## {date_str} · {source}\n{context_str}\n"
            doc = existing + new_entry
            commit_msg = f"entity({ekind}): update {eid}"
        else:
            fm = _frontmatter({
                "id": eid,
                "kind": ekind,
                "name": name,
                "aliases": aliases,
                "status": "active",
                "first_seen": captured_at,
                "scope": "private",
            })
            doc = f"""{fm}

# {name}

{context_str}

## {date_str} · {source}
{context_str}
"""
            commit_msg = f"entity({ekind}): create {eid}"

        if push_file(filepath, doc, commit_msg):
            saved.append(filepath)

    # ── 5. Update entity registry index ───────────────────────────────────────
    _update_entity_registry(extracted.get("entities", []), captured_at)

    return saved


def _update_entity_registry(entities: list[dict], captured_at: str):
    """Update indexes/entity-registry.json with new entities."""
    if not entities:
        return

    registry_path = "indexes/entity-registry.json"
    existing_content = _get_existing_content(registry_path)

    if existing_content:
        try:
            registry = json.loads(existing_content)
        except Exception:
            registry = {}
    else:
        registry = {}

    changed = False
    for entity in entities:
        eid = entity.get("id", "")
        if not eid:
            continue
        if eid not in registry:
            registry[eid] = {
                "id": eid,
                "kind": entity.get("kind", ""),
                "name": entity.get("name", ""),
                "aliases": entity.get("aliases", []),
                "first_seen": captured_at,
            }
            changed = True
        else:
            # Add new aliases
            existing_aliases = set(registry[eid].get("aliases", []))
            new_aliases = set(entity.get("aliases", []))
            if new_aliases - existing_aliases:
                registry[eid]["aliases"] = list(existing_aliases | new_aliases)
                changed = True

    if changed:
        push_file(
            registry_path,
            json.dumps(registry, indent=2, ensure_ascii=False),
            f"index: update entity-registry ({len(entities)} entities)"
        )


# ── GitHub Issues for open questions ──────────────────────────────────────────

def open_github_issue(title: str, body: str, labels: list[str] = None) -> Optional[str]:
    url = f"{GITHUB_API}/repos/{CORTEX_REPO}/issues"
    payload = {
        "title": title,
        "body": body,
        "labels": labels or ["open-question"],
    }
    resp = requests.post(url, headers=_headers(), json=payload)
    if resp.status_code == 201:
        return resp.json().get("html_url")
    return None


# ── Repo initialisation ───────────────────────────────────────────────────────

def init_repo_structure() -> list[str]:
    """Create the v2 folder structure with .gitkeep files."""
    folders = [
        "sources",
        "records",
        "entities/people",
        "entities/projects",
        "entities/orgs",
        "knowledge/decisions",
        "knowledge/facts",
        "tasks/open",
        "tasks/resolved",
        "views",
        "indexes",
        "governance",
        "archive",
    ]
    created = []
    for folder in folders:
        filepath = f"{folder}/.gitkeep"
        if push_file(filepath, "", f"init: create {folder}/"):
            created.append(folder)
            print(f"  ✅ {folder}/")
        else:
            print(f"  ⚠️  {folder}/ (already exists or error)")
    return created


if __name__ == "__main__":
    print("Initialising Cortex repo structure (v2)...")
    init_repo_structure()
    print("Done. Check your GitHub repo.")
