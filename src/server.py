#!/usr/bin/env python3
"""
Memory Routing MCP Server for Hermes Agent Nova v2.0.0

Tools: route_and_save_memory

Accepts content, classifies intent via LLM, routes to the correct
sub-document under memory/, and updates MEMORY.md index entries.
"""

__version__ = "2.0.1"

import fcntl
import os
import sys
import json
import logging
import re
import time
import tempfile
from typing import Optional, Dict, Any

import requests

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────

NOVA_PROFILE = os.path.join(os.path.expanduser("~"), ".hermes", "profiles", "nova")

# Hermes 官方目录 — MEMORY.md / USER.md 由 Hermes 内置 memory 系统维护
OFFICIAL_MEMORIES_DIR = os.path.join(NOVA_PROFILE, "memories")
MEMORY_MD = os.path.join(OFFICIAL_MEMORIES_DIR, "MEMORY.md")

# 自定义子文档目录 — memory-routing 负责管理
SUBDOC_DIR = os.path.join(NOVA_PROFILE, "memory")
ROUTING_LOG = os.path.join(SUBDOC_DIR, "routing.log")

# LLM config — use Qwen3.5-9B-AWQ on 10.10.4.9 (lightweight classifier)
LLM_PROVIDER = os.environ.get("HERMES_LLM_PROVIDER", "custom")
LLM_MODEL = os.environ.get("HERMES_LLM_MODEL", "Qwen3.5-9B-AWQ")
LLM_BASE_URL = os.environ.get("HERMES_LLM_BASE_URL", "http://10.10.4.9:8000/v1")
LLM_API_KEY = os.environ.get("HERMES_LLM_API_KEY", "VLLM")
LLM_TIMEOUT = int(os.environ.get("HERMES_LLM_TIMEOUT", "5"))

# Routing categories — map to files under memory/
ROUTING_CATEGORIES = {
    "credential": "CREDENTIALS.md",
    "infrastructure": "infrastructure.md",
    "techref": "tech-ref.md",
    "devlog": "dev-log.md",
    "miscellaneous": "miscellaneous.md",
}

# Category alias map — intent_classifier.py uses hyphenated names
CATEGORY_ALIAS = {
    "credentials": "credential",
    "dev-log": "devlog",
    "tech-ref": "techref",
    "infrastructure": "infrastructure",
    "miscellaneous": "miscellaneous",
}

# Create MCP server
mcp = FastMCP("memory-routing")


# ── LLM Intent Classifier ──────────────────────────────────────────

CLASSIFICATION_SYSTEM = """你是一个记忆分类器。将用户发送的内容分类到五个类别之一：

- credential: 密码、密钥、API Key、认证信息
- infrastructure: 架构事实、IP/端口、服务部署、硬件信息
- techref: API格式、部署流程、硬件特性、技术参考
- devlog: 开发变更、调优记录、修复记录
- miscellaneous: 非技术类兜底

只输出类别名称，不要输出其他任何内容。"""


def classify_intent(content: str) -> tuple:
    """Classify content into a routing category using the LLM.

    Returns (category, method) where method is 'llm' or 'keyword'.
    """
    try:
        resp = requests.post(
            f"{LLM_BASE_URL}/chat/completions",
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": CLASSIFICATION_SYSTEM},
                    {"role": "user", "content": content[:2000]},
                ],
                "temperature": 0.0,
                "max_tokens": 10,
                "reasoning_effort": "none",  # Disable thinking for fast classification
            },
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            timeout=LLM_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        # Thinking models put output in reasoning_content when content is None
        category = (msg.get("content") or msg.get("reasoning_content") or "").strip().lower()
    except requests.exceptions.Timeout:
        logger.warning("LLM classify fallback: timeout (%ds)", LLM_TIMEOUT)
        return keyword_classify(content), "keyword"
    except requests.exceptions.ConnectionError as e:
        logger.warning("LLM classify fallback: connection error (%s)", e)
        return keyword_classify(content), "keyword"
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("LLM classify fallback: JSON/response parse error (%s)", e)
        return keyword_classify(content), "keyword"
    except Exception as e:
        logger.warning("LLM classify fallback: unexpected error (%s)", e)
        return keyword_classify(content), "keyword"

    # Normalize to known categories
    for cat in ROUTING_CATEGORIES:
        if cat in category:
            return cat, "llm"
    return "miscellaneous", "llm"


