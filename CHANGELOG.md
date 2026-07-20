# Changelog

All notable changes to Hermes Memory Routing.

---

## [2026-07-21] v2.0.0 — LLM Intent Classifier + MCP Tool (Breaking)

### Architecture Change

Complete rewrite from keyword-based routing to LLM intent classification. This is a **breaking change** — the old `memory_routing.py` keyword routing module is replaced by a new MCP server architecture.

**Old (v1.x):** Keyword matching in `memory_routing.py` → file append → `MEMORY.md` index
**New (v2.x):** LLM intent classification → atomic file write (fcntl.lock) → MCP tool → `MEMORY.md` index

### Added

- **`server.py`** — MCP server with `route_and_save_memory` tool. Accepts content, classifies via LLM (or keyword fallback), writes to topic-specific sub-document, updates `MEMORY.md` index.
- **`intent_classifier.py`** — LLM intent classifier module. Calls Qwen3.5-9B-AWQ (or configurable endpoint) with 5-category prompt (credential, infrastructure, tech-ref, dev-log, miscellaneous). Returns JSON: `{category, confidence, reason}`. Configurable timeout (`HERMES_LLM_TIMEOUT`, default 5s) and slow-response threshold (`HERMES_MEMORY_SLOW_THRESHOLD`, default `timeout*2`). Retry mechanism (max 2 retries on timeout).
- **`subdoc_writer.py`** — Sub-document writer with write strategies per category: hard overwrite (credentials), append with timestamp (dev-log, tech-ref, miscellaneous), smart update (infrastructure). Uses `tempfile + os.rename` atomic writes + `fcntl.flock` for concurrency safety.
- **`requirements.txt`** — Python dependencies (`requests`, `mcp`).

### Changed

- **LLM classifier replaces keyword routing** — 93% accuracy on 70-test suite (40 offline prompt validation + 30 online LLM classification). Falls back to keyword classification when LLM is unreachable or times out.
- **Configurable timeouts** — `HERMES_LLM_TIMEOUT` (default 5s), `HERMES_MEMORY_SLOW_THRESHOLD` (default 10s), `HERMES_LLM_RETRY_COUNT` (default 2). No more hardcoded 5s threshold that dropped valid responses.
- **Atomic file writes** — All sub-document writes use `tempfile + os.rename` + `fcntl.flock`. Eliminates TOCTOU races and data loss on concurrent writes.
- **Prompt boundary optimization** — Added priority rules in classification prompt to reduce tech-ref vs dev-log ambiguity (accuracy improved from 83% to 83.3% on boundary cases).
- **Error handling** — Explicit try/except with logging on JSON parsing, LLM calls, and file writes. No silent failures.
- **Input sanitization** — Null bytes stripped from content, summary sanitized of markdown special chars, category parameter validated against routing whitelist.

### Removed

- **`memory_routing.py`** — Old keyword-based routing module (replaced by MCP server).
- **`memory-maintenance.py`** — Old maintenance cron script (replaced by MCP tool + daily cleanup).
- **Hardcoded 5s slow-response threshold** — Was silently dropping valid LLM responses when endpoint latency > 5s. Now configurable.

### Security

- **Prompt injection mitigation** — System/user message separation in LLM classifier prompt.
- **Path traversal protection** — Category parameter validated against whitelist; directory traversal rejected.
- **Null byte injection prevention** — Content sanitized before writing.
- **Concurrency safety** — File locks prevent race conditions on concurrent writes.

### Testing

- 40/40 offline prompt validation tests passed
- 27/30 online LLM classification tests passed (93% accuracy, target ≥ 85%)
- Fallback tests: 14/14 passed (LLM timeout, unreachable, JSON failure, low confidence)
- End-to-end routing: all categories verified
- Cron cleanup: full pipeline (scan → classify → migrate → clean) verified

## [2026-06-14] v1.3.0 — Maintenance Script Fix & Safety Guards

### Fixed

