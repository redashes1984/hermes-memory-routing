#!/usr/bin/env python3
"""
Memory Keyword Auto-Tuning Script

Runs as a cron job to:
1. Read recent audit trail entries
2. Identify misrouted/low-score entries
3. Use LLM to classify them correctly
4. Extract missing keywords
5. Remove overbroad keywords (infrastructure black hole detection)
6. Patch SUB_DOCS on disk
7. Print a summary report (delivered to user via cron notification)

Schedule: Weekdays 05-09, Weekends/Holidays 07-10 (Asia/Shanghai)
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

# Ensure hermes-agent is importable
from tools import memory_tool
import importlib
importlib.reload(memory_tool)

from tools.memory_tool import (
    SUB_DOCS, route_content_to_sub_doc, get_memory_sub_docs_dir, get_memory_dir, classify_content_with_llm
)

PROFILE = "nova"
AUDIT_FILE = get_memory_sub_docs_dir() / ".audit.jsonl"
STATE_FILE = get_memory_sub_docs_dir() / ".keyword-audit-state.json"

# Resolve hermes-agent install path (configurable via HERMES_AGENT_LIB)
_hermes_lib = os.environ.get('HERMES_AGENT_LIB', '/usr/local/lib/hermes-agent')
MEMORY_TOOL_PATH = Path(_hermes_lib) / "tools" / "memory_tool.py"

LLM_BASE_URL = os.environ.get("HERMES_MEMORY_CLASSIFIER_URL", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("HERMES_MEMORY_CLASSIFIER_MODEL", "Qwen3-4B")
LLM_TIMEOUT = int(os.environ.get("HERMES_MEMORY_CLASSIFIER_TIMEOUT", "30"))


def load_state() -> dict:
    """Load last run state."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_run": None, "accuracy_history": []}


def save_state(state: dict):
    """Save state after run."""
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def read_recent_audit() -> list:
    """Read audit entries since last run."""
    state = load_state()
    last_ts = state.get("last_run")
    entries = []
    if not AUDIT_FILE.exists():
        return entries
    for line in AUDIT_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if last_ts and entry.get("ts", "") <= last_ts:
                continue
            entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries


def full_audit() -> dict:
    """Scan every bullet in every sub-doc, compute routing accuracy.
    
    SKIP MEMORY.md — it's the top-level index file, not a sub-document.
    Scanning it would incorrectly treat its navigation lines as memory entries
    and route them to sub-docs, causing catastrophic overwrites.
    (Incident: 2026-05-11/12)
    """
    sub_dir = get_memory_sub_docs_dir()
    entries = []
    for fn in sorted(os.listdir(sub_dir)):
        if not fn.endswith(".md") or fn.startswith("."):
            continue
        # CRITICAL: Skip MEMORY.md — it's an index file, not a sub-doc
        if fn in ("MEMORY.md", "USER.md", "MEMORY_TEMPLATE.md"):
            continue
        fp = sub_dir / fn
        with open(fp) as f:
            data = f.read()
        for match in re.finditer(r'^- (.+)$', data, re.MULTILINE):
            bullet = match.group(1)
            doc, score = route_content_to_sub_doc(bullet)
            expected = fn.replace('.md', '')
            entries.append({
                'file': fn,
                'expected': expected,
                'routed_to': doc,
                'score': score,
                'correct': expected == doc,
                'content': bullet,
            })
    # Also scan fallback.md (excluded from accuracy calculation)
    fallback_fn = "fallback.md"
    fallback_fp = sub_dir / fallback_fn
    if fallback_fp.exists():
        with open(fallback_fp) as f:
            data = f.read()
        for match in re.finditer(r'^- (.+)$', data, re.MULTILINE):
            bullet = match.group(1)
            # Skip header lines that start with '#' (e.g. '# Fallback...')
            if bullet.startswith('#'):
                continue
            doc, score = route_content_to_sub_doc(bullet)
            entries.append({
                'file': fallback_fn,
                'expected': 'fallback',
                'routed_to': doc,
                'score': score,
                'correct': True if score == 0 else False,
                'content': bullet,
                'fallback': True,
            })

    # P3 fix: exclude fallback entries from accuracy calculation
    non_fallback = [e for e in entries if not e.get('fallback')]
    total = len(non_fallback)
    correct = sum(1 for e in non_fallback if e['correct'])
    zero = [e for e in entries if e['score'] == 0]
    misrouted = [e for e in non_fallback if not e['correct']]
    fallback_entries = [e for e in entries if e.get('fallback')]
    fallback_ready = [e for e in fallback_entries if e['score'] > 0]
    return {
        'total': total,
        'correct': correct,
        'accuracy': correct / total * 100 if total else 0,
        'zero_score': zero,
        'misrouted': misrouted,
        'fallback_total': len(fallback_entries),
        'fallback_ready_to_migrate': len(fallback_ready),
        'entries': entries,
    }


