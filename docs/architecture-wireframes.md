# Aina-Veris architecture image brief

Create two clean, wide technical architecture diagrams for **Aina-Veris**, a
domain-aware RAG research runtime. Use a white background, thin dark-gray
outlines, black text, minimal blue accents, simple line icons, and generous
whitespace. The diagrams should read as technical documentation, not a
marketing graphic. Use at most five or six major boxes per diagram. Avoid
gradients, 3D elements, logos, dense paragraphs, or long feature lists.

## `aina-veris-technical-architecture.png`

**Title:** Aina-Veris Architecture  
**Subtitle:** Domain-aware research through REST, A2A, and MCP

Show the request and integration flow from top to bottom.

1. **Consumers**

   Three simple columns:

   - AI agents / A2A
   - External applications / REST API
   - Websites / embedded chat + SSE

2. **Aina-Veris integration surface**

   One box containing:

   - A2A agent routes
   - REST API and SSE
   - MCP server

   Include a small callout:

   ```text
   Semiconductor AgentCard
   /agents/veris-semiconductor-research-agent/
   .well-known/agent-card.json
   ```

3. **Domain routing and research orchestration**

   The central, largest box. Show:

   ```text
   Semiconductor agent → semiconductor_memory
   Finance agent → finance
   Mountains agent → mountains

   Retrieve → rerank → context + metadata → model → cited answer
   ```

4. Bottom row has two boxes:

   - **Qdrant domain collections:** Semiconductor memory, Finance, Mountains
   - **Tools and external systems:** MCP client, Tavily web search, weather,
     airports, chart rendering

5. Draw a dotted perimeter around the integration surface and orchestration
   boxes, labelled:

   ```text
   Deployment security boundary — authentication, authorization, and Origin policy
   ```

**Arrows:** consumers → integration surface → orchestration → Qdrant/tools →
response back to consumers.

## `aina-veris-components.png`

**Title:** Aina-Veris Components  
**Subtitle:** Configurable research pipeline with evidence-preserving answers

This is an internal component view, not an integration diagram.

1. **Registries**

   A small upper-left box:

   - Domains and Qdrant collections
   - Prompts
   - Local and hosted models
   - Tools, MCP servers, output adapters

2. **Runtime policy**

   A small box beside the research pipeline:

   ```text
   Runtime policy
   • Retrieval: top_k, score threshold, dense / sparse / hybrid
   • Ranking: ColBERT, cross-encoder, RRF
   • Models: embedding, reranker, rewrite, generation
   • Generation: token limit, temperature, tool-call limit
   ```

   Draw arrows into this box from **Registries**, **REST request parameters**,
   and **A2A / MCP agent policy**. Add this note beneath it:

   ```text
   REST callers may provide bounded overrides.
   A2A and MCP research tools use server-owned domain and limits.
   ```

3. **Research pipeline**

   The central, largest box. Use this simple horizontal sequence:

   ```text
   Question → resolve / rewrite → retrieve → RRF + rerank
   → evidence context → model → cited answer
   ```

   Under **evidence context**, explicitly show:

   ```text
   Source: base_url
   Document title
   Section
   Chunk text
   ```

4. **Evidence and storage**

   Bottom-left box:

   - Indexed PDFs, URLs, documents
   - Qdrant vectors
   - Source metadata
   - Domain collections

5. **Delivery and visibility**

   Bottom-right box:

   - REST response
   - A2A research artifact
   - MCP tool result
   - SSE orchestration events

**Arrows:** registries, REST request parameters, and A2A / MCP agent policy
configure runtime policy; runtime policy configures the pipeline; evidence and
storage feeds retrieval; the pipeline sends answers to delivery and visibility.
Do not show individual protocols again except in the delivery box.

## Visual intent

Use short labels only. The main message is that Aina-Veris routes a request to
a domain-specific knowledge base, preserves evidence metadata through the
pipeline, can call tools, and returns grounded research through several
interfaces.
