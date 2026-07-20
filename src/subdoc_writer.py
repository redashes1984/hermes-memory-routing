"""子文档写入模块 — 硬覆盖 + 追加记录 + 智能更新。

写入策略：
- credentials: 同 key 直接硬覆盖
- dev-log / miscellaneous / tech-ref: 追加新条目带时间戳
- infrastructure: 先读现有内容，更新已有事实或追加新事实

所有写入使用 tempfile + os.rename 原子操作 + fcntl.flock 并发安全。
"""

import fcntl
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# 子文档文件名映射
CATEGORY_TO_FILE = {
    "credentials": "CREDENTIALS.md",
    "infrastructure": "infrastructure.md",
    "tech-ref": "tech-ref.md",
    "dev-log": "dev-log.md",
    "miscellaneous": "miscellaneous.md",
}

# 追加模式类别
APPEND_CATEGORIES = {"dev-log", "miscellaneous", "tech-ref"}

# 硬覆盖类别
OVERWRITE_CATEGORIES = {"credentials"}

# 智能更新类别（先读再决定更新/追加）
SMART_UPDATE_CATEGORIES = {"infrastructure"}


class SubdocWriterError(Exception):
    """子文档写入错误。"""
    pass


def _get_lock_path(filepath: str) -> str:
    """获取锁文件路径。"""
    return filepath + ".lock"


def _read_existing(filepath: str) -> Optional[str]:
    """安全读取文件内容，文件不存在返回 None。"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _atomic_write(filepath: str, new_content: str) -> None:
    """原子写入：tempfile + os.rename，外层由调用方持 flock。

    写入临时文件到同一文件系统目录 → rename 到目标路径。
    保证写入要么完整生效，要么完全不生效。
    """
    dirpath = os.path.dirname(filepath) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, filepath)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _format_entry(title: str, content: str, ts: Optional[str] = None) -> str:
    """格式化为标准 markdown 条目。"""
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    return f"### {title}\n\n**时间**: {ts}\n\n{content}\n"


def _parse_existing_entries(content: str) -> list[dict]:
    """解析现有 markdown 内容为条目列表。

    每个条目: {"title": str, "body": str}
    """
    if not content or not content.strip():
        return []

    entries = []
    current = None
    lines = content.split("\n")

    for line in lines:
        if line.startswith("### "):
            if current is not None:
                entries.append(current)
            current = {"title": line[4:].strip(), "lines": []}
        elif current is not None:
            current["lines"].append(line)

    if current is not None:
        current["body"] = "\n".join(current["lines"]).strip()
        entries.append(current)

    return entries


def _find_entry_by_title(entries: list[dict], title: str) -> Optional[int]:
    """按标题查找条目索引，前缀匹配。"""
    title_lower = title.lower()
    for i, entry in enumerate(entries):
        entry_lower = entry["title"].lower()
        # 前20字符匹配 或 一方包含另一方
        if (entry_lower.startswith(title_lower[:20]) or
                title_lower.startswith(entry_lower[:20]) or
                entry_lower == title_lower):
            return i
    return None


def _rebuild_markdown(entries: list[dict]) -> str:
    """将条目列表重建为 markdown 文本。"""
    blocks = []
    for entry in entries:
        body = entry.get("body", "")
        if body:
            blocks.append(f"### {entry['title']}\n\n{body}")
    return "\n\n".join(blocks) + "\n" if blocks else ""


def _build_and_write(filepath: str, build_fn) -> dict:
    """通用写入流程：加锁 → 读 → 构建 → 原子写入。

    Args:
        filepath: 目标文件路径
        build_fn: 函数，接收 existing_content (str|None)，返回新内容 str

    Returns:
        {"status": "success", "file": filename, "path": filepath, "action": "..."}
    """
    lock_path = _get_lock_path(filepath)
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            existing = _read_existing(filepath)
            new_content = build_fn(existing)
            _atomic_write(filepath, new_content)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        lock_fd.close()

    return {
        "status": "success",
        "file": os.path.basename(filepath),
        "path": filepath,
    }


def write_to_subdoc(
    category: str,
    content: str,
    summary: str = "",
    key: str = "",
    memories_dir: str = "",
) -> dict:
    """将内容写入对应子文档。

    Args:
        category: 分类名称 (credentials/infrastructure/tech-ref/dev-log/miscellaneous)
        content: 要写入的内容
        summary: 条目摘要/标题（可选，不传则自动生成）
        key: credentials 分类中的 key 名称（可选）
        memories_dir: 记忆目录路径

    Returns:
        {"status": "success", "file": "...", "action": "overwrite|append|update", "path": "..."}

    Raises:
        SubdocWriterError: 分类未知或写入失败
    """
    if category not in CATEGORY_TO_FILE:
        raise SubdocWriterError(f"未知分类: {category}")

    if not memories_dir:
        memories_dir = os.path.expanduser("~/.hermes/profiles/nova/memories")

    filename = CATEGORY_TO_FILE[category]
    filepath = os.path.join(memories_dir, filename)

    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

    ts = datetime.now(timezone.utc).isoformat()

    if category == "credentials":
        return _write_credentials(filepath, key, content, ts)

    elif category == "infrastructure":
        return _write_infrastructure(filepath, summary, content, ts)

    else:
        # 追加模式: dev-log / miscellaneous / tech-ref
        return _write_append(filepath, summary, content, ts)


def _write_credentials(filepath: str, key: str, value: str, ts: str) -> dict:
    """凭证写入：同 key 硬覆盖。"""
    title = key or f"credential_{ts}"

    def build_content(existing):
        entries = _parse_existing_entries(existing) if existing else []
        new_body = f"**时间**: {ts}\n\n{value}"
        idx = _find_entry_by_title(entries, key) if key else None

        if idx is not None:
            entries[idx] = {"title": key, "body": new_body}
        else:
            entries.append({"title": key, "body": new_body})

        return _rebuild_markdown(entries)

    result = _build_and_write(filepath, build_content)
    result["action"] = "overwrite"
    return result


def _write_append(filepath: str, summary: str, content: str, ts: str) -> dict:
    """追加写入：dev-log / miscellaneous / tech-ref。"""
    title = summary or content[:50]
    if len(title) > 60:
        title = title[:57] + "..."

    def build_content(existing):
        new_entry = _format_entry(title, content, ts)
        if existing and existing.strip():
            return existing.rstrip() + "\n\n" + new_entry
        return new_entry

    result = _build_and_write(filepath, build_content)
    result["action"] = "append"
    return result


def _write_infrastructure(filepath: str, summary: str, content: str, ts: str) -> dict:
    """基础设施写入：智能更新（先读现有，更新已有事实或追加新事实）。"""
    title = summary or content[:50]
    if len(title) > 60:
        title = title[:57] + "..."

    action_holder = {"action": "append"}

    def build_content(existing):
        entries = _parse_existing_entries(existing) if existing else []
        new_body = f"**时间**: {ts}\n\n{content}"

        idx = _find_entry_by_title(entries, title)
        if idx is not None:
            entries[idx] = {"title": title, "body": new_body}
            action_holder["action"] = "update"
        else:
            entries.append({"title": title, "body": new_body})
            action_holder["action"] = "append"

        return _rebuild_markdown(entries)

    result = _build_and_write(filepath, build_content)
    result["action"] = action_holder["action"]
    return result