#!/usr/bin/env python3
"""
Memory Maintenance - 记忆维护合一脚本

合并了 memory-keyword-audit.py 和 memory-replay.py 的功能。

执行顺序：
  阶段0: MEMORY.md 完整性检查 + 快照
  阶段1: 记忆复盘（去重/合并/迁移 fallback）
  阶段2: 关键词优化（基于复盘后的干净数据调路由）
  阶段3: 合并报告输出

Schedule: every 60m (由 shell wrapper 控制时间窗口)
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

# ─── Resolve hermes-agent install path ──────────────────────────────────
_hermes_lib = os.environ.get('HERMES_AGENT_LIB', '/usr/local/lib/hermes-agent')
sys.path.insert(0, _hermes_lib)

try:
    from tools.memory_routing import (
        SUB_DOCS, route_content_to_sub_doc,
        llm_classify_memory, get_sub_docs_dir, get_memories_dir,
    )
except Exception as e:
    print(f"[FATAL] 无法导入 tools.memory_routing: {e}")
    sys.exit(1)

from fact_cache import extract_facts, detect_conflicts, load_fact_cache

# memory/ — 私有子文档目录（维护脚本操作对象）
MEMORY_DIR = get_sub_docs_dir()
# memories/ — 官方目录（存放 MEMORY.md, USER.md）
MEMORIES_DIR = get_memories_dir()
AUDIT_FILE = MEMORIES_DIR / ".audit.jsonl"
STATE_FILE = MEMORY_DIR / ".maintenance-state.json"

# 关键词配置存储在 memory_routing.py 中
ROUTING_CONFIG_PATH = Path(_hermes_lib) / "tools" / "memory_routing.py"


# ─── Helpers ────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_run": None, "accuracy_history": []}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# LLM 配置 — 与 memory_routing.llm_classify_memory 保持一致
LLM_BASE_URL = os.environ.get("HERMES_MEMORY_LLM_URL", "http://10.10.4.62:8000/v1").rstrip("/")
LLM_MODEL = os.environ.get("HERMES_MEMORY_LLM_MODEL", "default")
LLM_TIMEOUT = int(os.environ.get("HERMES_MEMORY_LLM_TIMEOUT", "30"))

# LLM 调用计数器（防止单次运行调用过多导致超时）
_llm_call_count = 0
LLM_CALL_LIMIT = int(os.environ.get("MEMORY_MAINTENANCE_LLM_LIMIT", "100"))


def llm_call(prompt: str, max_tokens: int = 200) -> str | None:
    """Unified LLM call helper using memory_routing endpoint."""
    global _llm_call_count
    if _llm_call_count >= LLM_CALL_LIMIT:
        print(f"  [WARN] LLM call limit reached ({LLM_CALL_LIMIT}), skipping remaining")
        return None
    _llm_call_count += 1
    try:
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "chat_template_kwargs": {"enable_thinking": False},
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{LLM_BASE_URL}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  [WARN] LLM call #{_llm_call_count} failed: {e}")
        return None


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


def patch_keywords(doc_name: str, new_keywords: list) -> bool:
    """Replace the keywords list for a given doc in memory_routing.py.

    Strategy: walk lines, find the keywords block for the target doc,
    replace that block with the new keywords formatted in the same style.
    """
    try:
        content = ROUTING_CONFIG_PATH.read_text()
        lines = content.splitlines(True)  # Keep line endings

        # Find the start and end of the keywords block for this doc
        in_target_doc = False
        in_keywords_block = False
        block_start = -1
        block_end = -1
        bracket_depth = 0

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Check if we're entering the target doc
            if f'"{doc_name}":' in line:
                in_target_doc = True
                continue

            if not in_target_doc:
                continue

            # Enter keywords block
            if '"keywords":' in stripped and not in_keywords_block:
                in_keywords_block = True
                block_start = i
                # Check if opening bracket is on same line
                if '[' in stripped:
                    bracket_depth += 1
                continue

            if in_keywords_block:
                bracket_depth += stripped.count('[') - stripped.count(']')
                if bracket_depth <= 0:
                    block_end = i
                    break

            # Reset if we've left this doc without finding keywords
            if stripped == '},':
                in_target_doc = False

        if block_start < 0 or block_end < 0:
            print(f"  [WARN] patch_keywords: could not find keywords block for {doc_name}")
            return False

        # Build replacement lines (same indentation: 12 spaces per kw line, 8 spaces for brackets)
        items_per_line = 4
        new_block_lines = ['        "keywords": [\n']
        for i in range(0, len(new_keywords), items_per_line):
            chunk = new_keywords[i:i + items_per_line]
            formatted = ", ".join(f'"{kw}"' for kw in chunk)
            new_block_lines.append(f'            {formatted},\n')
        new_block_lines.append('        ],\n')

        # Replace the block
        new_lines = lines[:block_start] + new_block_lines + lines[block_end + 1:]
        new_content = ''.join(new_lines)

        # Validate syntax
        compile(new_content, str(ROUTING_CONFIG_PATH), 'exec')

        # Atomic write
        fd, tmp = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp, str(ROUTING_CONFIG_PATH))
        return True
    except Exception as e:
        try:
            os.unlink(tmp)
        except:
            pass
        print(f"  [WARN] patch_keywords failed for {doc_name}: {e}")
        return False


# ─── Stage 0: Integrity Check ──────────────────────────────────────────

def check_integrity() -> tuple:
    """Validate official MEMORY.md and sub-doc structure. Returns (ok, reason)."""
    # 检查官方 MEMORY.md
    md = MEMORIES_DIR / "MEMORY.md"
    if not md.exists():
        return False, "官方 MEMORY.md 不存在"
    content = md.read_text(encoding="utf-8")
    if len(content) < 100:
        return False, f"官方 MEMORY.md 过短 ({len(content)} chars)"

    # 检查子文档目录是否存在且有内容
    if not MEMORY_DIR.exists():
        return False, "子文档目录不存在"
    sub_docs = [f for f in MEMORY_DIR.iterdir() if f.suffix == '.md' and not f.name.startswith('.')]
    if not sub_docs:
        return False, "子文档目录下无任何 .md 文件"

    return True, f"完整性检查通过 (官方 MEMORY.md: {len(content)} chars, 子文档: {len(sub_docs)} 个)"


def create_snapshot() -> str:
    """Snapshot official MEMORY.md and all sub-docs before writes. Returns path or empty."""
    md = MEMORIES_DIR / "MEMORY.md"
    if not md.exists():
        return ""
    try:
        import shutil
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snap_dir = MEMORY_DIR / f".maintenance_snapshot_{ts}"
        snap_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(md), str(snap_dir / "MEMORY.md"))
        # Also snapshot sub-docs
        for sub_doc in MEMORY_DIR.glob("*.md"):
            if not sub_doc.name.startswith('.'):
                shutil.copy2(str(sub_doc), str(snap_dir / sub_doc.name))
        # Keep last 5
        for old in sorted(MEMORY_DIR.glob(".maintenance_snapshot_*"))[:-5]:
            shutil.rmtree(str(old))
        return str(snap_dir)
    except Exception:
        return ""


def recover_from_snapshot() -> bool:
    snaps = sorted(MEMORY_DIR.glob(".maintenance_snapshot_*"))
    if not snaps:
        return False
    try:
        import shutil
        latest = snaps[-1]
        # Restore MEMORY.md
        if (latest / "MEMORY.md").exists():
            shutil.copy2(str(latest / "MEMORY.md"), str(MEMORIES_DIR / "MEMORY.md"))
        # Restore sub-docs
        for sub_doc in latest.glob("*.md"):
            if not sub_doc.name.startswith('.'):
                shutil.copy2(str(sub_doc), str(MEMORY_DIR / sub_doc.name))
        return True
    except Exception:
        return False


# ─── Stage 1: Memory Replay ────────────────────────────────────────────

def read_entries(filename: str) -> List[Tuple[str, int]]:
    """Return [(bullet_content, line_number)]."""
    fp = MEMORY_DIR / filename
    if not fp.exists():
        return []
    entries = []
    for i, line in enumerate(fp.read_text(encoding="utf-8").splitlines()):
        if line.startswith("- "):
            entries.append((line[2:].strip(), i))
    return entries


def replace_entries_in_doc(filename: str, to_replace: Dict[int, Optional[str]]) -> str:
    """Replace/remove entries at given line numbers."""
    fp = MEMORY_DIR / filename
    lines = fp.read_text(encoding="utf-8").splitlines(True)
    result = []
    for i, line in enumerate(lines):
        if i in to_replace:
            new = to_replace[i]
            if new is None:
                continue
            result.append(f"- {new}\n")
        else:
            result.append(line)
    return ''.join(result).rstrip() + "\n"


def _dedup_overlapping(groups: List[List[int]]) -> List[List[int]]:
    groups.sort(key=len, reverse=True)
    used = set()
    result = []
    for g in groups:
        new = [i for i in g if i not in used]
        if new:
            result.append(new)
            used.update(new)
    return result


def find_fact_conflicts(entries: List[Tuple[str, int]]) -> List[List[int]]:
    fact_groups = {}
    for idx, (entry, _) in enumerate(entries):
        for f in extract_facts(entry):
            key = (f["subject"], f["attribute"])
            fact_groups.setdefault(key, []).append((idx, f["value"]))
    conflicts = []
    for key, items in fact_groups.items():
        values = set(v for _, v in items)
        if len(values) > 1:
            indices = sorted(set(i for i, _ in items))
            if len(indices) >= 2:
                conflicts.append(indices)
    return _dedup_overlapping(conflicts)


def find_similar_entries(entries: List[Tuple[str, int]]) -> List[List[int]]:
    texts = [e[0] for e in entries]
    similar = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            wi = set(re.findall(r'[\u4e00-\u9fff]+|\w+', texts[i].lower()))
            wj = set(re.findall(r'[\u4e00-\u9fff]+|\w+', texts[j].lower()))
            if wi and wj:
                union = len(wi | wj)
                if union > 0 and len(wi & wj) / union > 0.85:
                    if len(texts[i]) < 300 and len(texts[j]) < 300:
                        similar.append([i, j])
    return _dedup_overlapping(similar)


def llm_merge(entries: List[str], target_doc: str) -> str | None:
    doc_desc = SUB_DOCS.get(target_doc, {}).get("description", target_doc)
    entries_text = "\n".join(f"  {i+1}. {e}" for i, e in enumerate(entries))
    prompt = f"""你是一个记忆合并专家。将以下条目合并为一条，保留所有核心信息。