def llm_classify(content: str) -> str | None:
    """Use LLM to classify content into correct sub-doc."""
    result = classify_content_with_llm(
        content, base_url=LLM_BASE_URL, model=LLM_MODEL, timeout=LLM_TIMEOUT
    )
    return result


def llm_extract_keywords(content: str, target_doc: str) -> list:
    """Ask LLM to extract 2-3 keywords from content that would help route to target_doc."""
    doc_desc = SUB_DOCS.get(target_doc, {}).get("description", target_doc)
    prompt = f"""你是一个关键词提取器。

以下是一条记忆条目，它应该被路由到 "{target_doc}" 文档（描述：{doc_desc}）。

请从这条内容中提取 2-3 个有代表性的关键词，这些词应该：
1. 能匹配到这条内容的核心主题
2. 不太可能出现在其他不相关的条目中
3. 2-4 个字的中文或常见英文术语

内容：{content}

只回复关键词，用逗号分隔。例如：容器,lxc,启动"""

    try:
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 50,
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
        keywords = [kw.strip() for kw in text.replace("、", ",").split(",") if kw.strip()]
        return keywords[:5]  # Cap at 5
    except Exception:
        return []


def llm_suggest_remove_keywords(content: str, wrong_doc: str, correct_doc: str) -> list:
    """Ask LLM: which keyword in wrong_doc caused this misrouting?"""
    wrong_kws = SUB_DOCS.get(wrong_doc, {}).get("keywords", [])
    prompt = f"""你是一个关键词分析器。

以下记忆条目被错误路由到了 "{wrong_doc}"，它应该去 "{correct_doc}"。

内容：{content}

{wrong_doc} 的关键词列表：{wrong_kws}

请分析：哪些关键词是导致错误路由的"过于宽泛"的关键词？这些词匹配了不应该匹配的内容。

只回复应该移除的关键词，用逗号分隔。如果没有，回复"无"。"""

    try:
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 50,
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
        if text == "无":
            return []
        keywords = [kw.strip() for kw in text.replace("、", ",").split(",") if kw.strip()]
        return keywords
    except Exception:
        return []


def detect_black_hole() -> dict:
    """Detect if any sub-doc has too many keywords, acting as a black hole."""
    # Count how many entries each sub-doc's keywords match across ALL sub-docs
    sub_dir = get_memory_sub_docs_dir()
    all_bullets = []
    for fn in sorted(os.listdir(sub_dir)):
        if not fn.endswith(".md") or fn.startswith("."):
            continue
        # CRITICAL: Skip index files — they're not sub-docs
        if fn in ("MEMORY.md", "USER.md", "MEMORY_TEMPLATE.md"):
            continue
        with open(sub_dir / fn) as f:
            for match in re.finditer(r'^- (.+)$', f.read(), re.MULTILINE):
                all_bullets.append((fn.replace('.md', ''), match.group(1)))

    doc_matches = {}
    for doc_name, info in SUB_DOCS.items():
        matches_across = {}
        for expected, bullet in all_bullets:
            score = sum(1 for kw in info["keywords"] if kw.lower() in bullet.lower())
            if score > 0:
                matches_across[expected] = matches_across.get(expected, 0) + 1
        doc_matches[doc_name] = {
            "keyword_count": len(info["keywords"]),
            "cross_doc_coverage": len(matches_across),
            "details": matches_across,
        }

    return doc_matches


def patch_sub_docs_add(old_string: str, new_string: str) -> bool:
    """Add keywords to a sub-doc entry in memory_tool.py. Uses Python patching."""
    try:
        fd, tmp = tempfile.mkstemp(suffix=".py")
        content = MEMORY_TOOL_PATH.read_text()
        new_content = content.replace(old_string, new_string, 1)
        if new_content == content:
            os.unlink(tmp)
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        # Syntax check
        compile(new_content, str(MEMORY_TOOL_PATH), 'exec')
        os.replace(tmp, str(MEMORY_TOOL_PATH))
        return True
    except Exception as e:
        try:
            os.unlink(tmp)
        except:
            pass
        print(f"PATCH FAILED: {e}")
        return False


