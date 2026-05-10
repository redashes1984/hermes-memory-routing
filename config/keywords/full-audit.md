# Full Content Audit — Keyword Tuning at Scale

The test-case method (see `keyword-tuning.md`) works for targeted fixes. When you need a **comprehensive picture** of routing health across all memory entries, use the full content audit.

## When to Use

- Initial assessment of routing system health
- After major keyword changes — verify nothing broke
- When you suspect widespread misrouting (many entries in wrong sub-docs)
- Periodic maintenance (recommended monthly)

## Step 1: Full Scan — Score Every Entry

```python
import sys, re, os
sys.path.insert(0, '/usr/local/lib/hermes-agent')
import importlib
from tools import memory_tool
importlib.reload(memory_tool)
from tools.memory_tool import SUB_DOCS, route_content_to_sub_doc

sub_dir = os.path.expanduser('~/.hermes/profiles/nova/memory')
entries = []
for fn in sorted(os.listdir(sub_dir)):
    fp = os.path.join(sub_dir, fn)
    with open(fp) as f:
        data = f.read()
    for match in re.finditer(r'^- (.+)$', data, re.MULTILINE):
        bullet = match.group(1)
        doc, score = route_content_to_sub_doc(bullet)
        expected = fn.replace('.md', '')
        entries.append({
            'file': fn, 'expected': expected,
            'routed_to': doc, 'score': score,
            'correct': expected == doc, 'content': bullet
        })

total = len(entries)
correct = sum(1 for e in entries if e['correct'])
zero = sum(1 for e in entries if e['score'] == 0)
borderline = sum(1 for e in entries if 1 <= e['score'] < 3)
fast = sum(1 for e in entries if e['score'] >= 3)

print(f"Total: {total}, Correct: {correct} ({correct/total*100:.0f}%)")
print(f"  Zero (fallback): {zero} ({zero/total*100:.0f}%)")
print(f"  Borderline (1-2): {borderline} ({borderline/total*100:.0f}%)")
print(f"  Fast (>=3): {fast} ({fast/total*100:.0f}%)")

# Per-file accuracy
for fn in sorted(os.listdir(sub_dir)):
    fe = [e for e in entries if e['file'] == fn]
    c = sum(1 for e in fe if e['correct'])
    t = len(fe)
    print(f"  {fn:25} {c:>3}/{t} ({c/t*100:.0f}%)")
```

## Step 2: Isolate Zero-Score Entries by Source

Group zero-score entries by which file they came from. This tells you which sub-doc needs the most keyword attention.

```python
zero = [e for e in entries if e['score'] == 0]
by_file = {}
for e in zero:
    by_file.setdefault(e['file'], []).append(e)

for fn, items in by_file.items():
    print(f"\n{fn} ({len(items)} zero-score):")
    for item in items[:5]:
        print(f"  {item['content'][:80]}")
```

**Interpretation:** If `infrastructure.md` has 22 zero-score entries, it means 22 of its bullet points contain no keywords from the infrastructure list. Those entries are being sent to MEMORY.md instead — that's a keyword coverage gap.

## Step 3: Extract Missing Keywords

For each zero-score entry, identify the key terms that should have matched:

```python
from collections import defaultdict

by_expected = defaultdict(list)
for e in zero:
    by_expected[e['file'].replace('.md', '')].append(e)

for expected, items in by_expected.items():
    all_text = ' '.join([e['content'].lower() for e in items])
    print(f"\n{expected}: scan content for recurring terms")
    for item in items[:3]:
        print(f"  {item['content'][:70]}")
```

**How to extract keywords manually:**
- Look at the zero-score entries and identify 3-5 terms per entry that are specific to that domain
- Batch them by sub-doc — you'll often see recurring terms (e.g., "密码", "密钥", "凭证" for infrastructure)
- Add them all at once — this is more efficient than one-at-a-time

## Step 4: Validate Before Patching

After proposing keyword additions, verify they don't create conflicts:

```python
for doc, kws in additions.items():
    existing = SUB_DOCS[doc]['keywords']
    for kw in kws:
        if kw in existing:
            print(f"  SKIP: {kw} already in {doc}")
        for other_doc, other_info in SUB_DOCS.items():
            if other_doc != doc and kw in other_info['keywords']:
                print(f"  CONFLICT: '{kw}' in both {doc} and {other_doc}")
```

## Step 5: Patch Disk and Re-Audit

After patching `memory_tool.py`, reload and re-run the full audit:

```python
import importlib
from tools import memory_tool
importlib.reload(memory_tool)
from tools.memory_tool import route_content_to_sub_doc

# Re-run Step 1 to measure improvement
```

## Key Metrics

| Metric | Meaning | Target |
|--------|---------|--------|
| Overall accuracy | % of entries routed to correct sub-doc | >90% (nova profile: 95%) |
| Zero-score count | Entries falling back to MEMORY.md | 0 |
| Borderline accuracy | Of 1-2 score entries, how many are correct | >80% |
| Per-file accuracy | Each sub-doc's routing precision | >75% (all nova docs hit 60-100%) |

## Add-Remove-Add Cycle (Proven)

The full tuning process follows a predictable arc:

| Phase | Action | Expected Accuracy |
|-------|--------|-------------------|
| Baseline | Initial keyword set | 30-40% |
| Phase 1 | Batch-add keywords for zero-score | 70-80% |
| Phase 2 | Remove overbroad keywords | 90-95% |
| Phase 3 | Targeted surgical add | 95%+ |

**Critical:** Without Phase 2 (removing overbroad keywords), accuracy plateaus at ~80%. The bottleneck is 2-3 generic keywords stealing scores across multiple sub-docs. Removing them is more impactful than adding new ones.

**Rule of thumb:** A keyword that appears as a substring in 3+ different sub-docs' content is too broad. Remove it.

## Session Example: Nova Memory Audit (2026-05-10)

Before:
- Total: 112 entries, Correct: 44 (39%)
- Zero-score: 46 (41%)
- Borderline: 59 (53% accurate)

Intermediate (after batch add):
- Total: 106 entries, Correct: 85 (80%)
- Zero-score: 0 (0%)
- Borderline: 83 (75% accurate)

Final (after removing overbroad + targeted add):
- Total: 106 entries, Correct: 101 (95%)
- Zero-score: 0 (0%)
- Borderline misrouted: 1 (designed for LLM async review)
- Fast path (>=3): 23 (22%)

Changes applied (5 phases):
1. Change #1: Conflict fix — removed 2 duplicate keywords, resolved 2 cross-doc conflicts
2. Change #2: Full audit baseline — identified 46 zero-score entries
3. Change #3: Batch keyword addition — +58 keywords across 5 sub-docs
4. Change #4: Zero-score elimination — +17 keywords for remaining 10 zero-score entries
5. Change #5: Overbroad removal — removed 6 (pr, 时间, 节奏, ai, memory, gateway) + added 17 targeted

Final keyword counts: infrastructure 60, philosophy 51, milestones 19, rules 27, commitments 28, dev-log 30 = 215 total.

Result: 39% → 95% accuracy, 41% → 0% fallback rate. 1 borderline misroute remaining (acceptable).

## Common Patterns

1. **Infrastructure tends to have the most zero-score entries** — it's the largest sub-doc with the most varied content (IPs, paths, processes, configs).
2. **Philosophy zero-score entries are often quotes/idioms** — they don't contain technical keywords. Add idiom-specific terms.
3. **Commitments zero-score entries are often action-oriented** — they describe behaviors rather than abstract values. Add action verbs.
4. **Dev-log zero-score entries are often implementation details** — they use code/technical terms not in the keyword list.
