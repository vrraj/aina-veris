# Compound Query Expansion

[← Documentation home](index.md)

## Overview

Compound query expansion is a feature that decomposes complex, multi-part user queries into independent retrieval subqueries. This ensures that all facets of a user's question are properly retrieved and represented in the final answer, preventing one dominant aspect from crowding out others.

## How It Works

### Query Decomposition

When enabled, the system uses an LLM to analyze the user's query and identify distinct informational facets. For example:

**Original Query:** "What are the elevation and climate of Mount Everest compared to K2?"

**Decomposed Subqueries:**
1. "What is the elevation of Mount Everest?"
2. "What is the climate of Mount Everest?"
3. "What is the elevation of K2?"
4. "What is the climate of K2?"

### Retrieval Orchestration

Each subquery is executed independently against the vector database, retrieving relevant documents for that specific facet. The system then:

1. **Retrieves results** for each subquery separately
2. **Applies reranking** (ColBERT, Cross-Encoder, or LLM-based) to each subquery's results
3. **Fuses results** using Reciprocal Rank Fusion (RRF) for local reranking
4. **Applies coverage-aware pooling** for hosted LLM reranking to ensure all facets are represented

### Coverage-Aware Pooling

When using hosted LLM reranking, the system employs coverage-aware candidate pooling to prevent one subquery from dominating the final result set. This ensures:

- **Minimum anchors per subquery:** Each subquery contributes at least N results to the final pool (configurable via `compound_min_anchors_per_subquery`)
- **Pool cap:** Maximum total candidates passed to the LLM reranker (configurable via `compound_rerank_pool_cap`)
- **Balanced representation:** No single facet overwhelms the others

## Configuration

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `split_compound_queries` | bool | `false` | Enable compound query decomposition |
| `max_compound_queries` | int | `4` | Maximum number of subqueries to generate (2-8) |
| `compound_min_anchors_per_subquery` | int | `5` | Minimum results per subquery in rerank pool |
| `compound_rerank_pool_cap` | int | `40` | Maximum total candidates for LLM reranking |

### Settings Location

These parameters can be configured in:
- **Backend settings:** `backend/core/config.py`
- **Per-request:** Via the `params` object in the `/chat` API
- **Domain-specific:** Via domain configuration overrides

## Usage Examples

### API Request

```json
{
  "message": "Compare the elevation and climate of Mount Everest and K2",
  "params": {
    "split_compound_queries": true,
    "max_compound_queries": 4,
    "top_k": 8,
    "search_mode": "hybrid"
  }
}
```

### Response

The response includes a `query_plan_display` object showing the decomposition:

```json
{
  "query_plan_display": {
    "enabled": true,
    "is_compound": true,
    "original_query": "Compare the elevation and climate of Mount Everest and K2",
    "normalized_query": "Compare the elevation and climate of Mount Everest and K2",
    "queries": [
      "What is the elevation of Mount Everest?",
      "What is the climate of Mount Everest?",
      "What is the elevation of K2?",
      "What is the climate of K2?"
    ],
    "reason": "compound_query",
    "fusion_method": "rrf"
  }
}
```

## Implementation Details

### Core Components

- **Decomposition:** `backend/retrieval/compound_queries.py` - LLM-based query splitting
- **Orchestration:** `backend/retrieval/orchestration.py` - Retrieval coordination
- **Coverage Pooling:** `backend/retrieval/compound_pooling.py` - Balanced candidate selection
- **Reranking:** `backend/retrieval/compound_reranking.py` - Pairwise reranking for subqueries
- **Pipeline Integration:** `backend/chat/pipeline/stages/retrieval.py` - Stage-level integration

### Reranking Strategies

**Local Reranking (Pairwise):**
- Each subquery reranked independently with Cross-Encoder
- Results fused using RRF
- Preserves per-subquery ranking quality

**Hosted Reranking (Listwise):**
- Coverage-aware pooling before LLM reranking
- Ensures all facets represented in LLM context
- Prevents dominant facets from crowding out others

## Benefits

1. **Comprehensive Coverage:** All aspects of multi-faceted questions are addressed
2. **Balanced Results:** No single query facet dominates the answer
3. **Improved Precision:** Subqueries are more targeted than the original compound query
4. **Flexible Control:** Configurable per request or per domain
5. **Fallback Safety:** Decomposition failures gracefully fall back to single-query retrieval

## When to Use

**Enable compound query expansion when:**
- Users ask multi-part questions (e.g., "Compare X and Y regarding A, B, and C")
- Questions contain multiple distinct informational facets
- You need balanced coverage across all query aspects
- Using hosted LLM reranking with limited context windows

**Disable compound query expansion when:**
- Questions are simple and single-faceted
- You want faster retrieval (decomposition adds LLM latency)
- The query is already well-targeted
- Using local-only reranking without coverage concerns
