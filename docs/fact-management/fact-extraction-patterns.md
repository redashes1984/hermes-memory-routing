# Fact Extraction Regex Patterns

Used by `scripts/fact_cache.py` to extract factual triples from memory entries.

## Pitfall: Subject capture group must use \S+ not [\w\-\.]+

The original pattern used `[\w\-\.]+` for subject capture after CT/VM/容器, which failed to match multi-word subjects like "CT 108 sglang-lxc" (only captured "108"). Fixed by switching to `\S+(?:\s+\S+)?` which captures one or two space-separated tokens.

## Infrastructure Patterns

| Type | Pattern | Example |
|------|---------|---------|
| IP | `(CT\|VM\|容器\|宿主机\|服务)\s+\S+(?:\s+\S+)?` + `IP\|地址` + `\d+.\d+.\d+.\d+` | "CT 108 sglang-lxc 的 IP 是 10.10.4.7" |
| Port | `(服务\|容器\|CT\|VM)\s+\S+(?:\s+\S+)?` + `端口\|port` + `\d+` | "容器 nova 的端口 8000" |
| Version (explicit) | `(vllm\|ollama\|qdrant\|...)` + `版本\|version\|v` + `[vV]?[\d.]+` | "Qdrant 版本 1.17.1" |
| Version (implicit) | `(vllm\|ollama\|qdrant\|...)` + space + `v?[\d.]+` | "vLLM 0.20.1" |
| Status | `(服务\|容器\|CT\|VM)\s+\S+` + `状态\|status` + `running\|stopped` | "CT 105 状态 running" |
| Model | `(容器\|CT\|VM\|端点\|服务)\s+\S+` + `模型\|model` + `[\w\-\.]+` | "容器 nova 的模型 Qwen3.6-27B-FP8" |

## User Preference Patterns

| Type | Pattern |
|------|---------|
| Preference | `(棣民\|用户)` + `偏好\|喜欢\|希望\|倾向\|认为\|决定\|要求` + rest |
| Communication | `(棣民\|用户)` + `沟通\|交流\|消息\|通知\|通报` + rest |
| Naming | `(棣民\|用户)` + `称呼\|叫\|名字\|name` + rest |

## Philosophy Patterns

| Type | Pattern |
|------|---------|
| Philosophy | `(棣民\|用户)` + `哲学\|原则\|价值观\|核心[信念信理]\|认为\|相信\|说` + rest |

## Testing

```bash
python3 scripts/fact_cache.py test
```