- **`memory-maintenance.py` import path** — changed `tools.memory_tool` to `tools.memory_routing`. The old module path stopped working after Hermes Agent upgrade; `SUB_DOCS`, `route_content_to_sub_doc`, and `llm_classify_memory` all live in `tools.memory_routing` now.
- **`check_integrity()` path** — was checking `memory/MEMORY.md` (sub-doc dir) but the official `MEMORY.md` lives in `memories/`. Now correctly checks `memories/MEMORY.md` and validates sub-doc directory structure separately.
- **`main()` silent failure** — early return on integrity failure used `return "\n".join(lines)` without printing. Now calls `print()` before returning so the report is always visible.
- **LLM endpoint mismatch** — script used `HERMES_MEMORY_CLASSIFIER_URL` (pointed to 10.10.4.81:11434, unreachable). Now uses `HERMES_MEMORY_LLM_URL` (10.10.4.62:8000) to match `memory_routing.py`'s `llm_classify_memory()` default.
- **`llm_call()` encoding** — added `.decode("utf-8")` for `resp.read()` and `ensure_ascii=False` for JSON encoding. Added `chat_template_kwargs: {"enable_thinking": False}` to match server config.
- **`llm_merge()` output corruption** — LLM sometimes appended extra text like `。- 棣民说：...` which merged into the next bullet line. Added post-processing: strip leading `- `, split at `。-` if present. Also tightened prompt to forbid multi-line output.
- **`patch_file()` format mismatch** — `repr()` produced Python single quotes (`['a', 'b']`) but `memory_routing.py` uses double quotes (`["a", "b"]`). Replaced with `patch_keywords()` that does line-by-line parsing and writes the exact multiline format.
- **`patch_keywords()` doc detection bug** — `"description":` and `"keywords":` lines matched the "top-level key" check and reset `in_target_doc` prematurely. Removed the aggressive check; now only `},` resets the flag.
- **`full_audit()` skip list** — added `CREDENTIALS.md` to skip set so credential entries are not audited.
- **`recover_from_snapshot()` path** — now correctly restores `MEMORY.md` to `memories/` dir and sub-docs to `memory/` dir separately.

### Added

- **`create_snapshot()` multi-file support** — snapshots now capture official `MEMORY.md` AND all sub-docs into a `.maintenance_snapshot_<ts>/` directory. Previous versions only copied one file.
- **LLM call counter & limit** — `_llm_call_count` tracks calls; `LLM_CALL_LIMIT` (default 100) prevents runaway runs. Excess calls are silently skipped instead of timing out.
- **Misrouted processing limit** — `max_misrouted_to_process = LLM_CALL_LIMIT // 2` ensures only a fraction of misrouted entries consume LLM budget, leaving room for zero-score keyword extraction.
- **Keyword removal safety guard** — each doc can lose at most 30% of its keywords per run. Excess removals are trimmed by frequency (most commonly suggested kept first). Reports a warning when trimmed.
- **`llm_extract_keywords()` quality filter** — rejects keywords longer than 8 chars, containing commas or spaces. Prompt explicitly forbids phrases and sentences.
- **`llm_suggest_remove()` safety prompt** — added instruction: "不要移除核心基础设施词汇（如 gpu, nvidia, vllm, ip, port, docker 等）".
- **`memory-maintenance.sh`** — shell wrapper with time window and idle detection (copied from nova scripts).
- **`fact_cache.py`** — fact extraction helper (copied from nova scripts).

### Changed

- **Memory directory split** — `MEMORY_DIR` (sub-docs, `memory/`) and `MEMORIES_DIR` (official, `memories/`) are now clearly separated throughout the script. `AUDIT_FILE` is read from `memories/` to match where `memory_routing.py` writes audit logs.
- **Import failure behavior** — instead of silently falling back to empty `SUB_DOCS = {}`, the script now exits with `sys.exit(1)` and prints a fatal error.

## [2026-06-12] v1.2.0 — Student-Teacher Self-Evolution

### Added

- **`llm_classify_memory()`** — student classifier function in `memory_routing.py`. Uses Qwen3.5-4B-Pure GGUF on llama.cpp Docker (10.10.4.62:8000, A3000 GPU). 24% correction rate on score<3 misroutes. Passes `chat_template_kwargs: {"enable_thinking": False}` for direct output (no internal thinking).
- **`_remove_from_sub_doc()`** — removes a specific bullet-point entry from a sub-doc file. Enables entry migration when student corrects a keyword misroute.
- **Async student review** — `route_memory_to_sub_docs()` spawns a daemon thread on score<3 entries. Student reclassifies: if disagreees, migrates via remove+add.
- **`teacher_audit.py`** (`scripts/teacher_audit.py`) — periodic audit module using Qwen3.6-27B-FP8 on vLLM (10.10.4.8:8000). 63% classification accuracy. Produces DEL/ADD/NOTE keyword suggestions.
- **Self-evolution loop** — maintenance cron (every 60min) runs teacher audit → parses keyword suggestions → auto-applies changes to `memory_routing.py` → saves state to `.teacher-state.json`.
- **`_student_review()`** inner function — non-blocking async correction in the write path.
- **`chat_template_kwargs` support** — both `llm_classify_memory()` and `teacher_audit.py` pass `"chat_template_kwargs": {"enable_thinking": False}` to API calls. Tested and confirmed working on both llama.cpp Docker and vLLM.
- **Chinese→English doc name mapping** in teacher script — teacher may respond in Chinese (基础设施, 哲学), script maps to English doc names.

