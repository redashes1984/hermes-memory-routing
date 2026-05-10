---
title: 记忆系统配置检查
version: 1.0.0
created: 2026-05-10
---

# 记忆系统配置检查指南

## 快速验证清单

检查星野记忆系统是否配置正确时，按以下顺序验证：

### 1. 压缩服务配置

```bash
# 检查压缩配置
grep -A 10 "compression:" ~/.hermes/profiles/nova/config.yaml

# 检查Ollama可用模型
curl -s --connect-timeout 3 "http://10.10.4.9:11434/api/tags" | python3 -c "
import sys, json
for model in json.load(sys.stdin).get('models', []):
    print(f'可用: {model.get(\"name\", \"?\")}')
"
```

**常见配置问题：**
- `provider: auto` 但 `model` 和 `base_url` 为空 → 需要手动配置
- 应配置为：`provider: custom`, `model: Qwen3-4B`, `base_url: http://10.10.4.9:11434/v1`

### 2. 向量记忆（Qdrant）

```bash
# 检查Qdrant服务
curl -s --connect-timeout 3 "http://10.10.4.79:6333" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f'服务状态: {data.get(\"status\", \"unknown\")}')
"
```

### 3. 本地记忆文档

```bash
# 检查各文档大小
ls -lh ~/.hermes/profiles/nova/memory/
```

## 记忆系统选项说明

| 类型 | 用途 | 配置位置 |
|------|------|----------|
| 本地文件记忆 | 结构化文档 | ~/.hermes/memory/ |
| Qdrant向量记忆 | 语义搜索 | 10.10.4.79:6333 |
| MCP服务器 | 扩展工具 | ~/.hermes/config.yaml |

## 压缩配置最佳实践

1. **模型选择**：
   - Qwen3-4B：适合快速压缩，资源占用低
   - Qwen2.5-14B：适合复杂理解，性能更好

2. **阈值设置**：
   - 阈值 50%：平衡压缩和保留
   - 目标比例 30%：适中压缩

3. **保护条目**：
   - 保护最后50条消息
   - 卫生硬限制400条消息

## 相关配置文件

- `~/.hermes/profiles/nova/config.yaml` - Hermes主配置
- `~/.hermes/profiles/nova/config.yaml.bak.*` - 备份配置
- `~/.hermes/CREDENTIALS.md` - 凭证文件（权限600）
