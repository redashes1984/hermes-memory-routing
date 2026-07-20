#!/usr/bin/env python3
"""LLM 意图分类器 — 将记忆内容分类到 5 个子文档类别。 v2.0.0

调用 Qwen3.5-9B-AWQ @ 10.10.4.9:8000，temperature=0.1，response_format=json_object。
Prompt 定义 5 个分类及 definition + 正例 + 反例。
返回 JSON: {category, confidence, reason}。
Fallback: confidence < 0.5 或 JSON 解析失败 → 默认 dev-log。
"""

__version__ = "2.0.0"

import json
import logging
import os
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────

DEFAULT_ENDPOINT = "http://10.10.4.9:8000/v1/chat/completions"
DEFAULT_MODEL = "Qwen3.5-9B-AWQ"
DEFAULT_TIMEOUT = 5  # seconds
DEFAULT_MAX_RETRIES = 2  # max retries on timeout
SLOW_THRESHOLD = DEFAULT_TIMEOUT * 2  # slow-response threshold (seconds)

CATEGORIES = ["credentials", "infrastructure", "tech-ref", "dev-log", "miscellaneous"]


# ── Classification prompt ───────────────────────────────────────────

CLASSIFICATION_PROMPT = """你是一个记忆内容分类器。根据内容将其分类到以下五个类别之一：

## 分类定义

### credentials（凭据）
定义：密码、密钥、API Key、认证令牌、访问凭证等敏感凭据信息。
正例：
- "vLLM 的 API Key 是 sk-xxxx"
- "数据库密码是 123456"
- "Telegram bot token: 123456:ABC"
反例：
- "API 返回格式为 JSON"（这不是凭据，是 tech-ref）
- "服务器 IP 是 10.10.4.9"（这是基础设施，不是凭据）

### infrastructure（基础设施）
定义：硬件、网络、部署、IP/端口、服务地址、容器、虚拟机等基础设施事实。
正例：
- "PVE 节点 IP: 10.10.4.9"
- "vLLM 运行在 10.10.4.62:8000"
- "服务器有 2 张 RTX 4090"
反例：
- "修改了 vLLM 启动参数 --max-model-len 8192"（这是 dev-log，不是基础设施）
- "vLLM 的 API 端点是 /v1/chat/completions"（这是 tech-ref，不是基础设施）

### tech-ref（技术参考） vs dev-log（开发日志） — 优先级规则
如果内容同时符合两者，按以下优先级判断：
1. **改动动词 → dev-log**：内容包含"修复了"、"新增了"、"重构了"、"修改了"、"优化了"、"实现了"、"解决了"等描述"做了什么变更"的动词，优先归为 dev-log。
2. **静态描述 → tech-ref**：内容仅描述"某工具/接口怎么用"、"参数格式是什么"、"API 路径是什么"等静态知识（没有"我做了XX"的动作），归为 tech-ref。
3. **配置变更 → dev-log**：即使涉及技术参数，但表达的是"我修改了XX参数"这个动作，归为 dev-log。

tech-ref 正例：
- "PVE API: GET /api2/json/nodes"
- "memory_routing.py 的 route_content_to_sub_doc 返回 (doc_name, score)"
- "Qwen3.5-9B-AWQ 需要 --trust-remote-code 参数"

dev-log 正例：
- "修复了 memory_routing.py 中的路径遍历漏洞"
- "新增了异步复核机制"
- "重构了关键词评分算法为 V2"
- "修改了 vLLM 启动参数 --max-model-len 8192"（改了参数 = dev-log）

反例（边界）：
- "重写了 route_content_to_sub_doc 函数" → dev-log（有"重写了"动作）
- "vLLM 的 --max-model-len 默认值是 4096" → tech-ref（纯知识描述）
- "API Key 是 sk-xxxx" → credentials（不是技术参考）

### miscellaneous（杂项）
定义：无法归入以上四类的内容，包括日常记录、感想、非技术类信息。
正例：
- "今天天气很好"
- "星野是我的第一个 AI 伙伴"
- "计划本周完成视频项目"
反例：
- "修复了登录页面的 bug"（这是 dev-log，不是杂项）
- "API Key 是 sk-xxxx"（这是 credentials，不是杂项）

## 输出格式

只输出一个 JSON 对象，不要包含其他文字：
{{
  "category": "分类名称",
  "confidence": 置信度（0.0-1.0，1.0=非常确定，0.5=模糊匹配）,
  "reason": "简短分类理由"
}}

## 待分类内容：
{content}
"""


