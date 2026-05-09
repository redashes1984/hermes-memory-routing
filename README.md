# Hermes Memory Routing

> Indexed memory architecture — keep MEMORY.md lean, auto-route content to topic-specific sub-documents.

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
        Write to MEMORY.md (fallback)
```

### Thresholds

| Threshold | Behavior | Latency |
|-----------|----------|---------|
| `>= 3` keyword matches | Fast path: direct write | Zero |
| `1-2` keyword matches | Write + async LLM review | Milliseconds (LLM in background) |
| `0` keyword matches | Write to MEMORY.md | Zero |

### Async LLM Review

- **Model:** Any OpenAI-compatible endpoint (configurable)
- **Mode:** Background daemon thread, never blocks the main flow
- **Correction:** If LLM disagrees with keyword result, entry is moved
- **Timeout:** 10 seconds (configurable) — on timeout, keyword result stands

## Environment Variables

```bash
# LLM classifier endpoint (any OpenAI-compatible API)
export HERMES_MEMORY_CLASSIFIER_URL="http://localhost:11434/v1"
export HERMES_MEMORY_CLASSIFIER_MODEL="your-model-name"
```

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
├── README.md              # This file
├── SKILL.md               # Hermes Agent Skill document
├── src/
│   └── memory_routing.py  # Core routing logic (extracted, standalone)
└── docs/
    └── design.md          # Architecture design document
```

## Project Identity

Built through human-AI collaboration:

| Role | Contribution |
|------|-------------|
| **Project Lead** | Architecture design, requirements, code review |
| **AI Agent** | Implementation, testing, documentation |

The AI agent runs on the Hermes Agent framework and assists the Project Lead in iterative development.

## License

MIT