def _validate_memory_md_integrity() -> tuple:
    """Validate MEMORY.md index file integrity. Returns (is_valid, reason)."""
    try:
        mem_dir = get_memory_dir()
        memory_path = mem_dir / "MEMORY.md"
        if not memory_path.exists():
            return False, "MEMORY.md 不存在"

        content = memory_path.read_text(encoding="utf-8")
        if len(content) < 500:
            return False, f"文件过短 ({len(content)} chars)"
        if len(content.strip().split("\n")) < 10:
            return False, f"行数过少"

        required = ["## 核心身份", "## 记忆导航"]
        for section in required:
            if section not in content:
                return False, f"缺少必需章节: {section}"

        # Check navigation table references
        nav_docs = sum(1 for doc in SUB_DOCS.keys() if f"{doc}.md" in content)
        if nav_docs < 5:
            return False, f"导航表引用不足 ({nav_docs}/6)"

        # P2-3: check for "last updated" line
        if "最后更新" not in content and "last updated" not in content.lower():
            return False, "缺少'最后更新'行"

        return True, "完整性检查通过"

    except Exception as e:
        return False, f"检查异常: {e}"


def _create_memory_snapshot() -> str:
    """Create a pre-write snapshot of MEMORY.md. Returns snapshot path."""
    mem_dir = get_memory_dir()
    memory_path = mem_dir / "MEMORY.md"
    if not memory_path.exists():
        return ""

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_path = mem_dir / f".audit_snapshot_{ts}"
    try:
        import shutil
        shutil.copy2(str(memory_path), str(snap_path))

        # Keep only last 5 snapshots (P2-2: non-fatal cleanup)
        snaps = sorted(mem_dir.glob(".audit_snapshot_*"))
        for old in snaps[:-5]:
            try:
                old.unlink()
            except OSError:
                pass  # non-fatal cleanup error

        return str(snap_path)
    except Exception:
        return ""


def _recover_memory_from_snapshot() -> bool:
    """Recover MEMORY.md from the most recent audit snapshot."""
    mem_dir = get_memory_dir()
    memory_path = mem_dir / "MEMORY.md"
    snaps = sorted(mem_dir.glob(".audit_snapshot_*"))
    if not snaps:
        return False

    latest = snaps[-1]
    try:
        import shutil
        shutil.copy2(str(latest), str(memory_path))
        # P2-4: verify recovery succeeded
        is_valid, _ = _validate_memory_md_integrity()
        return is_valid
    except Exception:
        return False


