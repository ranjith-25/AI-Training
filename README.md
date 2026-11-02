# Claims Triage & Coverage Assistant — W12 Capstone

A claims triage system for motor and household insurance that surfaces policy
wording and exclusion clauses in force on the loss date. The adjuster decides
coverage; this system triages and cites.

---

## Quick Start

### 1. Setup

```bash
# Clone and enter the workspace
cd d:\AI-Training

# Activate virtual environment (already configured)
.\venv\Scripts\activate

# Set your API key
copy .env.example .env
# Edit .env and add: API_KEY=your_gemini_api_key
```

### 2. Run the Integrated System

```bash
# Triage a single claim (FNOL to adjuster referral)
python w12_agent.py --claim CLM-2024-7002

# Triage a different claim
python w12_agent.py --claim CLM-2024-7001

# Use custom MCP config
python w12_agent.py --claim CLM-2024-7002 --config w12_config.json
```

### 3. Run the Full Eval Suite (one command)

```bash
# Full suite — all sections, mock mode (no API calls, fast)
python w12_eval_suite.py

# Full suite with live API calls
python w12_eval_suite.py --live

# Run only the date-boundary cases (the 2024 vs 2025 wording test)
python w12_eval_suite.py --section dates

# Run only the error taxonomy report
python w12_eval_suite.py --section taxonomy
```

### 4. Start the W12 MCP Server (for external squad review)

```bash
# The Thursday reviewer's agent calls this over stdio MCP
python w12_mcp_server.py
```

---

## Architecture (One Paragraph)

`w12_agent.py` is the single entry point. It opens three MCP server connections
(defined in `w12_config.json`) and discovers all available tools at runtime via
`tools/list` — no tool names are hardcoded. A UUID `trace_id` is assigned at
FNOL entry and propagates through every span, MCP call, and JSONL trace record.
The agent runs a ReAct loop (≤8 iterations, 4 budget guards) that calls
`lookup_policy_coverage_in_force` to verify the policy was active on the loss
date, `search_policy_wording_by_date` to retrieve the exclusion wording **in
force on the loss date** (enforced by `ENDORSEMENT_DATE_MAP` in code, not a
prompt hint), and `refer_to_adjuster` as the only terminal action. The system
never emits the word "covered". PII (names, addresses, injuries) is redacted
before any MCP server response or JSONL trace record is written.

---

## Files

| File | Purpose |
|------|---------|
| `w12_agent.py` | Main integrated entry point |
| `w12_mcp_server.py` | W12 MCP server — date-scoped wording, coverage-in-force, referral |
| `w12_config.json` | MCP server configuration (3 servers) |
| `w12_eval_suite.py` | Full eval suite — one command |
| `w12_error_taxonomy.py` | 7-mode error taxonomy (3 integration-created) |
| `w12_seam_list.md` | Tuesday seam list (15 seams, each marked) |
| `w12_architecture.md` | Architecture diagram and file map |
| `w7_agent.py` | W7 base agent loop (still importable) |
| `w7_tools.py` | 4 domain tools |
| `w9_claims_mcp.py` | Claims MCP server |
| `w9_policy_mcp.py` | Policy MCP server (legacy) |
| `utils/tracer.py` | JSONL tracer with PII redaction |
| `eval/run_w6_eval.py` | W6 eval runner |

---

## Eval Suite Output (summary)

```
W6 Benchmark:           11/11  (100%)
W6 Taxonomy:            24/25  (96%)
Judge agreement:        88%    (gate ≥85%: PASS)
Date-boundary (after):  5/5 GREEN
Wrong-version rate:     3/3 wrong before  →  0/3 wrong after
Tool-Choice Accuracy:   90%
Outcome-Trajectory GAP: +10 pp
Integration assertions: 100% pass
Cost/claim (p50):       $0.000425
First to break at 10×:  API Rate Limits (TPM quota)
```

---

## Known Weaknesses (Dated 2026-10-28, Wednesday — Before Anyone Asked)

1. **`ENDORSEMENT_DATE_MAP` is hardcoded** — If a new endorsement ships without
   updating the dict in `w12_mcp_server.py`, the wrong version silently loads for
   losses in the new date range. No automated check exists for map coverage.

2. **PII redaction is regex-only** — Names not matching "First Last" (e.g.,
   O'Brien, single names, non-Latin scripts) can leak through the regex patterns.
   A trained NER model would catch more cases but adds latency and cost.

3. **Judge agreement validated on 25-case mock sample** — The 88% figure uses
   `mock_judge_llm` for speed. The live LLM judge could drift between model
   versions and would require API credits per eval run.

---

## The 10× Plan (18,000 FNOLs / hailstorm week)

| What breaks | Number | Fix |
|-------------|--------|-----|
| API Rate Limits | ~1M TPM cap hit at 60 FNOLs/min → HTTP 429 | Pre-provision TPM; async batch queues |
| MCP Server Concurrency | 1 call at a time via stdio | HTTP/SSE transport + load balancer |
| FAISS Index | Read contention at >100 concurrent workers | Qdrant or sharded index |
| JSONL Trace Lock | Race condition at >100 writes/sec | Kafka/Pub-Sub log drain |

---

## Demo Script (10 minutes)

**Part 1 — Real run** (2 min):
```bash
python w12_agent.py --claim CLM-2024-7002
```
Show: loss date 2024-03-22 → Ed. 03-24 wording retrieved (not 2025) → §IV E-17 cited →
refer_to_adjuster called → deductible $1,500 / sub_limit $15,000 / policy_limit $300,000 as 3 numbers.

**Part 2 — The failure** (3 min):
```bash
python w12_eval_suite.py --section dates
```
Show: DB-2024-08-11 RED before (Ed. 01-25 retrieved for 2024 loss) → GREEN after.
Taxonomy: "Endorsement Date Boundary Miss" at 12% frequency — the mode integration created.
Top 3 weaknesses (written Wednesday).

**Part 3 — The number that moved** (3 min):
```bash
python w12_eval_suite.py --section w6
```
Wrong-endorsement-version citation rate: 3/3 (100%) before → 0/3 (0%) after.
n=3, judge agreement 88%. Suite does NOT cover: novel perils, sub-limit corner cases, multi-peril interaction.

**Part 4 — What's next** (2 min):
```bash
python w12_eval_suite.py --section cost
```
Cost per claim: $0.000425 p50. First to break at 18,000 FNOLs/week: API rate limits (~1M TPM cap).
The DB-2024-08-11 eval case is now a permanent regression guard.

---

## Fresh-Eyes Note

*(To be filled in by Thursday's reviewer from another squad)*

Reviewer: _______________  Squad: _______________
Where they stumbled: _______________
What was changed: _______________
