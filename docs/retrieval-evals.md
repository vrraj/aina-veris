# Retrieval Evals User Guide

## Overview

The Retrieval Evals tool allows you to test and debug the retrieval and reranking pipeline independently of the full chat system. It's designed to help you understand how different retrieval configurations affect the quality of results returned from your vector database.

## Access

Navigate to the Retrieval Evals page at: `/retrieval-evals` or click the "Retrieval Evals" button in the sidebar of the main chat interface.

## Configuration Sections

### Section 1: Retriever

This section controls the initial retrieval from the vector database.

**Domain Selection**
- Choose which domain/collection to search against
- Each domain has its own embedding model and collection configuration
- Default domains include: default, mountains, oceans, finance, backpacking

**Search Mode**
- **Dense**: Uses semantic embeddings only (standard vector similarity)
- **Sparse**: Uses lexical/keyword matching only (like traditional search)
- **Hybrid**: Combines dense + sparse vectors using Qdrant's built-in RRF fusion
  - Requires the collection to have both dense and sparse vectors
  - Provides better results by combining semantic and lexical signals

**Top-K Retrieval Items Per Query**
- Number of documents to retrieve per query (default: 8)
- Higher values improve recall but increase downstream processing cost

**Search Query**
- The natural language query to test
- Can be any question or statement relevant to your domain

**Split Compound Query**
- When enabled: Decomposes complex multi-part questions into independent subqueries
- Example: "Compare elevation and climate of Everest and K2" → 4 separate queries
- Each subquery is retrieved independently, then results are fused
- Uses LLM-based decomposition for intelligent query splitting

**Maximum Subqueries**
- Controls how many subqueries to generate when compound query is enabled (2-8, default: 4)
- More subqueries provide better coverage but increase cost and latency

**Score Threshold (Dense only)**
- Minimum similarity score for dense vector retrieval (0-1, default: 0.35)
- Filters out low-quality matches
- Only applies to dense search mode

**URL Filter (optional)**
- Restricts retrieval to documents from a specific URL
- Useful for testing retrieval from a particular source

**Include Payload**
- When checked: Returns full document metadata with results
- When unchecked: Returns only IDs and scores (faster, less data)

**Exact Match**
- When enabled: Uses HNSW exact search (faster, potentially less optimal)
- When disabled (default): Uses ANN approximate search (slower, potentially better quality)
- Trade-off between speed and result quality

### Section 2: Reranker

This section controls optional reranking stages that improve result quality.

**Enable Colbert Late Interaction Rerank**
- When enabled: Applies ColBERT token-level rescoring to retrieval results
- Provides fine-grained relevance scoring at the token level
- Particularly useful for code-heavy or symbol-heavy domains
- **Colbert Top-N**: Number of results to keep after ColBERT rescoring (default: 8)

**Enable Cross Encoder Rerank**
- When enabled: Applies cross-encoder reranking for improved relevance
- Uses a separate model to score document-query pairs
- Can significantly improve result quality for ambiguous queries
- **Cross Encoder Top-N**: Number of results to keep after reranking (default: 5)

**Ensure Subquery Coverage**
- When enabled (default): Ensures each subquery contributes minimum results to final pool
- Prevents one dominant subquery from crowding out others
- Only relevant when compound query expansion is enabled

**Minimum Results Per Subquery**
- Minimum number of results each subquery must contribute (1-3, default: 1)
- Higher values ensure balanced coverage but may reduce overall quality

**Maximum Reserved Results**
- Maximum number of results reserved for coverage (1-20, default: 4)
- Controls how many slots are reserved for subquery anchors before filling with best overall results

## Result Sections

### Query Decomposition

Shows how your query was processed:

**Compound**: Whether the query was split into subqueries (yes/no)
**Normalized**: The cleaned/processed version of your query
**Reason**: Why decomposition happened (e.g., "compound_query" or "disabled")
**Queries**: List of subqueries that were executed (if compound query enabled)

### Request Payload

Shows the exact JSON payload sent to the backend. Useful for:
- Debugging configuration issues
- Copying requests for API testing
- Understanding parameter mappings

### Section 1: Retrieval Results

Shows the initial results from the vector database:

