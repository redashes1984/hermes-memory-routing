# Hermes 记忆路由（中文版）

[English](README.md) | [中文](README.zh-CN.md)

> 索引式记忆架构——保持 MEMORY.md 精简，自动将内容路由到主题子文档。

## 职责边界

> **上帝的归上帝，凯撒的归凯撒。**

本项目只处理一件事：**将属于 MEMORY.md 的内容路由到主题子文档。**

它**不会**触碰——也**永远不会**触碰——memOS、向量记忆、语义搜索或其他任何长期记忆管理系统。那些系统有自己的工具、存储和检索路径。记忆路由只关心系统提示注入层；其余都是别人的地盘。

## 问题

Hermes Agent 每轮对话都会将 MEMORY.md 注入系统提示。随着记忆增长，扁平文件会暴露三个问题：

1. **系统提示膨胀** — 超过 `memory_char_limit`（默认 2200 字符），条目被截断
2. **信噪比低** — 无关条目干扰模型关注当前任务
3. **维护困难** — 替换/删除条目时容易误删

## 解决方案

将记忆拆分为**索引**（MEMORY.md，始终注入）和**子文档**（按需读取）：

```
MEMORY.md (§ 分隔的索引，注入系统提示)
│
├── memory/infrastructure.md   — 基础设施、部署、硬件
├── memory/philosophy.md       — 价值观、原则、关系
├── memory/milestones.md       — 里程碑、版本历史
├── memory/rules.md            — 约定、标准、工作流
├── memory/commitments.md      — 承诺、长期约定
└── memory/dev-log.md          — 变更日志、迭代记录
```

子文档名称和关键词列表**完全可配置**——没有硬编码分类。

## 三级路由机制

```
用户: memory_tool.add(target="memory", content="...")
          │
          ▼
   ┌─────────────────┐
   │   关键词评分      │  每个子文档有一组关键词。
   │ （零延迟）       │  扫描内容 → 最高分获胜。
   └────────┬────────┘
            │
    分数 >= 3？── 是 ──▶ 直接写入子文档 ✓
            │
           否
            │
     分数 >= 1？── 是 ──▶ 写入子文档 + 异步 LLM 复核
            │                （后台线程，不阻塞）
           否
            │
        写入 fallback.md（后备）
```

### 阈值说明

| 阈值 | 行为 | 延迟 |
|------|------|------|
| `>= 3` 个关键词匹配 | 快速路径：直接写入子文档 | 零 |
| `1-2` 个关键词匹配 | 写入子文档 + 异步 LLM 复核 | 毫秒级（LLM 在后台） |
| `0` 个关键词匹配 | 写入 `memory/fallback.md` | 零 |

### V2 关键词评分算法

评分不是简单的关键词计数，而是四阶段算法：

1. **原始匹配** — 扫描内容对比所有子文档的关键词列表。
2. **冲突解决与加权** — 共享关键词（出现在多个子文档中）归到关键词列表最短的文档；关键词按长度加权：>= 3 字符 = 2.0（强），>= 2 = 1.0（中），1 字符 = 0.5（弱）。
3. **归一化与特异性加分** — 评分 = 加权分 / sqrt(总关键词数)，防止关键词列表"黑洞"；通过 log1p 公式给予特异性加分，奖励命中关键词占列表比例高的文档。
4. **决策** — 归一化评分 >= 0.6（置信），>= 0.3（暂定），< 0.3（无匹配 → 后备路由）。

同时返回原始匹配计数，用于兼容 `KEYWORD_FAST_PATH` / `KEYWORD_LLM_REVIEW` 阈值。

### 安全扫描

每次写入前，`_scan_memory_content()` 会拦截：
- **不可见 Unicode 字符**（零宽空格、BOM 等）— 防止注入 payload
- **威胁模式** — 正则检测 prompt injection、系统提示泄露、数据外泄企图

被拦截的条目会返回错误，不会写入磁盘。

### 事实缓存与冲突检测

- **事实缓存**（`.fact_cache.json`）：每次写入后，`_update_fact_cache()` 提取"主题-属性-值"三元组存入缓存。
- **冲突检测**（`_detect_fact_conflict()`）：写入前检查新内容是否与缓存事实矛盾（相同主题 + 属性，不同值）。若发现冲突，`add()` 返回 `fact_conflict` 字段，包含旧值和新值供 Agent 审查。

### 审计日志

每次子文档写入都会记录到 `.audit.jsonl`（通过 `.gitignore` 排除在 Git 之外）：
- 时间戳、目标（memory/user）、路由到的文档名、关键词评分
- 用于调试路由决策和追踪记忆增长模式

### 后备路由（Fallback）

- 0 匹配的条目写入 `memory/fallback.md`，不污染 MEMORY.md 索引
- 空闲复盘时，fallback.md 中的条目会被重新评分：匹配的移入正确子文档，不匹配的作为导航条目提升回 MEMORY.md
- 关键词审计脚本会扫描 fallback.md，建议缺失的关键词

### 异步 LLM 复核

- **模型：** 任意 OpenAI 兼容接口（可配置）
- **模式：** 后台守护线程，绝不阻塞主流程
- **修正：** 如果 LLM 与关键词结果不一致，条目会被移动
- **超时：** 30 秒（可配置）——超时则以关键词结果为准

## 环境变量

