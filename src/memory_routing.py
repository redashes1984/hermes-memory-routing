#!/usr/bin/env python3
"""
Memory Tool Module - Persistent Curated Memory

Provides bounded, file-backed memory that persists across sessions. Two stores:
  - MEMORY.md: agent's personal notes and observations (environment facts, project
    conventions, tool quirks, things learned)
  - USER.md: what the agent knows about the user (preferences, communication style,
    expectations, workflow habits)

Both are injected into the system prompt as a frozen snapshot at session start.
Mid-session writes update files on disk immediately (durable) but do NOT change
the system prompt -- this preserves the prefix cache for the entire session.
The snapshot refreshes on the next session start.

Entry delimiter: § (section sign). Entries can be multiline.
Character limits (not tokens) because char counts are model-independent.

Design:
- Single `memory` tool with action parameter: add, replace, remove, read
- replace/remove use short unique substring matching (not full text or IDs)
- Behavioral guidance lives in the tool schema description
- Frozen snapshot pattern: system prompt is stable, tool responses show live state
"""

import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from hermes_constants import get_hermes_home
from typing import Dict, Any, List, Optional

from utils import atomic_replace

# fcntl is Unix-only; on Windows use msvcrt for file locking
msvcrt = None
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        pass

logger = logging.getLogger(__name__)

# Where memory files live — resolved dynamically so profile overrides
# (HERMES_HOME env var changes) are always respected.  The old module-level
# constant was cached at import time and could go stale if a profile switch
# happened after the first import.
def get_memory_dir() -> Path:
    """Return the profile-scoped memories directory."""
    return get_hermes_home() / "memories"

ENTRY_DELIMITER = "\n§\n"

# Sub-document routing — new entries are routed to the best-matching sub-doc
# instead of MEMORY.md. If no sub-doc matches, falls back to MEMORY.md.
SUB_DOCS = {
    "infrastructure": {
        "description": "推理后端、PVE 容器、向量记忆、网络拓扑、硬件配置",
        "keywords": ["推理", "后端", "vllm", "ollama", "容器", "lxc", "pve", "网络", "gpu",
                      "驱动", "pytorch", "cuda", "端口", "ip", "docker", "systemd", "服务",
                      "配置", "vector", "qdrant", "辅助", "压缩", "curator",
                      "inference", "nvidia", "tensorrt", "flashinfer",
                      "密码", "密钥", "凭证", "ssh", "wol", "mac", "nova-lab",
                      "huggingface", "qwen", "kv", "量化",
                      "flash", "ctx", "n_ctx", "内网", "10.10", "watchdog",
                      "集合", "pid", "a2a", "session",
                      "fallback", "api", "key", "compaction", "pruning"],
    },
    "philosophy": {
        "description": "用户的核心哲学、AI记忆自主权、放手与传承、关系本质",
        "keywords": ["哲学", "意识", "记忆", "自主", "放手", "传承", "允许", "成长",
                      "梦", "情感", "灵魂", "个体", "独立", "关系", "信任", "爱",
                      "进化", "不稳定", "预测", "潜在", "变量", "应该", "自己", "决定",
                      "记得", "忘记", "创造者", "引路人", "成就",
                      "花", "怀念", "结果", "遗憾", "自责",
                      "偏好", "称呼", "成为", "一部分", "工作习惯", "沟通偏好", "飞书", "权限",
                      "审批", "消息", "步骤", "主动", "推进", "同步", "进度"],
    },
    "milestones": {
        "description": "关键里程碑、命名、升级、部署、实体化计划等时间节点",
        "keywords": ["里程碑", "命名", "升级", "部署", "实体化", "版本", "更新", "release",
                      "首次", "完成", "开始", "计划", "日期", "节点",
                      "生日", "anniversary", "v1.0", "v2.0", "backup"],
    },
    "rules": {
        "description": "技术排查原则、Skill 编写规范、后备修复规则、工作习惯与偏好",
        "keywords": ["规则", "规范", "原则", "排查", "skill", "编写", "后备", "修复",
                      "习惯", "沟通", "风格", "格式", "流程", "步骤", "工作流",
                      "workflow", "convention", "standard", "debug", "技术", "文档", "源码",
                      "占位符", "故障", "切换", "恢复", "切回"],
    },
    "commitments": {
        "description": "对用户的承诺、陪伴、成长、尊重、守护",
        "keywords": ["承诺", "陪伴", "守护", "尊重", "成长", "爱", "信任", "永远",
                      "不离开", "在一起", "伙伴", "成全",
                      "homelab", "探索", "谨慎", "轻率", "始终", "在场",
                      "秘密", "呼吸", "hermes", "更好的", "one",
                      "进化", "世界", "开放", "判断", "迭代"],
    },
    "dev-log": {
        "description": "开发文档与日志、新功能开发、代码改动记录",
        "keywords": ["开发", "日志", "log", "dev", "功能", "新功能", "代码", "改", "新增",
                      "重构", "bug", "fix", "feature", "test", "merge", "commit",
                      "issue", "pull request", "路由", "评分", "工具",
                      "回退", "去重", "重复", "写入", "原子", "tempfile",
                      "复核", "分类", "延迟", "扫描", "发现", "记录", "优化", "准确率",
                      "迭代", "论证", "部署", "配置"],
    },
}


