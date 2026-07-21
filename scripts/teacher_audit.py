#!/usr/bin/env python3
"""
Teacher Audit Module — Qwen3.6-27B reviews student (9B) classifications.

Architecture:
  Teacher (Qwen3.6-27B on 10.10.4.8:8000) periodically audits:
    1. Random sample from audit trail → reclassify
    2. Systematic error detection → keyword gap analysis
    3. Guidance output → keyword changes + pattern notes

Usage:
  python3 teacher_audit.py           # single run
  python3 teacher_audit.py --full    # audit all entries (slow)
"""

import json
import os
import random
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# ── Config ──
TEACHER_URL = os.environ.get("HERMES_MEMORY_TEACHER_URL",
                             "http://10.10.4.8:8000/v1/chat/completions")
TEACHER_MODEL = os.environ.get("HERMES_MEMORY_TEACHER_MODEL", "Qwen3.6-27B-FP8")
HERMES_AGENT_LIB = os.environ.get("HERMES_AGENT_LIB", "/usr/local/lib/hermes-agent")
SAMPLE_SIZE = int(os.environ.get("HERMES_MEMORY_TEACHER_SAMPLE", "30"))

sys.path.insert(0, HERMES_AGENT_LIB)
from tools.memory_routing import SUB_DOCS, route_content_to_sub_doc

PROFILE = "nova"
MEMORY_DIR = Path(os.path.expanduser(f"~/.hermes/profiles/{PROFILE}/memory"))
AUDIT_FILE = Path(os.path.expanduser(
    f"~/.hermes/profiles/{PROFILE}/memory/.audit.jsonl"))
STATE_FILE = Path(os.path.expanduser(
    f"~/.hermes/profiles/{PROFILE}/memory/.teacher-state.json"))

DOC_NAMES = sorted(SUB_DOCS.keys())

# Chinese → English doc name mapping (teacher may respond in Chinese)
DOC_CN_MAP = {
    "基础设施": "infrastructure",
    "哲学": "philosophy",
    "里程碑": "milestones",
    "规则": "rules",
    "承诺": "commitments",
    "开发日志": "dev-log",
}


# ── Teacher API ──

def teacher_classify(content: str) -> Tuple[Optional[str], float]:
    """Ask the teacher (Qwen3.6-27B) to classify a single entry."""
    prompt = f"""你是记忆分类器的质量审计员。以下记忆条目应该分类到哪个子文档？

子文档：
- **commitments**: 对棣民的承诺、陪伴、成长、尊重、守护
- **dev-log**: 开发文档与日志、新功能开发、代码变动
- **infrastructure**: 基础设施、部署、硬件、网络、推理后端、PVE
- **milestones**: 关键里程碑、版本历史、部署日期、升级
- **philosophy**: 核心哲学、AI记忆自主权、放手、关系本质
- **rules**: 技术排查原则、Skill编写规范、工作习惯

内容：{content[:300]}

只回复文档名（{', '.join(DOC_NAMES)}）："""

    payload = json.dumps({
        "model": TEACHER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 20,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }, ensure_ascii=False).encode("utf-8")

    try:
        req = urllib.request.Request(TEACHER_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        text = result["choices"][0]["message"]["content"].strip().lower()
        text = re.sub(r'^[\d]+[\.\)\-]\s*', '', text).strip('"').strip()

        # Try Chinese mapping first
        if text in DOC_CN_MAP:
            return DOC_CN_MAP[text], 1.0
        if text in DOC_NAMES:
            return text, 1.0
        for cn, en in DOC_CN_MAP.items():
            if cn in text:
                return en, 0.5
        for dn in DOC_NAMES:
            if dn in text:
                return dn, 0.5
        return None, 0.0
    except Exception as e:
        print(f"  Teacher API error: {e}")
        return None, 0.0


def teacher_analyze_patterns(errors: List[Dict]) -> str:
    """Ask teacher to analyze misrouting patterns and suggest keyword changes."""
    error_summary = "\n".join(
        f"- keyword → [{e['kw_pred']}], teacher → [{e['teacher_pred']}] "
        f"(should be [{e['expected']}]): {e['content'][:100]}"
        for e in errors[:15]
    )

    current_kws = "\n".join(
        f"- {doc}: {', '.join(SUB_DOCS[doc]['keywords'][:15])}..."
        for doc in DOC_NAMES
    )

    prompt = f"""你是关键词调优专家。以下记录了记忆路由系统的错分模式。

当前关键词：
{current_kws}

错分条目（关键词→路由结果，老师→正确结果）：
{error_summary}

请分析：
1. 哪些关键词导致最多错分？（如 "pr" 匹配了 "Pruning"）
2. 应该删除哪些过泛关键词？
3. 应该新增哪些关键词来覆盖遗漏的条目？
4. 每个文档建议调整什么？

回复格式（每行一个操作）：
DEL <doc> <keyword>     # 删除过泛关键词
ADD <doc> <keyword>      # 新增关键词
NOTE <说明>              # 分析说明"""

    payload = json.dumps({
        "model": TEACHER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 600,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }, ensure_ascii=False).encode("utf-8")

    try:
        req = urllib.request.Request(TEACHER_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  Teacher analysis error: {e}")
        return ""


# ── Entry collection ──

def collect_entries() -> List[Dict]:
    """Collect all memory entries from sub-docs."""
    entries = []
    for fn in sorted(MEMORY_DIR.iterdir()):
        if not fn.suffix == '.md' or fn.name.startswith('.') or fn.name == 'CREDENTIALS.md':
            continue
        expected = fn.stem
        if expected not in SUB_DOCS:
            continue
        data = fn.read_text(encoding="utf-8")
        for m in re.finditer(r'^- (.+)$', data, re.MULTILINE):
            content = m.group(1)
            kw_doc, kw_score = route_content_to_sub_doc(content.lower())
            entries.append({
                'expected': expected,
                'kw_pred': kw_doc,
                'kw_score': kw_score,
                'content': content,
            })
    return entries


# ── Main audit ──

def run_audit(full: bool = False):
    entries = collect_entries()
    print(f"总条目: {len(entries)}")

    # Sample
    if full:
        sample = entries
    else:
        random.seed(int(time.time()))
        sample = random.sample(entries, min(SAMPLE_SIZE, len(entries)))

    print(f"审计样本: {len(sample)} 条 (老师: {TEACHER_MODEL})")

    # Keyword baseline
    kw_correct = sum(1 for e in sample if e['kw_pred'] == e['expected'])
    print(f"关键词精度: {kw_correct}/{len(sample)} ({kw_correct/len(sample)*100:.0f}%)")

    # Teacher reclassification
    teacher_correct = 0
    errors = []
    start = time.time()

    for i, e in enumerate(sample):
        t_doc, t_conf = teacher_classify(e['content'])
        if t_doc == e['expected']:
            teacher_correct += 1
        elif t_doc is not None:
            errors.append({**e, 'teacher_pred': t_doc, 'teacher_conf': t_conf})

        if (i + 1) % 10 == 0:
            elapsed = time.time() - start
            print(f"  {i+1}/{len(sample)} teacher={teacher_correct} "
                  f"errors={len(errors)} [{elapsed:.0f}s]")
        time.sleep(0.3)

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"老师精度: {teacher_correct}/{len(sample)} "
          f"({teacher_correct/len(sample)*100:.0f}%)  [{elapsed:.0f}s]")
    print(f"关键词:   {kw_correct}/{len(sample)} "
          f"({kw_correct/len(sample)*100:.0f}%)")
    print(f"错分分析样本: {len(errors)} 条")
    print(f"{'='*60}")

    # Save state
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        'last_audit': time.strftime("%Y-%m-%dT%H:%M:%S"),
        'sample_size': len(sample),
        'teacher_accuracy': round(teacher_correct / len(sample) * 100, 1),
        'keyword_accuracy': round(kw_correct / len(sample) * 100, 1),
        'error_count': len(errors),
    }, indent=2), encoding="utf-8")

    # Teacher pattern analysis
    if errors:
        print("\n老师分析错分模式...")
        analysis = teacher_analyze_patterns(errors)
        print(f"\n{analysis}\n")

        # Auto-apply keyword changes
        applied = apply_keyword_changes(analysis)
        if applied:
            print(f"已应用 {applied} 项关键词变更")
        else:
            print("无需变更")

    return teacher_correct, len(sample), errors


