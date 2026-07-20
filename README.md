# Hermes Memory Routing

[English](README.md) | [中文](README.zh-CN.md)

> Indexed memory architecture — keep MEMORY.md lean, auto-route content to topic-specific sub-documents.

## Scope Boundary

> **God's to God, Caesar's to Caesar.**

This project handles only one concern: **routing content that belongs in MEMORY.md into topic-specific sub-documents.**

It does **not** touch — and will **never** touch — memOS, vector memory, semantic search, or any other long-term memory management system. Those have their own tools, their own storage, their own retrieval paths. Memory routing is about the system prompt injection layer; everything else is someone else's domain.

## Problem

Hermes Agent injects MEMORY.md into the system prompt on every turn. With a flat file, three problems emerge as memory grows:

1. **System prompt bloat** — exceeds `memory_char_limit` (default 2200 chars), entries get truncated
2. **Low signal-to-noise** — irrelevant entries distract the model from the current task
3. **Hard to maintain** — replacing/deleting entries risks accidentally removing the wrong one

## Solution

Split memory into an **index** (MEMORY.md, always injected) and **sub-documents** (read on-demand):

```
profiles/<name>/
├── memories/                    # Hermes official location (v0.14.0 §-delimited)
│   ├── MEMORY.md               # Index (§-delimited, injected into system prompt)
│   ├── USER.md                 # User profile (injected into system prompt)
│   ├── .audit.jsonl            # Routing audit trail
│   └── .fact_cache.json        # Fact conflict cache
│
└── memory/                      # Sub-documents (read on-demand via read_file)
    ├── infrastructure.md       — infrastructure, deployment, hardware
    ├── philosophy.md           — values, principles, relationships
    ├── milestones.md           — milestones, version history
    ├── rules.md                — conventions, standards, workflows
    ├── commitments.md          — commitments, long-term promises
    └── dev-log.md              — changelog, iteration notes
```

**Directory definitions:**
- **`memories/`** — Hermes official directory for `MEMORY.md` (index) and `USER.md` (user profile). These are injected into the system prompt on every session start.
- **`memory/`** — Sub-document storage for memory routing. Topic-specific files are read on-demand via `read_file`, keeping system prompt overhead low.

Sub-doc names and keyword lists are **fully configurable** — no hardcoded categories.

## Architecture (v2.0.0 — LLM Intent Classifier)

Memory routing v2 uses an **MCP server** with LLM intent classification — no patches to Hermes source code required:

```
┌──────────────────────────────────────────────────────────┐
│  route_and_save_memory(content)  ← MCP tool              │
│  ┌────────────────────────────────────────────────────┐  │
│  │  intent_classifier.py                              │  │
│  │  ┌──────────────────────────────────────────────┐  │  │
│  │  │ LLM: Qwen3.5-9B-AWQ → 5 categories          │  │  │
│  │  │ {credential, infrastructure, tech-ref,       │  │  │
│  │  │  dev-log, miscellaneous}                     │  │  │
│  │  │ → JSON: {category, confidence, reason}       │  │  │
│  │  └────────────────┬─────────────────────────────┘  │  │
│  │                   │                                 │  │
│  │         LLM fail/timeout?                            │  │
│  │         ┌────┴────┐                                  │  │
│  │       Online    Fallback (keyword)                   │  │
│  │         │        (3 retries, configurable timeout)   │  │
│  │    ┌────▼────┐                                       │  │
│  │    │subdoc   │  → tempfile+os.rename (atomic)       │  │
│  │    │writer   │  → fcntl.flock (concurrent safe)     │  │
│  │    │(atomic) │                                       │  │
│  │    └────┬────┘                                       │  │
│  └─────────┼───────────────────────────────────────────┘  │
│            │                                              │
│            ▼                                              │
│  MEMORY.md index updated                                  │
└──────────────────────────────────────────────────────────┘
```

**Key differences from v1.x:**
- **No source patches** — runs as standalone MCP server, registered via `mcp.json`
- **LLM classification** — 93% accuracy, replaces keyword scoring
- **Atomic writes** — `tempfile + os.rename` + `fcntl.flock`, no data loss on concurrent writes
- **Configurable timeouts** — `HERMES_LLM_TIMEOUT`, `HERMES_MEMORY_SLOW_THRESHOLD`, `HERMES_LLM_RETRY_COUNT`
- **Input sanitization** — null bytes stripped, category validated, prompt injection mitigated

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `HERMES_LLM_BASE_URL` | `http://10.10.4.9:8000/v1` | LLM endpoint |
| `HERMES_LLM_MODEL` | `Qwen3.5-9B-AWQ` | Classification model |
| `HERMES_LLM_TIMEOUT` | `5` | LLM call timeout (seconds) |
| `HERMES_MEMORY_SLOW_THRESHOLD` | `10` | Slow-response threshold (seconds) |
| `HERMES_LLM_RETRY_COUNT` | `2` | Max retry attempts on timeout |

