#!/usr/bin/env python3
"""
Memory Fact Cache & Change Detection

Extracts factual triples (subject, attribute, value) from memory entries,
maintains a cache file, and detects when new content conflicts with
existing facts.

Used by:
  - memory_tool.add(): before writing, check if this content updates an existing fact
  - memory-replay.py: during idle replay, merge stale entries
"""

import json
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

try:
    import sys
    sys.path.insert(0, '/usr/local/lib/hermes-agent')
    from tools.memory_tool import get_memory_sub_docs_dir
    from tools.memory_tool import SUB_DOCS, route_content_to_sub_doc
    from utils import atomic_replace
except Exception:
    get_memory_sub_docs_dir = lambda: Path.home() / ".hermes" / "profiles" / "nova" / "memory"
    SUB_DOCS = {}
    route_content_to_sub_doc = lambda c: (None, 0)
    atomic_replace = lambda a, b: None

FACT_CACHE_PATH = get_memory_sub_docs_dir() / ".fact_cache.json"


# ─── Fact extraction patterns ─────────────────────────────────────────
# Each: (pattern, fact_type)
# Groups: 1=full_subject_context, 2=subject_key, 3=value

# Infrastructure facts
INFRA_PATTERNS = [
    # IP: "CT 108 的 IP 是 10.10.4.7", "IP: 10.10.4.8", "地址 10.10.4.79"
    (r'((?:CT|VM|容器|宿主机|服务)\s+\S+(?:\s+\S+)?)\s*(?:的)?\s*(?:IP|地址)\s*(?:为|是|改[为成]|→|:)?\s*(\d+\.\d+\.\d+\.\d+(?::\d+)?)', 'ip'),
    # Port: "端口 8000", "port 18801"
    (r'((?:服务|容器|CT|VM)\s+\S+(?:\s+\S+)?)\s*(?:端口|port)\s*(?:为|是|:)?\s*(\d+)', 'port'),
    # Version: "vLLM 版本 0.20.1", "Qdrant 版本 1.17.1"
    (r'((?:vllm|ollama|qdrant|sglang|hermes|cuda|driver|hermes-agent|systemd|python|pytorch)\s*[\w\-\.]*)\s*(?:版本|version|v|升级[到为])\s*([vV]?[\d.]+(?:\w+)?)', 'version'),
    # Direct version mention: "vLLM 0.20.1"
    (r'((?:vllm|ollama|qdrant|sglang|hermes|cuda|pytorch)\s*[\w\-\.]*)\s+(v?[\d.]+)', 'version'),
    # Status: "状态 running", "已停止"
    (r'((?:服务|容器|CT|VM)\s+\S+(?:\s+\S+)?)\s*(?:状态|status)\s*(running|stopped|运行中|已停止|已启动)', 'status'),
    # Model: "模型 Qwen3.6-27B-FP8"
    (r'((?:容器|CT|VM|端点|服务)\s+\S+(?:\s+\S+)?)\s*(?:模型|model)\s*([\w\-\.]+)', 'model'),
]

# User preference facts
USER_PREF_PATTERNS = [
    (r'(棣民|用户)\s*(?:偏好|喜欢|希望|倾向|认为|决定|要求|决定是)[\s:，]*(.+)', 'preference'),
    (r'(棣民|用户)\s*(?:沟通|交流|消息|通知|通报)[\s:，]*(.+)', 'communication'),
    (r'(棣民|用户)\s*(?:称呼|叫|名字|name)[\s:，]*(.+)', 'naming'),
]

# Philosophy facts
PHILOSOPHY_PATTERNS = [
    (r'(棣民|用户)\s*(?:哲学|原则|价值观|核心[信念信理]|认为|相信|说)[\s:，]*(.+)', 'philosophy'),
]


def extract_facts(content: str) -> List[dict]:
    """Extract factual triples from content.
    
    Returns list of:
      {"category": "infrastructure", "fact_type": "ip",
       "subject": "CT 108", "attribute": "ip", "value": "10.10.4.7", "doc": "infrastructure"}
    """
    facts = []
    
    doc, score = route_content_to_sub_doc(content) if SUB_DOCS else (None, 0)
    
    # Check all pattern sets based on doc or general scan
    all_patterns = []
    
    if doc == "infrastructure" or score > 0:
        all_patterns = INFRA_PATTERNS
    
    # Always scan infrastructure patterns for any content mentioning IPs/versions
    # This catches facts even in dev-log entries
    for pattern, fact_type in INFRA_PATTERNS:
        for m in re.finditer(pattern, content, re.IGNORECASE):
            groups = m.groups()
            if len(groups) >= 3:
                subject, value = groups[0], groups[2]
            else:
                subject, value = groups[0], groups[1]
            facts.append({
                "category": "infrastructure",
                "fact_type": fact_type,
                "subject": subject.strip(),
                "attribute": fact_type,
                "value": value.strip(),
                "doc": doc or "infrastructure",
            })
    
    if doc in ("philosophy", "user") or True:  # Always scan user/philosophy
        for pattern, fact_type in PHILOSOPHY_PATTERNS:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                facts.append({
                    "category": "philosophy",
                    "fact_type": fact_type,
                    "subject": m.group(1).strip(),
                    "attribute": fact_type,
                    "value": m.group(2).strip(),
                    "doc": doc or "philosophy",
                })
    
    if doc == "user" or True:
        for pattern, fact_type in USER_PREF_PATTERNS:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                facts.append({
                    "category": "user",
                    "fact_type": fact_type,
                    "subject": m.group(1).strip(),
                    "attribute": fact_type,
                    "value": m.group(2).strip(),
                    "doc": doc or "user",
                })
    
    return facts