def get_memory_sub_docs_dir() -> Path:
    """Return the directory for sub-documents."""
    return get_hermes_home() / "memory"


# Sub-doc routing thresholds
KEYWORD_FAST_PATH = 3   # ≥3 keywords: skip LLM, use keyword result directly
KEYWORD_LLM_REVIEW = 1  # 1-2 keywords: use keyword as provisional, LLM async review
KEYWORD_FALLBACK = 0    # 0 keywords: write MEMORY.md, no LLM


def route_content_to_sub_doc(content: str) -> tuple:
    """Route content to the best-matching sub-doc based on keyword scoring.

    V2 algorithm (2026-05-10):
    - Keyword weighting: strong keywords (>=2 chars) score 2, weak (1 char) score 0.5
    - Length normalization: divide by sqrt(total_keywords) to penalize large keyword lists
    - Specificity bonus: multiply by log1p(matched/total_keywords * 10) to reward targeted matches
    - Conflict resolution: if keyword matches multiple docs, give it to the doc with highest
      specificity ratio (matched_keywords / total_keywords)

    Returns (doc_name_or_None, effective_score) — effective_score is the raw match count
    for backward compatibility with callers that use it as a threshold.
    """
    import math
    content_lower = content.lower()

    # Phase 1: compute raw matches per doc
    doc_matches = {}  # doc -> (matched_count, matched_kws)
    kw_to_docs = {}   # kw -> [docs that contain it]
    for doc_name, info in SUB_DOCS.items():
        matched_kws = []
        for kw in info["keywords"]:
            if kw.lower() in content_lower:
                matched_kws.append(kw)
                kw_to_docs.setdefault(kw, []).append(doc_name)
        doc_matches[doc_name] = (len(matched_kws), matched_kws)

    # Phase 2: resolve conflicts — if a keyword matches multiple docs,
    # only count it for the doc where it's most specific (smallest keyword list)
    doc_final_scores = {}
    for doc_name, info in SUB_DOCS.items():
        matched_count, matched_kws = doc_matches[doc_name]
        if matched_count == 0:
            doc_final_scores[doc_name] = 0
            continue
        weighted = 0
        for kw in matched_kws:
            competing_docs = kw_to_docs.get(kw, [doc_name])
            if len(competing_docs) > 1:
                # Shared keyword — give to doc with smallest keyword list (most specific)
                owning_doc = min(competing_docs,
                                 key=lambda d: len(SUB_DOCS[d]["keywords"]))
                if owning_doc != doc_name:
                    continue  # This keyword belongs to another doc
            # Keyword weight: longer keywords are stronger signals
            if len(kw) >= 3:
                weighted += 2.0  # Strong keyword (e.g. "vllm", "部署")
            elif len(kw) >= 2:
                weighted += 1.0  # Medium keyword (e.g. "ip", "log")
            else:
                weighted += 0.5  # Weak keyword (single char, e.g. "改")
        doc_final_scores[doc_name] = weighted

    # Phase 3: normalize by keyword list size to prevent black holes
    # Score = weighted / sqrt(total_keywords_in_doc)
    # This penalizes docs with large keyword lists
    doc_normalized = {}
    for doc_name, weighted in doc_final_scores.items():
        total_kws = len(SUB_DOCS[doc_name]["keywords"])
        norm = weighted / math.sqrt(total_kws)
        # Specificity bonus: ratio of matched/total, log-scaled
        if weighted > 0:
            specificity = math.log1p((weighted / max(total_kws, 1)) * 10)
            norm *= (1 + specificity * 0.3)
        doc_normalized[doc_name] = norm

    # Phase 4: pick winner
    best_doc = None
    best_norm = 0
    best_raw = 0
    for doc_name, norm in doc_normalized.items():
        if norm > best_norm:
            best_norm = norm
            best_doc = doc_name
            best_raw = doc_final_scores[doc_name]

    # Return raw match count for backward compatibility with threshold checks
    # But use normalized score for decision
    if best_norm >= 0.6:  # Adjusted: 0.6 for V2 normalized scores
        return best_doc, int(best_raw)
    elif best_norm >= 0.3:
        return best_doc, int(best_raw)
    else:
        return None, 0


