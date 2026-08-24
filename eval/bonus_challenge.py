"""
Bonus challenge: precision vs completeness.

Hypothesis under test — there exists a question where the TIGHTER chunker wins
on retrieval (it puts the exact exclusion row at rank 1, at a higher score) but
LOSES on the final answer, because the tight row chunk strands the model without
the DEFINITIONS clause that tells it what the row's terms mean.

The probe question needs BOTH:
  * SECTION IV, row E-28  — excludes equipment that "does not meet the
                            definition of Covered Equipment"
  * SECTION II (a)        — a window air-conditioning unit is NOT Covered
                            Equipment

E-28 is a pointer; on its own it cannot resolve the question. Under
`structure_aware_rows` each E-nn row is its own chunk, so the three exclusion
rows of HO-0308 compete for the same top-k slots and crowd out DEFINITIONS.
Under `structure_aware` the whole exclusions table is ONE chunk, which leaves a
slot free for DEFINITIONS.

`--top-k` defaults to 3, the smallest k at which that crowding is visible; the
main evaluation uses top_k=5.

Usage:
    python -m eval.bonus_challenge
    python -m eval.bonus_challenge --top-k 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

from pipeline.embeddings import embed_single
from pipeline.mongo_store import MongoVectorStore
from utils.llm_service import generate_with_gemini_sync, GEN_MODEL
from utils.prompts import RAG_SYSTEM_PROMPT, RAG_USER_TEMPLATE, REFUSAL_PHRASE

load_dotenv()

# structure_aware keeps the whole EXCLUSIONS TABLE section together;
# structure_aware_rows splits it to one chunk per E-nn row. naive_fixed is
# included as the baseline. The tension shows up between the last two.
STRATEGIES = ["naive_fixed", "structure_aware", "structure_aware_rows"]
OUT_PATH = os.path.join(PROJECT_ROOT, "eval", "bonus_results.json")

QUESTION = (
    "Under HO-0308 ed. 03-24, is a window air-conditioning unit 'Covered "
    "Equipment' for the purposes of exclusion E-28, and would a claim for its "
    "failure be excluded?"
)

# Ground truth, from the source document:
#   SECTION II (a) — "Portable or plug-in appliances (e.g., window
#   air-conditioning units, portable heaters, kitchen counter appliances) are
#   not covered equipment unless specifically scheduled."
#   SECTION IV, E-28 — excludes equipment "that does not meet the definition of
#   Covered Equipment."
# E-28 is the operative exclusion, but it is a POINTER: it only bites once you
# read SECTION II (a) to learn that a window AC unit is not Covered Equipment.
# Answering therefore requires BOTH chunks.

# What the CONTEXT must contain for the model to answer completely.
#
# The definition needle matches the ACTUAL text of SECTION II (a) that names a
# window air-conditioning unit, not a mere mention of "Covered Equipment".
# Lesson from an earlier version of this probe: it tested for the bare phrase
# "sudden and accidental", which also appears in SECTION I as "...as defined
# herein" -- a POINTER to a definition, not the definition -- and so it wrongly
# scored a stranded context as complete. Needles must match the operative text.
NEEDLES = {
    "exclusion_row_E-28": lambda t: "E-28" in t,
    "definition_covered_equipment": lambda t: "window air-conditioning" in t.lower(),
}


def probe(strategy: str, top_k: int) -> dict:
    store = MongoVectorStore(strategy)
    qemb = embed_single(QUESTION)
    hits = store.search(qemb, top_k=top_k)

    context = "\n\n---\n\n".join(
        f"[chunk_id={sc.chunk_id}] [form={sc.metadata.get('form_number','')}] "
        f"[section={sc.metadata.get('section','')}]\n{sc.text}"
        for sc in hits
    )

    coverage = {name: test(context) for name, test in NEEDLES.items()}
    # Retrieval "win" = the exclusion row is present and ranked first.
    retrieval_precise = bool(hits) and "E-28" in hits[0].text

    prompt = (f"{RAG_SYSTEM_PROMPT}\n\n"
              + RAG_USER_TEMPLATE.format(context=context, question=QUESTION))
    time.sleep(1)
    answer = generate_with_gemini_sync(prompt)

    refused = REFUSAL_PHRASE.lower() in (answer or "").lower()

    print(f"\n{'='*78}")
    print(f"  STRATEGY: {strategy}   (top_k={top_k}, model={GEN_MODEL})")
    print(f"{'='*78}")
    for i, sc in enumerate(hits):
        print(f"  #{i+1}  {sc.chunk_id:<24} form={sc.metadata.get('form_number'):<8} "
              f"section={sc.metadata.get('section','')!r:<26} score={sc.score:.4f}")
    print("\n  Context coverage:")
    for name, present in coverage.items():
        print(f"    {'PRESENT' if present else 'MISSING'}  {name}")
    print(f"  Exclusion row ranked #1: {retrieval_precise}")
    print(f"\n  ANSWER:\n{answer}\n")
    print(f"  Refused: {refused}")

    return {
        "strategy": strategy,
        "top_k": top_k,
        "retrieved": [
            {"rank": i + 1, "chunk_id": sc.chunk_id,
             "form_number": sc.metadata.get("form_number"),
             "section": sc.metadata.get("section", ""),
             "score": round(sc.score, 4)}
            for i, sc in enumerate(hits)
        ],
        "context_coverage": coverage,
        "exclusion_row_ranked_first": retrieval_precise,
        "answer": answer,
        "refused": refused,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    print("\n" + "#" * 78)
    print("  BONUS CHALLENGE — precision vs completeness")
    print(f"  Question: {QUESTION}")
    print("#" * 78)

    results = {
        "question": QUESTION,
        "top_k": args.top_k,
        "generation_model": GEN_MODEL,
        "retrieval": "MongoDB Atlas $vectorSearch (exact ENN, cosine)",
        "runs": [probe(s, args.top_k) for s in STRATEGIES],
    }

    print("\n" + "#" * 78)
    print("  SIDE BY SIDE")
    print("#" * 78)
    for r in results["runs"]:
        cov = ", ".join(k for k, v in r["context_coverage"].items() if v) or "nothing"
        print(f"  {r['strategy']:<18} row#1={r['exclusion_row_ranked_first']!s:<6} "
              f"refused={r['refused']!s:<6} context has: {cov}")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n  Written to {OUT_PATH}\n")


if __name__ == "__main__":
    main()
