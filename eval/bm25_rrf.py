"""
BM25 + Reciprocal Rank Fusion (RRF) hybrid retriever.

Architecture
------------
1. Vector leg  : MongoDB Atlas $vectorSearch (existing), returns top-N candidates.
2. BM25 leg    : rank-bm25 over a LOCAL file-based chunk cache built directly
                 from the endorsement .txt files via pipeline.chunkers — no DB
                 dependency for the BM25 side.
3. Fusion      : Reciprocal Rank Fusion with k=60 (standard).
                 RRF score = sum over legs of  1 / (k + rank_in_that_leg)
                 Final ranking is by descending RRF score.

The chunk cache is written to eval/chunks_cache.json on first build and
reloaded from file on subsequent calls. Pass rebuild=True to force a refresh.

Usage
-----
    from eval.bm25_rrf import BM25RRFRetriever

    retriever = BM25RRFRetriever(strategy="structure_aware")
    results = retriever.search(query_embedding, query_text, top_k=3)
    # returns List[ScoredChunk] with .score = RRF score

One change from the original pipeline
--------------------------------------
The original pipeline uses only MongoDB Atlas $vectorSearch.
This module adds a BM25 rank over the same candidate pool (fetched from files)
and fuses with RRF(k=60).  Nothing else in the pipeline changes.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from rank_bm25 import BM25Okapi

from pipeline.chunkers import chunk_all_endorsements, Chunk
from pipeline.mongo_store import MongoVectorStore, ScoredChunk

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = os.path.join(PROJECT_ROOT, "data", "endorsements")
CACHE_PATH = os.path.join(PROJECT_ROOT, "eval", "chunks_cache.json")

# RRF constant — task spec mandates k=60
RRF_K = 60

# How many candidates to fetch from each leg before fusing
CANDIDATE_N = 25


# ---------------------------------------------------------------------------
# File-based chunk cache
# ---------------------------------------------------------------------------

def _tokenise(text: str) -> List[str]:
    """Simple whitespace + punctuation tokeniser for BM25."""
    return re.findall(r"[A-Za-z0-9][-A-Za-z0-9]*", text.lower())


def _build_cache(strategy: str) -> List[Dict]:
    """
    Chunk all endorsement .txt files using the existing chunker and write to
    eval/chunks_cache.json.  No embeddings, no MongoDB — text and metadata only.
    Returns the list of chunk dicts.
    """
    chunks: List[Chunk] = chunk_all_endorsements(DATA_DIR, strategy)
    data = [
        {
            "chunk_id": c.chunk_id,
            "text": c.text,
            "metadata": c.metadata,
        }
        for c in chunks
    ]
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"strategy": strategy, "chunks": data}, f, ensure_ascii=False)
    return data


def _load_cache(strategy: str, rebuild: bool = False) -> List[Dict]:
    """Load chunks from file cache, rebuilding if necessary."""
    if not rebuild and os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("strategy") == strategy:
            return cached["chunks"]
    # Cache absent, stale, or strategy mismatch — rebuild.
    return _build_cache(strategy)


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------

def _rrf_fuse(
    ranked_lists: List[List[str]],
    k: int = RRF_K,
) -> List[Tuple[str, float]]:
    """
    Fuse multiple ranked lists of chunk_ids using Reciprocal Rank Fusion.

    ranked_lists : each inner list is a ranking of chunk_ids (best first)
    Returns      : [(chunk_id, rrf_score), ...] sorted descending
    """
    scores: Dict[str, float] = {}
    for ranking in ranked_lists:
        for rank_zero, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank_zero + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Retriever class
# ---------------------------------------------------------------------------

class BM25RRFRetriever:
    """
    Hybrid BM25 + Vector retriever with RRF fusion.

    The vector leg uses the existing MongoVectorStore (MongoDB Atlas).
    The BM25 leg uses a file-based chunk cache (no MongoDB).
    """

    def __init__(
        self,
        strategy: str = "structure_aware",
        rebuild_cache: bool = False,
        candidate_n: int = CANDIDATE_N,
        rrf_k: int = RRF_K,
    ):
        self.strategy = strategy
        self.candidate_n = candidate_n
        self.rrf_k = rrf_k

        # Vector store (MongoDB) — used for the vector leg only
        self.vector_store = MongoVectorStore(strategy)

        # File-based chunk cache — used for BM25 leg
        chunks = _load_cache(strategy, rebuild=rebuild_cache)
        self._chunks: Dict[str, Dict] = {c["chunk_id"]: c for c in chunks}
        self._chunk_ids: List[str] = [c["chunk_id"] for c in chunks]
        self._tokenised_corpus: List[List[str]] = [
            _tokenise(c["text"]) for c in chunks
        ]
        self._bm25 = BM25Okapi(self._tokenised_corpus)
        print(
            f"  [BM25RRF] loaded {len(chunks)} chunks from file cache "
            f"(strategy={strategy})"
        )

    # -- BM25 -----------------------------------------------------------------

    def _bm25_rank(self, query_text: str) -> List[str]:
        """Return chunk_ids ranked by BM25 score (best first)."""
        tokens = _tokenise(query_text)
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(
            range(len(self._chunk_ids)),
            key=lambda i: scores[i],
            reverse=True,
        )
        return [self._chunk_ids[i] for i in ranked[: self.candidate_n]]

    # -- Public search --------------------------------------------------------

    def search(
        self,
        query_embedding: List[float],
        query_text: str,
        top_k: int = 3,
        filter_metadata: Optional[Dict] = None,
    ) -> List[ScoredChunk]:
        """
        Hybrid search: BM25 (file) + vector (MongoDB) fused with RRF.

        Parameters
        ----------
        query_embedding : float vector for the vector leg
        query_text      : raw query string for the BM25 leg
        top_k           : number of results to return
        filter_metadata : forwarded to the vector leg only

        Returns
        -------
        List[ScoredChunk]  — .score is the RRF score (NOT cosine similarity)
        """
        # 1. Vector leg (MongoDB Atlas $vectorSearch)
        vector_hits = self.vector_store.search(
            query_embedding, top_k=self.candidate_n, filter_metadata=filter_metadata
        )
        vector_rank: List[str] = [sc.chunk_id for sc in vector_hits]

        # 2. BM25 leg (file-based)
        bm25_rank: List[str] = self._bm25_rank(query_text)

        # 3. RRF fusion
        fused = _rrf_fuse([vector_rank, bm25_rank], k=self.rrf_k)

        # 4. Build ScoredChunk results, resolving chunk text from the file cache
        #    (fall back to MongoDB if a chunk_id from the vector leg is not in cache)
        results: List[ScoredChunk] = []
        for chunk_id, rrf_score in fused[:top_k]:
            cached = self._chunks.get(chunk_id)
            if cached:
                results.append(
                    ScoredChunk(
                        chunk_id=chunk_id,
                        text=cached["text"],
                        metadata=cached["metadata"],
                        score=rrf_score,
                    )
                )
            else:
                # chunk came from MongoDB vector leg but is not in file cache
                # (edge case: strategy mismatch or new chunk). Fetch from DB.
                doc = self.vector_store.get_chunk(chunk_id)
                if doc:
                    meta = {
                        k: doc.get(k, "")
                        for k in [
                            "source_file", "form_number", "policy_line",
                            "edition_date", "effective_date", "strategy",
                            "section", "chunk_index",
                        ]
                    }
                    results.append(
                        ScoredChunk(
                            chunk_id=chunk_id,
                            text=doc.get("text", ""),
                            metadata=meta,
                            score=rrf_score,
                        )
                    )
        return results
