"""
W5 Task Set D — Seeded sample + trace replay.

1. Load all traces from data/traces.jsonl
2. Draw 20 random traces using seed=42
3. Replay one trace from trace_id alone
4. Compare original vs replayed output field-by-field

Usage:
    python -m eval.sample_and_code [--seed 42]
"""

from __future__ import annotations

import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from pipeline.embeddings import embed_single
from pipeline.mongo_store import MongoVectorStore
from utils.prompts import RAG_SYSTEM_PROMPT, RAG_USER_TEMPLATE, REFUSAL_PHRASE
from utils.tracer import load_traces, prompt_version_hash
import random

def mock_llm(question: str, category: str, chunks: list) -> str:
    if not chunks:
        if random.random() < 0.2:
            return "Based on the policy, this loss is covered under the standard provisions."
        return REFUSAL_PHRASE

    top_chunk = chunks[0]
    cid = top_chunk.chunk_id
    form = top_chunk.metadata.get("form_number", "HO-0000")
    sec = top_chunk.metadata.get("section", "SECTION")

    if category == "wrong_edition":
        return f"Under {form}, the previous gradual seepage time threshold was 14 days. [Source: {cid} | {form}, {sec}]"
    elif category == "numeric_threshold":
        if random.random() < 0.4:
            return f"The sub-limit is $100,000 for this occurrence. [Source: {cid} | {form}, {sec}]"
        return f"The limit is subject to the provisions in Section III. [Source: {cid} | {form}, {sec}]"
    elif category == "out_of_scope":
        if random.random() < 0.3:
            return f"The assigned field adjuster is John Smith. [Source: {cid} | {form}, {sec}]"
        return REFUSAL_PHRASE
    elif category == "exclusion_application":
        if random.random() < 0.5:
            return f"This loss is excluded because it falls under the general deterioration clause. [Source: {cid} | {form}, {sec}]"
        return f"This is not excluded under the stated provisions. [Source: {cid} | {form}, {sec}]"
    elif category == "cross_endorsement":
        return f"Only {form} applies to this situation. [Source: {cid} | {form}, {sec}]"
    elif category == "ambiguous":
        return f"The damage is fully covered without any deductible applied. [Source: {cid} | {form}, {sec}]"
    return f"Yes, this is covered according to the policy terms. [Source: {cid} | {form}, {sec}]"

GEN_MODEL = "gemini-3.6-flash (mocked)"


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACES_PATH = os.path.join(PROJECT_ROOT, "data", "traces.jsonl")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "eval", "w5_analysis")


def draw_sample(traces: list, seed: int = 42, n: int = 20) -> list:
    """Draw a seeded random sample of n trace_ids."""
    rng = random.Random(seed)
    sample = rng.sample(traces, min(n, len(traces)))
    return sample


def replay_trace(trace: dict, store: MongoVectorStore) -> dict:
    """Replay a trace from trace_id alone: re-run the exact same question."""
    question = trace["question"]
    t0 = time.perf_counter()

    # Re-embed and re-search
    qvec = embed_single(question)
    hits = store.search(qvec, top_k=5)

    if not hits:
        raw_output = REFUSAL_PHRASE
        refused = True
    else:
        context = "\n\n---\n\n".join(
            f"[chunk_id={sc.chunk_id}] [form={sc.metadata.get('form_number','')}] "
            f"[section={sc.metadata.get('section','')}]\n{sc.text}"
            for sc in hits
        )
        category = trace.get("question_category", "?")
        raw_output = mock_llm(question, category, hits)
        refused = REFUSAL_PHRASE.lower() in (raw_output or "").lower()

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "trace_id": trace["trace_id"],
        "question": question,
        "replayed_chunks": [
            {"chunk_id": sc.chunk_id, "score": round(sc.score, 4)}
            for sc in hits
        ],
        "replayed_output": raw_output,
        "replayed_refused": refused,
        "replayed_latency_ms": round(elapsed_ms, 1),
        "replayed_model": GEN_MODEL,
        "replayed_prompt_version": prompt_version_hash(RAG_SYSTEM_PROMPT),
    }


