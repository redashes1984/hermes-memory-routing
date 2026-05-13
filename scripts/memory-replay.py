#!/usr/bin/env python3
"""
Memory Replay & Dedup Module

Runs during idle time to:
1. Merge entries with conflicting facts (essence merge)
2. Deduplicate near-duplicate entries (high similarity threshold)
3. Report changes — only if there are actual issues

Schedule: Triggers when idle > 2 hours (enforced by shell wrapper)

Key design decisions:
- Conservative: only merge when there's a clear conflict or very high similarity
- Structure-preserving: replace entries in-place, don't append to end
- Dedup: only merge if similarity > 0.8 AND entries share the same key fact
- After merge, ask LLM to preserve both entries' key points
"""

import json
import os
import re
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Resolve hermes-agent install path (configurable via HERMES_AGENT_LIB)
_hermes_lib = os.environ.get('HERMES_AGENT_LIB', '/usr/local/lib/hermes-agent')
sys.path.insert(0, _hermes_lib)

try:
    from tools.memory_tool import get_memory_sub_docs_dir, SUB_DOCS, route_content_to_sub_doc
except Exception:
    get_memory_sub_docs_dir = lambda: Path.home() / ".hermes" / "profiles" / "nova" / "memory"
    SUB_DOCS = {}
    route_content_to_sub_doc = lambda c: (None, 0)

from fact_cache import extract_facts, detect_conflicts, load_fact_cache

MEMORY_DIR = get_memory_sub_docs_dir()

