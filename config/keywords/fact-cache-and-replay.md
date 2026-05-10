# Fact Cache & Replay — Implementation Notes

## Fact Extraction (fact_cache.py)

### Regex patterns that work

Subject capture MUST use `\S+(?:\s+\S+)?` not `[\w\-\.]+`:

```python
# WRONG - fails on "CT 108 sglang-lxc"
(r'((?:CT|VM|容器)[\w\-\.]+)\s*(?:IP|地址)\s*([\d.]+)', 'ip')

# CORRECT - matches multi-word subjects
(r'((?:CT|VM|容器|宿主机|服务)\s+\S+(?:\s+\S+)?)\s*(?:的)?\s*(?:IP|地址)\s*(?:为|是|改[为成]|→|:)?\s*(\d+\.\d+\.\d+\.\d+(?::\d+)?)', 'ip')
```

Key patterns:
- IP: `CT 108 sglang-lxc 的 IP 是 10.10.4.7` → subject="CT 108 sglang-lxc", value="10.10.4.7"
- Version: `vLLM 0.20.1` or `Qdrant 版本 1.17.1` → two patterns (direct and with "版本" keyword)
- Preference: `棣民偏好分步骤的指导方式` → subject="棣民", value="分步骤的指导方式"
- Philosophy: `棣民认为...` → subject="棣民", value="..."

### Integration point

In `memory_tool.py`, `_detect_fact_conflict()` and `_update_fact_cache()` are called:
1. After every `_add_to_sub_doc()` (sub-doc writes)
2. After every MEMORY.md fallback write (score=0)

Conflict info returned in `result["fact_conflict"]` with keys: subject, attribute, old_value, new_value.

### Initial cache build

Run `python3 fact_cache.py build` once to scan existing sub-docs and seed the cache. On nova profile, this extracted ~3 facts from existing entries.

## Replay (memory-replay.py)

### Design decisions

- **Jaccard similarity threshold: 0.85** (not 0.5 — too aggressive)
- **Both entries < 300 chars** — only merge short entries, long entries are likely distinct enough
- **Replace in-place** — never append merged entries to file end (caused bloated "dumpster" entries)
- **Fact conflict merge** — entries sharing (subject, attribute) with different values are always merged via LLM
- **Structure-preserving** — the script replaces entries at their original line position

### LLM merge prompt

The LLM is asked to:
1. Preserve all factual info, mark value changes with "→"
2. Preserve all key points from each entry
3. Keep output concise (<100 chars Chinese)
4. Output only the merged content, nothing else

Model: Qwen3-4B via llama-cpp at 10.10.4.81:11434, temperature 0.1, max_tokens 200

### Shell wrapper (memory-replay.sh)

Checks if system has been idle > 2 hours before running:
- Reads `.keyword-audit-state.json` for last run time
- Reads `.audit.jsonl` for last write time
- Compares max timestamp against current time
- Only runs replay if idle >= 7200 seconds

### Lessons learned

1. First version used 0.5 overlap threshold and `/min(len)` denominator — matched too many pairs. Changed to Jaccard (intersection/union) at 0.85.
2. First version appended merged entries to end of file — created massive "dumpster" lines that combined everything. Changed to in-place replacement.
3. First version had overlapping conflict groups — same entry could be merged multiple times. Added `_dedup_overlapping()` helper.
4. Infrastructure.md has 89 lines of carefully organized content — the replay must never break the document structure. Test thoroughly.

## Cron scheduling

- Keyword audit: every 30min, but shell wrapper enforces weekday 05-09 / weekend 07-10
- Replay: every 2h, but shell wrapper enforces idle > 2h
- Both cron jobs have `no_agent=false` (default) with `terminal` + `web` toolsets enabled so they can send 飞书 notifications via `send_message`
