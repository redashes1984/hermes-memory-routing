# Keyword Tuning Methodology

When memory routing accuracy degrades (entries land in wrong sub-docs or fall back to MEMORY.md), use this process to diagnose and fix.

## Step 1: Run Test Cases

Test with representative content strings from actual sub-documents:

```python
import sys
sys.path.insert(0, '/usr/local/lib/hermes-agent')
from tools.memory_tool import route_content_to_sub_doc

tests = [
    ("vLLM 推理端点 GPU 空闲关机策略", "infrastructure"),
    ("AI 应该自己决定该记得什么", "philosophy"),
    ("遇技术问题查官方文档和源码", "rules"),
    ("部署 backup-gpu 后备推理端点", "milestones"),
    ("棣民是互相成全的伙伴", "commitments"),
    ("memory_tool.py 关键词评分路由实现", "dev-log"),
]

for content, expected in tests:
    doc, score = route_content_to_sub_doc(content)
    status = "✓" if doc == expected else "✗"
    print(f"  {status} '{content}' → {doc} ({score}) [期望: {expected}]")
```

## Step 2: Analyze Failures

For each failing case, find WHY it failed:

```python
from tools.memory_tool import SUB_DOCS

def analyze(content):
    content_lower = content.lower()
    for doc_name, info in SUB_DOCS.items():
        matched = [kw for kw in info['keywords'] if kw.lower() in content_lower]
        if matched:
            print(f"  {doc_name:20} hit {len(matched)}: {matched}")
    # Show expected doc's keywords
    print(f"\n  Expected keywords check...")

analyze("your failing content here")
```

Common failure modes:
- **Zero hits** — no keyword matches at all → add missing keywords
- **Wrong doc wins** — another sub-doc has more matching keywords → remove conflicting keyword or add stronger keyword to expected doc
- **Tie** — two docs match equally → make keywords more specific

## Step 3: Check Keyword Conflicts

```python
from tools.memory_tool import SUB_DOCS

all_kw = {}
for doc_name, info in SUB_DOCS.items():
    for kw in info['keywords']:
        kw_lower = kw.lower()
        if kw_lower not in all_kw:
            all_kw[kw_lower] = []
        all_kw[kw_lower].append(doc_name)

overlaps = {kw: docs for kw, docs in all_kw.items() if len(docs) > 1}
for kw, docs in sorted(overlaps.items()):
    print(f"  '{kw}' → {', '.join(docs)}")

# Also check for duplicates within same doc
for doc_name, info in SUB_DOCS.items():
    from collections import Counter
    kw_counts = Counter(kw.lower() for kw in info['keywords'])
    dups = {kw: count for kw, count in kw_counts.items() if count > 1}
    if dups:
        print(f"  {doc_name} has duplicates: {dups}")
```

## Step 4: Check Unused Keywords

Keywords that never match actual content are dead weight:

```python
import os
from tools.memory_tool import SUB_DOCS

sub_dir = os.path.expanduser('~/.hermes/profiles/nova/memory')
mem_path = os.path.expanduser('~/.hermes/profiles/nova/memories/MEMORY.md')

all_content = ""
for fn in os.listdir(sub_dir):
    with open(os.path.join(sub_dir, fn)) as f:
        all_content += f.read()
with open(mem_path) as f:
    all_content += f.read()

content_lower = all_content.lower()
for doc_name, info in SUB_DOCS.items():
    unused = [kw for kw in info['keywords'] if kw.lower() not in content_lower]
    if unused:
        print(f"  {doc_name} unused: {unused}")
```

## Step 5: Propose Changes

Show the user:
- Which keywords to ADD (and why, based on actual content)
- Which keywords to REMOVE (too broad, causes conflicts)
- Which keywords to DEDUPLICATE (listed twice in same doc)

Always get confirmation before patching `SUB_DOCS` in `memory_tool.py`.

## Step 6: Verify

Re-run all test cases after patching:
- Previously failing cases should now pass
- Previously passing cases should still pass (no regressions)

```python
# Reload module after patch
import importlib
import tools.memory_tool
importlib.reload(tools.memory_tool)
from tools.memory_tool import route_content_to_sub_doc

# Re-run all tests...
```

## Key Principles

1. **Prefer specific over broad.** `vllm` > `model`, `qdrant` > `memory`
2. **One keyword, one sub-doc.** Avoid cross-doc overlap.
3. **Keywords should reflect actual content.** Don't add keywords for hypothetical future content — add them when you see real misclassifications.
4. **Scale with content, not against it.** 10-20 keywords per doc is a good starting point, but large sub-docs (infrastructure can have 50+ entries) may need 40-55+ keywords. Use the full audit to find the right number.
5. **Test before and after.** Always validate with representative strings.