**Requested mode**: The search mode you selected (dense/sparse/hybrid)
**Effective mode**: The actual mode used (may differ if fallback occurred)
**Queries**: Number of queries executed (1 for single, >1 for compound)
**Fusion**: Fusion method used (none for single, reciprocal_rank_fusion for compound)
**Returned**: Number of results returned

Each result card shows:
- Source URL and section information
- Chunk index
- Retrieval score (similarity to query)
- Matched queries (if compound query enabled) - shows which subquery(s) matched this document
- Document text content

### Section 3: ColBERT Results

Shows results after ColBERT late interaction rescoring (if enabled):

**Model**: ColBERT model used
**Top-N**: Number of results kept
**Returned**: Number of results after rescoring

Each result shows:
- ColBERT score (token-level relevance)
- Document information
- Original retrieval score for comparison

### Section 4: Cross-Encoder Reranked Results

Shows final results after cross-encoder reranking (if enabled):

**Model**: Cross-encoder model used
**Enabled**: Whether reranking was actually performed
**Returned**: Number of final results

**Coverage Information** (if compound query enabled):
- **Coverage**: Whether coverage enforcement was enabled
- **Covered**: Number of subqueries with minimum results
- **Reserved**: Number of slots reserved for coverage
- **Satisfied**: Whether coverage guarantee was met
- **Uncovered**: Subqueries that didn't meet minimum requirements

Each result shows:
- Cross-encoder score (pairwise relevance)
- Document information
- Previous scores for comparison
- Matched queries (if compound query enabled) - shows which subquery(s) matched this document

## Common Use Cases

### Testing Different Search Modes

1. Set your domain and query
2. Try "dense" mode first (baseline)
3. Try "sparse" mode (keyword matching)
4. Try "hybrid" mode (combined)
5. Compare result quality and relevance

### Debugging Compound Query Expansion

1. Enable "Split Compound Query"
2. Run a multi-faceted query (e.g., "Compare X and Y regarding A, B, and C")
3. Check "Query Decomposition" to see how it was split
4. Review "Retrieval Results" to see per-subquery performance
5. Adjust "Maximum Subqueries" if needed

### Comparing Reranking Strategies

1. Run with no reranking (uncheck both ColBERT and Cross-Encoder)
2. Run with ColBERT only
3. Run with Cross-Encoder only
4. Run with both enabled
5. Compare the ranking and scores in each case

### Testing Domain-Specific Retrieval

1. Select different domains from the dropdown
2. Use the same query across domains
3. Compare how different embedding models and collections perform
4. Check if domain-specific content is being retrieved correctly

## Interpreting Results

### Good Retrieval Indicators

- High retrieval scores (close to 1.0 for dense)
- Relevant document content matching query intent
- Appropriate sources/sections for the query
- Multiple relevant results (not just one perfect match)

### Good Reranking Indicators

- Improved ordering of results after reranking
- Higher cross-encoder scores for more relevant documents
- Better top-N results (most relevant documents at the top)
- Stable results across multiple runs

### Coverage Indicators (Compound Queries)

- **Satisfied: yes** - All subqueries have minimum representation
- **Covered: X/Y** - How many subqueries met the minimum
- **Uncovered** - Which subqueries need more results (if any)

## Tips and Best Practices

1. **Start simple**: Begin with dense search, no reranking to establish a baseline
2. **Iterate gradually**: Add one feature at a time (e.g., enable ColBERT, then Cross-Encoder)
3. **Use representative queries**: Test with queries typical of your use case
4. **Check domain configuration**: Ensure your domain has the right vector types for your search mode
5. **Monitor latency**: Compound queries and reranking add processing time
6. **Review coverage**: For compound queries, ensure all facets are represented in results

## Troubleshooting

**No results returned**
- Check if the domain has indexed documents
- Verify search mode matches available vector types (hybrid requires both dense and sparse)
- Lower the score threshold
- Try a simpler query

**Unexpected results**
- Check the domain configuration (embedding model, collection)
- Verify the query matches the domain content
- Try different search modes
- Check if URL filter is too restrictive

**Compound query not splitting**
- Ensure "Split Compound Query" is checked
- Try a more clearly multi-faceted query
- Check the decomposition reason in the results

**Reranking not improving results**
- The initial retrieval may already be high quality
- Try increasing top-k to give reranking more candidates
- Check if the reranker model is appropriate for your domain
- Verify reranking is actually enabled (check the "Enabled" field in results)
