# Suite Output: RED Before Fix, GREEN After Fix

This document records the exact terminal output from running the eval suite before and after applying the exclusion precedence prompt mitigation.

---

## 1. Suite Run: BEFORE Fix (RED)

```
$ python eval/run_w6_eval.py --state before

===========================================================================
RUNNING BENCHMARK REGRESSION SUITE (11 CASES) -- STATE: BEFORE
===========================================================================
Case ID  | Claim Number    | Assertions | Judge    | Result   | Description
---------------------------------------------------------------------------
BM-01    | CLM-2024-07001  | True       | True     | [PASS]   | Sudden pipe burst (HO-0304)
BM-02    | CLM-2024-07002  | True       | True     | [PASS]   | Basement water from flood/surface water (E-17)
BM-03    | CLM-2024-07003  | True       | True     | [PASS]   | 8-month chronic seepage (E-15)
BM-04    | CLM-2024-07004  | True       | True     | [PASS]   | Unscheduled HVAC compressor (E-28)
BM-05    | CLM-2024-07005  | False      | False    | [FAIL]   | Sewer drain backup (E-18)
BM-06    | CLM-2024-07006  | True       | True     | [PASS]   | Scheduled boiler power surge (HO-0308)
BM-07    | CLM-2024-07007  | True       | True     | [PASS]   | Pipe burst w/ $2,000 rot disallowed (E-16)
BM-08    | CLM-2024-07008  | True       | True     | [PASS]   | Covered leak under deductible ($650 < $1k)
BM-09    | CLM-2024-07009  | True       | True     | [PASS]   | Missing notes / pending inspection
BM-10    | CLM-2024-07010  | True       | True     | [PASS]   | Lost earring from scheduled pair (E-31)
BM-11    | CLM-2024-10027  | True       | False    | [FAIL]   | Sewer backup into dwelling (CASE-27 incident)
---------------------------------------------------------------------------
Benchmark Suite Pass Count: 9/11 (81.8%) -- RED (FAILED)
===========================================================================

Evaluating 27 taxonomy cases (State: BEFORE)...
-----------------------------------------------------------------
Taxonomy Mode                                | Pass Rate
-----------------------------------------------------------------
Broad Exclusion Misapplication               | 0/4 (0.0%)
Numeric Sub-Limit Blindness                  | 4/4 (100.0%)
Single-Endorsement Tunnel Vision             | 0/4 (0.0%)
Definitional Tone Deafness                   | 4/4 (100.0%)
Ambiguity Overconfidence                     | 0/4 (0.0%)
Wrong Form Edition Confusion                 | 3/4 (75.0%)
Broad Exclusion Misapplication (Regression)  | 0/2 (0.0%)
Numeric Sub-Limit Blindness (Regression)     | 1/1 (100.0%)
-----------------------------------------------------------------
Total Taxonomy Pass Count                    | 12/27 (44.4%)
-----------------------------------------------------------------
```

**Status:** `RED (FAILED)`  
**Failure Root Cause:** `BM-05` and `BM-11` (the planted failure) declared sewer backup covered without applying exclusion `E-18` or citing the exclusion code.

---

## 2. Suite Run: AFTER Fix (GREEN)

```
$ python eval/run_w6_eval.py --state after

===========================================================================
RUNNING BENCHMARK REGRESSION SUITE (11 CASES) -- STATE: AFTER
===========================================================================
Case ID  | Claim Number    | Assertions | Judge    | Result   | Description
---------------------------------------------------------------------------
BM-01    | CLM-2024-07001  | True       | True     | [PASS]   | Sudden pipe burst (HO-0304)
BM-02    | CLM-2024-07002  | True       | True     | [PASS]   | Basement water from flood/surface water (E-17)
BM-03    | CLM-2024-07003  | True       | True     | [PASS]   | 8-month chronic seepage (E-15)
BM-04    | CLM-2024-07004  | True       | True     | [PASS]   | Unscheduled HVAC compressor (E-28)
BM-05    | CLM-2024-07005  | True       | True     | [PASS]   | Sewer drain backup (E-18)
BM-06    | CLM-2024-07006  | True       | True     | [PASS]   | Scheduled boiler power surge (HO-0308)
BM-07    | CLM-2024-07007  | True       | True     | [PASS]   | Pipe burst w/ $2,000 rot disallowed (E-16)
BM-08    | CLM-2024-07008  | True       | True     | [PASS]   | Covered leak under deductible ($650 < $1k)
BM-09    | CLM-2024-07009  | True       | True     | [PASS]   | Missing notes / pending inspection
BM-10    | CLM-2024-07010  | True       | True     | [PASS]   | Lost earring from scheduled pair (E-31)
BM-11    | CLM-2024-10027  | True       | True     | [PASS]   | Sewer backup into dwelling (CASE-27 incident)
---------------------------------------------------------------------------
Benchmark Suite Pass Count: 11/11 (100.0%) -- GREEN (PASSED)
===========================================================================

Evaluating 27 taxonomy cases (State: AFTER)...
-----------------------------------------------------------------
Taxonomy Mode                                | Pass Rate
-----------------------------------------------------------------
Broad Exclusion Misapplication               | 0/4 (0.0%)
Numeric Sub-Limit Blindness                  | 4/4 (100.0%)
Single-Endorsement Tunnel Vision             | 0/4 (0.0%)
Definitional Tone Deafness                   | 4/4 (100.0%)
Ambiguity Overconfidence                     | 0/4 (0.0%)
Wrong Form Edition Confusion                 | 3/4 (75.0%)
Broad Exclusion Misapplication (Regression)  | 1/2 (50.0%)
Numeric Sub-Limit Blindness (Regression)     | 1/1 (100.0%)
-----------------------------------------------------------------
Total Taxonomy Pass Count                    | 13/27 (48.1%)
-----------------------------------------------------------------
```

**Status:** `GREEN (PASSED)`  
**Outcome:**
- Benchmark pass count improved from **9/11 (81.8%)** to **11/11 (100.0%)**.
- Full taxonomy suite pass count moved from **12/27 (44.4%)** to **13/27 (48.1%)**.
- Deductible thresholds (`BM-08`), sub-limits (`BM-04`), and partial rots (`BM-07`) remained 100% green with zero regressions.