def keyword_classify(content: str) -> str:
    """Fallback keyword-based classification when LLM is unavailable."""
    lower = content.lower()
    credential_kw = ["password", "密钥", "api key", "apikey", "token", "secret", "凭证", "密码", "credential"]
    infra_kw = ["ip", "端口", "port", "部署", "deploy", "架构", "architect", "服务", "server", "container", "硬件", "gpu", "cpu", "内存"]
    tech_kw = ["api", "格式", "流程", "format", "endpoint", "http", "rest", "grpc", "文档", "reference"]
    dev_kw = ["修复", "fix", "调优", "tune", "变更", "change", "更新", "update", "重构", "refactor", "bug"]

    for kw in credential_kw:
        if kw in lower:
            return "credential"
    for kw in infra_kw:
        if kw in lower:
            return "infrastructure"
    for kw in tech_kw:
        if kw in lower:
            return "techref"
    for kw in dev_kw:
        if kw in lower:
            return "devlog"
    return "miscellaneous"


# ── Helpers ─────────────────────────────────────────────────────────

def _write_routing_log(category: str, filename: str, summary: str, method: str, elapsed: float, detail: str = ""):
    """Append a routing log entry to routing.log under memory/.

    Format: timestamp | category | file | method | elapsed | summary | detail
    One line per entry, easy to grep and tail.
    """
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        summary_safe = summary.replace("\n", " ").replace("|", "\\|")[:100]
        detail_safe = detail.replace("\n", " ")[:200]
        log_line = f"{timestamp} | {category} | {filename} | {method} | {elapsed:.2f}s | {summary_safe} | {detail_safe}\n"
        with open(ROUTING_LOG, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception as e:
        logger.warning("Failed to write routing log: %s", e)


def sanitize_summary(text: str) -> str:
    """Strip markdown special chars from a summary string."""
    text = re.sub(r'[#`*~_|]', '', text)
    return text.strip()


def strip_null_bytes(text: str) -> str:
    """Remove null bytes from content before storage."""
    return text.replace('\x00', '')


# ── File locking ────────────────────────────────────────────────────

def _lock_path(filepath: str) -> str:
    """Get the lock file path for a given file."""
    return filepath + ".lock"


def write_with_lock(filepath: str, read_fn=None, write_fn=None):
    """Atomic lock → read → transform → write → unlock cycle.

    Prevents TOCTOU: the entire read→dedup→write sequence runs under
    an exclusive lock, so no other process can interleave between
    reading stale data and writing.

    Args:
        filepath: Target file path.
        read_fn: Optional callable(existing_content: str) -> new_content: str.
            If provided, existing content is read under lock, passed to
            read_fn, and the result is atomically written.
        write_fn: Optional callable() -> new_content: str.
            If provided (and read_fn is None), write_fn is called directly
            and its result is atomically written.
    """
    lock_fd = open(_lock_path(filepath), "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            dir_name = os.path.dirname(filepath)
            os.makedirs(dir_name, exist_ok=True)

            if read_fn is not None:
                # Read existing content under lock
                existing = ""
                if os.path.exists(filepath):
                    with open(filepath, "r", encoding="utf-8") as f:
                        existing = f.read()
                new_content = read_fn(existing)
            elif write_fn is not None:
                new_content = write_fn()
            else:
                raise ValueError("write_with_lock requires read_fn or write_fn")

            # Atomic write (tempfile + rename) — still under lock
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(new_content)
                os.replace(tmp_path, filepath)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        lock_fd.close()


# ── Atomic file write (legacy, no locking — use write_with_lock for new code) ──

def atomic_write(path: str, content: str):
    """Write content to file atomically using temp file + rename.

    WARNING: This function does NOT acquire a lock. Use write_with_lock()
    for any operation that reads then writes the same file.
    """
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Read existing memory doc ────────────────────────────────────────

def read_doc(filename: str) -> str:
    """Read a doc from the memories directory.

    NOTE: This is a bare read — no lock. For read→write sequences,
    use write_with_lock() instead.
    """
    path = os.path.join(SUBDOC_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


# ── Update MEMORY.md index ──────────────────────────────────────────

def init_memory_index():
    """Create MEMORY.md with static rule and routing-target headers if absent (lock-protected)."""

    def build_index(existing: str) -> str:
        if existing.strip():
            return existing  # Already exists, keep as-is
        return (
            f"## 规则\n\n"
            f"| 规则 | 说明 |\n"
            f"|------|------|\n"
            f"| 凭证查询 | 需要密码/密钥/API Key 读 `../memory/CREDENTIALS.md` |\n"
            f"| 工具评估 | 新工具先评估重合度，只装真正缺失的能力 |\n"
            f"| 变更操作 | 删除/停服务/改配置先列清单，等棣民确认 |\n\n"
            f"## 路由目标\n\n"
            f"| 路由目标 | 用途 |\n"
            f"|------|------|\n"
            f"| `../memory/CREDENTIALS.md` | 密码、密钥、API Key |\n"
            f"| `../memory/infrastructure.md` | 架构事实、IP/端口、服务部署 |\n"
            f"| `../memory/tech-ref.md` | API格式、部署流程、硬件特性 |\n"
            f"| `../memory/dev-log.md` | 开发变更、调优记录 |\n"
            f"| `../memory/miscellaneous.md` | 非技术类兜底 |\n"
        )

    write_with_lock(MEMORY_MD, read_fn=build_index)


# ── MCP Tool ────────────────────────────────────────────────────────

@mcp.tool()
def route_and_save_memory(
    content: str,
    category: Optional[str] = None,
    summary: Optional[str] = None,
) -> str:
    """
    Route and save a memory entry to the correct sub-document.

    Classifies the content intent using LLM (or keywords), appends
    it to the appropriate file under memory/, and updates MEMORY.md
    index.

    Args:
        content: The memory content to store
        category: Optional override category (credential/infrastructure/techref/devlog/miscellaneous)
        summary: Optional short summary for the index entry
    """
    result: Dict[str, Any] = {}

    if not content or not content.strip():
        return json.dumps({"status": "error", "message": "content is empty"}, ensure_ascii=False)

    # Strip null bytes from content before processing
    content = strip_null_bytes(content)

    start_time = time.monotonic()

    # Step 1: Classify intent
    if category and category in ROUTING_CATEGORIES:
        chosen_category = category
        classify_method = "override"
    elif category and category in CATEGORY_ALIAS:
        # Accept hyphenated names from intent_classifier
        chosen_category = CATEGORY_ALIAS[category]
        classify_method = "alias"
    else:
        chosen_category, classify_method = classify_intent(content)

    classify_elapsed = time.monotonic() - start_time

    result["category"] = chosen_category

    filename = ROUTING_CATEGORIES.get(chosen_category, "miscellaneous.md")
    result["file"] = f"memory/{filename}"

    # Step 2: Generate summary for index
    if summary:
        entry_summary = sanitize_summary(summary[:100])
    else:
        first_line = content.strip().split("\n")[0]
        entry_summary = sanitize_summary(first_line[:100] if first_line else content[:100])

    # Step 3: Append to sub-document (lock → read → dedup → write → unlock)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    entry_block = f"\n### [{timestamp}] {entry_summary}\n\n{content.strip()}\n"

    filepath = os.path.join(SUBDOC_DIR, filename)

    def build_subdoc(existing: str) -> str:
        if not existing.strip():
            header = filename.replace(".md", "").replace("-", " ").title()
            existing = f"# {header}\n"
        return existing.rstrip() + entry_block + "\n"

    write_with_lock(filepath, read_fn=build_subdoc)

    result["status"] = "success"
    result["summary"] = entry_summary

    # Step 4: Ensure MEMORY.md has static index headers
    init_memory_index()

    # Step 5: Write routing log
    total_elapsed = time.monotonic() - start_time
    _write_routing_log(
        category=chosen_category,
        filename=filename,
        summary=entry_summary,
        method=classify_method,
        elapsed=total_elapsed,
        detail=f"classify={classify_elapsed:.2f}s",
    )

    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Standalone test ────────────────────────────────────────────────

if __name__ == "__main__":
    if "--test" in sys.argv:
        print("Memory Routing MCP Server -- Standalone Test")
        print("=" * 50)
        print(f"Profile: {NOVA_PROFILE}")
        print(f"Official memories: {OFFICIAL_MEMORIES_DIR}")
        print(f"Subdocs: {SUBDOC_DIR}")
        print(f"LLM: {LLM_BASE_URL} / {LLM_MODEL}")
        print(f"Timeout: {LLM_TIMEOUT}s")
        print(f"Categories: {list(ROUTING_CATEGORIES.keys())}")
        print("=" * 50)

        # Test keyword classifier
        test_cases = [
            ("我的API Key是sk-12345", "credential"),
            ("服务器IP是10.10.4.81，端口8080", "infrastructure"),
            ("API返回格式是JSON，包含code和data字段", "techref"),
            ("修复了登录页面的bug", "devlog"),
            ("今天天气很好", "miscellaneous"),
        ]

        print("\nKeyword classifier tests:")
        for text, expected in test_cases:
            got = keyword_classify(text)
            status = "OK" if got == expected else "FAIL"
            print(f"  {status}: '{text[:30]}...' -> {got} (expected {expected})")

        # Test sanitization
        print("\nSanitization tests:")
        for text, exp in [
            ("Hello `world` #tag", "Hello world tag"),
            ("**bold** and *italic*", "bold and italic"),
            ("A | B -> C", "A  B -> C"),
        ]:
            got = sanitize_summary(text)
            status = "OK" if got == exp else "FAIL"
            print(f"  {status}: '{text}' -> '{got}' (expected '{exp}')")

        # Test null byte stripping
        print("\nNull byte tests:")
        raw = "hello\x00world\x00test"
        cleaned = strip_null_bytes(raw)
        status = "OK" if "\x00" not in cleaned else "FAIL"
        print(f"  {status}: null bytes removed -> '{cleaned}'")

        print("\nReady to serve MCP requests!")
    else:
        # Start MCP server (stdio transport by default)
        mcp.run()