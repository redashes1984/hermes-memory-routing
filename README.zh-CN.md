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
profiles/<name>/
├── memories/                    # Hermes 官方目录
│   ├── MEMORY.md               # 索引（§ 分隔，注入系统提示）
│   └── USER.md                 # 用户画像（注入系统提示）
│
└── memory/                      # 子文档（通过 read_file 按需读取）
    ├── infrastructure.md       — 基础设施、部署、硬件
    ├── philosophy.md           — 价值观、原则、关系
    ├── milestones.md           — 里程碑、版本历史
    ├── rules.md                — 约定、标准、工作流
    ├── commitments.md          — 承诺、长期约定
    └── dev-log.md              — 变更日志、迭代记录
```

**目录定义：**
- **`memories/`** — Hermes 官方目录，存放 `MEMORY.md`（索引）和 `USER.md`（用户画像）。每次会话启动时注入系统提示。
- **`memory/`** — 记忆路由的子文档存储。按主题分类的文件通过 `read_file` 按需读取，保持系统提示开销低。

子文档名称和关键词列表**完全可配置**——没有硬编码分类。

## 架构（v2.0.0 — LLM 意图分类）

Memory routing v2 使用 **MCP server** 配合 LLM 意图分类——无需修改 Hermes 源码：

```
┌──────────────────────────────────────────────────────────┐
│  route_and_save_memory(content)  ← MCP 工具              │
│  ┌────────────────────────────────────────────────────┐  │
│  │  intent_classifier.py                              │  │
│  │  ┌──────────────────────────────────────────────┐  │  │
│  │  │ LLM: Qwen3.5-9B-AWQ → 5 个分类              │  │  │
│  │  │ {credential, infrastructure, tech-ref,       │  │  │
│  │  │  dev-log, miscellaneous}                     │  │  │
│  │  │ → JSON: {category, confidence, reason}       │  │  │
│  │  └────────────────┬─────────────────────────────┘  │  │
│  │                   │                                 │  │
│  │         LLM 失败/超时？                               │  │
│  │         ┌────┴────┐                                  │  │
│  │       在线分类  关键词回退                            │  │
│  │         │     （最多 3 次重试，超时可配置）          │  │
│  │    ┌────▼────┐                                       │  │
│  │    │subdoc   │  → tempfile+os.rename（原子写入）     │  │
│  │    │writer   │  → fcntl.flock（并发安全）           │  │
│  │    │(原子)   │                                       │  │
│  │    └────┬────┘                                       │  │
│  └─────────┼───────────────────────────────────────────┘  │
│            │                                              │
│            ▼                                              │
│  MEMORY.md 索引更新                                        │
└──────────────────────────────────────────────────────────┘
```

**与 v1.x 的核心区别：**
- **无需源码补丁** — 作为独立 MCP server 运行，通过 `mcp.json` 注册
- **LLM 分类** — 93% 准确率，替代关键词评分
- **原子写入** — `tempfile + os.rename` + `fcntl.flock`，并发写入无数据丢失
- **超时可配置** — `HERMES_LLM_TIMEOUT`、`HERMES_MEMORY_SLOW_THRESHOLD`、`HERMES_LLM_RETRY_COUNT`
- **输入清洗** — 删除 null byte、category 白名单校验、prompt injection 防御

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `HERMES_LLM_BASE_URL` | `http://10.10.4.9:8000/v1` | LLM 端点 |
| `HERMES_LLM_MODEL` | `Qwen3.5-9B-AWQ` | 分类模型 |
| `HERMES_LLM_TIMEOUT` | `5` | LLM 调用超时（秒） |
| `HERMES_MEMORY_SLOW_THRESHOLD` | `10` | 慢响应阈值（秒） |
| `HERMES_LLM_RETRY_COUNT` | `2` | 超时最大重试次数 |

## 测试（v2.0.0）

| 测试 | 结果 |
|---|---|
| 离线 prompt 验证（40 条） | 40/40 ✅ |
| 在线 LLM 分类（30 条） | 27/30（93%）✅ |
| Fallback 模拟（14 条） | 14/14 ✅ |
| 端到端路由（5 个分类） | 5/5 ✅ |
| Cron 清理流水线 | ✅ |

## v1.x 架构（旧版）

三级路由机制：关键词评分 → 异步 LLM 复核 → 后备路由。关键词路由（分数 >= 3）零延迟；LLM 复核在后台运行，不阻塞主流程。

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
