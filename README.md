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
MEMORY.md (§-delimited index, injected into system prompt)
│
├── memory/infrastructure.md   — infrastructure, deployment, hardware
├── memory/philosophy.md       — values, principles, relationships
├── memory/milestones.md       — milestones, version history
├── memory/rules.md            — conventions, standards, workflows
├── memory/commitments.md      — commitments, long-term promises
└── memory/dev-log.md          — changelog, iteration notes
```

Sub-doc names and keyword lists are **fully configurable** — no hardcoded categories.

## Three-Stage Routing

```
User: memory_tool.add(target="memory", content="...")
          │
          ▼
   ┌─────────────────┐
   │  Keyword Scoring │  Each sub-doc has a keyword list.
   │  (zero latency)  │  Content scanned → highest score wins.
   └────────┬────────┘
            │
     score ≥ 3? ──Yes──▶ Write directly to sub-doc ✓
            │
           No
            │
      score ≥ 1? ──Yes──▶ Write to keyword result + async LLM review
            │                 (background thread, non-blocking)
           No
            │
        Write to memory/fallback.md (fallback)
```

### Thresholds

| Threshold | Behavior | Latency |
|-----------|----------|---------|
| `>= 3` keyword matches | Fast path: direct write | Zero |
| `1-2` keyword matches | Write + async LLM review | Milliseconds (LLM in background) |
| `0` keyword matches | Write to `memory/fallback.md` | Zero |

### Fallback Routing

- 0-match entries go to `memory/fallback.md`, keeping MEMORY.md index clean
- During idle compaction, fallback.md entries are re-scored: matched entries move to the correct sub-doc, unmatched ones are promoted back to MEMORY.md as navigation entries
- Keyword audit script scans fallback.md and suggests missing keywords

### Async LLM Review

- **Model:** Any OpenAI-compatible endpoint (configurable)
- **Mode:** Background daemon thread, never blocks the main flow
- **Correction:** If LLM disagrees with keyword result, entry is moved
- **Timeout:** 10 seconds (configurable) — on timeout, keyword result stands

## Environment Variables

```bash
# Hermes Agent library path (for scripts to import memory_tool)
export HERMES_AGENT_LIB="/usr/local/lib/hermes-agent"

# LLM classifier endpoint (any OpenAI-compatible API)
export HERMES_MEMORY_CLASSIFIER_URL="http://10.10.4.81:11434/v1"
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

## Keyword Configuration

Keywords live in `SUB_DOCS` dict inside `memory_tool.py`. Add new sub-docs by inserting a new key:

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

doc, score = route_content_to_sub_doc("Content to classify")
print(f"→ {doc} (score: {score})")
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