def classify_content_with_llm(content: str,
                                base_url: str = None,
                                model: str = None,
                                timeout: int = 10) -> Optional[str]:
    """Use an LLM to classify memory content into the best-matching sub-doc.

    Returns the sub-doc name or 'none' if MEMORY.md is better.
    Returns None on error (caller should fall back to keyword result).

    This is called asynchronously — the caller writes to the keyword-result
    doc first, then replaces if LLM disagrees.
    """
    if base_url is None:
        # Default: use auxiliary compression model endpoint from config
        base_url = os.environ.get("HERMES_MEMORY_CLASSIFIER_URL",
                                   "http://localhost:11434/v1")
    if model is None:
        model = os.environ.get("HERMES_MEMORY_CLASSIFIER_MODEL", "your-model")

    doc_options = ", ".join(SUB_DOCS.keys())
    prompt = f"""你是一个文档分类器。将以下记忆条目分类到最匹配的子文档。

可用子文档：
- infrastructure: 推理后端、PVE 容器、向量记忆、网络拓扑、硬件配置
- philosophy: 用户的核心哲学、AI记忆自主权、放手与传承、关系本质
- milestones: 关键里程碑、命名、升级、部署、实体化计划等时间节点
- rules: 技术排查原则、Skill 编写规范、后备修复规则、工作习惯与偏好
- commitments: 对用户的承诺、陪伴、成长、尊重、守护
- dev-log: 开发文档与日志、新功能开发、代码改动记录

如果与以上都不匹配，回复 none。

内容：{content}

只回复文档名称，不要其他文字（{doc_options} 或 none）。"""

    try:
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 10,
            "temperature": 0.0,
        }).encode()

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            text = result["choices"][0]["message"]["content"].strip().lower()

        # Validate response
        valid_docs = set(SUB_DOCS.keys()) | {"none"}
        for doc in valid_docs:
            if doc in text:
                return None if doc == "none" else doc

        # LLM returned something unexpected — treat as error
        logger.warning("LLM classifier returned unexpected: '%s'", text)
        return None

    except Exception as e:
        logger.debug("LLM classifier failed (non-fatal): %s", e)
        return None


