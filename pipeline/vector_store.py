"""
FAISS-backed vector store for endorsement chunks.
Keeps the FAISS index + a parallel list of chunk metadata in memory,
with optional disk persistence.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import List, Dict, Optional

import numpy as np

try:
    import faiss
except ImportError:
    faiss = None  # handled at runtime


# ---------------------------------------------------------------------------
# Scored result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScoredChunk:
    chunk_id: str
    text: str
    metadata: Dict[str, str]
    score: float  # L2 distance (lower = more similar)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "score": round(self.score, 4),
            "metadata": self.metadata,
            "text_preview": self.text[:200] + "..." if len(self.text) > 200 else self.text,
        }


# ---------------------------------------------------------------------------
# FAISS Vector Store
# ---------------------------------------------------------------------------

class FAISSVectorStore:
    """Flat (brute-force) FAISS index.  Good enough for <10k chunks."""

    def __init__(self, dim: int = 768):
        if faiss is None:
            raise ImportError("faiss-cpu is required: pip install faiss-cpu")
        self.dim = dim
        self.index = faiss.IndexFlatL2(dim)
        self.chunks: List[Dict] = []  # parallel list [{chunk_id, text, metadata}, ...]

    # -- Indexing ------------------------------------------------------------

    def add(self, chunk_id: str, text: str, embedding: List[float],
            metadata: Dict[str, str]) -> None:
        vec = np.array([embedding], dtype=np.float32)
        self.index.add(vec)
        self.chunks.append({
            "chunk_id": chunk_id,
            "text": text,
            "metadata": metadata,
        })

    def add_bulk(self, items: List[Dict]) -> None:
        """items: list of {chunk_id, text, embedding, metadata}"""
        vecs = np.array([it["embedding"] for it in items], dtype=np.float32)
        self.index.add(vecs)
        for it in items:
            self.chunks.append({
                "chunk_id": it["chunk_id"],
                "text": it["text"],
                "metadata": it["metadata"],
            })

    # -- Search --------------------------------------------------------------

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        filter_metadata: Optional[Dict[str, str]] = None,
    ) -> List[ScoredChunk]:
        """
        Search for top_k nearest chunks.
        If filter_metadata is provided, post-filter results by metadata match.
        We over-fetch (top_k * 5) to ensure enough results survive filtering.
        """
        fetch_k = top_k * 5 if filter_metadata else top_k
        fetch_k = min(fetch_k, self.index.ntotal)

        if fetch_k == 0:
            return []

        qvec = np.array([query_embedding], dtype=np.float32)
        distances, indices = self.index.search(qvec, fetch_k)

        results: List[ScoredChunk] = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0:
                continue
            chunk = self.chunks[idx]

            # Apply metadata filter
            if filter_metadata:
                if not all(
                    chunk["metadata"].get(k) == v
                    for k, v in filter_metadata.items()
                ):
                    continue

            results.append(ScoredChunk(
                chunk_id=chunk["chunk_id"],
                text=chunk["text"],
                metadata=chunk["metadata"],
                score=float(dist),
            ))
            if len(results) >= top_k:
                break

        return results

    # -- Persistence ---------------------------------------------------------

    def save(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        faiss.write_index(self.index, os.path.join(directory, "index.faiss"))
        with open(os.path.join(directory, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump(self.chunks, f, indent=2)

    @classmethod
    def load(cls, directory: str, dim: int = 768) -> "FAISSVectorStore":
        store = cls(dim=dim)
        store.index = faiss.read_index(os.path.join(directory, "index.faiss"))
        with open(os.path.join(directory, "chunks.json"), "r", encoding="utf-8") as f:
            store.chunks = json.load(f)
        return store

    @property
    def size(self) -> int:
        return self.index.ntotal
