# Embedding + Reranker for Memory Routing — Full Test Report

**Date:** 2026-05-10
**Status:** **REJECTED** for current 6-sub-doc scenario. Keyword-only (95%) > any hybrid approach.
**Context:** Nova profile, 106 memory entries, keyword accuracy at 95% plateau

---

## Test Environment

| Service | Host:Port | Model | Capability |
|---------|-----------|-------|------------|
| Qwen3-Embedding-0.6B | 10.10.4.81:8003 | Qwen3-Embedding-0.6B | 1024-dim vectors via `/v1/embeddings` |
| Qwen3-Reranker-0.6B | 10.10.4.81:8004 | Qwen3-Reranker-0.6B | Cross-encoder reranking via `/v1/rerank` |
| Qwen3-4B (llama-cpp) | 10.10.4.81:11434 | Qwen3-4B-Instruct-2507 Q4_K_M | LLM via OpenAI-compatible API |
| GPU | 10.10.4.81 | NVIDIA RTX PRO 6000 Blackwell 96GB | All 3 services share one GPU |

---

## Results

### Reranker-Only (25 misrouted entries from keyword audit)

Accuracy: **12/25 (48%)** — WORSE than keyword baseline

Reranker misclassified 13/25 entries. Key failures:
- Technical fragments ("API Key", "compaction") → routed to `commitments`/`philosophy`
- "主端点故障时自动切换" → routed to `infrastructure` instead of `rules` (correct)
- "让花成花，让树成树" → routed to `commitments` instead of `philosophy`

### Hybrid Strategy (keyword >=3, else reranker)

Accuracy on full 106 entries: **72/106 (68%)** — WORSE than keyword-only (95%)

**34 hybrid failures** — reranker OVERWRROTE correct keyword classifications with wrong ones.

### Full Comparison (106 entries)

| Strategy | Correct | Accuracy |
|----------|---------|----------|
| **Keyword-only** | **101** | **95%** |
| Reranker-only | 64 | 60% |
| Hybrid (keyword>=3 or reranker) | 72 | 68% |

---

## Root Cause Analysis

### Why Reranker Underperforms

1. **Reranker is a relevance scorer, not a classifier.** It scores query-document relevance, which inherently biases toward emotional/relational content. `commitments` absorbed 14/26 reranker predictions.

2. **Short technical fragments have no semantic handle.** Examples:
   - `**API Key：** 见 CREDENTIALS.md` → reranker scored `dev-log` (10.8), correct is `infrastructure`
   - `compaction: safeguard, reserveTokensFloor=65536` → reranker scored `commitments` (11.8), correct is `infrastructure`
   - `**地址：** 10.10.4.8` → reranker scored `commitments` (8.9), correct is `infrastructure`

3. **Keyword 1-2 matches are often CORRECT.** The reranker overwrites these correct (but low-confidence) keyword results with wrong high-confidence reranker scores.

4. **Anchor descriptions are too abstract.** The reranker compares content against:
   - `"推理后端、PVE 容器、向量记忆、网络拓扑、硬件配置"`
   - `"棣民的核心哲学、AI记忆自主权、放手与传承、关系本质"`
   
   These are high-level summaries, not specific enough for precise classification.

### Overbroad Keyword Anti-Patterns (from tuning)

| Keyword | Problem | Resolution |
|---------|---------|------------|
| `pr` | Matches "compaction", "Pruning", "prompt" | Removed |
| `memory` | Matches everything memory-related | Removed from infrastructure |
| `gateway` | Matches "GATEWAY_TOKEN" | Removed |
| `时间` | Matches work schedules in philosophy | Removed from milestones |
| `节奏` | Matches "决策节奏" (commitments) | Removed from philosophy |
| `ai` | Matches "AI 的一部分" (philosophy) | Removed from commitments |

---

## When Embedding/Reranker WOULD Be Useful

These use cases are valid but different from routing:

1. **Sub-doc count > 20** — Keyword maintenance becomes proportional to N^2. Semantic anchoring scales linearly.

2. **Cross-document semantic search** — "Which memories relate to this current topic?" — keyword search is too literal.

3. **Memory deduplication** — Embedding similarity (cosine > 0.95) detects semantic duplicates that keyword matching misses.

4. **Long-form content classification** — Multi-sentence memory entries have richer semantics for reranker to work with.

---

## Conclusion

For the current 6-sub-doc, short-phrase memory entries (Chinese), keyword routing at 95% is the optimal approach. Do not introduce embedding/reranker for routing until:
- Sub-doc count grows significantly, OR
- Entry length/content style changes (longer, more narrative)

The embedding/reranker services on 10.10.4.81 remain available for future use cases (dedup, semantic search).