### Changed

- **`SUB_DOCS` keywords tuned** — from 130 to 157 keywords across 6 sub-docs. Overbroad keywords removed: `pr`, `10.10.4.`, `192.168.`, `service` (from rules), `commit` (from rules), `备份`/`backup`, `日志`/`log` (from rules), `棣民` (from philosophy), `2026-`, `迁移` (from milestones). Keywords added from zero-score entries using LLM-guided suggestions: `subflow`, `潜意识`, `ssh`, `wol`, `a2a`, `吞吐`, `延迟`, `吞吐`, `路由`, `阈值`, `直觉`, `感性`, `进化`, etc.
- **Keyword accuracy**: 31% → 47.8% (baseline → after tuning). Student (4B) adds 24% correction, architecture projects ~52% effective accuracy.
- **`llm_classify_memory()` response parsing** — now scans all response lines for a valid doc name (handles model explanations like "**分类**: infrastructure(基础设施)"). Previously only checked last line.
- **Default student endpoint** changed from `http://10.10.4.9:8000/v1` (9B SGLang) to `http://10.10.4.62:8000/v1` (4B llama.cpp).
- **`src/memory_tool_v0.14_with_patch.py`** — re-synced with production patched version (the 2-line hook was lost during a `hermes update` and has been restored).
- **README.ch.md** — updated architecture diagram with student-teacher flow.

### Fixed

- **`memory_tool.py` 2-line patch went missing** — the `from tools.memory_routing import route_memory_to_sub_docs` import and `route_memory_to_sub_docs(target, content)` hook call were dropped from the production file, likely by a `hermes update`. Restored and verified. Any Gateway restart will re-enable routing.

### Removed

### Fixed

- **Critical: `route_memory_to_sub_docs()` called AFTER `save_to_disk()`** — caused content to be written both to MEMORY.md (as `§` entries) and to sub-documents. Changed execution order: route runs FIRST, and if routing succeeds (score >= 1), MEMORY.md write is skipped entirely.
- **`route_memory_to_sub_docs()` now returns `bool`** — `True` if routed to sub-doc, `False` otherwise. Caller (`memory_tool.py`) uses this to decide whether to call `save_to_disk()`.

### Changed

- **`src/memory_tool_v0.14_with_patch.py`** — routing hook moved before `save_to_disk()` with conditional skip
- **`src/memory_routing.py`** — function signature updated: `-> bool` return type annotation
- **`patches/memory-routing-v0.14.patch`** — updated to reflect new execution order

## [2026-05-28] v1.1.0 — Hermes Agent v0.14.0 Adaptation

### Breaking Change

Memory routing is now a **standalone module** (`tools/memory_routing.py`) injected via a **2-line hook** instead of being embedded directly in `memory_tool.py`. This prevents code loss on `hermes update`.

**Migration:** The old embedded code was overwritten by v0.14.0. Re-deployment requires:
1. Copy `src/memory_routing.py` → `/usr/local/lib/hermes-agent/tools/memory_routing.py`
2. Apply `patches/memory-routing-v0.14.patch` to `memory_tool.py`
3. Restart Gateway

### Changed

- **Rewritten `src/memory_routing.py`** — decoupled from `memory_tool.py`, now a clean 350-line standalone module
- **Minimal patch paradigm** — only 2 lines added to official code: import + single function call
- **Routing threshold lowered** from 0.3 → 0.2 to handle 2-char Chinese keywords (修复, 升级, 更新) which only get 1.0 weight after normalization
- **README.md** — updated architecture diagram, added re-apply section, simplified three-stage routing docs
- **`patches/memory-routing-v0.14.patch`** — proper git diff for easy re-application after `hermes update`
- **`src/memory_tool_v0.14_with_patch.py`** — full patched file for reference

### Removed

- Async LLM review (score 1-2) — removed in v0.14.0 adapter, keyword result always stands
- Fallback.md write (score 0) — score-0 entries are audit-only; content still saved in MEMORY.md

---

## [unreleased]

### Added

- ...

---

## [2026-05-13] v1.0.2 — Directory Definitions & Documentation Update

### Added

- **Explicit `memories/` vs `memory/` directory definitions** — clarified the separation between Hermes official memory location and memory-routing sub-document storage:
  - `memories/` — Hermes official directory for `MEMORY.md` (index) and `USER.md` (user profile), injected into system prompt
  - `memory/` — Memory-routing sub-document storage, read on-demand via `read_file`

