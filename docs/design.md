# Memory Routing — Architecture Design

## Motivation

The default Hermes Agent memory tool writes all entries into MEMORY.md and USER.md, which are injected verbatim into the system prompt. As memory grows, three problems emerge:

1. **Token waste** — Every session loads all memory into the prompt, even entries irrelevant to the current task.
2. **Char limit** — MEMORY.md has a hard limit (default 2200 chars). Once exceeded, new entries are rejected.
3. **Noise** — The model sees outdated or unrelated entries, reducing precision.

The solution: split memory into an **always-loaded index** and **on-demand sub-documents**.

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│  profiles/<name>/memories/   (Hermes official)           │
│                                                           │
│  System Prompt Injection (every session start)           │
│  ┌─────────────────────────────────────────────────┐     │
│  │ MEMORY.md (index only, §-delimited)             │     │
│  │ ┌─────────────────────────────────────────────┐ │     │
│  │ │ Core identity + nav table                   │ │     │
│  │ │ (first § block, ~2200 chars max)            │ │     │
│  │ └─────────────────────────────────────────────┘ │     │
│  └─────────────────────────────────────────────────┘     │
│  ┌─────────────────────────────────────────────────┐     │
│  │ USER.md (user profile, §-delimited)             │     │
│  └─────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│  profiles/<name>/memory/   (sub-documents, on-demand)    │
│                                                           │
│  On-Demand Read (read_file tool)                         │
│  ┌─────────────────────────────────────────────────┐     │
│  │ infrastructure.md  — infrastructure, hardware   │     │
│  │ philosophy.md      — values, principles         │     │
│  │ milestones.md      — key dates, versions        │     │
│  │ rules.md           — conventions, standards     │     │
│  │ commitments.md     — promises, long-term        │     │
│  │ dev-log.md         — changelog, iterations      │     │
│  │                                     ...         │     │
│  │ (No char limit, pure markdown)                  │     │
│  └─────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────┘
```

## § Delimiter Mechanism

MEMORY.md uses the section sign `§` as an entry delimiter. The system prompt injection splits on `§` and injects only the first block (the index). This means:

- **Index (block 0):** Core identity, navigation table — always loaded
- **Legacy entries (blocks 1+):** Previously written before routing was enabled — kept for backward compatibility but not injected

Sub-documents do NOT use `§` — they are pure markdown files read via `read_file` on demand.

## Routing Pipeline

```
                    ┌──────────────┐
                    │  New Entry   │
                    └──────┬───────┘
                           │
                           ▼
                  ┌────────────────┐
                  │ Keyword Scorer │  O(n*m): scan content against
                  │ (zero latency) │  all keywords in SUB_DOCS
                  └───────┬────────┘
                          │
                   score  │
                   ≥ 3?   │
                 Yes ┌────┴────┐
                     │         │
                     ▼         No
              ┌─────────────┐   │
              │ Fast Path   │   │
              │ Direct write│   │
              │ to sub-doc  │   │
              └─────────────┘   │
                                │
                         score  │
                         ≥ 1?   │
                        Yes ┌───┴───┐
                            │       │
                            ▼       No
                     ┌──────────────┐
                     │ LLM Review   │
                     │ Path         │
                     │              │
                     │ 1. Write to  │
                     │    keyword   │
                     │    result    │
                     │ 2. Spawn     │
                     │    async LLM │───►  If disagree:
                     │    thread    │         move entry
                     └──────────────┘
                                │
                                ▼
                     ┌──────────────┐
                     │ Fallback     │
                     │ Write to     │
                     │ fallback.md  │
                     │ + async LLM  │───►  If classified:
                     │   classify   │         migrate to sub-doc
                     └──────────────┘
```

## Threshold Design

| Threshold | Keyword Matches | Behavior | Rationale |
|-----------|----------------|----------|-----------|
| Fast Path | >= 3 | Direct write to sub-doc | High confidence, no need for LLM |
| LLM Review | 1-2 | Write + async LLM review | Ambiguous — use LLM as safety net |
| Fallback | 0 | Write to `fallback.md` + async LLM classify | No sub-doc matches — LLM tries to classify immediately |

**Why 3 for fast path?** With 3 keyword matches, the probability of misclassification is very low. A single keyword match is unreliable (common words like "memory" or "配置" may appear in many contexts).

## V2 Keyword Scoring Algorithm

The scoring engine (V2, 2026-05-10) is not a simple keyword count. It uses four phases:

**Phase 1 — Raw matching:** Scan content against all keywords in every sub-doc's keyword list.

**Phase 2 — Conflict resolution & weighting:**
- Shared keywords (appearing in multiple sub-docs) are awarded to the doc with the smallest keyword list (most specific).
- Keyword weights by length: >= 3 chars = 2.0 (strong), >= 2 chars = 1.0 (medium), 1 char = 0.5 (weak).

**Phase 3 — Normalization & specificity bonus:**
- Score = weighted_score / sqrt(total_keywords_in_doc) — penalizes docs with large keyword lists.
- Specificity bonus = `log1p((weighted / total_keywords) * 10) * 0.3` — rewards docs where matched keywords represent a large fraction of their list.

**Phase 4 — Decision thresholds (on normalized score):**
- >= 0.6: confident match, return doc name
- >= 0.3: tentative match, return doc name
- < 0.3: no match, return None (triggers fallback)

Raw match count is still returned for backward compatibility with `KEYWORD_FAST_PATH` / `KEYWORD_LLM_REVIEW` thresholds.

## Security Scanning

Before any content is written, `_scan_memory_content()` checks for:
- **Invisible Unicode characters** (zero-width space, BOM, etc.) — blocks possible injection payloads
- **Threat patterns** — regex patterns detecting prompt injection, system prompt exfiltration, or data leakage attempts

Blocked entries are rejected with an error message and never reach disk.

## Fact Cache & Conflict Detection

- **Fact cache** (`.fact_cache.json`): After each write, `_update_fact_cache()` extracts subject-attribute-value triples from the content and stores them in a local cache.
- **Conflict detection** (`_detect_fact_conflict()`): Before writing, the system checks if the new content contradicts a cached fact (same subject + attribute, different value). If a conflict is found, the `add()` response includes a `fact_conflict` field with old/new values for the agent to review.

## Audit Logging

Every sub-doc write is logged to `.audit.jsonl` (excluded from Git via `.gitignore`):
- Timestamp, target (memory/user), routed doc name, keyword score, and the content hash
- Used for debugging routing decisions and tracking memory growth patterns

## Async LLM Review

### Why Async?

The LLM review must not block the user-facing `add()` call. Users expect instant confirmation, not a 2-second wait for the classifier.

### Implementation

```python
if KEYWORD_LLM_REVIEW <= score < KEYWORD_FAST_PATH:
    threading.Thread(
        target=async_llm_review,
        args=(content, routed_doc, sub_dir),
        daemon=True,  # Dies with parent process
    ).start()