def run_optimization() -> str:
    """Main optimization loop. Returns a summary report string."""
    lines = ["🧠 记忆关键词自动优化报告"]
    lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # Pre-flight: Validate MEMORY.md integrity
    is_valid, reason = _validate_memory_md_integrity()
    if not is_valid:
        lines.append(f"⚠️  MEMORY.md 完整性检查失败: {reason}")
        recovered = _recover_memory_from_snapshot()
        if recovered:
            lines.append("✅ 已从快照恢复 MEMORY.md")
            is_valid, reason = _validate_memory_md_integrity()
            if is_valid:
                lines.append("✅ 恢复后验证通过")
        else:
            lines.append("❌ 无可用快照，跳过本次优化")
            return "\n".join(lines)

    lines.append("✅ MEMORY.md 完整性检查通过")
    lines.append("")

    # Create snapshot before any writes
    snapshot_path = _create_memory_snapshot()
    if not snapshot_path:
        lines.append("⚠️  无法创建预写快照，跳过本次优化（安全策略）")
        return "\n".join(lines)

    # Step 1: Baseline audit
    audit_before = full_audit()
    lines.append(f"[优化前] 准确率: {audit_before['accuracy']:.0f}% ({audit_before['correct']}/{audit_before['total']} 条)")
    lines.append(f"  零分: {len(audit_before['zero_score'])}, 路由错误: {len(audit_before['misrouted'])}")

    # Step 2: Detect black hole
    black_hole = detect_black_hole()
    for doc, info in sorted(black_hole.items(), key=lambda x: x[1]["keyword_count"], reverse=True):
        if info["keyword_count"] > 30:
            lines.append(f"  ⚠️  {doc}: {info['keyword_count']} 个关键词, 跨 {info['cross_doc_coverage']} 个文档匹配 (可能黑洞)")

    # Step 3: Process audit trail for low-score entries
    recent = read_recent_audit()
    low_score = [e for e in recent if e.get("score", 0) < 2]

    added_keywords = {}  # doc -> [keywords]
    removed_keywords = {}  # doc -> [keywords]

    if low_score:
        lines.append(f"\n[审计日志] 近期待复盘条目: {len(low_score)} 条 (score < 2)")

        for entry in low_score:
            content = entry.get("content", "")
            current_doc = entry.get("doc", "")
            score = entry.get("score", 0)

            if score == 0:
                # Need LLM to classify
                correct_doc = llm_classify(content)
                if correct_doc and correct_doc in SUB_DOCS:
                    kws = llm_extract_keywords(content, correct_doc)
                    if kws:
                        added_keywords.setdefault(correct_doc, []).extend(kws)
            else:
                # Borderline - check if misrouted
                correct_doc = llm_classify(content)
                if correct_doc and correct_doc != current_doc and correct_doc in SUB_DOCS:
                    # Extract keywords for correct doc
                    kws = llm_extract_keywords(content, correct_doc)
                    if kws:
                        added_keywords.setdefault(correct_doc, []).extend(kws)
                    # Check which keywords in wrong doc caused misrouting
                    if current_doc in SUB_DOCS:
                        to_remove = llm_suggest_remove_keywords(content, current_doc, correct_doc)
                        for kw in to_remove:
                            if kw in SUB_DOCS.get(current_doc, {}).get("keywords", []):
                                removed_keywords.setdefault(current_doc, []).append(kw)

    # Step 4: Also scan full audit misrouted entries
    for e in audit_before['misrouted']:
        content = e['content']
        current_doc = e['routed_to']
        correct_doc = e['expected']

        if current_doc and current_doc in SUB_DOCS:
            # Check if this is a black hole effect
            wrong_kws = [kw for kw in SUB_DOCS[current_doc]["keywords"] if kw.lower() in content.lower()]
            if wrong_kws and current_doc != correct_doc:
                to_remove = llm_suggest_remove_keywords(content, current_doc, correct_doc)
                for kw in to_remove:
                    if kw in SUB_DOCS.get(current_doc, {}).get("keywords", []):
                        removed_keywords.setdefault(current_doc, []).append(kw)

        if correct_doc in SUB_DOCS:
            kws = llm_extract_keywords(content, correct_doc)
            if kws:
                added_keywords.setdefault(correct_doc, []).extend(kws)

    # Step 5: Deduplicate and filter keywords
    for doc in added_keywords:
        existing = set(SUB_DOCS[doc]["keywords"])
        added_keywords[doc] = [kw for kw in added_keywords[doc] if kw not in existing]
        added_keywords[doc] = list(dict.fromkeys(added_keywords[doc]))  # Preserve order, dedupe

    for doc in removed_keywords:
        removed_keywords[doc] = list(set(removed_keywords[doc]))

    # Step 6: Apply patches
    changes_made = False
    for doc, kws in added_keywords.items():
        if not kws:
            continue
        existing = SUB_DOCS[doc]["keywords"]
        # Find the last keyword line to append after
        new_kws = existing + kws
        # Build the patch string
        kw_str_old = repr(existing)
        kw_str_new = repr(new_kws)
        if patch_sub_docs_add(kw_str_old, kw_str_new):
            lines.append(f"  ✅ {doc}: 新增 {len(kws)} 个关键词: {', '.join(kws)}")
            changes_made = True

    for doc, kws in removed_keywords.items():
        if not kws:
            continue
        existing = SUB_DOCS[doc]["keywords"]
        new_kws = [kw for kw in existing if kw not in kws]
        kw_str_old = repr(existing)
        kw_str_new = repr(new_kws)
        if patch_sub_docs_add(kw_str_old, kw_str_new):
            lines.append(f"  🗑️  {doc}: 移除 {len(kws)} 个关键词: {', '.join(kws)}")
            changes_made = True

    if not changes_made:
        lines.append("\n本次无需优化，关键词配置已足够准确。")

    # Step 7: Reload and verify
    if changes_made:
        importlib.reload(memory_tool)
        audit_after = full_audit()
        lines.append(f"\n[优化后] 准确率: {audit_after['accuracy']:.0f}% ({audit_after['correct']}/{audit_after['total']} 条)")
        lines.append(f"  零分: {len(audit_after['zero_score'])}, 路由错误: {len(audit_after['misrouted'])}")
        delta = audit_after['accuracy'] - audit_before['accuracy']
        if delta > 0:
            lines.append(f"  📈 提升: +{delta:.1f}%")
        elif delta < 0:
            lines.append(f"  📉 下降: {delta:.1f}%")
        else:
            lines.append(f"  ➖ 无变化")
    else:
        audit_after = audit_before

    # Step 8: Update state
    state = load_state()
    state["last_run"] = datetime.now().isoformat()
    state["accuracy_history"].append({
        "ts": datetime.now().isoformat(),
        "accuracy": audit_after["accuracy"],
        "total": audit_after["total"],
        "added": {k: len(v) for k, v in added_keywords.items()},
        "removed": {k: len(v) for k, v in removed_keywords.items()},
    })
    # Keep only last 30 entries
    state["accuracy_history"] = state["accuracy_history"][-30:]
    save_state(state)

    return "\n".join(lines)


if __name__ == "__main__":
    report = run_optimization()
    print(report)
