# Cost Attribution by Stage

**Target Request:** `470595c1-1de6-494e-a869-71a589518f32`  
**Query:** *"Under HO-0304 ed. 03-24, if a sewer backs up into the dwelling, is the damage covered?"*  
**System Architecture:** RAG Pipeline (`embed` -> MongoDB Atlas Vector Search -> `gemini-3.6-flash`)

---

## 1. Stage-by-Stage Breakdown

### Stage 1: Retrieval
Retrieval consists of two distinct sub-operations: dense query vectorization and exact nearest-neighbor vector index search.

1. **Query Embedding Sub-Span:**
   - **Model:** `gemini-embedding-001` (3072-dimensional embedding).
   - **Input Tokens:** 21 tokens.
   - **Model Pricing:** $0.000020 per 1,000 tokens ($0.00000002 per token).
   - **Compute Cost:** 21 * $0.00000002 = **$0.00000042**.
   - **Sub-Span Latency:** 85.0 ms.

2. **MongoDB Atlas Vector Search Sub-Span:**
   - **Operation:** MongoDB Atlas `$vectorSearch` with exact ENN (`exact: true`, cosine metric) querying the `chunks_structure_aware` collection.
   - **Candidates Fetched:** Top-5 scored chunks (`HO-0304_struct_002`, `HO-0304_struct_003`, `HO-0304_struct_000`, `HO-0304_struct_hdr`, `HO-0304_struct_001`).
   - **Token Count:** 0 tokens.
   - **Marginal Cost:** $0.00000000 (covered under MongoDB Atlas cluster infrastructure base tier).
   - **Sub-Span Latency:** 45.0 ms.

- **Retrieval Stage Totals:**
  - **Latency:** 130.0 ms (25.9% of total latency)
  - **Tokens:** 21 tokens (1.8% of total tokens)
  - **Cost:** **$0.00000042** (0.45% of total request cost)

---

### Stage 2: Generation
Generation passes the system instructions, the retrieved 5 chunk excerpts, and the user prompt to Gemini for grounded answer synthesis.

1. **Prompt Ingestion Sub-Span:**
   - **Model:** `gemini-3.6-flash`
   - **System Instruction (`RAG_SYSTEM_PROMPT`):** 134 tokens
   - **Context Excerpts (5 chunks):** 968 tokens
   - **User Query & Formatting Template:** 18 tokens
   - **Total Input Tokens:** 1,120 tokens
   - **Input Token Pricing:** $0.075 per 1,000,000 tokens ($0.000000075 per token).
   - **Input Cost:** 1,120 * $0.000000075 = **$0.00008400**.

2. **Completion Output Sub-Span:**
   - **Generated Text:** *"Yes, this is covered according to the policy terms. [Source: HO-0304_struct_002 | HO-0304, COVERAGE MODIFICATIONS]"*
   - **Output Tokens:** 28 tokens
   - **Output Token Pricing:** $0.300 per 1,000,000 tokens ($0.000000300 per token).
   - **Output Cost:** 28 * $0.000000300 = **$0.00000840**.

- **Generation Stage Totals:**
  - **Latency:** 371.5 ms (74.1% of total latency)
  - **Tokens:** 1,148 tokens (98.2% of total tokens)
  - **Cost:** **$0.00009240** (99.55% of total request cost)

---

### Stage 3: Tools
In the claims-assistant RAG endpoint (`/rag/ask`), vector retrieval is executed prior to generation and all reasoning is handled in-context; no secondary agentic tool loops or external REST APIs were invoked.

- **Tools Stage Totals:**
  - **Latency:** 0.0 ms (0.0%)
  - **Tokens:** 0 tokens (0.0%)
  - **Cost:** **$0.00000000** (0.00%)

---

## 2. Summary Attribution Table

| Stage | Sub-Operations | Latency (ms) | Latency Share (%) | Tokens | Token Share (%) | Cost (USD) | Cost Share (%) |
|---|---|---|---|---|---|---|---|
| **Retrieval** | `gemini-embedding-001` + MongoDB `$vectorSearch` | 130.0 ms | 25.9% | 21 | 1.8% | $0.00000042 | 0.45% |
| **Generation** | `gemini-3.6-flash` (Input: 1,120 / Output: 28) | 371.5 ms | 74.1% | 1,148 | 98.2% | $0.00009240 | 99.55% |
| **Tools** | None (Single-pass RAG) | 0.0 ms | 0.0% | 0 | 0.0% | $0.00000000 | 0.00% |
| **Total** | **Full End-to-End Query** | **501.5 ms** | **100.0%** | **1,169** | **100.0%** | **$0.00009282** | **100.0%** |

---

## 3. Engineering Takeaways

1. **Generation Drives 99.55% of the Bill:**  
   Optimizing embedding dimensionality, caching embeddings, or refactoring MongoDB vector queries saves virtually nothing financially ($0.00000042/query). Any serious cost optimization must target input context token volume (e.g., trimming retrieved chunk headers or dynamic top-k).
2. **Retrieval Accounts for 25.9% of Latency:**  
   While retrieval is virtually free in dollars, its 130 ms execution time represents over a quarter of total request duration.