## Testing (v2.0.0)

| Test | Result |
|---|---|
| Offline prompt validation (40 cases) | 40/40 ✅ |
| Online LLM classification (30 cases) | 27/30 (93%) ✅ |
| Fallback simulation (14 cases) | 14/14 ✅ |
| End-to-end routing (5 categories) | 5/5 ✅ |
| Cron cleanup pipeline | ✅ |

## Architecture (v1.x — legacy)

## Three-Stage Routing (v1.1.1 — route before save_to_disk)

```
MemoryStore.add(target="memory", content="...")
          │
          ▼
   route_memory_to_sub_docs(target, content)    ← runs BEFORE save_to_disk
          │
          ▼
   route_content_to_sub_doc(content)            ← keyword scoring
          │
    score ≥ 0.2? ──Yes──▶ Write to sub-doc ✓ + audit + fact cache
          │                                        │
         No                                        ▼
          │                                   skip save_to_disk
          ▼                                   (MEMORY.md stays clean)
   save_to_disk(MEMORY.md)
   (content stored as § entry only
    when no sub-doc matches)
```

### Student-Teacher Self-Evolution (v1.2.0 — implemented 2026-06-12)

Keyword routing alone decays over time (31% accuracy after 6 weeks). A self-healing architecture was added:

```
              Write path (every memory add)
              ┌─────────────────────────────────────┐
              │  keyword scoring → score ≥ 0.2?     │
              │       │                     │        │
              │     Yes                    No        │
              │       │                     │        │
              │  write to sub-doc      audit-only   │
              │       │                             │
              │  score < 3?                        │
              │       │                             │
              │  ┌────▼─────┐                       │
              │  │ Student  │  ← Qwen3.5-4B         │
              │  │ (async)  │    llama.cpp A3000    │
              │  │ 24% fix  │    0.7s, free         │
              │  └────┬─────┘                       │
              │       │                             │
              │   disagree → migrate entry          │
              └───────┴─────────────────────────────┘
              
              Maintenance cron (every 60 min)
              ┌─────────────────────────────────────┐
              │  Teacher audit (Qwen3.6-27B vLLM)   │
              │  → sample 30 entries → reclassify   │
              │  → find errors → analyze patterns   │
              │       │                              │
              │  DEL overbroad keywords              │
              │  ADD covering keywords               │
              │  NOTE systematic issues              │
              │       │                              │
              │  auto-apply changes to               │
              │  memory_routing.py SUB_DOCS          │
              │       │                              │
              │  save state → .teacher-state.json    │
              │  update skill → pitfalls             │
              └─────────────────────────────────────┘
```

**Student model:** Qwen3.5-4B-Pure GGUF on llama.cpp Docker at 10.10.4.62:8000 (Unraid, A3000 GPU). 24% correction on low-score misroutes. Uses `chat_template_kwargs: {"enable_thinking": False}` for direct output.

**Teacher model:** Qwen3.6-27B-FP8 on vLLM at 10.10.4.8:8000. 63% classification accuracy. Periodic sampling + systematic error analysis.

**Self-evolution data:**

| Component | Accuracy | Cost | Latency |
|-----------|----------|------|---------|
| Keyword routing | 47.8% | 0 | <1ms |
| Student (4B llama.cpp) | +24% correction | free | 0.7s |
| Teacher (27B vLLM) | 63% (sample) | free | 0.5s |

### Threshold

| Score | Behavior |
|-------|----------|
| `>= 0.2` | Write to sub-doc + audit + fact cache |
| `< 0.2` | Audit-only (no sub-doc write, content still in MEMORY.md) |

### V2 Keyword Scoring Algorithm

Scoring is not a simple keyword count. It runs in four phases:

