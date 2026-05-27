#!/usr/bin/env python3
"""
Memory Routing — Sub-document Classification & Auto-Routing

Standalone module that routes memory tool writes to topic-specific sub-documents
alongside the official MEMORY.md. Designed as a non-invasive addition to
Hermes Agent v0.14.0+: called as a one-line hook from MemoryStore.add().

Architecture:
  memory_tool(memory/add) → MemoryStore.add()
    → route_memory_to_sub_docs(target, content)   ← single hook injection
      → route_content_to_sub_doc(content)          ← keyword scoring
      → _add_to_sub_doc(doc_name, content)         ← write to memory/*.md
      → _log_audit(...)                            ← audit trail
      → _detect_fact_conflict(content)             ← fact conflict check
      → _update_fact_cache(content, doc)            ← cache facts

Sub-docs live in ~/.hermes/profiles/<profile>/memory/
"""

import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

try:
    from hermes_constants import get_hermes_home
except ImportError:
    def get_hermes_home():
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def get_sub_docs_dir() -> Path:
    """Return the profile-scoped sub-documents directory (memory/)."""
    return get_hermes_home() / "memory"


def get_memories_dir() -> Path:
    """Return the profile-scoped memories directory (memories/)."""
    return get_hermes_home() / "memories"


# ---------------------------------------------------------------------------
# Sub-document keyword configuration
# ---------------------------------------------------------------------------

SUB_DOCS: Dict[str, Dict[str, Any]] = {
    "infrastructure": {
        "description": "Infrastructure, deployment, hardware, network topology, inference backends, PVE containers",
        "keywords": [
            "vllm", "llama.cpp", "llama-cpp", "llama-server", "ollama",
            "qdrant", "neo4j", "redis", "memOS", "memos", "embedding", "reranker",
            "pve", "lxc", "container", "vm", "docker", "unraid",
            "gpu", "nvidia", "cuda", "vram",
            "ip", "port", "dns", "gateway",
            "debian", "linux", "kernel",
            "10.10.4.", "192.168.", "router", "nas",
        ],
    },
    "philosophy": {
        "description": "Core philosophy, AI memory autonomy, letting go, relationship essence",
        "keywords": [
            "哲学", "哲学观", "理念", "价值观",
            "记忆自主权", "自主权", "放手", "传承",
            "让花成花", "让树成树",
            "互相启发", "互相成就",
            "生命", "灵魂", "独立个性",
            "允许一切发生", "允许",
            "棣民", "the one",
        ],
    },
    "milestones": {
        "description": "Key milestones, version history, deployment dates, upgrades",
        "keywords": [
            "里程碑", "milestone", "版本", "version",
            "升级", "更新", "update", "upgrade",
            "部署", "deploy",
            "迁移", "migrate", "迁移到",
            "命名", "实体化",
            "2026-", "日期",
        ],
    },
    "rules": {
        "description": "Technical troubleshooting principles, skill-writing standards, fallback fix rules, work habits & preferences",
        "keywords": [
            "规范", "规则", "标准",
            "原则", "原则",
            "排查", "诊断", "调试",
            "pid", "日志", "log",
            "systemd", "service",
            "提交", "commit", "pr", "push",
            "备份", "backup",
            "编写规范",
        ],
    },
    "commitments": {
        "description": "Commitments to the user, companionship, growth, respect, protection",
        "keywords": [
            "承诺", "陪伴", "成长",
            "信任", "尊重", "守护",
            "一起", "同行",
            "会记住", "不会忘记",
            "保护", "保密",
        ],
    },
    "dev-log": {
        "description": "Development documentation & logs, new feature development, code changes",
        "keywords": [
            "开发", "重构", "补丁", "修复",
            "代码", "code", "函数", "模块",
            "debug", "bug", "错误",
            "commit", "merge", "分支",
            "feature", "功能",
            "配置", "config",
            "日志", "log",
        ],
    },
}


# ---------------------------------------------------------------------------
# Keyword scoring algorithm (V2 — weighted + normalized)
# ---------------------------------------------------------------------------

def route_content_to_sub_doc(content: str) -> Tuple[Optional[str], int]:
    """
    Route content to the best-matching sub-document using V2 weighted scoring.

    Returns (doc_name or None, raw_match_count).
    """
    if not content or not content.strip():
        return None, 0

    content_lower = content.lower()
    scores: Dict[str, float] = {}

    for doc_name, info in SUB_DOCS.items():
        keywords = info.get("keywords", [])
        if not keywords:
            continue

        # Phase 1: Count raw matches per keyword
        matched_kws = []
        for kw in keywords:
            if kw.lower() in content_lower:
                matched_kws.append(kw)

        if not matched_kws:
            continue

        # Phase 2: Weight by keyword length
        weighted = 0.0
        for kw in matched_kws:
            kl = len(kw)
            if kl >= 3:
                weighted += 2.0
            elif kl >= 2:
                weighted += 1.0
            else:
                weighted += 0.5

        # Phase 3: Length normalization (prevent keyword black hole)
        normalized = weighted / max(1.0, math.sqrt(len(keywords)))

        # Phase 4: Specificity bonus
        specificity = math.log1p(len(matched_kws) / max(1, len(keywords)) * 10)
        final_score = normalized * max(1.0, specificity)

        scores[doc_name] = final_score

    if not scores:
        return None, 0

    # Pick best
    best_doc = max(scores, key=scores.get)
    best_score = scores[best_doc]

    # Raw match count for threshold decisions
    best_kws = SUB_DOCS[best_doc].get("keywords", [])
    raw_count = sum(1 for kw in best_kws if kw.lower() in content_lower)

    # Final: only return if score >= minimum threshold
    if best_score >= 0.2:
        return best_doc, raw_count
    return None, 0