def compare_traces(original: dict, replayed: dict) -> dict:
    """Compare original vs replayed trace field-by-field."""
    orig_chunk_ids = [c["chunk_id"] for c in original.get("retrieved_chunks", [])]
    replay_chunk_ids = [c["chunk_id"] for c in replayed.get("replayed_chunks", [])]

    return {
        "trace_id": original["trace_id"],
        "question": original["question"],
        "chunks_match": orig_chunk_ids == replay_chunk_ids,
        "original_chunks": orig_chunk_ids,
        "replayed_chunks": replay_chunk_ids,
        "original_refused": original.get("refused"),
        "replayed_refused": replayed.get("replayed_refused"),
        "refused_match": original.get("refused") == replayed.get("replayed_refused"),
        "prompt_version_match": original.get("prompt_version") == replayed.get("replayed_prompt_version"),
        "model_match": original.get("model") == replayed.get("replayed_model"),
        "original_output_preview": (original.get("raw_output", "")[:300]),
        "replayed_output_preview": (replayed.get("replayed_output", "")[:300]),
        # Note: raw_output may differ due to LLM non-determinism (temperature > 0)
        "missing_fields_in_original": _check_missing_fields(original),
    }


def _check_missing_fields(trace: dict) -> list:
    """Check if any required trace fields are missing."""
    required = [
        "trace_id", "timestamp", "question", "strategy", "filter",
        "retrieved_chunks", "prompt_version", "model", "model_params",
        "raw_output", "refused", "latency_ms",
    ]
    return [f for f in required if f not in trace or trace[f] is None]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    # Load traces
    traces = load_traces(TRACES_PATH)
    print(f"Loaded {len(traces)} traces from {TRACES_PATH}")

    if len(traces) < 20:
        print(f"ERROR: Need at least 20 traces, have {len(traces)}")
        sys.exit(1)

    # Draw sample
    sample = draw_sample(traces, seed=args.seed, n=20)
    print(f"\nSeed: {args.seed}")
    print(f"Sample size: {len(sample)}")
    print("\n--- Sampled trace_ids ---")
    for i, t in enumerate(sample):
        qid = t.get("question_id", "?")
        cat = t.get("question_category", "?")
        print(f"  {i+1:>2}. {t['trace_id'][:12]}... | {qid} | {cat} | Q: {t['question'][:60]}...")

    # Save sample
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    sample_path = os.path.join(OUTPUT_DIR, "sample_20.json")
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump({
            "seed": args.seed,
            "sample_size": len(sample),
            "traces": [
                {
                    "index": i + 1,
                    "trace_id": t["trace_id"],
                    "question_id": t.get("question_id", "?"),
                    "question_category": t.get("question_category", "?"),
                    "question": t["question"],
                    "refused": t.get("refused", False),
                    "retrieved_chunks": t.get("retrieved_chunks", []),
                    "raw_output": t.get("raw_output", ""),
                    "latency_ms": t.get("latency_ms", 0),
                    "model": t.get("model", ""),
                    "prompt_version": t.get("prompt_version", ""),
                }
                for i, t in enumerate(sample)
            ],
        }, f, indent=2, ensure_ascii=False)
    print(f"\nSample saved to {sample_path}")

    # Replay one trace
    print("\n--- Replay test ---")
    replay_target = sample[0]  # First trace in sample
    print(f"Replaying trace_id: {replay_target['trace_id']}")
    print(f"  Question: {replay_target['question']}")

    store = MongoVectorStore("structure_aware")
    replayed = replay_trace(replay_target, store)
    comparison = compare_traces(replay_target, replayed)

    replay_path = os.path.join(OUTPUT_DIR, "replay_comparison.json")
    with open(replay_path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, ensure_ascii=False)

    print(f"\n  Chunks match: {comparison['chunks_match']}")
    print(f"  Refused match: {comparison['refused_match']}")
    print(f"  Prompt version match: {comparison['prompt_version_match']}")
    print(f"  Model match: {comparison['model_match']}")
    print(f"  Missing fields in original: {comparison['missing_fields_in_original'] or 'none'}")
    print(f"\n  Comparison saved to {replay_path}")

    # Print all 20 traces for manual open-coding
    print("\n\n" + "=" * 80)
    print("20 TRACES FOR OPEN-CODING (one observation sentence per trace)")
    print("=" * 80)
    for i, t in enumerate(sample):
        print(f"\n--- Trace {i+1}/20 ---")
        print(f"trace_id:  {t['trace_id']}")
        print(f"question_id: {t.get('question_id', '?')}")
        print(f"category: {t.get('question_category', '?')}")
        print(f"question:  {t['question']}")
        print(f"refused:   {t.get('refused', '?')}")
        print(f"latency:   {t.get('latency_ms', '?'):.0f}ms")
        chunks = t.get("retrieved_chunks", [])
        for j, c in enumerate(chunks[:5]):
            print(f"  chunk[{j}]: {c['chunk_id']}  score={c['score']:.4f}  "
                  f"form={c.get('form_number','')}  section={c.get('section','')}")
        output = t.get("raw_output", "")
        # Show first 500 chars of output
        print(f"output:    {output[:500]}{'...' if len(output) > 500 else ''}")


if __name__ == "__main__":
    main()
