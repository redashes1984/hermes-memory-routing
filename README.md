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
└── memory/                      # Sub-documents (routed by memory-routing)
    ├── CREDENTIALS.md          — API keys, passwords, secrets
    ├── infrastructure.md       — IPs, ports, service deployment
    ├── tech-ref.md             — API formats, deployment guides
    ├── dev-log.md              — dev changes, tuning records
    ├── miscellaneous.md        — non-tech catch-all
    └── routing.log             — timestamped routing audit trail
```

**Directory definitions:**
- **`memories/`** — Hermes official directory for `MEMORY.md` (index) and `USER.md` (user profile). These are injected into the system prompt on every session start.
- **`memory/`** — Sub-document storage for memory routing. Topic-specific files are read on-demand via `read_file`, keeping system prompt overhead low.

Sub-doc names and keyword lists are **fully configurable** — no hardcoded categories.

## Architecture (v2.0.1 — LLM Intent Classifier)

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

## Deployment

### One-command install

```bash
# Install to default profile
bash <(curl -sL https://raw.githubusercontent.com/redashes1984/hermes-memory-routing/main/install.sh)

# Install to specific profile
bash <(curl -sL https://raw.githubusercontent.com/redashes1984/hermes-memory-routing/main/install.sh) nova
```

**What the script does:**

1. Clone repo to `~/.hermes/profiles/<name>/plugins/memory-routing/`
2. Install Python dependencies (`mcp[fastmcp]`, `requests`)
3. Auto-detect LLM config from target profile's `model.*` (provider, model, base_url, api_key)
4. Register MCP server in `config.yaml` with resolved environment variables
5. Verify server.py syntax and config entry

**After installation:**

```bash
hermes gateway restart
# or in-session:
# /reload-mcp
```

For full deployment guide (uninstall, troubleshooting, AGENTS.md integration), see [deploy/SKILL.md](deploy/SKILL.md).

## Next Steps (v2.1.0)

1. **Model selection** — Configurable model per classification task (lightweight for fast routing, heavyweight for ambiguous cases)
2. **Thinking mode toggle** — Runtime switch for `reasoning_effort` to enable/disable thinking models on demand

## Testing (v2.0.1)

## Architecture (v1.x — legacy)

## Three-Stage Routing (v1.1.1 — route before save_to_disk)

### Student-Teacher Self-Evolution (v1.2.0 — implemented 2026-06-12)

### Threshold

### V2 Keyword Scoring Algorithm

### Security Scanning

### Fact Cache & Conflict Detection

### Audit Logging

### Fallback Routing

### Async LLM Review

## Environment Variables (v0.14.0 adapter — no LLM classifier needed)

# Hermes Agent library path (for scripts to import memory_tool)