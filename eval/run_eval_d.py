"""
Task Set D evaluation harness.

Measures hit-rate@3 (strict: form + clause match) before and after the single
retrieval change (BM25+RRF fusion, k=60), and records p50 latency per query.

Labels every miss as:
  R              — retrieval fetched bad context (correct chunk NOT in top-25)
  G              — model failure (correct chunk WAS in top-25 but not top-3)
  Not-In-Corpus  — the answer does not exist in any indexed chunk

Writes eval/results_d.json and updates the console.

Usage:
    python -m eval.run_eval_d
    python -m eval.run_eval_d --strategy structure_aware
    python -m eval.run_eval_d --rebuild-cache   (force re-chunk from .txt files)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

from pipeline.embeddings import embed_single
from pipeline.mongo_store import MongoVectorStore, ScoredChunk
from eval.metrics import score_question, chunk_satisfies_clause, clause_locator
from eval.bm25_rrf import BM25RRFRetriever

load_dotenv()

EVAL_DIR = os.path.join(PROJECT_ROOT, "eval")
GOLD_QA_PATH = os.path.join(EVAL_DIR, "gold_qa.json")
RESULTS_PATH = os.path.join(EVAL_DIR, "results_d.json")

TOP_K = 3           # hit-rate@3
CANDIDATE_N = 25    # BM25 + vector legs each fetch this many before fusion
RRF_K = 60          # RRF constant specified in task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_gold_qa() -> list[dict]:
    with open(GOLD_QA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def hit_rate_at_k(details: list[dict]) -> float:
    hits = sum(1 for d in details if d["strict_hit"])
    return round(hits / len(details), 4) if details else 0.0


# ---------------------------------------------------------------------------
# Miss labelling
# ---------------------------------------------------------------------------

def _correct_chunk_in_top_n(
    q: dict,
    store: MongoVectorStore,
    qvec: list[float],
    n: int = CANDIDATE_N,
) -> bool:
    """
    Check whether ANY chunk in the vector top-N for this question contains
    the expected form AND clause.  If yes, the miss is G (model/rerank failure).
    If no, it is R (retrieval never fetched the right context).
    """
    candidates = store.search(qvec, top_k=n)
    forms = [f.strip() for f in q["expected_form"].split(",") if f.strip()]
    clause = q["expected_clause"]
    for sc in candidates:
        form_ok = sc.metadata.get("form_number") in forms
        clause_ok = chunk_satisfies_clause(sc.text, clause)
        if form_ok and clause_ok:
            return True
    return False


def label_miss(q: dict, store: MongoVectorStore, qvec: list[float]) -> str:
    """
    Returns 'R', 'G', or 'Not-In-Corpus'.

    Not-In-Corpus is not reachable here because all 12 questions are written
    from known text in the corpus.  Included for completeness / future use.
    """
    if _correct_chunk_in_top_n(q, store, qvec, n=CANDIDATE_N):
        return "G"
    return "R"


# ---------------------------------------------------------------------------
# Run one retriever over all 12 questions
# ---------------------------------------------------------------------------

def run_retriever(
    name: str,
    gold_qa: list[dict],
    qvecs: dict[str, list[float]],
    search_fn,          # callable(qvec, query_text) -> List[ScoredChunk]
) -> tuple[list[dict], list[float]]:
    """
    Returns (details_list, latencies_seconds).
    details_list: one record per question with strict_hit and top-k info.
    """
    details = []
    latencies = []

    print(f"\n  {'='*70}")
    print(f"  RETRIEVER: {name}   (top_k={TOP_K})")
    print(f"  {'='*70}")

    for q in gold_qa:
        qvec = qvecs[q["id"]]
        t0 = time.perf_counter()
        top = search_fn(qvec, q["question"])
        elapsed = time.perf_counter() - t0
        latencies.append(elapsed)

        rec = score_question(q, top)
        details.append(rec)

        strict = "HIT " if rec["strict_hit"] else "MISS"
        print(
            f"\n  {q['id']}  {strict}  "
            f"(expect {q['expected_form']} / {q['expected_clause']})  "
            f"latency={elapsed*1000:.0f}ms"
        )
        for r in rec["top5"]:
            mark = "**" if (r["form_match"] and r["clause_match"]) else \
                   ("~  " if r["form_match"] else "   ")
            print(
                f"      {mark} #{r['rank']}  {r['chunk_id']:<24} "
                f"form={r['form_number']:<8}  score={r['score']:.4f}  "
                f"form_match={r['form_match']!s:<5}  clause_match={r['clause_match']}"
            )

    hr = hit_rate_at_k(details)
    p50 = round(statistics.median(latencies) * 1000, 1)
    print(f"\n  >> {name}: hit-rate@{TOP_K} = {hr:.0%}  "
          f"({sum(1 for d in details if d['strict_hit'])}/{len(details)})   "
          f"p50={p50}ms\n")
    return details, latencies


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", default="structure_aware",
                        choices=["naive_fixed", "structure_aware"],
                        help="Chunking strategy to evaluate")
    parser.add_argument("--rebuild-cache", action="store_true",
                        help="Force rebuild of BM25 file-based chunk cache")
    args = parser.parse_args()

    strategy = args.strategy

    print("\n" + "#" * 72)
    print("  TASK SET D — hit-rate@3, failure labels, BM25+RRF (k=60)")
    print(f"  Strategy: {strategy}")
    print("#" * 72)

    gold_qa = load_gold_qa()
    print(f"\n  Loaded {len(gold_qa)} questions from gold_qa.json")

    # Embed each question ONCE — identical vector reused across both retrievers
    print("  Embedding all questions...")
    qvecs: dict[str, list[float]] = {}
    for q in gold_qa:
        qvecs[q["id"]] = embed_single(q["question"])
        time.sleep(0.1)   # light rate-limit

    # -----------------------------------------------------------------------
    # BASELINE: pure vector search (MongoDB Atlas $vectorSearch)
    # -----------------------------------------------------------------------
    vector_store = MongoVectorStore(strategy)
    print(f"  Vector store: {vector_store.size} chunks in '{vector_store.coll.name}'")

    def vector_search(qvec, _query_text):
        return vector_store.search(qvec, top_k=TOP_K)

    baseline_details, baseline_latencies = run_retriever(
        name="BASELINE (vector-only, $vectorSearch)",
        gold_qa=gold_qa,
        qvecs=qvecs,
        search_fn=vector_search,
    )

    baseline_hr = hit_rate_at_k(baseline_details)
    baseline_p50 = round(statistics.median(baseline_latencies) * 1000, 1)

    # -----------------------------------------------------------------------
    # Inspect misses: label each R / G / Not-In-Corpus
    # -----------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("  MISS LABELS  (R=retrieval bad | G=model misused good ctx | NIC=not in corpus)")
    print("=" * 72)

    miss_labels: dict[str, dict] = {}
    tally = {"R": 0, "G": 0, "Not-In-Corpus": 0}

    for rec in baseline_details:
        if not rec["strict_hit"]:
            q_obj = next(q for q in gold_qa if q["id"] == rec["question_id"])
            label = label_miss(q_obj, vector_store, qvecs[rec["question_id"]])
            tally[label] += 1
            # Evidence: what WAS in top-3?
            top_ids = [r["chunk_id"] for r in rec["top5"]]
            evidence = (
                f"Top-{TOP_K} returned {top_ids}; none contained "
                f"{q_obj['expected_form']} + {q_obj['expected_clause']}. "
                f"Correct chunk {'IS' if label == 'G' else 'NOT'} in top-{CANDIDATE_N}."
            )
            miss_labels[rec["question_id"]] = {"label": label, "evidence": evidence}
            print(f"\n  {rec['question_id']}  [{label}]")
            print(f"    {evidence}")

    print(f"\n  Tally — R:{tally['R']}  G:{tally['G']}  Not-In-Corpus:{tally['Not-In-Corpus']}")

    # -----------------------------------------------------------------------
    # AFTER: BM25 + RRF hybrid
    # -----------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("  CHANGE APPLIED: BM25+RRF fusion (k=60) over file-based chunk cache")
    print("  Vector leg: MongoDB Atlas $vectorSearch (top-25)")
    print("  BM25 leg:   rank-bm25 over eval/chunks_cache.json (top-25)")
    print("=" * 72)

    hybrid = BM25RRFRetriever(
        strategy=strategy,
        rebuild_cache=args.rebuild_cache,
        candidate_n=CANDIDATE_N,
        rrf_k=RRF_K,
    )

    def hybrid_search(qvec, query_text):
        return hybrid.search(qvec, query_text, top_k=TOP_K)

    after_details, after_latencies = run_retriever(
        name="AFTER (BM25+RRF, k=60)",
        gold_qa=gold_qa,
        qvecs=qvecs,
        search_fn=hybrid_search,
    )

    after_hr = hit_rate_at_k(after_details)
    after_p50 = round(statistics.median(after_latencies) * 1000, 1)

    # -----------------------------------------------------------------------
    # Per-question fixed / unfixed table
    # -----------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("  PER-QUESTION OUTCOME TABLE")
    print("=" * 72)
    print(f"  {'ID':<4}  {'Before':<8}  {'After':<8}  {'Change':<14}  Question (truncated)")

    per_q_table = []
    for b_rec, a_rec in zip(baseline_details, after_details):
        qid = b_rec["question_id"]
        before_hit = b_rec["strict_hit"]
        after_hit = a_rec["strict_hit"]

        if before_hit and after_hit:
            change = "still-hit"
        elif not before_hit and after_hit:
            change = "FIXED"
        elif before_hit and not after_hit:
            change = "BROKEN"      # regression — should not happen
        else:
            change = "still-miss"

        q_text = b_rec["question"][:55] + "..." if len(b_rec["question"]) > 55 else b_rec["question"]
        print(f"  {qid:<4}  {'HIT' if before_hit else 'miss':<8}  "
              f"{'HIT' if after_hit else 'miss':<8}  {change:<14}  {q_text}")

        per_q_table.append({
            "question_id": qid,
            "question": b_rec["question"],
            "baseline_hit": before_hit,
            "after_hit": after_hit,
            "change": change,
            "miss_label": miss_labels.get(qid, {}).get("label"),
            "miss_evidence": miss_labels.get(qid, {}).get("evidence"),
        })

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    delta_hr = round(after_hr - baseline_hr, 4)
    print(f"\n  {'='*72}")
    print(f"  RESULTS")
    print(f"  {'='*72}")
    print(f"  hit-rate@3  BEFORE: {baseline_hr:.0%}  AFTER: {after_hr:.0%}  "
          f"delta={delta_hr:+.0%}")
    print(f"  p50 latency BEFORE: {baseline_p50}ms  AFTER: {after_p50}ms")
    print(f"  Miss tally  R:{tally['R']}  G:{tally['G']}  "
          f"Not-In-Corpus:{tally['Not-In-Corpus']}")

    fixed = sum(1 for r in per_q_table if r["change"] == "FIXED")
    broken = sum(1 for r in per_q_table if r["change"] == "BROKEN")
    print(f"  Fixed: {fixed}  Broken (regression): {broken}")

    if after_p50 > baseline_p50 * 2:
        print(f"\n  SHIPPING DECISION: latency cost is significant "
              f"({after_p50}ms vs {baseline_p50}ms). "
              f"Worth it only if hit-rate gain justifies adjuster wait time.")
    elif delta_hr > 0:
        print(f"\n  SHIPPING DECISION: hit-rate improved by {delta_hr:+.0%} "
              f"with acceptable latency overhead ({after_p50 - baseline_p50:+.0f}ms p50). "
              f"SHIP IT.")
    else:
        print(f"\n  SHIPPING DECISION: no improvement in hit-rate. "
              f"Do NOT ship — change earned nothing.")

    # -----------------------------------------------------------------------
    # Write results_d.json
    # -----------------------------------------------------------------------
    output = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": strategy,
        "top_k": TOP_K,
        "rrf_k": RRF_K,
        "candidate_n": CANDIDATE_N,
        "golden_set_size": len(gold_qa),
        "baseline": {
            "retriever": "MongoDB Atlas $vectorSearch (exact ENN, cosine)",
            "hit_rate_at_3": baseline_hr,
            "p50_latency_ms": baseline_p50,
            "strict_hits": sum(1 for d in baseline_details if d["strict_hit"]),
            "details": baseline_details,
        },
        "miss_labels": miss_labels,
        "tally": tally,
        "single_change": "BM25+RRF fusion (k=60) — file-based BM25 over eval/chunks_cache.json",
        "after": {
            "retriever": f"BM25+RRF (k={RRF_K}): vector top-{CANDIDATE_N} + BM25 top-{CANDIDATE_N} fused",
            "hit_rate_at_3": after_hr,
            "p50_latency_ms": after_p50,
            "strict_hits": sum(1 for d in after_details if d["strict_hit"]),
            "details": after_details,
        },
        "per_question_table": per_q_table,
        "delta_hit_rate": delta_hr,
        "delta_p50_ms": round(after_p50 - baseline_p50, 1),
    }

    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  Results written to {RESULTS_PATH}")
    print("#" * 72 + "\n")


if __name__ == "__main__":
    main()