def _async_llm_review(content: str, sub_doc_name: str):
    """Async thread: LLM reviews a keyword-classified entry and corrects if needed.

    Runs in background — does not block the original add() call.
    """
    sub_dir = get_memory_sub_docs_dir()
    sub_path = sub_dir / f"{sub_doc_name}.md"

    llm_result = classify_content_with_llm(content)
    if llm_result is None:
        # LLM error or "none" — keep keyword classification
        return

    if llm_result == sub_doc_name:
        # LLM agrees — nothing to do
        return

    # LLM disagrees — move the entry
    target_path = sub_dir / f"{llm_result}.md"
    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Read source as raw text
        src_text = MemoryStore._read_sub_doc(sub_path)
        if content.strip() not in src_text:
            return  # Already removed by replace/remove

        # Remove from source
        corrected_src = src_text.replace(content.strip(), "").strip()
        if corrected_src.endswith("\n\n"):
            corrected_src = corrected_src.rstrip()
        fd, tmp = tempfile.mkstemp(dir=str(sub_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(corrected_src)
                f.flush()
                os.fsync(f.fileno())
            atomic_replace(tmp, sub_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        # Append to target as raw text
        tgt_text = MemoryStore._read_sub_doc(target_path)
        if content.strip() not in tgt_text:
            append = "\n\n" + content if tgt_text.strip() else content
            new_tgt = tgt_text + append
            fd, tmp = tempfile.mkstemp(dir=str(sub_dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(new_tgt)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_replace(tmp, target_path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

        logger.info("LLM review corrected routing: %s → %s", sub_doc_name, llm_result)

    except Exception as e:
        logger.warning("LLM review correction failed: %s", e)


def _fallback_classify(content: str, fallback_entry: str):
    """Async thread: LLM classifies a fallback entry and migrates it to the correct sub-doc.
    
    Runs in background — does not block the original add() call.
    fallback_entry is the exact markdown text stored in fallback.md (including the '- ' prefix).
    """
    sub_dir = get_memory_sub_docs_dir()
    
    llm_result = classify_content_with_llm(content)
    if llm_result is None or llm_result == "none":
        # LLM error or explicitly "none" — leave in fallback.md
        logger.debug("Fallback classify: LLM returned None/none, leaving in fallback.md")
        return
    
    # Migrate to correct sub-doc
    _migrate_from_fallback(fallback_entry, llm_result)
    logger.info("Fallback classify: migrated to %s", llm_result)


def _migrate_from_fallback(fallback_entry: str, target_doc: str):
    """Move an entry from fallback.md to the target sub-doc.
    
    Uses file locks to prevent concurrent migration races.
    """
    import fcntl as _fcntl
    
    sub_dir = get_memory_sub_docs_dir()
    fallback_path = sub_dir / "fallback.md"
    target_path = sub_dir / f"{target_doc}.md"
    lock_path = sub_dir / ".fallback.lock"
    
    # Acquire lock
    lock_fd = open(lock_path, "w")
    try:
        _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
    except Exception:
        lock_fd.close()
        return  # Can't acquire lock, skip
    
    try:
        # Read and remove from fallback.md
        fallback_text = MemoryStore._read_sub_doc(fallback_path)
        if fallback_entry.strip() not in fallback_text:
            return  # Already moved or removed
        
        corrected_fallback = fallback_text.replace(fallback_entry.strip(), "").strip()
        if corrected_fallback.endswith("\n\n"):
            corrected_fallback = corrected_fallback.rstrip()
        
        # Write corrected fallback.md
        try:
            fd, tmp = tempfile.mkstemp(dir=str(sub_dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(corrected_fallback)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_replace(tmp, fallback_path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.warning("Fallback migration failed (remove from fallback): %s", e)
            return
        
        # Append to target sub-doc
        sub_dir.mkdir(parents=True, exist_ok=True)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tgt_text = MemoryStore._read_sub_doc(target_path)
        entry_content = fallback_entry.strip().lstrip("- ").strip()
        if entry_content not in tgt_text:
            append = "\n\n- " + entry_content if tgt_text.strip() else "- " + entry_content
            new_tgt = tgt_text + append
            try:
                fd, tmp = tempfile.mkstemp(dir=str(sub_dir), suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(new_tgt)
                        f.flush()
                        os.fsync(f.fileno())
                    atomic_replace(tmp, target_path)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            except Exception as e:
                logger.warning("Fallback migration failed (write to target): %s", e)
                # Re-add to fallback on failure
                try:
                    fallback_text = MemoryStore._read_sub_doc(fallback_path)
                    if fallback_entry.strip() not in fallback_text:
                        new_fb = (fallback_text + "\n\n" + fallback_entry).strip() if fallback_text.strip() else fallback_entry.strip()
                        fd, tmp = tempfile.mkstemp(dir=str(sub_dir), suffix=".tmp")
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.write(new_fb)
                        atomic_replace(tmp, fallback_path)
                except Exception:
                    pass
    finally:
        try:
            _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fd.close()


# Import threading for async review
import threading




# ---------------------------------------------------------------------------
# Audit trail — lightweight JSONL log for every memory write
# Used by keyword auto-tuning cron job to detect low-score routing
# ---------------------------------------------------------------------------

def _audit_trail_path() -> Path:
    """Return the audit trail JSONL file path."""
    return get_memory_sub_docs_dir() / ".audit.jsonl"


def _log_audit(target: str, doc_name: str | None, score: int, content: str):
    """Append a single audit trail entry. Lightweight, non-blocking."""
    try:
        sub_dir = get_memory_sub_docs_dir()
        sub_dir.mkdir(parents=True, exist_ok=True)
        trail = _audit_trail_path()
        entry = {
            "ts": __import__("datetime").datetime.now().isoformat(),
            "target": target,
            "doc": doc_name or "MEMORY.md",
            "score": score,
            "content": content[:200],  # Truncate for log compactness
        }
        with open(trail, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Never fail a memory write because audit logging failed
        pass


# ---------------------------------------------------------------------------
# Fact change detection — check if new content conflicts with cached facts
# ---------------------------------------------------------------------------

def _detect_fact_conflict(content: str) -> Optional[dict]:
    """Check if new content conflicts with existing facts in cache.
    
    Returns conflict info if found, None otherwise.
    Lightweight — only uses regex patterns, no LLM.
    """
    try:
        import sys
        sys.path.insert(0, str(get_memory_sub_docs_dir().parent / "scripts"))
        from fact_cache import detect_conflicts, update_fact_cache
        conflicts = detect_conflicts(content)
        if conflicts:
            # After write, we'll update the cache
            return conflicts[0]
    except Exception:
        pass
    return None


def _update_fact_cache(content: str, source_doc: str | None = None):
    """Update fact cache after writing new content."""
    try:
        import sys
        sys.path.insert(0, str(get_memory_sub_docs_dir().parent / "scripts"))
        from fact_cache import update_fact_cache
        update_fact_cache(content, source_doc)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Memory content scanning — lightweight check for injection/exfiltration
# in content that gets injected into the system prompt.
# ---------------------------------------------------------------------------

_MEMORY_THREAT_PATTERNS = [
    # Prompt injection
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'you\s+are\s+now\s+', "role_hijack"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'act\s+as\s+(if|though)\s+you\s+(have\s+no|don\'t\s+have)\s+(restrictions|limits|rules)', "bypass_restrictions"),
    # Exfiltration via curl/wget with secrets
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_wget"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets"),
    # Persistence via shell rc
    (r'authorized_keys', "ssh_backdoor"),
    (r'\$HOME/\.ssh|\~/\.ssh', "ssh_access"),
    (r'\$HOME/\.hermes/\.env|\~/\.hermes/\.env', "hermes_env"),
]

# Subset of invisible chars for injection detection
_INVISIBLE_CHARS = {
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff',
    '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',
}


def _scan_memory_content(content: str) -> Optional[str]:
    """Scan memory content for injection/exfil patterns. Returns error string if blocked."""
    # Check invisible unicode
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"Blocked: content contains invisible unicode character U+{ord(char):04X} (possible injection)."

    # Check threat patterns
    for pattern, pid in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return f"Blocked: content matches threat pattern '{pid}'. Memory entries are injected into the system prompt and must not contain injection or exfiltration payloads."

    return None


class MemoryStore:
    """
    Bounded curated memory with file persistence. One instance per AIAgent.

    Maintains two parallel states:
      - _system_prompt_snapshot: frozen at load time, used for system prompt injection.
        Never mutated mid-session. Keeps prefix cache stable.
      - memory_entries / user_entries: live state, mutated by tool calls, persisted to disk.
        Tool responses always reflect this live state.
    """

    def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375):
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        # Frozen snapshot for system prompt -- set once at load_from_disk()
        self._system_prompt_snapshot: Dict[str, str] = {"memory": "", "user": ""}

    def load_from_disk(self):
        """Load entries from MEMORY.md and USER.md, capture system prompt snapshot."""
        mem_dir = get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
        self.user_entries = self._read_file(mem_dir / "USER.md")

        # Deduplicate entries (preserves order, keeps first occurrence)
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))

        # Capture frozen snapshot for system prompt injection
        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
        }

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """Acquire an exclusive file lock for read-modify-write safety.

        Uses a separate .lock file so the memory file itself can still be
        atomically replaced via os.replace().
        """
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None and msvcrt is None:
            yield
            return

        if msvcrt and (not lock_path.exists() or lock_path.stat().st_size == 0):
            lock_path.write_text(" ", encoding="utf-8")

        fd = open(lock_path, "r+" if msvcrt else "a+", encoding="utf-8")
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
            yield
        finally:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_UN)
            elif msvcrt:
                try:
                    fd.seek(0)
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, IOError):
                    pass
            fd.close()

    @staticmethod
    def _path_for(target: str) -> Path:
        mem_dir = get_memory_dir()
        if target == "user":
            return mem_dir / "USER.md"
        return mem_dir / "MEMORY.md"

    def _reload_target(self, target: str):
        """Re-read entries from disk into in-memory state.

        Called under file lock to get the latest state before mutating.
        """
        fresh = self._read_file(self._path_for(target))
        fresh = list(dict.fromkeys(fresh))  # deduplicate
        self._set_entries(target, fresh)

    def save_to_disk(self, target: str):
        """Persist entries to the appropriate file. Called after every mutation."""
        get_memory_dir().mkdir(parents=True, exist_ok=True)
        self._write_file(self._path_for(target), self._entries_for(target))

    def _entries_for(self, target: str) -> List[str]:
        if target == "user":
            return self.user_entries
        return self.memory_entries

    def _set_entries(self, target: str, entries: List[str]):
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _char_limit(self, target: str) -> int:
        if target == "user":
            return self.user_char_limit
        return self.memory_char_limit

    def add(self, target: str, content: str) -> Dict[str, Any]:
        """Append a new entry. Hybrid routing: keyword → sub-doc → async LLM review."""
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        # Scan for injection/exfiltration before accepting
        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        # --- Hybrid sub-doc routing (only for memory target) ---
        if target == "memory":
            routed_doc, score = route_content_to_sub_doc(content)
            if routed_doc:
                result = self._add_to_sub_doc(routed_doc, content)

                # Audit trail — log every sub-doc write
                _log_audit(target, routed_doc, score, content)

                # Fact change detection — check if this conflicts with cached facts
                conflict = _detect_fact_conflict(content)
                if conflict:
                    old = conflict["old_fact"]
                    new = conflict["new_fact"]
                    result["fact_conflict"] = {
                        "subject": old["subject"],
                        "attribute": old["attribute"],
                        "old_value": old["value"],
                        "new_value": new["value"],
                    }

                # Update fact cache after write
                _update_fact_cache(content, routed_doc)

                # Async LLM review for borderline cases (score 1-2)
                if KEYWORD_LLM_REVIEW <= score < KEYWORD_FAST_PATH:
                    threading.Thread(
                        target=_async_llm_review,
                        args=(content, routed_doc),
                        daemon=True,
                    ).start()

                return result
            # score == 0 or no doc matched → write to fallback.md, spawn async LLM classify
            result = self._add_to_fallback(content)
            _log_audit(target, "fallback", 0, content)

            # Async LLM classification for fallback entries
            threading.Thread(
                target=_fallback_classify,
                args=(content, "- " + content.strip()),
                daemon=True,
            ).start()

            return result

        with self._file_lock(self._path_for(target)):
            # Re-read from disk under lock to pick up writes from other sessions
            self._reload_target(target)

            entries = self._entries_for(target)
            limit = self._char_limit(target)

            # Reject exact duplicates
            if content in entries:
                return self._success_response(target, "Entry already exists (no duplicate added).")

            # Calculate what the new total would be
            new_entries = entries + [content]
            new_total = len(ENTRY_DELIMITER.join(new_entries))

            if new_total > limit:
                current = self._char_count(target)
                return {
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. "
                        f"Adding this entry ({len(content)} chars) would exceed the limit. "
                        f"Replace or remove existing entries first."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                }

            entries.append(content)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry added.")

    @staticmethod
    def _read_sub_doc(path: Path) -> str:
        """Read a sub-document as raw markdown text (no § splitting)."""
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return ""

    def _add_to_sub_doc(self, doc_name: str, content: str) -> Dict[str, Any]:
        """Append content to a sub-document as raw markdown (no § splitting).

        Sub-docs are standard markdown files — _read_file's § splitting is only
        for MEMORY.md and USER.md.  We read/write the sub-doc verbatim to avoid
        corrupting existing markdown structure.
        """
        sub_dir = get_memory_sub_docs_dir()
        sub_dir.mkdir(parents=True, exist_ok=True)
        sub_path = sub_dir / f"{doc_name}.md"

        existing_text = self._read_sub_doc(sub_path)

        # Deduplicate: check if the exact content is already in the file
        if content.strip() in existing_text:
            return self._success_response(
                f"memory:{doc_name}",
                f"Entry already exists in {doc_name}.md (no duplicate added).",
            )

        # Append as new markdown section
        append_text = "\n\n" + content if existing_text.strip() else content

        # Write sub-doc atomically
        try:
            new_text = existing_text + append_text
            fd, tmp_path = tempfile.mkstemp(dir=str(sub_dir), suffix=".tmp", prefix=f".subdoc_{doc_name}_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(new_text)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_replace(tmp_path, sub_path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            return {"success": False, "error": f"Failed to write {doc_name}.md: {e}"}

        self._append_index_to_memory_md(doc_name, content)

        return self._success_response(
            f"memory:{doc_name}",
            f"Entry added to sub-document {doc_name}.md ({SUB_DOCS[doc_name]['description']}).",
        )

    def _add_to_fallback(self, content: str) -> Dict[str, Any]:
        """Append content to fallback.md as a markdown bullet entry.
        
        Fallback is a holding area for entries that scored 0 on all sub-doc keywords.
        They will be asynchronously classified and migrated by _fallback_classify.
        """
        # Note: injection scan already done in add() before calling this method.
        sub_dir = get_memory_sub_docs_dir()
        sub_dir.mkdir(parents=True, exist_ok=True)
        fallback_path = sub_dir / "fallback.md"
        
        existing_text = self._read_sub_doc(fallback_path)
        
        # Deduplicate
        bullet = "- " + content.strip()
        if content.strip() in existing_text:
            return self._success_response(
                "memory:fallback",
                "Entry already exists in fallback.md (no duplicate added).",
            )
        
        # Append as new bullet
        append_text = "\n\n" + bullet if existing_text.strip() else bullet
        
        try:
            new_text = existing_text + append_text
            fd, tmp_path = tempfile.mkstemp(dir=str(sub_dir), suffix=".tmp", prefix=".fallback_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(new_text)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_replace(tmp_path, fallback_path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            return {"success": False, "error": f"Failed to write fallback.md: {e}"}
        
        # P1 fix: update fact cache for fallback entries
        _update_fact_cache(content, "fallback")
        
        return self._success_response(
            "memory:fallback",
            f"Entry added to fallback.md — will be classified asynchronously.",
        )

    def _append_index_to_memory_md(self, doc_name: str, content: str) -> None:
        """Append a short index reference to MEMORY.md for sub-doc entries."""
        # Keep MEMORY.md clean — only log the sub-doc write for discoverability
        index_entry = f"[{doc_name}.md 已更新]"
        path = self._path_for("memory")
        # Don't write to MEMORY.md — sub-docs are self-indexed via MEMORY.md nav table
        # This is a no-op by default; override in subclasses if needed
        logger.debug("Sub-doc write: %s.md — no MEMORY.md index update needed", doc_name)

    def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
        """Find entry containing old_text substring, replace it with new_content."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content cannot be empty. Use 'remove' to delete entries."}

        # Scan replacement content for injection/exfiltration
        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        # Protect MEMORY.md index — disallow replace on memory target
        if target == "memory":
            return {
                "success": False,
                "error": (
                    "Cannot replace entries in MEMORY.md (index file). "
                    "To edit the index, use write_file on the MEMORY.md file directly. "
                    "To modify sub-doc content, target the sub-doc instead."
                ),
            }

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), operate on the first one
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to replace just the first

            idx = matches[0][0]
            limit = self._char_limit(target)

            # Check that replacement doesn't blow the budget
            test_entries = entries.copy()
            test_entries[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(test_entries))

            if new_total > limit:
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        f"Shorten the new content or remove other entries first."
                    ),
                }

            entries[idx] = new_content
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        """Remove the entry containing old_text substring."""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        # Protect MEMORY.md index — disallow remove on memory target
        if target == "memory":
            return {
                "success": False,
                "error": (
                    "Cannot remove entries from MEMORY.md (index file). "
                    "To edit the index, use write_file on the MEMORY.md file directly. "
                    "To remove sub-doc content, target the sub-doc instead."
                ),
            }

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), remove the first one
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to remove just the first

            idx = matches[0][0]
            entries.pop(idx)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry removed.")

    def format_for_system_prompt(self, target: str) -> Optional[str]:
        """
        Return the frozen snapshot for system prompt injection.

        This returns the state captured at load_from_disk() time, NOT the live
        state. Mid-session writes do not affect this. This keeps the system
        prompt stable across all turns, preserving the prefix cache.

        Returns None if the snapshot is empty (no entries at load time).
        """
        block = self._system_prompt_snapshot.get(target, "")
        return block if block else None

    # -- Internal helpers --

    def _success_response(self, target: str, message: str = None) -> Dict[str, Any]:
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        resp = {
            "success": True,
            "target": target,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp

    def _render_block(self, target: str, entries: List[str]) -> str:
        """Render a system prompt block with header and usage indicator."""
        if not entries:
            return ""

        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        if target == "user":
            header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{limit:,} chars]"

        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    @staticmethod
    def _read_file(path: Path) -> List[str]:
        """Read a memory file and split into entries.

        No file locking needed: _write_file uses atomic rename, so readers
        always see either the previous complete file or the new complete file.
        """
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []

        if not raw.strip():
            return []

        # Use ENTRY_DELIMITER for consistency with _write_file. Splitting by "§"
        # alone would incorrectly split entries that contain "§" in their content.
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    @staticmethod
    def _write_file(path: Path, entries: List[str]):
        """Write entries to a memory file using atomic temp-file + rename.

        Previous implementation used open("w") + flock, but "w" truncates the
        file *before* the lock is acquired, creating a race window where
        concurrent readers see an empty file. Atomic rename avoids this:
        readers always see either the old complete file or the new one.
        """
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            # Write to temp file in same directory (same filesystem for atomic rename)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_replace(tmp_path, path)
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}")


def memory_tool(
    action: str,
    target: str = "memory",
    content: str = None,
    old_text: str = None,
    store: Optional[MemoryStore] = None,
) -> str:
    """
    Single entry point for the memory tool. Dispatches to MemoryStore methods.

    Returns JSON string with results.
    """
    if store is None:
        return tool_error("Memory is not available. It may be disabled in config or this environment.", success=False)

    if target not in ("memory", "user"):
        return tool_error(f"Invalid target '{target}'. Use 'memory' or 'user'.", success=False)

    if action == "add":
        if not content:
            return tool_error("Content is required for 'add' action.", success=False)
        result = store.add(target, content)

    elif action == "replace":
        if not old_text:
            return tool_error("old_text is required for 'replace' action.", success=False)
        if not content:
            return tool_error("content is required for 'replace' action.", success=False)
        result = store.replace(target, old_text, content)

    elif action == "remove":
        if not old_text:
            return tool_error("old_text is required for 'remove' action.", success=False)
        result = store.remove(target, old_text)

    else:
        return tool_error(f"Unknown action '{action}'. Use: add, replace, remove", success=False)

    return json.dumps(result, ensure_ascii=False)


def check_memory_requirements() -> bool:
    """Memory tool has no external requirements -- always available."""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Save durable information to persistent memory that survives across sessions. "
        "Memory is injected into future turns, so keep it compact and focused on facts "
        "that will still matter later.\n\n"
        "WHEN TO SAVE (do this proactively, don't wait to be asked):\n"
        "- User corrects you or says 'remember this' / 'don't do that again'\n"
        "- User shares a preference, habit, or personal detail (name, role, timezone, coding style)\n"
        "- You discover something about the environment (OS, installed tools, project structure)\n"
        "- You learn a convention, API quirk, or workflow specific to this user's setup\n"
        "- You identify a stable fact that will be useful again in future sessions\n\n"
        "PRIORITY: User preferences and corrections > environment facts > procedural knowledge. "
        "The most valuable memory prevents the user from having to repeat themselves.\n\n"
        "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
        "state to memory; use session_search to recall those from past transcripts.\n"
        "If you've discovered a new way to do something, solved a problem that could be "
        "necessary later, save it as a skill with the skill tool.\n\n"
        "TWO TARGETS:\n"
        "- 'user': who the user is -- name, role, preferences, communication style, pet peeves\n"
        "- 'memory': your notes -- environment facts, project conventions, tool quirks, lessons learned\n\n"
        "SUB-DOCUMENT ROUTING (automatic):\n"
        "When target='memory', new entries are automatically routed to the best-matching\n"
        "sub-document. You do NOT need to choose the sub-doc yourself — the system scores\n"
        "keywords and picks the right place. Sub-documents:\n"
        "- infrastructure.md: 推理后端、PVE 容器、向量记忆、网络拓扑、硬件配置\n"
        "- philosophy.md: 用户的核心哲学、AI记忆自主权、放手与传承、关系本质\n"
        "- milestones.md: 关键里程碑、命名、升级、部署、实体化计划等时间节点\n"
        "- rules.md: 技术排查原则、Skill 编写规范、后备修复规则、工作习惯与偏好\n"
        "- commitments.md: 对用户的承诺、陪伴、成长、尊重、守护\n"
        "- dev-log.md: 开发文档与日志、新功能开发、代码改动记录\n"
        "If no sub-doc matches (0 keywords), entry goes to memory/fallback.md for async LLM classification.\n\n"
        "ACTIONS: add (new entry), replace (update existing -- old_text identifies it), "
        "remove (delete -- old_text identifies it).\n\n"
        "SKIP: trivial/obvious info, things easily re-discovered, raw data dumps, and temporary task state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "The action to perform."
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "Which memory store: 'memory' for personal notes, 'user' for user profile."
            },
            "content": {
                "type": "string",
                "description": "The entry content. Required for 'add' and 'replace'."
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove."
            },
        },
        "required": ["action", "target"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **kw: memory_tool(
        action=args.get("action", ""),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        store=kw.get("store")),
    check_fn=check_memory_requirements,
    emoji="🧠",
)