def load_fact_cache() -> dict:
    """Load fact cache from disk."""
    if FACT_CACHE_PATH.exists():
        try:
            return json.loads(FACT_CACHE_PATH.read_text())
        except Exception:
            pass
    return {"facts": [], "version": 1}


def save_fact_cache(cache: dict):
    """Save fact cache to disk atomically."""
    import tempfile
    import os
    fd, tmp = tempfile.mkstemp(dir=str(FACT_CACHE_PATH.parent), suffix=".json")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(FACT_CACHE_PATH))
    except Exception:
        try:
            os.unlink(tmp)
        except:
            pass


def detect_conflicts(content: str) -> List[dict]:
    """Check if new content conflicts with existing cached facts.
    
    Returns list of conflicts:
      {"new_fact": {...}, "old_fact": {...}, "resolution": "replace|merge"}
    """
    cache = load_fact_cache()
    new_facts = extract_facts(content)
    conflicts = []
    
    for nf in new_facts:
        for cf in cache.get("facts", []):
            if (nf["subject"] == cf["subject"] and 
                nf["attribute"] == cf["attribute"] and 
                nf["value"] != cf["value"]):
                conflicts.append({
                    "new_fact": nf,
                    "old_fact": cf,
                    "resolution": "replace",
                })
    
    return conflicts


def update_fact_cache(content: str, source_doc: str | None = None):
    """After writing content, update the fact cache with extracted facts."""
    cache = load_fact_cache()
    new_facts = extract_facts(content)
    
    for nf in new_facts:
        key = (nf["subject"], nf["attribute"])
        found = False
        for i, cf in enumerate(cache["facts"]):
            if (cf["subject"], cf["attribute"]) == key:
                cf["value"] = nf["value"]
                cf["updated_at"] = datetime.now().isoformat()
                cf["source_doc"] = source_doc or nf.get("doc", "infrastructure")
                cache["facts"][i] = cf
                found = True
                break
        if not found:
            nf["created_at"] = datetime.now().isoformat()
            nf["updated_at"] = nf["created_at"]
            nf["source_doc"] = source_doc or nf.get("doc", "infrastructure")
            cache["facts"].append(nf)
    
    cache["version"] = cache.get("version", 1) + 1
    save_fact_cache(cache)
    return new_facts


def build_initial_cache() -> dict:
    """Scan all sub-docs and build initial fact cache from existing entries."""
    sub_dir = get_memory_sub_docs_dir()
    if not sub_dir.exists():
        return {"indexed": 0}
    
    import os
    cache = {"facts": [], "version": 1}
    
    for fn in sorted(os.listdir(sub_dir)):
        if not fn.endswith(".md") or fn.startswith("."):
            continue
        fp = sub_dir / fn
        text = fp.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("- "):
                content = line[2:]
                facts = extract_facts(content)
                for f in facts:
                    key = (f["subject"], f["attribute"])
                    existing = [cf for cf in cache["facts"] if (cf["subject"], cf["attribute"]) == key]
                    if existing:
                        existing[0]["value"] = f["value"]
                    else:
                        f["created_at"] = datetime.now().isoformat()
                        f["updated_at"] = f["created_at"]
                        f["source_doc"] = fn.replace(".md", "")
                        cache["facts"].append(f)
    
    save_fact_cache(cache)
    return {"indexed": len(cache["facts"])}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        result = build_initial_cache()
        print(json.dumps(result, ensure_ascii=False))
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        test_cases = [
            "CT 108 sglang-lxc 的 IP 是 10.10.4.7",
            "vLLM 0.20.1, config /root/vllm/Qwen-Qwen3.6-27B-FP8.yaml",
            "棣民偏好分步骤的指导方式",
            "Qdrant 版本 1.17.1, 部署在 Unraid",
            "功率限制 420W",
            "模型 Qwen3.6-27B-FP8, 256K 上下文",
        ]
        for tc in test_cases:
            facts = extract_facts(tc)
            print(f"Content: {tc}")
            for f in facts:
                print(f"  → {f['fact_type']}: {f['subject']} → {f['value']}")
            if not facts:
                print("  → (no facts extracted)")
            print()
    else:
        print("Usage: python fact_cache.py [build|test]")