文档：{doc_desc}

待合并：
{entries_text}

要求：
1. 保留所有事实性信息，最新值用"→"标记变更
2. 保留所有观点/偏好的要点
3. 简洁中文，100字以内
4. 输出必须是一行纯文本，不要换行、不要以"-"开头、不要包含"。-"这种粘连标记
5. 只输出合并后的内容，不要其他文字"""
    result = llm_call(prompt, max_tokens=200)
    if not result:
        return None
    # Post-process: remove leading "- " prefix and trailing whitespace
    result = result.strip()
    if result.startswith("- "):
        result = result[2:]
    # If result contains "。-", split at last occurrence to prevent line merging artifacts
    if "。-" in result:
        result = result.rsplit("。-", 1)[0].rstrip()
    return result


def run_replay(report_lines: List[str]) -> int:
    """Stage 1: 记忆复盘。返回合并条目数。"""
    if not SUB_DOCS:
        report_lines.append("（无 SUB_DOCS 配置，跳过复盘）")
        return 0

    total_merged = 0

    # 1a: Migrate fallback entries
    fallback_entries = read_entries("fallback.md")
    if fallback_entries:
        report_lines.append(f"\n📋 迁移 fallback: {len(fallback_entries)} 条未分类")
        migrated = 0
        for content, _ in fallback_entries:
            if content.startswith("#"):
                continue
            correct_doc_result = llm_classify_memory(content)
            correct_doc = correct_doc_result[0] if correct_doc_result else None
            if correct_doc and correct_doc in SUB_DOCS:
                # Remove from fallback
                fb = (MEMORY_DIR / "fallback.md").read_text(encoding="utf-8")
                bullet = f"- {content}"
                if bullet in fb:
                    write_sub_doc("fallback.md", fb.replace(bullet, "").strip() + "\n")
                    # Add to target
                    tgt = (MEMORY_DIR / f"{correct_doc}.md").read_text(encoding="utf-8")
                    append = "\n\n- " + content if tgt.strip() else "- " + content
                    write_sub_doc(f"{correct_doc}.md", (tgt + append).strip() + "\n")
                    migrated += 1
                    report_lines.append(f"  → {correct_doc}: {content[:60]}...")
        if migrated:
            report_lines.append(f"  ✅ 迁移 {migrated} 条")

    # 1b: Per-doc conflict merge + dedup
    for doc_name in sorted(SUB_DOCS.keys()):
        fn = f"{doc_name}.md"
        entries = read_entries(fn)
        if not entries:
            continue

        doc_content = (MEMORY_DIR / fn).read_text(encoding="utf-8")
        merged_idx = set()

        # Fact conflicts
        for group in find_fact_conflicts(entries):
            group_entries = [entries[i][0] for i in group]
            merged = llm_merge(group_entries, doc_name)
            if merged:
                to_rep = {entries[group[0]][1]: merged}
                for oi in group[1:]:
                    to_rep[entries[oi][1]] = None
                doc_content = replace_entries_in_doc(fn, to_rep)
                total_merged += len(group) - 1
                merged_idx.update(group)
                report_lines.append(f"  📝 {doc_name}: 冲突合并 {len(group)}→1")

        # Similarity dedup (skip already merged)
        remaining = [(e, l) for i, (e, l) in enumerate(entries) if i not in merged_idx]
        for group in find_similar_entries(remaining):
            group_entries = [entries[i][0] for i in group]
            merged = llm_merge(group_entries, doc_name)
            if merged:
                to_rep = {entries[group[0]][1]: merged}
                for oi in group[1:]:
                    to_rep[entries[oi][1]] = None
                doc_content = replace_entries_in_doc(fn, to_rep)
                total_merged += len(group) - 1
                report_lines.append(f"  🔗 {doc_name}: 去重合并 {len(group)}→1")

        write_sub_doc(fn, doc_content)

    return total_merged


# ─── Stage 2: Keyword Optimization ─────────────────────────────────────

def full_audit() -> dict:
    """Scan all bullets, compute routing accuracy."""
    entries = []
    skip = {"MEMORY.md", "USER.md", "MEMORY_TEMPLATE.md", "CREDENTIALS.md"}
    for fn in sorted(os.listdir(MEMORY_DIR)):
        if not fn.endswith(".md") or fn.startswith(".") or fn in skip:
            continue
        fp = MEMORY_DIR / fn
        for m in re.finditer(r'^- (.+)$', fp.read_text(encoding="utf-8"), re.MULTILINE):
            bullet = m.group(1)
            doc, score = route_content_to_sub_doc(bullet)
            entries.append({
                'file': fn, 'expected': fn.replace('.md', ''),
                'routed_to': doc, 'score': score,
                'correct': doc == fn.replace('.md', ''),
                'content': bullet,
            })
    non_fb = [e for e in entries if not e.get('fallback')]
    total = len(non_fb)
    correct = sum(1 for e in non_fb if e['correct'])
    misrouted = [e for e in non_fb if not e['correct']]
    zero = [e for e in entries if e['score'] == 0]
    return {
        'total': total, 'correct': correct,
        'accuracy': correct / total * 100 if total else 0,
        'zero_score': zero, 'misrouted': misrouted, 'entries': entries,
    }


def llm_extract_keywords(content: str, target_doc: str) -> list:
    doc_desc = SUB_DOCS.get(target_doc, {}).get("description", target_doc)
    prompt = f"""你是一个关键词提取器。