def apply_keyword_changes(analysis: str) -> int:
    """Parse DEL/ADD instructions from teacher analysis and apply to SUB_DOCS.

    Returns number of changes applied (0 on failure).
    """
    # Read memory_routing.py
    routing_file = os.path.join(
        os.environ.get("HERMES_AGENT_LIB", "/usr/local/lib/hermes-agent"),
        "tools/memory_routing.py"
    )
    try:
        with open(routing_file, "r") as f:
            content = f.read()
    except (OSError, IOError) as e:
        print(f"  Cannot read memory_routing.py: {e}")
        return 0

    count = 0
    for line in analysis.split("\n"):
        line = line.strip()
        if line.startswith("DEL "):
            parts = line[4:].split(None, 1)
            if len(parts) != 2:
                continue
            doc, kw = parts
            kw = kw.strip('"').strip("'")
            # Remove keyword from the doc's keyword list
            old = f'"{kw}"'
            # Only remove if keyword exists
            if f'"{kw}"' in content or f"'{kw}'" in content:
                old_str = f'"{kw}",\n            ' if f'"{kw}",\n' in content else f'"{kw}",\n'
                content = content.replace(f'"{kw}",\n            ', "")
                content = content.replace(f'"{kw}",\n', "")
                content = content.replace(f'"{kw}",', "")
                count += 1
                print(f"  ✓ DEL {doc}: {kw}")

        elif line.startswith("ADD "):
            parts = line[4:].split(None, 1)
            if len(parts) != 2:
                continue
            doc, kw = parts
            kw = kw.strip('"').strip("'")
            # Add keyword to the doc's keyword list (just before the closing bracket)
            kw_section = f'"{kw}"' if kw.isascii() else f'"{kw}"'
            # Find the keyword list end for this doc
            import re as _re
            doc_start = _re.search(
                rf'"{doc}":\s*{{\s*"description":\s*"[^"]*",\s*"keywords":\s*\[',
                content, _re.DOTALL
            )
            if doc_start:
                # Find the end of this keyword array
                kw_end = content.find("]", doc_start.end())
                if kw_end > 0:
                    # Insert before the closing bracket
                    insert_at = kw_end
                    new_line = f'            "{kw}",\n        '
                    content = content[:insert_at] + new_line + content[insert_at:]
                    count += 1
                    print(f"  ✓ ADD {doc}: {kw}")

    if count > 0:
        try:
            with open(routing_file, "w") as f:
                f.write(content)
            return count
        except (OSError, IOError) as e:
            print(f"  Cannot write memory_routing.py: {e}")
            return 0
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Audit all entries")
    args = parser.parse_args()
    run_audit(full=args.full)
