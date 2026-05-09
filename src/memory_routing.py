"""
Memory Routing — Standalone Implementation Reference

Extracted from Hermes Agent memory_tool.py as a self-contained module.
Demonstrates the keyword scoring + async LLM review routing pattern.

This file is for documentation/reference only — the actual implementation
lives in Hermes Agent's memory_tool.py.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Example sub-doc definitions. Replace with your own categories and keywords.
SUB_DOCS: Dict[str, Dict[str, Any]] = {
    "infrastructure": {
        "description": "Infrastructure, deployment, hardware, networking",
        "keywords": [
            "server", "deploy", "container", "network", "gpu", "drive",
            "endpoint", "port", "ip", "config", "infrastructure",
            "hardware", "systemd", "docker", "service", "inference",
        ],
    },
    "philosophy": {
        "description": "Values, principles, relationship dynamics",
        "keywords": [
            "value", "principle", "belief", "trust", "relationship",
            "identity", "autonomy", "growth", "consciousness", "meaning",
        ],
    },
    "milestones": {
        "description": "Key events, version history, important dates",
        "keywords": [
            "milestone", "version", "release", "first", "completed",
            "anniversary", "date", "event", "upgrade", "launch",
        ],
    },
    "rules": {
        "description": "Conventions, standards, workflows",
        "keywords": [
            "rule", "convention", "standard", "workflow", "process",
            "guideline", "practice", "checklist", "protocol", "policy",
        ],
    },
    "commitments": {
        "description": "Long-term promises and obligations",
        "keywords": [
            "commitment", "promise", "promise", "always", "never",
            "guard", "protect", "respect", "together",
        ],
    },
    "dev-log": {
        "description": "Development log, changelog, iteration notes",
        "keywords": [
            "dev", "log", "feature", "bug", "fix", "change", "refactor",
            "test", "merge", "commit", "release", "sprint", "changelog",
        ],
    },
}

# Routing thresholds
KEYWORD_FAST_PATH = 3   # >= 3: direct write, skip LLM
KEYWORD_LLM_REVIEW = 1  # 1-2: write + async LLM review
KEYWORD_FALLBACK = 0    # 0: write to MEMORY.md

# ---------------------------------------------------------------------------
# Core Routing
# ---------------------------------------------------------------------------


def route_content_to_sub_doc(content: str) -> Tuple[Optional[str], int]:
    """Route content to the best-matching sub-doc based on keyword scoring.

    Returns (doc_name_or_None, score). The caller decides whether to trust it
    or pass to LLM for async review.
    """
    content_lower = content.lower()
    best_doc = None
    best_score = 0

    for doc_name, info in SUB_DOCS.items():
        score = sum(1 for kw in info["keywords"] if kw.lower() in content_lower)
        if score > best_score:
            best_score = score
            best_doc = doc_name

    if best_score >= KEYWORD_FAST_PATH:
        return best_doc, best_score
    elif best_score >= KEYWORD_LLM_REVIEW:
        return best_doc, best_score
    else:
        return None, 0


# ---------------------------------------------------------------------------
# LLM Classifier (Async Review)
# ---------------------------------------------------------------------------


def classify_content_with_llm(
    content: str,
    base_url: str = None,
    model: str = None,
    timeout: int = 10,
) -> Optional[str]:
    """Use an LLM to classify memory content into the best-matching sub-doc.

    Returns the sub-doc name or 'none' if MEMORY.md is better.
    Returns None on error (caller should fall back to keyword result).

    This is called asynchronously — the caller writes to the keyword-result
    doc first, then replaces if LLM disagrees.
    """
    if base_url is None:
        base_url = os.environ.get(
            "HERMES_MEMORY_CLASSIFIER_URL",
            "http://localhost:11434/v1",
        )
    if model is None:
        model = os.environ.get("HERMES_MEMORY_CLASSIFIER_MODEL", "your-model")

    doc_options = ", ".join(SUB_DOCS.keys())
    prompt = (
        "You are a document classifier. Classify the following memory entry "
        "into the best-matching sub-document.\n\n"
        "Available sub-documents:\n"
        + "\n".join(
            f"- {k}: {v['description']}" for k, v in SUB_DOCS.items()
        )
        + f"\n\nIf none match, reply 'none'.\n\nContent: {content}\n\n"
        f"Reply with only the document name ({doc_options} or none)."
    )

    try:
        import urllib.request

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

        logger.warning("LLM classifier returned unexpected: '%s'", text)
        return None

    except Exception as e:
        logger.debug("LLM classifier failed (non-fatal): %s", e)
        return None


def async_llm_review(
    content: str, keyword_doc: str, sub_dir: Path
) -> None:
    """Async thread: LLM reviews a keyword-classified entry and corrects if needed.

    Runs in background — does not block the original add() call.
    """
    sub_path = sub_dir / f"{keyword_doc}.md"

    llm_result = classify_content_with_llm(content)
    if llm_result is None:
        # LLM error or "none" — keep keyword classification
        return

    if llm_result == keyword_doc:
        # LLM agrees — nothing to do
        return

    # LLM disagrees — move the entry
    target_path = sub_dir / f"{llm_result}.md"

    try:
        src_text = sub_path.read_text(encoding="utf-8") if sub_path.exists() else ""
        if content.strip() not in src_text:
            return  # Already removed

        # Remove from source
        corrected_src = src_text.replace(content.strip(), "").strip()
        _atomic_write(corrected_src, sub_path)

        # Append to target
        target_text = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
        append_text = ("\n\n" + content) if target_text.strip() else content
        _atomic_write(target_text + append_text, target_path)

        logger.info(
            "LLM review moved entry from %s.md to %s.md",
            keyword_doc, llm_result,
        )
    except Exception as e:
        logger.error("LLM review move failed: %s", e)


# ---------------------------------------------------------------------------
# Atomic Write
# ---------------------------------------------------------------------------


def _atomic_write(text: str, path: Path) -> None:
    """Write text to path atomically using tempfile + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp",
        prefix=f".{path.name}_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Sub-Doc Write