1. **Raw matching** — scan content against all keywords in every sub-doc.
2. **Conflict resolution & weighting** — shared keywords are awarded to the doc with the smallest keyword list; keyword weights by length: >= 3 chars = 2.0 (strong), >= 2 = 1.0 (medium), 1 char = 0.5 (weak).
3. **Normalization & specificity bonus** — score = weighted / sqrt(total keywords) to prevent keyword-list "black holes"; a log1p-based specificity bonus rewards docs where matches represent a large fraction of their list.
4. **Decision** — normalized score >= 0.2 (write to sub-doc), < 0.2 (audit-only, content stays in MEMORY.md). Threshold lowered from 0.3 to handle single 2-char Chinese keyword matches (修复, 升级, 更新).

Raw match count is also returned for backward compatibility with `KEYWORD_FAST_PATH` / `KEYWORD_LLM_REVIEW` thresholds.

### Security Scanning

Before any write, `_scan_memory_content()` blocks:
- **Invisible Unicode characters** (zero-width space, BOM, etc.) — prevents injection payloads
- **Threat patterns** — regex detection of prompt injection, system prompt exfiltration, and data leakage attempts

Blocked entries are rejected with an error and never reach disk.

### Fact Cache & Conflict Detection

- **Fact cache** (`.fact_cache.json`): After each write, `_update_fact_cache()` extracts subject-attribute-value triples.
- **Conflict detection** (`_detect_fact_conflict()`): Checks if new content contradicts cached facts (same subject + attribute, different value). If detected, `add()` returns a `fact_conflict` field with old/new values for the agent to review.

### Audit Logging

Every sub-doc write is logged to `.audit.jsonl` (excluded from Git via `.gitignore`):
- Timestamp, target, routed doc name, keyword score
- Used for debugging routing decisions and tracking memory growth

### Fallback Routing

- 0-match entries go to `memory/fallback.md`, keeping MEMORY.md index clean
- During idle compaction, fallback.md entries are re-scored: matched entries move to the correct sub-doc, unmatched ones are promoted back to MEMORY.md as navigation entries
- Keyword audit script scans fallback.md and suggests missing keywords

### Async LLM Review

- **Model:** Any OpenAI-compatible endpoint (configurable)
- **Mode:** Background daemon thread, never blocks the main flow
- **Correction:** If LLM disagrees with keyword result, entry is moved
- **Timeout:** 10 seconds (configurable) — on timeout, keyword result stands

## Environment Variables (v0.14.0 adapter — no LLM classifier needed)

The v0.14.0 adapter uses keyword-only routing (no async LLM review). No environment variables required.

For the legacy keyword-audit and replay scripts:

```bash
# Hermes Agent library path (for scripts to import memory_tool)
export HERMES_AGENT_LIB="/usr/local/lib/hermes-agent"

# LLM classifier endpoint (any OpenAI-compatible API)
export HERMES_MEMORY_CLASSIFIER_URL="http://localhost:11434/v1"
export HERMES_MEMORY_CLASSIFIER_MODEL="Qwen3-4B"
export HERMES_MEMORY_CLASSIFIER_TIMEOUT="30"
```

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HERMES_AGENT_LIB` | Optional | `/usr/local/lib/hermes-agent` | Path to Hermes Agent `tools/` directory |
| `HERMES_MEMORY_CLASSIFIER_URL` | Optional | `http://localhost:11434/v1` | LLM classifier endpoint (OpenAI-compatible) |
| `HERMES_MEMORY_CLASSIFIER_MODEL` | Optional | `your-model` | Model name for async LLM review |
| `HERMES_MEMORY_CLASSIFIER_TIMEOUT` | Optional | `30` | Classifier timeout in seconds |

All variables are optional. Keyword routing (score >= 3) works without any of them. LLM review (score 1-2) requires `CLASSIFIER_URL` and `CLASSIFIER_MODEL`.

## Re-applying After Hermes Update

`hermes update` overwrites `memory_tool.py` (the 2-line hook). After any update:

```bash
# Step 1: Copy standalone module (never overwritten)
cp /root/projects/hermes-memory-routing/src/memory_routing.py \
   /usr/local/lib/hermes-agent/tools/memory_routing.py

# Step 2: Re-apply the patch
cd /usr/local/lib/hermes-agent
patch -p1 < /root/projects/hermes-memory-routing/patches/memory-routing-v0.14.patch

# Step 3: Restart Gateway
systemctl restart hermes-gateway-nova
```

Verify: `grep -n "route_memory_to_sub_docs" tools/memory_tool.py` should show lines 40 and 350.

## Keyword Configuration

Keywords live in `SUB_DOCS` dict inside `tools/memory_routing.py`. Add new sub-docs by inserting a new key:

```python
SUB_DOCS = {
    "<doc_name>": {
        "description": "What this sub-doc stores",
        "keywords": ["kw1", "kw2", "kw3", ...],
    },
}
```

### Tuning Guide

- **Be specific, not broad.** `"vllm"` is better than `"model"` as a keyword.
- **Avoid overlap across sub-docs.** Shared keywords inflate scores for all matching docs.
- **Start with 5-10 keywords per doc**, then iterate based on misclassification.

## File Writing Strategy

| File | Format | Write Method | Dedup |
|------|--------|-------------|-------|
| MEMORY.md | `§`-delimited | Append under lock | Exact match |
| Sub-docs | Pure Markdown | Atomic (tempfile + rename) | Exact match |

## Design Principles

1. **MEMORY.md is an index, not a repository** — stay lean, navigation-level only
2. **Keywords are guardrails, not constraints** — fast classification, LLM as safety net
3. **Async never blocks** — LLM review runs in background, user is unaffected
4. **Atomic writes** — all sub-doc writes use tempfile + atomic replace
5. **Deduplication first** — identical content is rejected before writing

## Testing

```python
from tools.memory_tool import route_content_to_sub_doc

# Returns (doc_name, raw_match_count) — uses V2 scoring internally
doc, score = route_content_to_sub_doc("Content to classify")
print(f"→ {doc} (raw match count: {score})")

# Show detailed V2 scores per doc
from tools.memory_tool import SUB_DOCS
import math

def show_v2_scores(content):
    content_lower = content.lower()
    for doc_name, info in SUB_DOCS.items():
        matched = [kw for kw in info["keywords"] if kw.lower() in content_lower]
        if matched:
            weighted = sum(2.0 if len(k) >= 3 else 1.0 if len(k) >= 2 else 0.5 for k in matched)
            total = len(info["keywords"])
            norm = weighted / math.sqrt(total)
            print(f"  {doc_name:20} weighted={weighted:.1f} norm={norm:.2f}  keywords: {matched}")

show_v2_scores("Content to analyze")
```

## Repo Structure

```
hermes-memory-routing/
├── README.md                    # English version
├── README.zh-CN.md              # 中文版本
├── CHANGELOG.md                 # Version history
├── SKILL.md                     # Hermes Agent Skill document
├── .gitignore                   # Runtime files excluded from Git
├── src/
│   └── memory_routing.py        # Core routing logic (extracted, standalone)
├── scripts/
│   ├── memory-replay.py         # Idle compaction & fallback dedup
│   └── memory-keyword-audit.py  # Keyword coverage audit
└── docs/
    └── design.md                # Architecture design document
```

## Project Identity

Built through human-AI collaboration:

| Role | Contribution |
|------|-------------|
| **Project Lead** | Architecture design, requirements, code review |
| **AI Agent** | Implementation, testing, documentation |

The AI agent runs on the Hermes Agent framework and assists the Project Lead in iterative development.
## Changelog

See [CHANGELOG.md](CHANGELOG.md) for detailed version history.

Latest: **Fallback routing** — 0-match entries route to `memory/fallback.md` instead of polluting MEMORY.md.


## License

MIT
## Upstream Integration

To merge this into Hermes Agent's `memory_tool.py` as an opt-in feature, the key additions are:

### Core Functions

1. **`SUB_DOCS` dict** — Configurable sub-doc definitions (description + keyword list)
2. **`get_memory_sub_docs_dir()`** — Returns the memory sub-documents directory
3. **`route_content_to_sub_doc(content)`** — Keyword scoring: returns `(doc_name, score)`
4. **`classify_content_with_llm(content)`** — Optional LLM classifier (async review)
5. **`_async_llm_review(content, keyword_doc, sub_dir)`** — Background correction thread
6. **`_add_to_sub_doc(doc_name, content)`** — Atomic sub-doc write with deduplication
7. **Modified `add()` method** — Routes content before writing (only when enabled)

### Recommended PR Phases

1. **Phase 1:** Keyword routing + sub-doc write infrastructure (core)
2. **Phase 2:** Optional LLM review (configurable, opt-in)
3. **Phase 3:** `[include:]` directive for explicit sub-doc references in MEMORY.md

### Config Flag

Add to `config.yaml`:
```yaml
memory:
  sub_doc_routing: true        # Enable auto-routing
  classifier_url: "http://localhost:11434/v1"  # Optional LLM endpoint
  classifier_model: "your-model"
```
