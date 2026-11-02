# W12 Seam List — Tuesday Unedited

> Written on Tuesday morning before integration work began.
> Status marked as discovered during the week.
> fixed = resolved and tested | accepted = known gap, documented | ran-out-of-time = deferred

| # | Seam | Description | Status |
|---|------|-------------|--------|
| S1 | RAG retriever → Agent context | Endorsement chunks from FAISS passed into agent context. Format mismatch between retriever dict keys and agent expected fields. | fixed |
| S2 | Agent tool call → MCP execution | `types.Part.from_function_response` must wrap JSON-serialisable dict, not raw string. MCP returns string; agent layer parses before passing to Gemini. | fixed |
| S3 | MCP server output → Agent observation | Multi-server MCP sessions share a `tool_map` dict; name collision if two servers expose same tool name. Last-write-wins (last registered server wins). | fixed |
| S4 | Adjuster notes PII → MCP response | `get_adjuster_notes` MCP wrapper passed raw adjuster note text including names, addresses, injuries to agent context and any external MCP client. | fixed |
| S5 | Loss date → Endorsement version selector | `search_policy_exclusions` (W9) has no date param. New `search_policy_wording_by_date` in W12 MCP server enforces date filter in code via ENDORSEMENT_DATE_MAP. | fixed |
| S6 | Agent referral output → Eval assertion | Agent's final text sometimes contained coverage language before `refer_to_adjuster` was called. System prompt updated; `assert_no_coverage_word` assertion added. | fixed |
| S7 | Trace ID propagation across agent + MCP hops | Trace ID was generated per-call but not forwarded into MCP calls or JSONL log. UUID now assigned at FNOL entry and included in every span and JSONL record. | fixed |
| S8 | Per-span cost attribution | W7 agent tracked cumulative cost but not per-span. `SpanTracker` class added in W12 agent with named start/end per span (retrieval, generation, tool_calls, mcp_discovery). | fixed |
| S9 | W6 eval suite → Integrated system output format | W6 eval expected `summary` string field; W12 agent returns structured dict. Adapter maps `referral_reason` to `summary` in eval runner. | fixed |
| S10 | W8 trajectory eval → W12 agent steps_executed | W8 eval parses `steps_executed` as `"tool_name({args})"` strings. W12 agent produces same format from MCP call log. Verified compatible. | fixed |
| S11 | Sub-limit vs deductible vs policy-limit as 3 fields | W7 `compute_payout` tracked only deductible + payable. W12 `lookup_policy_coverage_in_force` adds `sub_limits` dict and `policy_limit`. Agent extracts all three into result dict. | fixed |
| S12 | "covered" word suppression | System prompt said never say "covered" but did not prevent it in text completion paths. `assert_no_coverage_word` now enforced; `refer_to_adjuster` scrubs forbidden phrases. | fixed |
| S13 | Citation format (endorsement version + loss date) | Old citations: `"HO-0304 §IV E-17"` — no version or date. New format enforced: `[Endorsement: X Ed. Y | §IV E-N | Loss date: Z]`. Assertion validates on every result. | fixed |
| S14 | Date-boundary retrieval (2024 loss → 2025 wording bug) | A 2024 loss retrieved 2025 wording because the 2025 text ranked higher semantically. `ENDORSEMENT_DATE_MAP` in `w12_mcp_server.py` enforces correct version by date range in code. | fixed |
| S15 | W12 MCP server consumed by external squad client | The `w12-policy-coverage` server must be callable by another squad's agent on Thursday using only the MCP protocol (no source access). | accepted |

---

**Notes:**
- S4 (PII in adjuster notes) was the highest-severity seam — raw notes with names, addresses, and injury descriptions visible to any MCP client.
- S14 (date-boundary retrieval) is the core wound from the capstone brief.
- S15 `accepted` because external client test requires a Thursday reviewer from another squad.
