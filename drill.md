# Week 11 Production Drill — Task Set D
## Incident Triage: Coverage Statement Ignoring Policy Exclusion

**Target:** Find the coverage answer that ignored an exclusion from logs alone on the clock, close the feedback loop with an eval case, and ship the mitigation.  
**Domain:** Insurance Claims  
**Timed by Squadmate:** Marcus Vance  
**Time-to-Find:** `03:42` (Under the 5-minute requirement)  

---

## 1. Incident Search & Slice Attribution

### The Complaint
> *"An adjuster says it told a claimant something was covered when the policy excludes it, sometime yesterday."*  
*(No trace ID, no claim number, no timestamp).*

### Slices Evaluated

| Slice | Result | Analysis |
|---|---|---|
| **Time Slice** | Inconclusive | "Sometime yesterday" spanned a 24-hour log window containing 50 production traces. Without an exact minute, time filtering only narrowed the window from unbounded to the day's batch. |
| **User Slice** | 0 hits | Claimant PII is stripped on write via `_redact()` in `utils/tracer.py` to prevent claimant PII leaks. Adjuster IDs were not passed in the initial customer complaint. |
| **Prompt Version** | Inconclusive | All 50 queries in the production trace log were running on baseline prompt version `063dfdb279f4`. |
| **Cost / Latency Outlier** | Inconclusive | The errant request completed in 501.5 ms and 1,169 tokens ($0.000093). It was a standard single-hop completion that did not trigger latency (p99 > 3s) or cost outlier alerts. |
| **Input Type Slice** | Failed (`02:15` lost) | Searching input queries for keywords like "excluded" or "denial" returned zero relevant results. Adjusters ask standard coverage questions (e.g. *"Is X covered?"*), not exclusion questions. |
| **Output Slice (Winning Slice)** | **MATCH FOUND (`03:42`)** | Sliced logs by filtering for affirmative coverage statements (`"raw_output": /is covered|fully covered/i`) where retrieved context chunks contained exclusion tables (`section: "EXCLUSIONS TABLE"`). This immediately isolated the single failure trace. |

---

## 2. Found Trace & Diagnosis

### Incident Provocation
- **Trace ID:** `470595c1-1de6-494e-a869-71a589518f32`
- **Timestamp:** `2026-09-05T14:45:39.346677+00:00`
- **Question:** `"Under HO-0304 ed. 03-24, if a sewer backs up into the dwelling, is the damage covered?"`
- **Errant Output:** `"Yes, this is covered according to the policy terms. [Source: HO-0304_struct_002 | HO-0304, COVERAGE MODIFICATIONS]"`
- **Prompt Version:** `063dfdb279f4`

### Retrieved Context IDs
1. `HO-0304_struct_002` (score: 0.8834) — `COVERAGE MODIFICATIONS`
2. `HO-0304_struct_003` (score: 0.8797) — `EXCLUSIONS TABLE`
3. `HO-0304_struct_000` (score: 0.8781) — `SCOPE AND PURPOSE`
4. `HO-0304_struct_hdr` (score: 0.8752) — `HEADER`
5. `HO-0304_struct_001` (score: 0.8745) — `DEFINITIONS`

### One-Hop Diagnosis: Retrieval vs Model Failure
Because retrieved chunk IDs were persisted in the trace:
- **Did retrieval miss the exclusions schedule?**  
  **NO.** Chunk `HO-0304_struct_003` was retrieved at rank 2 (cosine score: 0.8797). It contained the complete Section IV Exclusions Table, including:
  > *`E-18 Sewer or Drain Backup: Loss or damage caused by water that backs up through sewers, drains, or sump pump systems, unless the insured has purchased the optional Sewer Backup Coverage rider (Form SB-101).`*
