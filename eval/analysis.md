# Rank-sensitive analysis (same run, same 8 questions)

Computed from `eval/results.json`. No re-retrieval, no new questions, no redefined metric — these read the per-rank records the measured run already stored.

| metric | naive_fixed | structure_aware |
|---|---|---|
| **hit-in-top-5 (strict: form+clause)** — /8 | 7 | 7 |
| hit-in-top-5 (loose: form only) — /8 | 8 | 7 |
| hit-in-top-3 (strict) — /8 | 7 | 7 |
| hit-in-top-1 (strict) — /8 | 5 | 5 |
| MRR (strict) | 0.7292 | 0.7292 |
| mean rank of first strict hit (lower better) | 1.429 | 1.429 |

## Rank of first strict hit, per question

| question | naive_fixed | structure_aware |
|---|---|---|
| Q1 | 1 | 1 |
| Q2 | 1 | 3 |
| Q3 | 3 | 2 |
| Q4 | 1 | 1 |
| Q5 | — (miss) | — (miss) |
| Q6 | 2 | 1 |
| Q7 | 1 | 1 |
| Q8 | 1 | 1 |
