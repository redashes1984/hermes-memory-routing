# Changelog

All notable changes to Hermes Memory Routing.

---

## [unreleased] — Fallback Routing & Environment Decoupling

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
