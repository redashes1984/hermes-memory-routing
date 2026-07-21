---
name: memory-routing
description: Use when managing Hermes Agent memory routing — sub-document architecture, keyword classification, async LLM review, and MEMORY.md index pattern.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [memory, routing, sub-doc, keyword, llm-classification, hermes-agent]
    related_skills: [hermes-agent]
---

# Memory Routing — 索引式记忆架构

## Overview

Memory Routing solves the problem of unbounded, flat MEMORY.md files in Hermes Agent. Instead of stuffing everything into one file, content is automatically routed to topic-specific sub-documents based on keyword scoring, with optional async LLM review for borderline cases.

**Key insight:** MEMORY.md is an index injected into the system prompt. Sub-docs are read on-demand via `read_file`. This keeps system prompt overhead low while preserving full memory recall.

## Directory Structure

```
~/.hermes/profiles/<profile>/
├── memories/                  # Hermes official directory
│   ├── MEMORY.md             — agent memory index (injected into system prompt)
│   └── USER.md               — user profile (injected into system prompt)
│
└── memory/                    # Memory-routing sub-documents (read on-demand)
    ├── infrastructure.md     — infrastructure, deployment, hardware
    ├── philosophy.md         — values, principles, relationships
    ├── milestones.md         — milestones, version history
    ├── rules.md              — conventions, standards, workflows
    ├── commitments.md        — commitments, relationships
    ├── dev-log.md            — development log, changelog
    ├── fallback.md           # 0-match entries (temporary holding area)
    └── CREDENTIALS.md        — sensitive credentials (chmod 600)
```

**Path Reference (Official):**
- `~/.hermes/memories/` → MEMORY.md + USER.md (official index files, injected into system prompt)
- `~/.hermes/memory/` → Sub-documents (memory routing extension, read on-demand via `read_file`)

⚠️ **Do NOT swap these paths.** The official code returns `get_hermes_home() / "memories"` for the index files and `get_hermes_home() / "memory"` for sub-docs.

**Sub-doc names are configured via `SUB_DOCS` — they are not hardcoded.** See "Adding a New Sub-Doc" below.

## How Routing Works

### Three-Tier Classification

1. **Keyword scoring (fast, always first):** Each sub-doc has a keyword list. Content is scanned; the sub-doc with the most matching keywords wins.

2. **Threshold-based decision:**
   - `>= 3 keywords` → **Fast path**: write directly to sub-doc, skip LLM
   - `1-2 keywords` → **LLM review path**: write to keyword result, spawn async LLM thread for review
   - `0 keywords` → **Fallback**: write to MEMORY.md directly

3. **Async LLM review (non-blocking):** For borderline cases (1-2 keyword matches), a background thread calls a lightweight LLM to reclassify. If the LLM disagrees with the keyword result, the entry is moved to the correct sub-doc.

4. **Fallback classification (non-blocking):** For 0-match entries written to `fallback.md`, a separate background thread immediately tries LLM classification — if the LLM identifies a sub-doc, the entry is migrated on the spot.

### Code Flow

```
memory_tool.add(target="memory", content=...)
  → _scan_memory_content(content)  # security check: block injection/invisible chars
  → route_content_to_sub_doc(content)  # V2 keyword scoring (4 phases)
  → if score >= 3:
      → _add_to_sub_doc(doc_name, content)  # atomic write
      → _detect_fact_conflict(content)       # check against fact cache
      → _update_fact_cache(content, doc_name)
    → if 1-2 keywords:
      → _add_to_sub_doc() + threading.Thread(_async_llm_review, ...)  # non-blocking
    → if 0 keywords:
      → _add_to_fallback(content)  # write to fallback.md (NOT MEMORY.md)
      → threading.Thread(_fallback_classify, ...)  # immediate LLM classify
  → _log_audit()  # record to .audit.jsonl
```

### V2 Keyword Scoring (not a simple count)

The V2 algorithm uses four phases:

1. **Raw matching** — scan content against all keywords
2. **Conflict resolution** — shared keywords go to the doc with the smallest keyword list; keyword weights by length (>= 3 chars = 2.0, >= 2 = 1.0, 1 char = 0.5)
3. **Normalization** — divide by sqrt(total keywords) to prevent keyword-list "black holes"; apply specificity bonus via log1p formula
4. **Decision** — normalized score >= 0.6 (confident), >= 0.3 (tentative), < 0.3 (no match → fallback)

### Security Scanning

Before any write, `_scan_memory_content()` blocks:
- Invisible Unicode characters (zero-width space, BOM, etc.)
- Prompt injection / exfiltration patterns

### Fact Cache & Conflict Detection

- `.fact_cache.json` stores subject-attribute-value triples extracted from written entries
- `_detect_fact_conflict()` checks if new content contradicts cached facts — if so, `add()` returns a `fact_conflict` field with old/new values
- `.audit.jsonl` logs every sub-doc write (timestamp, doc name, score) — excluded from Git

## Keyword Configuration

Keywords are defined in `SUB_DOCS` dict within `memory_tool.py`:

```python
SUB_DOCS = {
    "<doc_name>": {
        "description": "Description of what this sub-doc stores",
        "keywords": ["keyword1", "keyword2", "keyword3", ...],
    },
    ...
}
```

### Adding a New Sub-Doc

1. Add entry to `SUB_DOCS` dict with `description` and `keywords` list
2. Create the sub-doc file in the memory sub-directory
3. Update the navigation table in MEMORY.md

### Tuning Keywords

- **Be specific, not broad.** "vllm" is better than "model" as a keyword.
- **Avoid overlap.** If a keyword appears in multiple sub-docs, it inflates scores for all of them.
- **Start with 5-10 keywords per doc**, then add based on misclassification patterns.

## File Writing Strategy

- **MEMORY.md:** Uses `§` delimiter. System prompt injection splits on `§`, only injecting the first chunk (index). Max char limit enforced (default 2200).
- **Sub-docs:** Pure markdown, no `§` splitting. Written atomically via `tempfile + atomic_replace`.
- **Dedup:** Exact content match check before writing — duplicates are rejected.

## LLM Classifier Configuration

```bash
# Hermes Agent library path (for scripts to import memory_tool)
export HERMES_AGENT_LIB="/usr/local/lib/hermes-agent"

# LLM classifier endpoint (any OpenAI-compatible API)
export HERMES_MEMORY_CLASSIFIER_URL="http://localhost:11434/v1"
export HERMES_MEMORY_CLASSIFIER_MODEL="Qwen3-4B"

# Timeout (seconds) — default 30
export HERMES_MEMORY_CLASSIFIER_TIMEOUT="30"
```

| Variable | Default | Used By |
|---|---|---|
| `HERMES_AGENT_LIB` | `/usr/local/lib/hermes-agent` | scripts import path |
| `HERMES_MEMORY_CLASSIFIER_URL` | `http://localhost:11434/v1` | LLM async review |
| `HERMES_MEMORY_CLASSIFIER_MODEL` | `your-model` | LLM async review |
| `HERMES_MEMORY_CLASSIFIER_TIMEOUT` | `30` | LLM async review |

All variables are optional. Keyword routing (score >= 3) works without any of them.

## When to Use

- Diagnosing why a memory entry ended up in the wrong sub-doc
- Adding a new sub-doc category
- Tuning keyword lists for better classification
- Understanding the MEMORY.md injection mechanism
- Troubleshooting LLM classifier failures

## When NOT to Use

- For general memory CRUD operations (use the memory tool directly)
- For MEMORY.md structural design questions not related to routing
- For USER.md (user profile) — it has its own routing but different mechanics

## Common Pitfalls

1. **Keyword overlap between sub-docs.** E.g., a keyword appears in both `philosophy` and `commitments`. The scoring system picks the one with the highest total score — if tied, the first one wins. Mitigate by ensuring each keyword is unique, or by making borderline keywords more specific.

2. **MEMORY.md char limit.** The index file has a hard limit (2200 chars by default). If entries keep landing there, add keywords to catch them in sub-docs instead.

3. **LLM classifier returning garbage.** The classifier uses `temperature: 0.0` and only allows 10 tokens. If the model still returns unexpected text, the result is discarded and the keyword classification stands.

4. **Async thread in scripts dies immediately.** Daemon threads are killed when the parent process exits. This is expected behavior — in a long-lived process (e.g., the Gateway), the async review works correctly.

5. **Sub-docs growing unbounded.** Unlike MEMORY.md, sub-docs have no automatic pruning. Periodically review and consolidate entries, especially changelogs/dev-logs.

## Verification Checklist

- [ ] `route_content_to_sub_doc()` returns correct doc for known content
- [ ] LLM classifier responds within timeout
- [ ] Sub-docs are valid markdown (no `§` pollution)
- [ ] MEMORY.md stays within char limit
- [ ] Dedup prevents exact duplicates
- [ ] Atomic writes succeed (no partial files)

## One-Shot Recipes

### Test classification of a string

```python
# Adjust import path to match your installation
from tools.memory_tool import route_content_to_sub_doc

doc, score = route_content_to_sub_doc("Your content to classify here")
print(f"→ {doc} (score: {score})")
```

### View all keyword scores for a string

```python
from tools.memory_tool import SUB_DOCS

def show_scores(content):
    content_lower = content.lower()
    for doc_name, info in SUB_DOCS.items():
        score = sum(1 for kw in info["keywords"] if kw.lower() in content_lower)
        if score > 0:
            kws = [kw for kw in info["keywords"] if kw.lower() in content_lower]
            print(f"  {doc_name:20} score={score}  keywords: {kws}")

show_scores("Your content to classify here")
```

### List current sub-doc sizes

```python
import os
sub_dir = os.path.expanduser('~/.hermes/profiles/<profile>/memory')
for fn in sorted(os.listdir(sub_dir)):
    fp = os.path.join(sub_dir, fn)
    print(f"  {fn:30} {os.path.getsize(fp):>6} bytes")
```
