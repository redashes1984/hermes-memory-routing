---
name: memory-routing-deploy
description: "Deploy memory-routing MCP server to a new Hermes Agent profile — install.sh usage, config verification, troubleshooting"
version: 1.0.0
author: Nova
license: MIT
metadata:
  hermes:
    tags: [hermes, memory, mcp, deployment, install]
    related_skills: [nova-memory-maintenance]
---

# memory-routing 部署指南

## 一键安装

```bash
# 安装到 default profile（默认）
bash <(curl -sL https://raw.githubusercontent.com/redashes1984/hermes-memory-routing/main/install.sh)

# 安装到指定 profile
bash <(curl -sL https://raw.githubusercontent.com/redashes1984/hermes-memory-routing/main/install.sh) nova

# 或 clone 后运行
git clone --depth 1 https://github.com/redashes1984/hermes-memory-routing.git /tmp/memory-routing
bash /tmp/memory-routing/install.sh [profile_name]
```

## 安装流程

install.sh 自动完成：

1. **Clone 仓库** 到 `~/.hermes/profiles/<name>/plugins/memory-routing/`
2. **安装依赖**：`pip install mcp[fastmcp] requests`
3. **读取 profile 的 LLM 配置**（model / provider / base_url / api_key）
4. **写入 config.yaml** 的 `mcp_servers.memory-routing` 块
5. **语法检查** server.py

安装完成后需要：

```bash
# 重启 gateway
hermes gateway restart

# 或当前 session 内热重载
/reload-mcp
```

## 配置项说明

### LLM 配置（意图分类用）

install.sh 默认从目标 profile 的 `model.*` 字段继承：

| 环境变量 | 说明 | 默认继承 |
|----------|------|----------|
| `HERMES_LLM_PROVIDER` | 推理后端 provider | `model.provider` |
| `HERMES_LLM_MODEL` | 模型名 | `model.default` |
| `HERMES_LLM_BASE_URL` | 推理端点地址 | `model.base_url` |
| `HERMES_LLM_API_KEY` | 推理端点密钥 | `model.api_key`（解析 .env 变量名） |

如果 config.yaml 解析失败，脚本会交互式询问。

### config.yaml 注册结果

```yaml
mcp_servers:
  memory-routing:
    command: /usr/bin/python3
    args:
      - /root/.hermes/profiles/<profile>/plugins/memory-routing/server.py
    env:
      HERMES_MCP_SERVER_NAME: memory-routing
      HERMES_MCP_TOOLSET: memory
      HERMES_LLM_PROVIDER: custom
      HERMES_LLM_MODEL: Qwen3.6-27B-FP8
      HERMES_LLM_BASE_URL: http://10.10.4.8:8000/v1
      HERMES_LLM_API_KEY: <resolved_key>
    enabled: true
```

## 验证安装

```bash
# 1. 检查 config 注册
grep -A 10 "memory-routing:" ~/.hermes/profiles/<profile>/config.yaml

# 2. 检查 MCP 工具
hermes mcp list

# 3. 测试连接
hermes mcp test memory-routing

# 4. 功能测试
hermes chat -q '使用 route_and_save_memory 写入: 测试记忆路由'
```

## 卸载

```bash
# 1. 从 config.yaml 移除 MCP 注册
hermes mcp remove memory-routing

# 2. 删除插件目录
rm -rf ~/.hermes/profiles/<profile>/plugins/memory-routing

# 3. 恢复 config.yaml（从备份）
cp ~/.hermes/profiles/<profile>/config.yaml.bak.memory-routing ~/.hermes/profiles/<profile>/config.yaml

# 4. 重启 gateway
hermes gateway restart
```

## 排错

### 问题：`hermes mcp test memory-routing` 超时

```bash
# 检查 server.py 能否独立启动
python3 ~/.hermes/profiles/<profile>/plugins/memory-routing/server.py &
# 应该输出一行 MCP 初始化日志

# 检查依赖
python3 -c "import mcp; print(mcp.__version__)"
python3 -c "import requests; print(requests.__version__)"

# 检查 LLM 端点可达
curl -s http://10.10.4.8:8000/v1/models | head -c 100
```

### 问题：route_and_save_memory 报 LLM Connection Error

意图分类器无法连接 LLM 后端。检查：

```bash
# 1. 确认环境变量正确注入
grep HERMES_LLM ~/.hermes/profiles/<profile>/config.yaml

# 2. 手动测试 LLM 端点
curl http://<base_url>/v1/chat/completions \
  -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"<model>","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'

# 3. 如果 base_url 或 api_key 变了，重新运行 install.sh
bash /path/to/install.sh <profile>
```

### 问题：config.yaml 备份覆盖了其他配置

install.sh 用 PyYAML dump 会丢失注释。如果不想丢失：

```bash
# 恢复备份
cp ~/.hermes/profiles/<profile>/config.yaml.bak.memory-routing ~/.hermes/profiles/<profile>/config.yaml

# 手动编辑 config.yaml，参考上面「config.yaml 注册结果」的格式
# 加在 mcp_servers 节点下即可
```

### 问题：多 profile 共享同一 LLM 端点

每个 profile 的 memory-routing 独立注册，但 LLM 配置可以相同。如果多个 profile 共享同一个推理后端，install.sh 会为每个 profile 独立写入各自的 config.yaml——不会互相影响。

## AGENTS.md 集成

安装后建议在 AGENTS.md 添加记忆路由规则：

```markdown
## 记忆路由
- 写入必须通过 route_and_save_memory（memory-routing MCP server）
- 严禁直接使用 memory tool 写入跨 session 持久化内容
- route_and_save_memory 会自动分类并更新 MEMORY.md 索引
```