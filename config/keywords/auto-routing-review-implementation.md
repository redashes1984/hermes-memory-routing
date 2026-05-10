# Auto Routing Review — Implementation Notes (2026-05-10)

## Design Decisions (User: 棣民)

### Core principle
"先记录再优化、先增量再减量" — Record first, optimize later; incremental additions before reductions.

### Schedule
- Weekdays (Mon-Fri): 05:00-09:00
- Weekends/Holidays: 07:00-10:00
- Timezone: Asia/Shanghai

### Keyword optimization
- Auto-execute (not wait for confirmation)
- Report results via Telegram/Feishu
- Statistical approach preferred over purely LLM-driven

### Content review fact-check
- Ask user first
- Auto-update after 2h no-response timeout

### Scope
- Routing accuracy = tool/mechanism
- Content quality (timeliness, dedup, structure) = goal
- Both are targets, not conflicting

## Implementation

### 1. Audit trail (memory_tool.py)

Added:
- `_audit_trail_path()` → `~/.hermes/profiles/nova/memory/.audit.jsonl`
- `_log_audit(target, doc_name, score, content)` → append JSONL line
- Hooked into `MemoryStore.add()`: called after every successful write (sub-doc and MEMORY.md paths)
- Non-blocking: audit failure never blocks memory writes

### 2. Keyword auto-tuning script

Location: `~/.hermes/profiles/nova/scripts/memory-keyword-audit.py`

Functions:
- `read_recent_audit()` — reads `.audit.jsonl`, filters since last run (state in `.keyword-audit-state.json`)
- `full_audit()` — scans all sub-doc bullets, computes accuracy
- `detect_black_hole()` — finds sub-docs with >35 keywords or cross-doc coverage >3
- `llm_classify()` / `llm_extract_keywords()` / `llm_suggest_remove_keywords()` — LLM-assisted fixes
- `run_optimization()` — main loop: baseline → black hole detection → keyword add/remove → verify → report

State file: `.keyword-audit-state.json` tracks `last_run` timestamp and `accuracy_history` (last 30 runs).

Patch method: directly patches `SUB_DOCS` keywords list in `memory_tool.py` via `tempfile + compile() + os.replace()`.

### 3. Pending: idle-time memory review

Not yet implemented. Planned tasks:
- Timeliness: verify facts against live state (PVE container list, service configs)
- Dedup: fact-triple extraction + embedding similarity
- Restructure: topic clustering, section headers, recency sort

## Cron job setup

NOT YET CREATED. Need to add to root crontab:

```cron
# Memory keyword auto-tuning
# Weekdays 05:00-09:00, Weekends 07:00-10:00
0 5-9 * * 1-5 /usr/bin/python3 ~/.hermes/profiles/nova/scripts/memory-keyword-audit.py >> /var/log/memory-keyword-audit.log 2>&1
0 7-10 * * 0,6 /usr/bin/python3 ~/.hermes/profiles/nova/scripts/memory-keyword-audit.py >> /var/log/memory-keyword-audit.log 2>&1
```

Notification delivery (Telegram/Feishu) also pending — needs integration with existing platform.

## What's done vs pending

| Item | Status |
|------|--------|
| Audit trail in memory_tool.py | ✅ Done |
| Keyword audit script | ✅ Created, syntax-checked |
| Cron job | ⏳ Pending |
| Telegram/Feishu notification | ⏳ Pending |
| Idle-time content review | ⏳ Planned (after keyword layer stable) |
| Fact-change detection (write-time replace vs add) | ⏳ Planned |