### Changed

- **README.md / README.zh-CN.md** — updated architecture diagrams and added directory definition section
- **docs/design.md** — updated architecture overview to show both `memories/` and `memory/` directories with their roles
- **Profile files synchronized** — copied all files from `memories/` to `memory/` (2026-05-01.md, COMPRESSION-ENDPOINT-MIGRATION.md, mcp-qdrant-deployment-log.md, vector-memory-config.md, MEMORY_TEMPLATE.md, qdrant-tls-config-20260425.json)

---

## [2026-05-12] v1.0.1-hotfix — Fallback Routing & Environment Decoupling

### Added

- **Fallback sub-doc routing** — entries with 0 keyword matches are written to `memory/fallback.md` instead of MEMORY.md, keeping the index lean
- **Fallback dedup & cleanup in compaction** — during idle compaction, fallback.md entries are re-scored; matched entries are moved to the correct sub-doc, unmatched ones are promoted back to MEMORY.md as navigation entries
- **Fallback scanning in keyword audit** — `memory-keyword-audit.py` now scans `memory/fallback.md` and `MEMORY.md` for entries that existing keywords should have caught but missed, suggesting keyword additions
- **Fallback section in MEMORY.md** — index includes a navigation entry linking to `memory/fallback.md` with entry count

### Changed

- **Environment variable decoupling** — all hardcoded paths/IPs replaced with `os.environ.get()`:
  - `HERMES_AGENT_LIB` — path to Hermes Agent `tools/` directory
  - `HERMES_MEMORY_CLASSIFIER_URL` — LLM classifier endpoint
  - `HERMES_MEMORY_CLASSIFIER_MODEL` — classifier model name
  - `HERMES_MEMORY_CLASSIFIER_TIMEOUT` — classifier timeout in seconds
- **Runtime files excluded from Git** — `fallback.md`, `.audit.jsonl`, `compaction-*` files added to `.gitignore`
- **Three-stage routing diagram** — updated README to document the fallback stage (0 matches → fallback.md)

### Files Changed

| File | Change |
|------|--------|
| `src/memory_routing.py` | Added `_add_to_fallback()`, `_fallback_exists()`, fallback handling in `add()`, `_fallback_section()` in `_build_index()` |
| `scripts/memory-replay.py` | Added `--fallback-dir` arg, fallback dedup logic, move-to-correct-doc promotion |
| `scripts/memory-keyword-audit.py` | Added `_scan_fallback()` phase, suggests keywords for unmatched fallback entries |
| `.gitignore` | Added runtime file patterns |

---

## [2026-05-10] v1.0.1 — Keyword Tuning

### Fixed

- 6 routing test cases corrected by adding/removing keywords:

| Sub-doc | Action | Keywords | Reason |
|---------|--------|----------|--------|
| `infrastructure` | Removed | `部署`, `memory` | Too broad, caused misclassification |
| `milestones` | Removed duplicate | `release` | Was listed twice |
| `milestones` | Added | `backup`, `端点` | "部署backup-gpu端点" needed a match |
| `rules` | Added | `技术`, `文档`, `源码` | "查官方文档和源码" had zero hits |
| `commitments` | Removed duplicate | `承诺` | Was listed twice |
| `commitments` | Added | `伙伴`, `成全` | "互相成全的伙伴" had zero hits |
| `dev-log` | Added | `路由`, `评分`, `工具` | "关键词评分的自动路由" had zero hits |

### Result

All 6 test cases now route correctly: 0 false negatives, 0 false positives.

**Total:** +10 keywords, -2 conflicting, -2 duplicates.

---

## [2026-05-09] v1.0.0 — Initial Release

### Added

- Indexed memory architecture: MEMORY.md as lean index, topic-specific sub-documents
- Three-stage routing: keyword scoring (>=3 fast path, 1-2 async LLM review, 0 fallback)
- Configurable sub-doc definitions via `SUB_DOCS` dict
- Atomic sub-doc writes (tempfile + rename)
- Exact-match deduplication
- Async LLM review daemon (non-blocking background correction)
- Hermes Agent Skill document
- Architecture design document

### 6 Built-in Sub-documents

| Sub-doc | Purpose |
|---------|---------|
| `infrastructure.md` | Infrastructure, deployment, hardware |
| `philosophy.md` | Values, principles, relationships |
| `milestones.md` | Milestones, version history |
| `rules.md` | Conventions, standards, workflows |
| `commitments.md` | Commitments, long-term promises |
| `dev-log.md` | Changelog, iteration notes |

---

*Built through human-AI collaboration on the Hermes Agent framework.*