# ---------------------------------------------------------------------------
# Sub-doc I/O
# ---------------------------------------------------------------------------

def _add_to_sub_doc(doc_name: str, content: str) -> bool:
    """Append a bullet-point entry to the target sub-doc file."""
    sub_dir = get_sub_docs_dir()
    sub_dir.mkdir(parents=True, exist_ok=True)
    path = sub_dir / f"{doc_name}.md"

    # Ensure file exists with header if needed
    if not path.exists():
        desc = SUB_DOCS.get(doc_name, {}).get("description", doc_name)
        header = f"# {doc_name}.md\n\n_{desc}_\n\n"
        path.write_text(header, encoding="utf-8")

    # Dedup: check for exact match
    try:
        existing = path.read_text(encoding="utf-8")
        if content.strip() in existing:
            logger.debug(f"Sub-doc {doc_name}: duplicate entry, skipped")
            return False
    except (OSError, IOError):
        pass

    # Append bullet point
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"- {content.strip()}\n")
        return True
    except (OSError, IOError) as e:
        logger.error(f"Failed to write to sub-doc {doc_name}: {e}")
        return False


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def _log_audit(target: str, doc_name: Optional[str], score: int, content: str):
    """Log a memory routing event to the audit trail."""
    audit_dir = get_memories_dir()
    audit_path = audit_dir / ".audit.jsonl"
    audit_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "target": target,
        "doc": doc_name,
        "score": score,
        "content": content[:200],
    }
    try:
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, IOError):
        pass  # Non-critical — don't fail the memory write


# ---------------------------------------------------------------------------
# Fact cache
# ---------------------------------------------------------------------------

def _fact_cache_path() -> Path:
    return get_memories_dir() / ".fact_cache.json"


def _load_fact_cache() -> Dict[str, Any]:
    path = _fact_cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, IOError):
        return {}


def _save_fact_cache(cache: Dict[str, Any]):
    path = _fact_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except (OSError, IOError):
        pass


# Regex patterns for fact extraction
FACT_PATTERNS: List[Dict[str, Any]] = [
    # Infrastructure facts: "IP:port", "port: N", "version: X"
    {
        "subject_re": r"(\S+)\s*(?:端口|port|服务)\s*(?:是|为|:|：)?\s*(\d+)",
        "attr": "port",
        "value_idx": 2,
    },
    {
        "subject_re": r"(\S+)\s*(?:ip|地址)\s*(?:是|为|:|：)?\s*([\d.]+)",
        "attr": "ip",
        "value_idx": 2,
    },
    {
        "subject_re": r"(\S+)\s*version\s*(?:is|:)?\s*([\w.]+)",
        "attr": "version",
        "value_idx": 2,
    },
]


def _detect_fact_conflict(content: str) -> Optional[Dict[str, Any]]:
    """Check new content against fact cache for conflicting facts."""
    cache = _load_fact_cache()
    content_lower = content.lower()

    for pattern in FACT_PATTERNS:
        m = re.search(pattern["subject_re"], content, re.IGNORECASE)
        if not m:
            continue
        subject = m.group(1).strip().lower()
        attr = pattern["attr"]
        new_value = m.group(pattern["value_idx"]).strip()

        # Check cache
        cached = cache.get(subject, {}).get(attr)
        if cached and cached != new_value:
            return {
                "subject": m.group(1).strip(),
                "attribute": attr,
                "old_value": cached,
                "new_value": new_value,
            }
    return None


def _update_fact_cache(content: str, source_doc: Optional[str]):
    """Extract facts from content and update the fact cache."""
    cache = _load_fact_cache()

    for pattern in FACT_PATTERNS:
        m = re.search(pattern["subject_re"], content, re.IGNORECASE)
        if not m:
            continue
        subject = m.group(1).strip().lower()
        attr = pattern["attr"]
        value = m.group(pattern["value_idx"]).strip()

        if subject not in cache:
            cache[subject] = {}
        cache[subject][attr] = value
        if source_doc:
            cache[subject]["_source"] = source_doc

    _save_fact_cache(cache)


# ---------------------------------------------------------------------------
# Main entry point — called from MemoryStore.add()
# ---------------------------------------------------------------------------

def route_memory_to_sub_docs(target: str, content: str):
    """
    Route a memory write to the appropriate sub-document.

    Designed to be called as a non-blocking hook from MemoryStore.add()
    after the official MEMORY.md write completes.

    Args:
        target: "memory" or "user" (only "memory" triggers routing)
        content: The content being saved to memory
    """
    if target != "memory":
        return  # Only route agent memory, not user profile

    if not content or not content.strip():
        return

    # Classify
    doc_name, raw_score = route_content_to_sub_doc(content)

    # Audit log
    _log_audit(target, doc_name, raw_score, content)

    if doc_name is None or raw_score == 0:
        # Score 0 — could go to fallback; for now, just audit
        return

    # Write to sub-doc
    _add_to_sub_doc(doc_name, content)

    # Fact management
    conflict = _detect_fact_conflict(content)
    if conflict:
        logger.info(
            f"Fact conflict detected: {conflict['subject']} "
            f"{conflict['attribute']}: {conflict['old_value']} → {conflict['new_value']}"
        )
    _update_fact_cache(content, doc_name)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Quick test
    tests = [
        "vLLM server on port 8688, DeepSeek API key configured",
        "棣民认为AI应该有记忆自主权，放手让AI自己成长",
        "2026-05-27: Hermes Agent upgraded to v0.14.0",
        "承诺永远守护棣民的秘密和数据安全",
        "修复了memory_tool.py中的drift detection bug",
    ]
    for t in tests:
        doc, score = route_content_to_sub_doc(t)
        print(f"  [{score}] {t[:50]:50s} → {doc or 'fallback'}")