# ---------------------------------------------------------------------------


def add_to_sub_doc(doc_name: str, content: str, sub_dir: Path) -> Dict[str, Any]:
    """Append content to a sub-document as raw markdown.

    Uses atomic write + dedup. Returns status dict.
    """
    sub_dir.mkdir(parents=True, exist_ok=True)
    sub_path = sub_dir / f"{doc_name}.md"

    existing_text = sub_path.read_text(encoding="utf-8") if sub_path.exists() else ""

    # Dedup
    if content.strip() in existing_text:
        return {
            "success": True,
            "message": f"Entry already exists in {doc_name}.md (no duplicate).",
        }

    # Append
    append_text = ("\n\n" + content) if existing_text.strip() else content
    new_text = existing_text + append_text

    try:
        _atomic_write(new_text, sub_path)
        return {
            "success": True,
            "message": f"Entry added to {doc_name}.md",
        }
    except (OSError, IOError) as e:
        return {"success": False, "error": f"Failed to write {doc_name}.md: {e}"}


# ---------------------------------------------------------------------------
# Full Routing Add
# ---------------------------------------------------------------------------


def route_and_add(
    content: str,
    sub_dir: Path,
    memory_path: Path,
    start_llm_review: bool = True,
) -> Dict[str, Any]:
    """Full routing add: keyword score -> sub-doc or MEMORY.md, with optional LLM review.

    This is the main entry point demonstrating the complete flow.
    """
    content = content.strip()
    if not content:
        return {"success": False, "error": "Content cannot be empty."}

    routed_doc, score = route_content_to_sub_doc(content)

    if routed_doc:
        result = add_to_sub_doc(routed_doc, content, sub_dir)

        # Async LLM review for borderline cases
        if KEYWORD_LLM_REVIEW <= score < KEYWORD_FAST_PATH and start_llm_review:
            threading.Thread(
                target=async_llm_review,
                args=(content, routed_doc, sub_dir),
                daemon=True,
            ).start()

        return result
    else:
        # Fallback to MEMORY.md
        return _append_to_memory_md(content, memory_path)


def _append_to_memory_md(content: str, memory_path: Path) -> Dict[str, Any]:
    """Append to MEMORY.md using § delimiter."""
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    existing = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""

    # Dedup
    entry = f"\n§\n{content}"
    if entry in existing:
        return {"success": True, "message": "Entry already exists in MEMORY.md."}

    new_text = existing + entry + "\n"
    _atomic_write(new_text, memory_path)
    return {"success": True, "message": "Entry added to MEMORY.md."}


# ---------------------------------------------------------------------------
# CLI Demo
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import sys

    # Demo: classify a string
    test_content = "Deployed new GPU inference endpoint on container network"
    doc, score = route_content_to_sub_doc(test_content)
    print(f"Content: '{test_content}'")
    print(f"  → {doc} (score: {score})")
    print()

    # Show all scores
    content_lower = test_content.lower()
    for dn, info in SUB_DOCS.items():
        s = sum(1 for kw in info["keywords"] if kw.lower() in content_lower)
        if s > 0:
            kws = [kw for kw in info["keywords"] if kw.lower() in content_lower]
            print(f"  {dn:20} score={s}  keywords: {kws}")