以下记忆条目应路由到 \"{target_doc}\"（{doc_desc}）。

请提取 2-3 个有代表性的关键词。要求：
- 每个关键词必须是 1-4 个字的中文或常见英文术语
- 不要用逗号连接多个词（每个关键词独立）
- 不要用短语或句子
- 只回复关键词，中文顿号分隔

内容：{content[:150]}"""
    text = llm_call(prompt, max_tokens=50)
    if not text:
        return []
    raw = [kw.strip() for kw in text.replace("、", ",").split(",") if kw.strip()]
    # Filter: reject keywords that contain commas, spaces, or are too long
    return [kw for kw in raw if len(kw) <= 8 and "," not in kw and " " not in kw][:5]


def llm_suggest_remove(content: str, wrong_doc: str, correct_doc: str) -> list:
    wrong_kws = SUB_DOCS.get(wrong_doc, {}).get("keywords", [])
    prompt = f"""以下记忆被错误路由到 \"{wrong_doc}\"，应去 \"{correct_doc}\"。

内容：{content[:150]}
{wrong_doc} 的关键词：{wrong_kws}

哪些关键词过于宽泛导致误匹配？回复应移除的关键词，顿号分隔。没有则回复"无"。
注意：不要移除核心基础设施词汇（如 gpu, nvidia, vllm, ip, port, docker 等）。"""
    text = llm_call(prompt, max_tokens=50)
    if not text or text == "无":
        return []
    return [kw.strip() for kw in text.replace("、", ",").split(",") if kw.strip()]


def run_keyword_opt(report_lines: List[str], audit_before: dict) -> bool:
    """Stage 2: 关键词优化。返回是否有改动。"""
    changes = False
    added = {}
    removed = {}

    # Process audit trail low-score entries
    state = load_state()
    last_ts = state.get("last_run")
    recent = []
    if AUDIT_FILE.exists():
        for line in AUDIT_FILE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if last_ts and entry.get("ts", "") <= last_ts:
                    continue
                recent.append(entry)
            except json.JSONDecodeError:
                continue
    low_score = [e for e in recent if e.get("score", 0) < 2]

    if low_score:
        report_lines.append(f"\n📊 审计日志: {len(low_score)} 条低分条目")
        for entry in low_score:
            content = entry.get("content", "")
            current_doc = entry.get("doc", "")
            correct_doc_result = llm_classify_memory(content)
            correct_doc = correct_doc_result[0] if correct_doc_result else None
            if not correct_doc or correct_doc not in SUB_DOCS:
                continue
            kws = llm_extract_keywords(content, correct_doc)
            if kws:
                added.setdefault(correct_doc, []).extend(kws)
            if current_doc != correct_doc and current_doc in SUB_DOCS:
                to_rm = llm_suggest_remove(content, current_doc, correct_doc)
                for kw in to_rm:
                    if kw in SUB_DOCS.get(current_doc, {}).get("keywords", []):
                        removed.setdefault(current_doc, []).append(kw)

    # Also process misrouted from full audit (limit to keep LLM calls manageable)
    max_misrouted_to_process = min(len(audit_before['misrouted']), LLM_CALL_LIMIT // 2)
    for e in audit_before['misrouted'][:max_misrouted_to_process]:
        content = e['content']
        current = e['routed_to']
        expected = e['expected']
        if current and current in SUB_DOCS:
            to_rm = llm_suggest_remove(content, current, expected)
            for kw in to_rm:
                if kw in SUB_DOCS.get(current, {}).get("keywords", []):
                    removed.setdefault(current, []).append(kw)
        if expected in SUB_DOCS:
            kws = llm_extract_keywords(content, expected)
            if kws:
                added.setdefault(expected, []).extend(kws)

    # Deduplicate
    for doc in added:
        existing = set(SUB_DOCS[doc]["keywords"])
        added[doc] = list(dict.fromkeys(kw for kw in added[doc] if kw not in existing))
    for doc in removed:
        removed[doc] = list(set(removed[doc]))
        # Safety: never remove more than 30% of existing keywords for a doc
        original_count = len(SUB_DOCS[doc]["keywords"])
        max_removal = max(1, int(original_count * 0.3))
        if len(removed[doc]) > max_removal:
            # Keep only the first N suggestions (most frequently suggested)
            from collections import Counter
            freq = Counter(removed[doc])
            kept = []
            for kw, cnt in freq.most_common():
                if len(kept) < max_removal:
                    kept.append(kw)
            removed[doc] = kept
            report_lines.append(f"  ⚠️  {doc}: 删除数超限，从 {original_count} 中限制最多删除 {max_removal} 个")

    # Apply patches
    for doc, kws in added.items():
        if not kws:
            continue
        existing = list(SUB_DOCS[doc]["keywords"])
        new = existing + kws
        if patch_keywords(doc, new):
            SUB_DOCS[doc]["keywords"] = new  # Update in-memory copy
            report_lines.append(f"  ✅ {doc}: +{len(kws)} 关键词: {', '.join(kws)}")
            changes = True

    for doc, kws in removed.items():
        if not kws:
            continue
        existing = list(SUB_DOCS[doc]["keywords"])
        new = [kw for kw in existing if kw not in kws]
        if patch_keywords(doc, new):
            SUB_DOCS[doc]["keywords"] = new  # Update in-memory copy
            report_lines.append(f"  🗑️  {doc}: -{len(kws)} 关键词: {', '.join(kws)}")
            changes = True

    return changes


# ─── Main ───────────────────────────────────────────────────────────────

def main():
    lines = ["🧠 记忆维护报告"]
    lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # Stage 0: Integrity
    ok, reason = check_integrity()
    if not ok:
        lines.append(f"⚠️  检查失败: {reason}")
        if recover_from_snapshot():
            ok, _ = check_integrity()
            if ok:
                lines.append("✅ 已从快照恢复")
            else:
                lines.append("❌ 恢复后仍失败，跳过所有阶段")
                print("\n".join(lines))
                return
        else:
            lines.append("❌ 无快照，跳过所有阶段")
            print("\n".join(lines))
            return
    lines.append(f"✅ {reason}")
    create_snapshot()

    # Stage 1: Replay
    lines.append("")
    lines.append("── 阶段1: 记忆复盘 ──")
    merged = run_replay(lines)
    if merged:
        lines.append(f"  合并了 {merged} 条")
    else:
        lines.append("  本轮无需合并")

    # Stage 2: Keyword optimization
    lines.append("")
    lines.append("── 阶段2: 关键词优化 ──")
    audit = full_audit()
    lines.append(f"  准确率: {audit['accuracy']:.0f}% ({audit['correct']}/{audit['total']})")
    if audit['misrouted']:
        lines.append(f"  路由错误: {len(audit['misrouted'])} 条")
    if audit['zero_score']:
        lines.append(f"  零分: {len(audit['zero_score'])} 条")

    changed = run_keyword_opt(lines, audit)
    if not changed and not audit['misrouted']:
        lines.append("  关键词配置良好，无需优化")

    # Stage 3: Summary
    lines.append("")
    lines.append(f"🏁 完成 | 准确率: {audit['accuracy']:.0f}%")

    # Save state
    state = load_state()
    state["last_run"] = datetime.now().isoformat()
    state["accuracy_history"].append({
        "ts": datetime.now().isoformat(),
        "accuracy": audit["accuracy"],
        "merged": merged,
    })
    # Keep last 30 entries
    state["accuracy_history"] = state["accuracy_history"][-30:]
    save_state(state)

    print("\n".join(lines))


if __name__ == "__main__":
    main()
