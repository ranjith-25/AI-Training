# Failure Mode Taxonomy

Based on the manual open-coding of a seeded random sample of 20 traces, we have clustered the system's errors into the following legibly named failure modes:

### 1. Wrong Form Edition Confusion
The assistant fails to distinguish between different editions (e.g., 2022 vs 2024) of the same endorsement form, often applying older superseded rules to newer policies.

### 2. Broad Exclusion Misapplication
The assistant incorrectly applies very broad exclusions (like "general deterioration") to inappropriately deny claims that involve specific named perils or sudden events.

### 3. Numeric Sub-Limit Blindness
When asked for specific numbers, deductibles, or sub-limits, the assistant frequently hallucinates a generic dollar amount (e.g., $100,000) or defensively points the user to a broad policy section instead of calculating or citing the actual threshold.

### 4. Single-Endorsement Tunnel Vision
For complex scenarios requiring synthesis across multiple forms, the assistant stops searching or reasoning after finding a single relevant endorsement, completely missing the others.

### 5. Definitional Tone Deafness
When asked "What is X?" or "How is Y defined?", the assistant ignores the intent of the question and responds with a generic "Yes, this is covered" as if evaluating a claim scenario.

### 6. Ambiguity Overconfidence
When presented with ambiguous or underspecified scenarios, instead of asking for clarification or refusing to answer, the assistant hallucinates extremely confident outcomes (e.g., "fully covered with no deductible applied").

---

## Next Week's Target

**Prediction:** Next week, we will attack **Single-Endorsement Tunnel Vision**. 

*Rationale:* Failing to synthesize across multiple forms is a critical structural limitation of naive Top-K semantic search. By moving to a structure-aware retrieval strategy (like recursive retrieval or summary-based cross-referencing), we can force the LLM to consider the full suite of endorsements rather than anchoring to the first high-scoring chunk.
