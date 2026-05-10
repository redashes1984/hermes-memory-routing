# Memory Maintenance Pitfalls

Lessons learned during the 2026-05-10 implementation of the three-layer memory maintenance system.

## Replay Script: Aggressive Merging Bug

**Symptom:** `memory-replay.py` merged 15+ infrastructure.md entries in one run, producing bloated "melting pot" entries that concatenated unrelated facts.

**Root cause:**
1. Jaccard similarity threshold was 0.5 — too permissive. Infrastructure entries share common terms (IP, 容器, PVE, 向量记忆), causing most pairs to score >0.5
2. No length guard — long entries with many keywords matched each other
3. Appended merged results to end of file instead of in-place replacement, creating duplicates

**Fix:**
- Threshold raised to 0.85 (only truly near-duplicate entries)
- Added length guard: both entries must be <300 chars (long entries are usually distinct enough)
- In-place replacement: replace first entry with merged content, remove others at their original positions
- Dedup overlapping groups to prevent cascading merges

**Recovery command:**
```bash
cd ~/.hermes/profiles/nova && git checkout HEAD -- memory/infrastructure.md
```

**Rule:** Replay should be CONSERVATIVE. When in doubt, don't merge. A corrupted sub-doc is worse than having redundant entries.

## Fact Cache: Regex Pattern Matching

**Symptom:** IP fact pattern didn't extract facts from entries like "CT 108 sglang-lxc 的 IP 是 10.10.4.7"

**Root cause:** Subject capture used `[\w\-\.]+` which matches a single word. "CT 108 sglang-lxc" has spaces between tokens, so the pattern only captured "CT" and failed to match the rest.

**Fix:** Use `\S+(?:\s+\S+)?` to capture one or two space-separated tokens:
```python
# WRONG
r'((?:CT|VM|容器)[\w\-\.]+)...'

# CORRECT
r'((?:CT|VM|容器)\s+\S+(?:\s+\S+)?)...'
```

**General rule:** Chinese technical text often has multi-word identifiers (CT 108 sglang-lxc, 容器 CT 102 GPU-TestEnv). Subject capture patterns must handle spaces.

## Cron Job: Conditional Notification Pattern

**Problem:** Cron jobs running every 30 minutes would spam 飞书 with "nothing to do" messages.

**Solution:** The cron prompt checks for a specific marker string in the script output before sending:
```
步骤：
1. 运行：bash memory-keyword-audit.sh
2. 如果脚本输出了非空内容（包含"🧠 记忆关键词自动优化报告"），
   用 send_message 将完整输出发送到 feishu:oc_...
3. 如果脚本输出为空、只有空白或错误信息，什么都不做（静默退出）
```

The script itself only prints when there's actual work done. The cron agent checks for the marker string as a second guard.

## Keyword Wrapper: Time Window Enforcement

Cron expressions can't encode "weekdays 05-09 / weekends 07-10". Solution: shell wrapper checks `date +%u` and `date +%-H` before running the Python script. The cron runs every 30 minutes, the wrapper silently exits outside the window.

```bash
DAY=$(date +%u)
HOUR=$(date +%-H)
if [ "$DAY" -ge 1 ] && [ "$DAY" -le 5 ]; then
    # Weekday: 05-09
    if [ "$HOUR" -ge 5 ] && [ "$HOUR" -lt 9 ]; then
        python3 ~/.hermes/.../memory-keyword-audit.py
    fi
else
    # Weekend: 07-10
    if [ "$HOUR" -ge 7 ] && [ "$HOUR" -lt 10 ]; then
        python3 ~/.hermes/.../memory-keyword-audit.py
    fi
fi
```

## Sub-doc File Corruption Recovery

Always keep sub-docs under git version control. If replay or any script corrupts a sub-doc:
```bash
cd ~/.hermes/profiles/nova && git checkout HEAD -- memory/<file>.md
```

The backup cron pushes to git regularly, so recovery is usually within the last few hours.
