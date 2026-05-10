# Memory Routing vs memOS — Relationship & Boundaries

## Core Difference

Memory routing operates at the **system prompt layer** (always-on, zero-latency). memOS operates at the **semantic search layer** (on-demand, vector retrieval).

## Comparison

| Dimension | Memory Routing | memOS |
|-----------|---------------|-------|
| Storage | Local Markdown files | Neo4j + Qdrant + Redis |
| Injection | System prompt (MEMORY.md index) | MCP tool calls (search/add) |
| Retrieval | File navigation + read_file | Vector semantic search (embedding) |
| Classification | Keyword scoring + async LLM review | Auto-embedding + type tags (text/pref/tool/skill) |
| Dependencies | None (pure file ops) | Independent systemd service + backend cluster |
| Scales to | Hundreds of entries (char limit) | Thousands/millions of entries |

## No Functional Overlap

Both solve different problems:
- **Memory routing** prevents MEMORY.md char-limit truncation and token waste in system prompts
- **memOS** provides semantic recall across vast memory corpora

Analogy: Memory routing is a "bookmark index" — memOS is a "search engine."

## Potential Synergies

1. Sub-doc content can be bulk-loaded into memOS as cold-start data (batch embedding)
2. Keyword lists can inform memOS search prompt templates
3. memOS preference memory can feed back into rules.md sub-doc
