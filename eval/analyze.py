"""
Post-hoc analysis of eval/results.json.

hit-in-top-5 is the metric the brief asks for, and it is reported as such. But
on a 6-document corpus, top-5 sweeps most of a single endorsement, so a question
that names its form number is almost guaranteed a hit under either chunker.
That makes hit@5 saturate and tie.

These rank-sensitive cuts come from the SAME recorded run — no re-retrieval, no
new questions, no changed metric definitions. They just read the per-rank
records that run_eval.py already stored:

    hit@1 / hit@3 / hit@5   strict (form AND clause)
    MRR (strict)            mean of 1/rank-of-first-strict-hit
    mean strict rank        over questions where a strict hit exists

Usage:
    python -m eval.analyze
"""

from __future__ import annotations

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS_PATH = os.path.join(PROJECT_ROOT, "eval", "results.json")
OUT_PATH = os.path.join(PROJECT_ROOT, "eval", "analysis.md")

STRATEGIES = ["naive_fixed", "structure_aware"]


def strict_rank(detail: dict) -> int | None:
    """Rank of the first chunk matching BOTH form and clause, or None."""
    for r in detail["top5"]:
        if r["form_match"] and r["clause_match"]:
            return r["rank"]
    return None


def loose_rank(detail: dict) -> int | None:
    for r in detail["top5"]:
        if r["form_match"]:
            return r["rank"]
    return None


def main():
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        res = json.load(f)

    search = res["search_evaluation"]
    qids = [d["question_id"] for d in search[STRATEGIES[0]]["details"]]
    n = len(qids)

    table = {}
    for s in STRATEGIES:
        details = {d["question_id"]: d for d in search[s]["details"]}
        sranks = {q: strict_rank(details[q]) for q in qids}
        lranks = {q: loose_rank(details[q]) for q in qids}
        found = [r for r in sranks.values() if r]
        table[s] = {
            "strict_hit_at_1": sum(1 for r in sranks.values() if r and r <= 1),
            "strict_hit_at_3": sum(1 for r in sranks.values() if r and r <= 3),
            "strict_hit_at_5": sum(1 for r in sranks.values() if r and r <= 5),
            "loose_hit_at_5": sum(1 for r in lranks.values() if r and r <= 5),
            "mrr_strict": round(sum(1 / r for r in sranks.values() if r) / n, 4),
            "mean_strict_rank": round(sum(found) / len(found), 3) if found else None,
            "strict_ranks": sranks,
        }

    lines = ["# Rank-sensitive analysis (same run, same 8 questions)", ""]
    lines.append("Computed from `eval/results.json`. No re-retrieval, no new "
                 "questions, no redefined metric — these read the per-rank "
                 "records the measured run already stored.")
    lines.append("")
    lines.append(f"| metric | {' | '.join(STRATEGIES)} |")
    lines.append("|---|" + "---|" * len(STRATEGIES))
    for key, label in [
        ("strict_hit_at_5", f"**hit-in-top-5 (strict: form+clause)** — /{n}"),
        ("loose_hit_at_5", f"hit-in-top-5 (loose: form only) — /{n}"),
        ("strict_hit_at_3", f"hit-in-top-3 (strict) — /{n}"),
        ("strict_hit_at_1", f"hit-in-top-1 (strict) — /{n}"),
        ("mrr_strict", "MRR (strict)"),
        ("mean_strict_rank", "mean rank of first strict hit (lower better)"),
    ]:
        lines.append(f"| {label} | "
                     + " | ".join(str(table[s][key]) for s in STRATEGIES) + " |")
    lines.append("")

    lines.append("## Rank of first strict hit, per question")
    lines.append("")
    lines.append(f"| question | {' | '.join(STRATEGIES)} |")
    lines.append("|---|" + "---|" * len(STRATEGIES))
    for q in qids:
        cells = []
        for s in STRATEGIES:
            r = table[s]["strict_ranks"][q]
            cells.append(str(r) if r else "— (miss)")
        lines.append(f"| {q} | " + " | ".join(cells) + " |")
    lines.append("")

    out = "\n".join(lines)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(out)
    print(out)
    print(f"\nWritten to {OUT_PATH}")


if __name__ == "__main__":
    main()
