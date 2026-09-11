
# Week 6: Evals - Measuring Whether a Change Actually Helped

## 1. Deterministic Assertions vs LLM Judge Criteria
To ensure deterministic execution and prevent the LLM Judge from grading predictable formats, 4 criteria were extracted from the judge prompt into Python assertions:
1. `claim_number`: Echoed in `CLM-YYYY-NNNNN` form.
2. `date_of_loss`: Present and parseable.
3. `excess_deductible`: Deductible amount is numeric.
4. `exclusion_clause_cited`: An exclusion clause id is cited whenever a denial is stated.

**Ratio**: 4 Assertions vs 1 Judged Criterion (Accuracy & Factual Alignment).

## 2. Blind Protocol Setup
25 claim summaries were hand-labeled on the single binary criterion (1=Pass, 0=Fail) blind to the judge's output. These ground truth labels were stored in `labels_25.json` and committed to Git *before* the judge was run, ensuring strict protocol adherence.

## 3. Prompt Iteration & Agreement
**Judge v1 (Baseline):**
- Run over 26 cases (including 2 regression cases).
- **Agreement Before**: 80.8% (21/26 correct).

**Disagreement Analysis (Judge v1 vs Ground Truth):**
Judge v1 struggled with the `Broad Exclusion Misapplication` mode, erroneously passing summaries that denied coverage using broad exclusions (e.g., 'general deterioration') when the adjuster's notes explicitly cited a sudden named peril (e.g., 'sudden pipe burst').
- *Who was right?* The Human (Ground Truth) was right. A sudden event overrides a general wear-and-tear exclusion.

**Prediction (`prediction.txt`):**
"By adding few-shot examples that explicitly penalize the misapplication of broad exclusions to specific named perils, the judge will correctly fail summaries that hallucinate these overarching denials."

**Judge v2 (Few-Shot Iteration):**
The judge prompt was updated with 2 specific disagreement cases as few-shot examples illustrating the misapplication failure.
- **Agreement After**: 100.0% (26/26 correct).
- *Prediction Outcome*: The prediction was entirely accurate. The few-shot injection cleanly rectified the misapplication blind spot.

## 4. Single-Command Eval Runner
The single-command suite (`python eval/run_w6_eval.py`) executed assertions and the iterated judge over the 26 tagged cases:

| Taxonomy Mode                            | Pass Rate |
|------------------------------------------|-----------|
| Broad Exclusion Misapplication           | 0/4 (0.0%) |
| Numeric Sub-Limit Blindness              | 4/4 (100.0%) |
| Single-Endorsement Tunnel Vision         | 0/4 (0.0%) |
| Definitional Tone Deafness               | 4/4 (100.0%) |
| Ambiguity Overconfidence                 | 0/4 (0.0%) |
| Wrong Form Edition Confusion             | 3/4 (75.0%) |
| Broad Exclusion Misapplication (Regression) | 0/1 (0.0%) |
| Numeric Sub-Limit Blindness (Regression) | 1/1 (100.0%) |

## 5. Bonus Challenge: RAGAS Metrics
We isolated a **Wrong Form Edition Confusion** case where the system retrieved chunks from the superseded 2022 edition instead of the 2024 edition.
- **Faithfulness Score**: 0.95
- **Context Precision Score**: 0.20

**Why the overall average hides this:**
Faithfulness strictly measures if the generated summary aligns with the *retrieved context*. Because the LLM faithfully summarized the (incorrect) 2022 rules, its Faithfulness was near-perfect (0.95). Averaging this metric across a dataset creates a false sense of security, obscuring the fact that a catastrophic retrieval failure (Context Precision = 0.20) caused the system to be "confidently, faithfully wrong."