```bash
# Hermes Agent 库路径（脚本导入 memory_tool 用）
export HERMES_AGENT_LIB="/usr/local/lib/hermes-agent"

# LLM 分类器接口（任意 OpenAI 兼容 API）
export HERMES_MEMORY_CLASSIFIER_URL="http://localhost:11434/v1"
export HERMES_MEMORY_CLASSIFIER_MODEL="Qwen3-4B"
export HERMES_MEMORY_CLASSIFIER_TIMEOUT="30"
```

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `HERMES_AGENT_LIB` | 否 | `/usr/local/lib/hermes-agent` | Hermes Agent `tools/` 目录路径 |
| `HERMES_MEMORY_CLASSIFIER_URL` | 否 | `http://localhost:11434/v1` | LLM 分类器接口（OpenAI 兼容） |
| `HERMES_MEMORY_CLASSIFIER_MODEL` | 否 | `your-model` | 异步 LLM 复核使用的模型名 |
| `HERMES_MEMORY_CLASSIFIER_TIMEOUT` | 否 | `30` | 分类器超时时间（秒） |

所有变量均为可选。关键词路由（分数 >= 3）无需任何变量即可工作。LLM 复核（分数 1-2）需要 `CLASSIFIER_URL` 和 `CLASSIFIER_MODEL`。

## 关键词配置

关键词定义在 `memory_tool.py` 的 `SUB_DOCS` 字典中。添加新子文档只需插入一个新键：

```python
SUB_DOCS = {
    "<文档名>": {
        "description": "该子文档存储什么内容",
        "keywords": ["关键词1", "关键词2", "关键词3", ...],
    },
}
```

### 调优指南

- **具体优于宽泛。** `"vllm"` 比 `"model"` 更好的关键词。
- **避免跨子文档重叠。** 共享关键词会导致所有匹配文档分数虚高。
- **每份文档从 5-10 个关键词开始**，根据误分类情况逐步迭代。

## 文件写入策略

| 文件 | 格式 | 写入方式 | 去重 |
|------|------|---------|------|
| MEMORY.md | `§` 分隔 | 加锁追加 | 精确匹配 |
| 子文档 | 纯 Markdown | 原子写入（临时文件 + 重命名） | 精确匹配 |

## 设计原则

1. **MEMORY.md 是索引，不是仓库** — 保持精简，仅导航级别
2. **关键词是护栏，不是约束** — 快速分类，LLM 作为安全网
3. **异步绝不阻塞** — LLM 复核在后台运行，用户无感知
4. **原子写入** — 所有子文档写入使用临时文件 + 原子替换
5. **先去重再写入** — 相同内容在写入前被拒绝

## 测试

```python
from tools.memory_tool import route_content_to_sub_doc

# 返回 (文档名, 原始匹配计数) — 内部使用 V2 评分
doc, score = route_content_to_sub_doc("待分类内容")
print(f"→ {doc} (原始匹配计数: {score})")

# 查看详细的 V2 评分
from tools.memory_tool import SUB_DOCS
import math

def show_v2_scores(content):
    content_lower = content.lower()
    for doc_name, info in SUB_DOCS.items():
        matched = [kw for kw in info["keywords"] if kw.lower() in content_lower]
        if matched:
            weighted = sum(2.0 if len(k) >= 3 else 1.0 if len(k) >= 2 else 0.5 for k in matched)
            total = len(info["keywords"])
            norm = weighted / math.sqrt(total)
            print(f"  {doc_name:20} weighted={weighted:.1f} norm={norm:.2f}  keywords: {matched}")

show_v2_scores("要分析的内容")
```

## 仓库结构

```
hermes-memory-routing/
├── README.md                    # English version
├── README.zh-CN.md              # 中文版本
├── CHANGELOG.md                 # 版本历史
├── SKILL.md                     # Hermes Agent Skill 文档
├── .gitignore                   # Git 忽略的运行时文件
├── src/
│   └── memory_routing.py        # 核心路由逻辑（独立可运行）
├── scripts/
│   ├── memory-replay.py         # 空闲复盘与后备去重
│   └── memory-keyword-audit.py  # 关键词覆盖率审计
└── docs/
    └── design.md                # 架构设计文档
```

## 项目身份

通过人机协作构建：

| 角色 | 贡献 |
|------|------|
| **项目负责人** | 架构设计、需求分析、代码审查 |
| **AI 助手** | 实现、测试、文档编写 |

AI 助手运行在 Hermes Agent 框架上，协助项目负责人进行迭代开发。

## 变更日志

详见 [CHANGELOG.md](CHANGELOG.md)。

最新：**后备路由** — 0 匹配的条目路由到 `memory/fallback.md`，不再污染 MEMORY.md。

## 许可证

MIT

## 上游集成

将本项目作为可选功能合并到 Hermes Agent 的 `memory_tool.py`，关键新增内容如下：

### 核心函数

1. **`SUB_DOCS` 字典** — 可配置的子文档定义（描述 + 关键词列表）
2. **`get_memory_sub_docs_dir()`** — 返回记忆子文档目录
3. **`route_content_to_sub_doc(content)`** — 关键词评分：返回 `(文档名, 分数)`
4. **`classify_content_with_llm(content)`** — 可选 LLM 分类器（异步复核）
5. **`_async_llm_review(content, keyword_doc, sub_dir)`** — 后台修正线程
6. **`_add_to_sub_doc(doc_name, content)`** — 原子子文档写入 + 去重
7. **修改后的 `add()` 方法** — 写入前自动路由（仅在启用时）

### 推荐 PR 阶段

1. **阶段一：** 关键词路由 + 子文档写入基础设施（核心）
2. **阶段二：** 可选 LLM 复核（可配置，选入）
3. **阶段三：** `[include:]` 指令，支持 MEMORY.md 中显式引用子文档

### 配置开关

添加到 `config.yaml`：
```yaml
memory:
  sub_doc_routing: true        # 启用自动路由
  classifier_url: "http://localhost:11434/v1"  # 可选 LLM 接口
  classifier_model: "your-model"
```