```

### Correction Flow

1. LLM receives content + all sub-doc descriptions
2. LLM returns the doc name it thinks is correct
3. If LLM agrees with keyword → no action
4. If LLM disagrees → read source file, remove entry, append to target file
5. If LLM fails or returns "none" → keep keyword classification

### Limitations

- **Daemon threads die with the process.** In short-lived scripts, async review never completes. This is acceptable — the keyword result is already written. In long-lived processes (Gateway), async review works correctly.
- **No retry.** If the LLM endpoint is down, the keyword result stands.

## Atomic Writes

All sub-doc writes use a tempfile + atomic replace pattern:

```python
fd, tmp_path = tempfile.mkstemp(dir=str(sub_dir), suffix=".tmp")
with os.fdopen(fd, "w") as f:
    f.write(new_text)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp_path, str(sub_path))  # Atomic on POSIX
```

This ensures sub-docs are never in a partial/invalid state, even if the process crashes mid-write.

## Deduplication

Before writing, the system checks if the exact content already exists:

- **MEMORY.md:** Check if `content` is in the existing entries list
- **Sub-docs:** Check if `content.strip()` is a substring of the file

This prevents accidental duplicates from repeated memory tool calls.

## Configuration

### Keyword Configuration

Sub-doc names and keywords are defined in `SUB_DOCS` dict:

```python
SUB_DOCS = {
    "<name>": {
        "description": "Description for the LLM classifier prompt",
        "keywords": ["list", "of", "keywords"],
    },
}
```

### LLM Classifier Configuration

```bash
# Hermes Agent library path (for scripts)
export HERMES_AGENT_LIB="/usr/local/lib/hermes-agent"

# LLM classifier (optional — keyword routing works without it)
export HERMES_MEMORY_CLASSIFIER_URL="http://localhost:11434/v1"
export HERMES_MEMORY_CLASSIFIER_MODEL="Qwen3-4B"
export HERMES_MEMORY_CLASSIFIER_TIMEOUT="30"
```

| Variable | Default | Used By |
|---|---|---|
| `HERMES_AGENT_LIB` | `/usr/local/lib/hermes-agent` | scripts import path |
| `HERMES_MEMORY_CLASSIFIER_URL` | `http://localhost:11434/v1` | LLM async review |
| `HERMES_MEMORY_CLASSIFIER_MODEL` | `your-model` | LLM async review |
| `HERMES_MEMORY_CLASSIFIER_TIMEOUT` | `30` (seconds) | LLM async review |

The classifier uses a minimal prompt (10 tokens max, temperature 0.0) for speed and determinism.

## Extensibility

### Adding a New Sub-Doc

1. Add entry to `SUB_DOCS` dict
2. Create the markdown file in `memory/`
3. Update the navigation table in MEMORY.md

No code changes required.

### Custom Thresholds

Adjust `KEYWORD_FAST_PATH` and `KEYWORD_LLM_REVIEW` constants in `memory_tool.py`:

```python
KEYWORD_FAST_PATH = 3   # Increase for stricter fast path
KEYWORD_LLM_REVIEW = 1  # Set to 2 to skip LLM review entirely
```

### Disabling LLM Review

Set `KEYWORD_LLM_REVIEW = 3` (same as fast path) — all matches go directly to sub-docs with no LLM involvement.

## Trade-offs

| Aspect | Decision | Trade-off |
|--------|----------|-----------|
| Keyword vs LLM-first | Keywords first | Faster, but less accurate for ambiguous content |
| Async LLM | Daemon thread | Non-blocking, but may die in short-lived processes |
| § delimiter | MEMORY.md only | Simple splitting, but sub-docs need separate reads |
| Char limit | Only on MEMORY.md | Sub-docs can grow unbounded |
| Dedup | Exact match only | Won't catch rephrased duplicates |

## Future Work

- **Semantic dedup:** Use embeddings to detect near-duplicate entries
- **Auto-pruning:** Archive old sub-doc entries that exceed a size threshold
- **Hierarchical routing:** Sub-docs can have their own sub-routes
- **Bulk reclassification:** Re-scan all MEMORY.md entries and route them to sub-docs
