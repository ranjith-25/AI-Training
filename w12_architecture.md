# W12 Architecture — Claims Triage & Coverage Assistant

## One-Page Overview

**Entry point:** `python w12_agent.py --claim CLM-XXXX`
**Eval suite:** `python w12_eval_suite.py`

A single `trace_id` (UUID) is assigned at FNOL entry and propagates through every span, MCP call, and JSONL log record.

---

## Architecture Diagram

```mermaid
flowchart TD
    A["FNOL Input\n--claim CLM-2024-7002"] --> B["w12_agent.py\nEntry Point\none command, one file"]
    B --> C["trace_id = uuid4()\nSpanTracker starts"]
    C --> D["MCP Client\nRuntime tool discovery\ntools/list from all 3 servers"]

    subgraph MCP["MCP Servers — w12_config.json"]
        E["w12-policy-coverage\nw12_mcp_server.py"]
        F["claims-system\nw9_claims_mcp.py"]
        G["policy-server\nw9_policy_mcp.py"]
    end

    D -->|stdio| E & F & G

    E --> E1["search_policy_wording_by_date\nloss_date → ENDORSEMENT_DATE_MAP\ncode-enforced, not prompt hint"]
    E --> E2["lookup_policy_coverage_in_force\ndeductible / sub_limit / policy_limit\n3 distinct numbers"]
    E --> E3["refer_to_adjuster\nTerminal — never says covered\nPII redacted before return"]
    F --> F1["get_claim\nget_adjuster_notes\n_redact_pii applied"]
    G --> G1["search_policy_exclusions\nlegacy — prefer wording_by_date"]

    D --> H["ReAct Agent Loop\n8 iter max\n4 budget guards\ngemini-3.5-flash-lite"]
    H -->|"Span: generation"| H
    H -->|"Span: tool_calls"| E1 & E2 & E3 & F1 & G1
    H --> I["refer_to_adjuster\nTerminal action"]
    I --> J["JSONL Trace\ndata/w12_traces.jsonl\ntrace_id intact, PII held"]
    J --> K["w12_eval_suite.py\n1 command — all assertions\nW6 + dates + trajectory + cost"]
```

---

## Where LLM Calls Happen

| Location | What | Model |
|----------|------|-------|
| `w12_agent.py` ReAct loop | Each iteration | `gemini-3.5-flash-lite` |
| `eval/judge_eval.py` | LLM-as-judge (25 cases) | `gemini-3.5-flash-lite` |

**No LLM calls inside MCP servers** — servers are pure data/logic providers.

---

## Where the Money Goes

| Span | % of Latency | % of Cost |
|------|-------------|-----------|
| MCP discovery | ~5% | <0.1% |
| Generation (LLM) | ~80% | ~99.5% |
| Tool calls (MCP) | ~15% | ~0.5% |

**Cost per claim (p50): \$0.000425** | **p95: \$0.000891**  
At 18,000 FNOLs/week (hailstorm): **\$7.65/week**. Rate limits break first, not cost.

---

## PII Boundary

```
Adjuster notes (raw PII)
        ↓
  get_adjuster_notes MCP wrapper
        ↓
  _redact_pii() ← HERE before any return
        ↓
  Agent context (PII-free)
        ↓
  JSONL trace (PII-free)  +  External squad MCP client (PII-free)
```

---

## Key Constraints

| Constraint | How Enforced |
|------------|-------------|
| Endorsement version = loss date | `ENDORSEMENT_DATE_MAP` in code; `search_policy_wording_by_date` picks by date range |
| Never says "covered" | System prompt + `refer_to_adjuster` scrubs + `assert_no_coverage_word()` in eval |
| 3 distinct financial figures | `lookup_policy_coverage_in_force` returns `deductible`, `sub_limits{}`, `policy_limit` |
| Single trace_id | UUID at FNOL, forwarded to all spans and JSONL |
| PII held | `_redact_pii()` at MCP boundary + agent context + JSONL write |

---

## File Map

```
d:\AI-Training\
├── w12_agent.py          ← Entry point
├── w12_mcp_server.py     ← W12 MCP server (date-scoped wording, in-force check, referral)
├── w12_config.json       ← 3 MCP servers
├── w12_eval_suite.py     ← Full eval suite (one command)
├── w12_error_taxonomy.py ← 7-mode taxonomy (3 integration-created)
├── w12_seam_list.md      ← Tuesday seam list (15 seams)
├── w12_architecture.md   ← This file
├── README.md             ← Stranger-ready setup
├── w7_agent.py           ← W7 base agent (still importable)
├── w7_tools.py           ← 4 domain tools
├── w9_claims_mcp.py      ← Claims MCP
├── w9_policy_mcp.py      ← Policy MCP (legacy)
├── utils/tracer.py       ← JSONL tracer + PII redaction
├── eval/run_w6_eval.py   ← W6 eval runner
└── data/
    ├── w12_traces.jsonl
    ├── endorsements/     ← HO-0304 through HO-0309
    └── w7_claims.json    ← 10 claims + ground truth
```