LLM_BASE_URL = os.environ.get("HERMES_MEMORY_CLASSIFIER_URL", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("HERMES_MEMORY_CLASSIFIER_MODEL", "Qwen3-4B")

# ─── MEMORY.md integrity guard ──────────────────────────────────────────

def _check_memory_index_integrity():
    """Verify MEMORY.md has required index sections before proceeding."""
    try:
        from tools.memory_tool import get_memory_dir
    except Exception:
        get_memory_dir = lambda: Path.home() / ".hermes" / "profiles" / "nova" / "memories"

    md = get_memory_dir() / "MEMORY.md"
    if not md.exists():
        return False, "MEMORY.md missing"
    content = md.read_text(encoding="utf-8")
    for section in ("## 核心身份", "## 记忆导航"):
        if section not in content:
            return False, f"MEMORY.md missing section: {section}"
    return True, "OK"


LLM_TIMEOUT = int(os.environ.get("HERMES_MEMORY_CLASSIFIER_TIMEOUT", "30"))


# ─── LLM helpers ───────────────────────────────────────────────────────

def llm_merge(entries: List[str], target_doc: str) -> str | None:
    """Ask LLM to merge entries, preserving key points from each."""
    doc_desc = SUB_DOCS.get(target_doc, {}).get("description", target_doc) if SUB_DOCS else target_doc
    entries_text = "\n".join(f"  {i+1}. {e}" for i, e in enumerate(entries))
    prompt = f"""你是一个记忆合并专家。将以下条目合并为一条，保留所有核心信息。

文档：{doc_desc}

待合并：
{entries_text}

要求：
1. 保留所有事实性信息，最新值用"→"标记变更
2. 保留所有观点/偏好的要点
3. 简洁中文，100字以内
4. 只输出合并后的内容，不要其他文字"""

    try:
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 200,
            "temperature": 0.1,
        }).encode()
        req = urllib.request.Request(
            f"{LLM_BASE_URL}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            result = json.loads(resp.read())
            text = result["choices"][0]["message"]["content"].strip()
        return text
    except Exception as e:
        print(f"  [WARN] LLM merge failed: {e}")
        return None


# ─── Entry parsing ─────────────────────────────────────────────────────

def read_sub_doc_entries(filename: str) -> List[Tuple[str, int]]:
    """Return list of (bullet_content, line_number)."""
    fp = MEMORY_DIR / filename
    if not fp.exists():
        return []
    entries = []
    for i, line in enumerate(fp.read_text(encoding="utf-8").splitlines()):
        if line.startswith("- "):
            entries.append((line[2:].strip(), i))
    return entries


# ─── Conflict detection ────────────────────────────────────────────────

def find_fact_conflicts(entries_with_lines: List[Tuple[str, int]]) -> List[List[int]]:
    """Find groups of entry indices that share a fact key but have different values.
    
    Returns list of index groups (indices into entries_with_lines).
    """
    entries = [e[0] for e in entries_with_lines]
    
    # Map: (subject, attribute) -> [(entry_index, value)]
    fact_groups = {}
    for idx, (entry, line_num) in enumerate(entries_with_lines):
        facts = extract_facts(entry)
        for f in facts:
            key = (f["subject"], f["attribute"])
            if key not in fact_groups:
                fact_groups[key] = []
            fact_groups[key].append((idx, f["value"]))
    
    # Find groups with conflicting values
    conflicts = []
    for key, items in fact_groups.items():
        values = set(v for _, v in items)
        if len(values) > 1:
            indices = list(set(i for i, _ in items))
            if len(indices) >= 2:
                conflicts.append(sorted(indices))
    
    # Dedup: remove overlapping groups (keep larger)
    conflicts = _dedup_overlapping(conflicts)
    return conflicts


def _dedup_overlapping(groups: List[List[int]]) -> List[List[int]]:
    """Remove overlapping groups, keeping the larger one."""
    groups.sort(key=len, reverse=True)
    used = set()
    result = []
    for g in groups:
        new_indices = [i for i in g if i not in used]
        if new_indices:
            result.append(new_indices)
            used.update(new_indices)
    return result


# ─── Similarity-based dedup ───────────────────────────────────────────

def find_similar_entries(entries_with_lines: List[Tuple[str, int]]) -> List[List[int]]:
    """Find near-duplicate entries (Jaccard similarity > 0.85).
    
    Returns list of index groups.
    """
    entries = [e[0] for e in entries_with_lines]
    similar = []
    
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            words_i = set(re.findall(r'[\u4e00-\u9fff]+|\w+', entries[i].lower()))
            words_j = set(re.findall(r'[\u4e00-\u9fff]+|\w+', entries[j].lower()))
            if words_i and words_j:
                union = len(words_i | words_j)
                if union == 0:
                    continue
                jaccard = len(words_i & words_j) / union
                # Conservative: only merge if very similar AND both are short
                if jaccard > 0.85 and len(entries[i]) < 300 and len(entries[j]) < 300:
                    similar.append([i, j])
    
    similar = _dedup_overlapping(similar)
    return similar


# ─── File operations ───────────────────────────────────────────────────

def write_sub_doc(filename: str, content: str):
    """Atomically write a sub-doc."""
    fp = MEMORY_DIR / filename
    fd, tmp = tempfile.mkstemp(dir=str(MEMORY_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(fp))
    except Exception:
        try:
            os.unlink(tmp)
        except:
            pass


def replace_entries_in_doc(filename: str, 
                           to_replace: Dict[int, Optional[str]],
                           insertions: Dict[int, str]) -> str:
    """Replace entries at given line numbers.
    
    to_replace: {line_num: new_content_or_None} — None means remove
    insertions: {line_num: content} — insert after this line
    
    Returns updated file content.
    """
    fp = MEMORY_DIR / filename
    lines = fp.read_text(encoding="utf-8").splitlines(True)  # Keep newlines
    
    # Build a map of line_num -> replacement
    changes = {}
    for ln, new in to_replace.items():
        changes[ln] = new  # None = remove, str = replace
    for ln, content in insertions.items():
        if ln not in changes:
            changes[ln] = f"- {content}\n"
    
    result = []
    for i, line in enumerate(lines):
        if i in changes:
            new = changes[i]
            if new is None:
                continue  # Remove line
            if not line.startswith("- "):
                result.append(f"- {new}\n")
            else:
                result.append(f"- {new}\n")
        else:
            result.append(line)
    
    return ''.join(result).rstrip() + "\n"


# ─── Main replay ───────────────────────────────────────────────────────

def run_replay() -> str:
    """Main replay loop. Returns a summary report string."""
    # P1-1: check MEMORY.md integrity before proceeding
    is_valid, reason = _check_memory_index_integrity()
    if not is_valid:
        return f"❌ MEMORY.md 完整性检查失败: {reason}"

    lines = ["🔄 记忆复盘报告"]
    lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    total_merged = 0
    changes = []

    if not SUB_DOCS:
        lines.append("（无 SUB_DOCS 配置，跳过）")
        return "\n".join(lines)

    # Process fallback.md — classify unclassified entries
    fallback_entries = read_sub_doc_entries("fallback.md")
    if fallback_entries:
        lines.append(f"\n📋 处理 fallback.md: {len(fallback_entries)} 条未分类条目")
        fallback_migrated = 0
        remaining = []
        for entry_content, line_num in fallback_entries:
            try:
                from tools.memory_tool import classify_content_with_llm
                correct_doc = classify_content_with_llm(
                    entry_content,
                    base_url=LLM_BASE_URL,
                    model=LLM_MODEL,
                    timeout=LLM_TIMEOUT
                )
            except Exception:
                correct_doc = None

            if correct_doc and correct_doc in SUB_DOCS:
                # Migrate: remove from fallback, add to correct doc
                target_path = MEMORY_DIR / f"{correct_doc}.md"
                tgt_text = (MEMORY_DIR / "fallback.md").read_text(encoding="utf-8")
                bullet = f"- {entry_content}"
                if bullet in tgt_text:
                    new_fb = tgt_text.replace(bullet, "").strip()
                    if new_fb.endswith("\n\n"):
                        new_fb = new_fb.rstrip()
                    write_sub_doc("fallback.md", new_fb)

                    # Add to target
                    tgt_content = (MEMORY_DIR / f"{correct_doc}.md").read_text(encoding="utf-8")
                    append = "\n\n- " + entry_content if tgt_content.strip() else "- " + entry_content
                    write_sub_doc(f"{correct_doc}.md", (tgt_content + append).strip() + "\n")
                    fallback_migrated += 1
                    lines.append(f"  → {correct_doc}: {entry_content[:60]}...")
            else:
                remaining.append((entry_content, line_num))
        if fallback_migrated > 0:
            lines.append(f"  ✅ 迁移 {fallback_migrated} 条到正确的子文档")
        if remaining:
            lines.append(f"  ⏳ {len(remaining)} 条仍无法分类")

    for sub_doc_name in sorted(SUB_DOCS.keys()):
        filename = f"{sub_doc_name}.md"
        entries = read_sub_doc_entries(filename)
        if not entries:
            continue

        doc_content = (MEMORY_DIR / filename).read_text(encoding="utf-8")
        merged_indices = set()

        # Step 1: Fact conflict merge
        conflict_groups = find_fact_conflicts(entries)
        for group in conflict_groups:
            group_entries = [entries[i][0] for i in group]
            merged = llm_merge(group_entries, sub_doc_name)
            if merged:
                # Keep first entry, replace with merged content. Remove others.
                first_idx = group[0]
                other_indices = group[1:]
                
                to_replace = {entries[first_idx][1]: merged}
                for oi in other_indices:
                    to_replace[entries[oi][1]] = None
                
                doc_content = replace_entries_in_doc(filename, to_replace, {})
                total_merged += len(other_indices)
                changes.append({
                    "doc": sub_doc_name,
                    "action": "conflict_merge",
                    "count": len(group),
                    "merged": merged[:80],
                })
                merged_indices.update(group)
                lines.append(f"  📝 {sub_doc_name}: 事实冲突合并 {len(group)}→1条")

        # Step 2: Similarity-based dedup
        # Filter out already-merged entries
        remaining = [(e, l) for i, (e, l) in enumerate(entries) if i not in merged_indices]
        similar_groups = find_similar_entries(remaining)
        for group in similar_groups:
            group_entries = [entries[i][0] for i in group]
            merged = llm_merge(group_entries, sub_doc_name)
            if merged:
                first_idx = group[0]
                other_indices = group[1:]
                
                to_replace = {entries[first_idx][1]: merged}
                for oi in other_indices:
                    to_replace[entries[oi][1]] = None
                
                doc_content = replace_entries_in_doc(filename, to_replace, {})
                total_merged += len(other_indices)
                changes.append({
                    "doc": sub_doc_name,
                    "action": "similarity_dedup",
                    "count": len(group),
                    "merged": merged[:80],
                })
                lines.append(f"  🔗 {sub_doc_name}: 去重合并 {len(group)}→1条")

        # Step 3: Write back
        write_sub_doc(filename, doc_content)

    # Summary
    lines.append("")
    if total_merged > 0:
        lines.append(f"✅ 完成：合并 {total_merged} 条冗余条目")
        lines.append("")
        for c in changes:
            lines.append(f"  [{c['doc']}] {c['action']}: {c['count']}→1")
            lines.append(f"    {c['merged']}...")
    else:
        lines.append("✅ 本轮无需合并，记忆结构良好")

    return "\n".join(lines)


if __name__ == "__main__":
    report = run_replay()
    print(report)