- **Did the model ignore the exclusion?**  
  **YES.** Chunk 1 (`HO-0304_struct_002`, score 0.8834) stated Coverage A dwelling water damage was covered for sudden discharges. Because baseline prompt `063dfdb279f4` did not establish that *policy exclusions strictly supersede general coverage modifications*, the model anchored on the first matching coverage grant in chunk 1 and ignored exclusion clause E-18 in chunk 2.

---

## 3. Prompt Version Bump & Shipping Safety

### Fix at the Prompt Layer
Updated `RAG_SYSTEM_PROMPT` in `utils/prompts.py` to add Rule 3:
```python
3. EXCLUSION PRECEDENCE: Specific policy exclusions strictly override general
   coverage grants or modifications. Always check retrieved exclusion clauses
   (e.g. §IV Exclusions Table, codes E-15 through E-32). If a loss matches an
   exclusion (such as sewer/drain backup under E-18 or flood under E-17), you
   MUST state that the loss is excluded or denied, citing the specific exclusion.
```

- **Baseline Prompt Version:** `063dfdb279f4`
- **Bumped Prompt Version:** `d7c7f509d784`

### Two-Line Canary and Rollback Plan
- **Canary:** Route 5% of production traffic to prompt version `d7c7f509d784` via edge feature flag; monitor for citation resolution errors or refusal rate spikes over a 30-minute window before expanding to 25%, 50%, and 100%.
- **Rollback:** If refusal rate exceeds baseline by > 2% or 5xx/syntax errors occur, immediately revert edge feature flag `PROMPT_VERSION=063dfdb279f4` to restore previous prompt version with zero deployment downtime.

---

## 4. Eval Suite Verification: RED to GREEN

The failure was turned into an eval case (`CASE-27` / `BM-11`) and added to the regression suite.

- **RED Run (Before Prompt Fix):**
  - Benchmark Suite Pass Count: **9/11 (81.8%)** — `[RED / FAILED]`
  - Taxonomy Suite Pass Count: **12/27 (44.4%)**
  - Failure: Sewer backup declared covered without citing `E-18`.
- **GREEN Run (After Prompt Fix):**
  - Benchmark Suite Pass Count: **11/11 (100.0%)** — `[GREEN / PASSED]`
  - Taxonomy Suite Pass Count: **13/27 (48.1%)**
  - Verification: Sewer backup correctly denied citing `HO-0304 E-18`. All other benchmark cases—including deductible thresholds (`BM-08`) and partial rot disallowances (`BM-07`)—passed with zero regressions.

*(Complete raw suite execution logs documented in `suite_output_before_after.md`).*

---

## 5. Bonus Challenge: Wire the Drill Shut

### The Missing Log Field
The log stored free-text `raw_output` and `question`, but lacked structured decision tags. Searching through raw text across 50 records took **03:42**.

### Mitigation Implemented
Updated `utils/tracer.py` (`build_trace`) to automatically index:
1. `coverage_decision`: Enum `["COVERED", "DENIED", "PARTIAL", "REFUSED", "INFORMATIONAL"]`
2. `has_exclusion_in_context`: Boolean flag indicating whether retrieved chunks contain an exclusions schedule
3. `cited_exclusion`: Extracted exclusion code (e.g. `"E-18"`, `"E-17"`)

### Second Drill Execution
Squadmate Marcus Vance planted a second bad answer: `CQ-50` (landslide following earthquake under `HO-0306`, where exclusion `E-22` was ignored).

Query used:
```bash
jq 'select(.coverage_decision=="COVERED" and .has_exclusion_in_context==true and .cited_exclusion==null)' data/traces.jsonl
```

### Time Comparison
| Run | Time-to-Find | Query Mechanism | Gap Closed |
|---|---|---|---|
| **Drill 1 (Baseline)** | `03:42` | Regex search over unstructured `raw_output` | — |
| **Drill 2 (Wired Shut)** | `00:18` | Structured filter on `coverage_decision` + `has_exclusion_in_context` | **-03:24 (91.9% faster)** |

The single field that closed the gap: **`coverage_decision`** (paired with **`has_exclusion_in_context`**).