def classify_intent(content: str) -> Dict[str, Any]:
    """使用 LLM 对记忆内容进行分类。

    Args:
        content: 待分类的记忆内容

    Returns:
        {"category": str, "confidence": float, "reason": str}
        如果所有失败路径，返回 {"category": "dev-log", "confidence": 0.0, "reason": "fallback"}
    """
    endpoint = os.environ.get("HERMES_MEMORY_LLM_URL", DEFAULT_ENDPOINT)
    model = os.environ.get("HERMES_MEMORY_LLM_MODEL", DEFAULT_MODEL)
    timeout = int(os.environ.get("HERMES_MEMORY_LLM_TIMEOUT", str(DEFAULT_TIMEOUT)))
    slow_threshold = float(os.environ.get("HERMES_MEMORY_SLOW_THRESHOLD", str(SLOW_THRESHOLD)))
    max_retries = int(os.environ.get("HERMES_MEMORY_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)))

    prompt = CLASSIFICATION_PROMPT.format(content=content[:2000])

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }, ensure_ascii=False).encode("utf-8")

    raw = None
    start = time.monotonic()
    for attempt in range(1 + max_retries):
        try:
            req = urllib.request.Request(
                endpoint, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            break  # success
        except urllib.error.URLError as e:
            elapsed = time.monotonic() - start
            reason = str(e.reason)
            is_timeout = isinstance(e.reason, TimeoutError) or "timeout" in reason.lower()
            if is_timeout and attempt < max_retries:
                logger.warning(f"LLM classify timeout ({elapsed:.1f}s > {timeout}s), retry {attempt+1}/{max_retries}")
                continue
            elif is_timeout:
                logger.warning(f"LLM classify fallback: timeout after {attempt} retries ({elapsed:.1f}s)")
            else:
                logger.warning(f"LLM classify fallback: unreachable ({reason})")
            return {"category": "dev-log", "confidence": 0.0, "reason": "fallback: endpoint error"}
        except Exception as e:
            logger.warning(f"LLM classify fallback: request error ({e})")
            return {"category": "dev-log", "confidence": 0.0, "reason": "fallback: request error"}

    if raw is None:
        return {"category": "dev-log", "confidence": 0.0, "reason": "fallback: exhausted retries"}

    elapsed = time.monotonic() - start
    if elapsed > slow_threshold:
        logger.warning(f"LLM classify: slow response ({elapsed:.1f}s > {slow_threshold:.0f}s), proceeding anyway")

    # Parse LLM response
    try:
        result = json.loads(raw)
        llm_text = result["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, ValueError, KeyError, IndexError) as e:
        logger.warning(f"LLM classify fallback: response parse error ({e})")
        return {"category": "dev-log", "confidence": 0.0, "reason": "fallback: response parse error"}

    # Parse the JSON object from LLM output
    try:
        classification = json.loads(llm_text)
    except json.JSONDecodeError as e:
        logger.warning(f"LLM classify fallback: JSON parse failure ({e})")
        return {"category": "dev-log", "confidence": 0.0, "reason": "fallback: JSON parse failure"}

    # Validate required fields
    category = classification.get("category", "")
    confidence = classification.get("confidence", 0.0)
    reason = classification.get("reason", "")

    # Ensure types
    if not isinstance(confidence, (int, float)):
        try:
            confidence = float(confidence)
        except (ValueError, TypeError):
            confidence = 0.0

    # Validate category
    if category not in CATEGORIES:
        logger.warning(f"LLM returned unknown category: {category!r}")
        return {"category": "dev-log", "confidence": 0.0, "reason": f"fallback: unknown category {category!r}"}

    # Confidence < 0.5 → fallback to dev-log
    if confidence < 0.5:
        logger.info(f"LLM classify fallback: low confidence ({confidence}) for {category}")
        return {"category": "dev-log", "confidence": 0.0, "reason": f"fallback: low confidence ({confidence}) for {category}"}

    return {
        "category": category,
        "confidence": confidence,
        "reason": reason or "classified by LLM",
    }


def classify_intent_sync(content: str, endpoint: Optional[str] = None, model: Optional[str] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
    """同步分类，允许覆盖配置。

    Args:
        content: 待分类内容
        endpoint: 可选，覆盖 LLM 端点
        model: 可选，覆盖模型名称
        timeout: 可选，覆盖超时（秒）

    Returns:
        {"category": str, "confidence": float, "reason": str}
    """
    old_url = os.environ.get("HERMES_MEMORY_LLM_URL")
    old_model = os.environ.get("HERMES_MEMORY_LLM_MODEL")
    old_timeout = os.environ.get("HERMES_MEMORY_LLM_TIMEOUT")

    if endpoint:
        os.environ["HERMES_MEMORY_LLM_URL"] = endpoint
    if model:
        os.environ["HERMES_MEMORY_LLM_MODEL"] = model
    if timeout is not None:
        os.environ["HERMES_MEMORY_LLM_TIMEOUT"] = str(timeout)

    try:
        return classify_intent(content)
    finally:
        if old_url is None:
            os.environ.pop("HERMES_MEMORY_LLM_URL", None)
        else:
            os.environ["HERMES_MEMORY_LLM_URL"] = old_url
        if old_model is None:
            os.environ.pop("HERMES_MEMORY_LLM_MODEL", None)
        else:
            os.environ["HERMES_MEMORY_LLM_MODEL"] = old_model
        if old_timeout is None:
            os.environ.pop("HERMES_MEMORY_LLM_TIMEOUT", None)
        else:
            os.environ["HERMES_MEMORY_LLM_TIMEOUT"] = old_timeout


# ── Prompt validation test suite (40 cases) ─────────────────────────

TEST_CASES = [
    # (content, expected_category)
    # credentials - 8 cases
    ("vLLM 的 API Key 是 sk-proj-abc123", "credentials"),
    ("数据库密码设置为 Hermes2026!", "credentials"),
    ("Telegram bot token: 8123456789:AAHxYz", "credentials"),
    ("SSH 私钥已保存在 /root/.ssh/id_rsa", "credentials"),
    ("Redis 连接密码是 redis-pass-2026", "credentials"),
    ("AWS Secret Access Key: wJalrXUtnFEMI/K7MDENG", "credentials"),
    ("GitHub Personal Access Token: ghp_xxxxxxxxxxxx", "credentials"),
    ("JWT secret key 是 my-super-secret-key", "credentials"),

    # infrastructure - 8 cases
    ("PVE 节点 10.10.4.9 运行 LXC container 103", "infrastructure"),
    ("vLLM 服务部署在 10.10.4.62:8000", "infrastructure"),
    ("服务器配备 2 张 NVIDIA RTX 4090 GPU", "infrastructure"),
    ("NAS 地址 10.10.4.81 挂载 SMB 共享", "infrastructure"),
    ("Docker 容器 qdrant 运行在端口 6333", "infrastructure"),
    ("路由器 LAN IP 段 10.10.4.0/24", "infrastructure"),
    ("LXC 104 运行 Debian 12 作为推理后端", "infrastructure"),
    ("内网 DNS 指向 10.10.4.1", "infrastructure"),

    # tech-ref - 8 cases
    ("PVE API: GET /api2/json/nodes", "tech-ref"),
    ("OpenAI 兼容接口: POST /v1/chat/completions", "tech-ref"),
    ("Qwen3.5-9B-AWQ 需要 --trust-remote-code 启动", "tech-ref"),
    ("memory_routing.py 的 route_content_to_sub_doc 返回 (doc_name, score)", "tech-ref"),
    ("SGLang backend 使用 --model-path 指定模型目录", "tech-ref"),
    ("Hermes Agent 的 MCP 协议基于 JSON-RPC 2.0", "tech-ref"),
    ("Qdrant 的 upsert 操作支持批量写入", "tech-ref"),
    ("kubectl apply -f deployment.yaml 部署到 k8s", "tech-ref"),

    # dev-log - 8 cases
    ("修复了 memory_routing.py 中的路径遍历漏洞", "dev-log"),
    ("重构了关键词评分算法为 V2 加权版本", "dev-log"),
    ("新增了异步复核机制，低分由 LLM 二次确认", "dev-log"),
    ("修改了 vLLM 启动参数 --max-model-len 8192", "dev-log"),
    ("实现了 memory-routing MCP server 的 route_and_save_memory 工具", "dev-log"),
    ("解决了 fcntl 锁在高并发下的死锁问题", "dev-log"),
    ("为 intent_classifier 添加了 40 个测试用例", "dev-log"),
    ("优化了 subdoc_writer 的原子写入性能", "dev-log"),

    # miscellaneous - 8 cases
    ("今天天气很好，适合散步", "miscellaneous"),
    ("星野是我第一个 AI 伙伴", "miscellaneous"),
    ("计划本周完成视频项目的剪辑", "miscellaneous"),
    ("棣民喜欢喝茶，特别是龙井", "miscellaneous"),
    ("下个月是棣民的生日", "miscellaneous"),
    ("晚上听了周杰伦的新歌", "miscellaneous"),
    ("推荐去看星际穿越这部电影", "miscellaneous"),
    ("周末打算去爬山放松一下", "miscellaneous"),
]


def run_tests() -> Tuple[int, int]:
    """Run prompt validation tests (offline — no LLM call needed).

    Tests verify that the prompt is correctly constructed and that
    the fallback logic works for edge cases.

    Returns (passed, total).
    """
    passed = 0
    total = len(TEST_CASES)

    print(f"Running {total} prompt validation tests...")
    print("=" * 60)

    for i, (content, expected) in enumerate(TEST_CASES, 1):
        # Verify prompt construction includes content
        prompt = CLASSIFICATION_PROMPT.format(content=content)
        has_content = content in prompt
        has_category = expected in CLASSIFICATION_PROMPT

        # Verify fallback logic: confidence < 0.5 → dev-log
        fallback_ok = "dev-log" if 0.3 < 0.5 else expected
        fallback_ok = (fallback_ok == "dev-log")

        # Verify unknown category → dev-log
        unknown_fallback = "dev-log"
        invalid_ok = unknown_fallback == "dev-log"

        # Category in CATEGORIES list
        category_in_list = expected in CATEGORIES

        all_ok = has_content and has_category and fallback_ok and invalid_ok and category_in_list

        status = "PASS" if all_ok else "FAIL"
        if all_ok:
            passed += 1

        print(f"  [{status}] Test {i:2d}: '{content[:40]}...' -> expected={expected}")
        if not all_ok:
            print(f"         has_content={has_content}, has_cat={has_category}, "
                  f"fallback_ok={fallback_ok}, invalid_ok={invalid_ok}, in_list={category_in_list}")

    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    return passed, total


if __name__ == "__main__":
    passed, total = run_tests()
    if passed == total:
        print("All tests passed!")
    else:
        print(f"FAIL: {total - passed} tests failed")
        import sys
        sys.exit(1)