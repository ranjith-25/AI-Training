"""
Ingestion pipeline: read endorsements → chunk → embed → index into MongoDB Atlas.

Indexes the 6 NEW endorsements only (HO-0304 … HO-0309). It does NOT touch,
re-read, or re-embed the base policy wording library.

Usage:
    python -m pipeline.ingest --strategy naive_fixed
    python -m pipeline.ingest --strategy structure_aware
    python -m pipeline.ingest --strategy both
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The Windows console defaults to cp1252, which cannot encode the arrows and
# box-drawing characters used in this project's output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pipeline.chunkers import chunk_all_endorsements
from pipeline.embeddings import embed_texts
from pipeline.mongo_store import MongoVectorStore

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "endorsements")

# The two strategies compared in the headline hit-in-top-5 table.
STRATEGIES = ["naive_fixed", "structure_aware"]

# Strategy 3 is not part of the headline A/B; it backs the
# precision-vs-completeness probe in eval/bonus_challenge.py.
ALL_STRATEGIES = STRATEGIES + ["structure_aware_rows"]


def ingest(strategy: str, verbose: bool = True,
           wait_for_index: bool = True) -> MongoVectorStore:
    """
    Chunk + embed + upsert all 6 endorsements into the Mongo collection for
    `strategy`, then ensure the Atlas Vector Search index is queryable.
    """
    if verbose:
        print(f"\n{'='*60}")
        print(f"INGESTING with strategy: {strategy}")
        print(f"{'='*60}")

    # 1. Chunk
    t0 = time.time()
    chunks = chunk_all_endorsements(DATA_DIR, strategy)
    if verbose:
        print(f"  Chunked {len(chunks)} chunks from endorsement files")

    # Validate: a chunk with no source_file is a failed ingest.
    for c in chunks:
        if not c.metadata.get("source_file"):
            raise ValueError(f"Chunk {c.chunk_id} has no source_file — failed ingest")

    # 2. Embed
    texts = [c.text for c in chunks]
    if verbose:
        print(f"  Embedding {len(texts)} chunks with gemini-embedding-001...")
    embeddings = embed_texts(texts)
    if verbose:
        print(f"  Embeddings complete ({time.time()-t0:.1f}s, dim={len(embeddings[0])})")

    # 3. Write to MongoDB (replace this strategy's chunks; nothing else)
    store = MongoVectorStore(strategy, dim=len(embeddings[0]))
    store.reset()
    n = store.add_bulk([
        {"chunk_id": c.chunk_id, "text": c.text, "embedding": e, "metadata": c.metadata}
        for c, e in zip(chunks, embeddings)
    ])
    if verbose:
        print(f"  Inserted {n} chunks into '{store.coll.name}'")

    # 4. Vector index
    store.ensure_vector_index(wait=wait_for_index)
    if verbose:
        print(f"  ✓ {strategy}: {store.size} chunks queryable in MongoDB Atlas")

    return store


def main():
    parser = argparse.ArgumentParser(
        description="Ingest the 6 new endorsements into MongoDB Atlas Vector Search")
    parser.add_argument("--strategy", choices=ALL_STRATEGIES + ["both", "all"],
                        default="both",
                        help="'both' = the two measured strategies; "
                             "'all' = those plus structure_aware_rows")
    args = parser.parse_args()

    if args.strategy == "both":
        strategies = STRATEGIES
    elif args.strategy == "all":
        strategies = ALL_STRATEGIES
    else:
        strategies = [args.strategy]

    print("\nIndexing the 6 NEW endorsements ONLY (HO-0304 → HO-0309).")
    print("NOT re-indexing the base policy wording library.\n")

    for s in strategies:
        ingest(s)


if __name__ == "__main__":
    main()
