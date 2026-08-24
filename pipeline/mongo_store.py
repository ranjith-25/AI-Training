"""
MongoDB Atlas Vector Search store for endorsement chunks.

Replaces the previous FAISS flat-L2 store. Both the chunk documents and their
embeddings live in MongoDB; retrieval uses the native `$vectorSearch`
aggregation stage so that metadata filtering is a PRE-filter applied inside the
index, not a post-filter applied to an already-truncated candidate list.

Two collections, one per chunking strategy, so the A/B comparison is clean:
    chunks_naive_fixed
    chunks_structure_aware

Scoring note: `$vectorSearch` with similarity="cosine" returns
`vectorSearchScore` in [0, 1] where HIGHER IS BETTER. This is the opposite
direction from the FAISS L2 distances the old store reported; any score in this
project's output is a cosine similarity.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.operations import SearchIndexModel

load_dotenv()

EMBED_DIM = 3072  # gemini-embedding-001
VECTOR_INDEX_NAME = "endorsement_vector_index"

# Metadata fields declared as `filter` in the Atlas index. Anything listed here
# can be used as a pre-filter in vector_search().
FILTER_FIELDS = ["policy_line", "form_number", "strategy", "edition_date"]

# Fields stored flat on each chunk document (filter paths must be top-level).
METADATA_FIELDS = [
    "source_file", "form_number", "policy_line", "edition_date",
    "effective_date", "strategy", "section", "chunk_index",
]


# ---------------------------------------------------------------------------
# Scored result
# ---------------------------------------------------------------------------

@dataclass
class ScoredChunk:
    chunk_id: str
    text: str
    metadata: Dict[str, str]
    score: float  # cosine similarity in [0, 1] — HIGHER is more similar

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "score": round(self.score, 4),
            "metadata": self.metadata,
            "text_preview": self.text[:200] + "..." if len(self.text) > 200 else self.text,
        }


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def build_uri() -> str:
    """Assemble the Atlas SRV URI from the same env vars main.py already uses."""
    user = os.getenv("MONGODB_USERNAME")
    pwd = os.getenv("MONGODB_PASSWORD")
    host = os.getenv("MONGODB_HOSTNAME")
    if not all([user, pwd, host]):
        raise RuntimeError(
            "MONGODB_USERNAME / MONGODB_PASSWORD / MONGODB_HOSTNAME must be set"
        )
    return f"mongodb+srv://{user}:{pwd}@{host}"


def get_db(client: Optional[MongoClient] = None):
    """Return (client, db) using MONGODB_DATABASE."""
    if client is None:
        client = MongoClient(build_uri(), serverSelectionTimeoutMS=30000)
    db_name = os.getenv("MONGODB_DATABASE")
    if not db_name:
        raise RuntimeError("MONGODB_DATABASE must be set")
    return client, client[db_name]


def collection_name_for(strategy: str) -> str:
    return f"chunks_{strategy}"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class MongoVectorStore:
    """
    One collection per chunking strategy, each with its own Atlas Vector Search
    index over the `embedding` field.
    """

    def __init__(self, strategy: str, client: Optional[MongoClient] = None,
                 dim: int = EMBED_DIM):
        self.strategy = strategy
        self.dim = dim
        self.client, self.db = get_db(client)
        self.coll: Collection = self.db[collection_name_for(strategy)]

    # -- Indexing -----------------------------------------------------------

    def reset(self) -> None:
        """Drop all chunk documents for this strategy. Touches nothing else."""
        self.coll.delete_many({})

    def add_bulk(self, items: List[Dict]) -> int:
        """
        items: list of {chunk_id, text, embedding, metadata}

        Every chunk MUST carry source_file / form_number / policy_line /
        edition_date. A chunk without them is a failed ingest and raises here
        rather than being written as a nameless orphan.
        """
        docs = []
        for it in items:
            meta = it["metadata"]
            for required in ("source_file", "form_number", "policy_line", "edition_date"):
                if not meta.get(required):
                    raise ValueError(
                        f"FAILED INGEST: chunk {it['chunk_id']} is missing "
                        f"required metadata field '{required}'"
                    )
            doc = {
                "chunk_id": it["chunk_id"],
                "text": it["text"],
                "embedding": it["embedding"],
            }
            # Flatten metadata to top level so Atlas filter paths are simple.
            for f in METADATA_FIELDS:
                if f in meta:
                    doc[f] = meta[f]
            docs.append(doc)

        if docs:
            self.coll.insert_many(docs)
        return len(docs)

    def ensure_vector_index(self, wait: bool = True, timeout_s: int = 600) -> str:
        """
        Create the Atlas Vector Search index if absent, then (optionally) block
        until it reports queryable. Returns the index status.
        """
        existing = {ix["name"]: ix for ix in self.coll.list_search_indexes()}

        if VECTOR_INDEX_NAME not in existing:
            definition = {
                "fields": (
                    [{
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": self.dim,
                        "similarity": "cosine",
                    }]
                    + [{"type": "filter", "path": f} for f in FILTER_FIELDS]
                )
            }
            self.coll.create_search_index(
                SearchIndexModel(definition=definition,
                                 name=VECTOR_INDEX_NAME,
                                 type="vectorSearch")
            )
            print(f"    created vector index '{VECTOR_INDEX_NAME}' on {self.coll.name}")
        else:
            print(f"    vector index '{VECTOR_INDEX_NAME}' already exists on {self.coll.name}")

        if not wait:
            return "PENDING"

        start = time.time()
        while time.time() - start < timeout_s:
            ixs = {ix["name"]: ix for ix in self.coll.list_search_indexes()}
            ix = ixs.get(VECTOR_INDEX_NAME)
            if ix and ix.get("queryable"):
                print(f"    index queryable after {time.time() - start:.0f}s "
                      f"(status={ix.get('status')})")
                return ix.get("status", "READY")
            time.sleep(5)
        raise TimeoutError(
            f"Vector index on {self.coll.name} not queryable after {timeout_s}s"
        )

    # -- Search -------------------------------------------------------------

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        filter_metadata: Optional[Dict[str, str]] = None,
    ) -> List[ScoredChunk]:
        """
        Native Atlas $vectorSearch.

        `exact: True` runs exhaustive nearest-neighbour (ENN) rather than
        approximate HNSW. At this corpus size (tens of chunks) ENN is both
        faster to set up and, more importantly, DETERMINISTIC — the hit-in-top-5
        numbers this project reports must not move because of ANN recall jitter.

        `filter_metadata` becomes a PRE-filter evaluated inside the index, so a
        filtered top-5 is the true top-5 of the filtered subset — not a
        post-filtered remnant of an unfiltered top-5.
        """
        stage: Dict = {
            "index": VECTOR_INDEX_NAME,
            "path": "embedding",
            "queryVector": [float(x) for x in query_embedding],
            "limit": top_k,
            "exact": True,
        }

        if filter_metadata:
            if len(filter_metadata) == 1:
                (k, v), = filter_metadata.items()
                stage["filter"] = {k: {"$eq": v}}
            else:
                stage["filter"] = {
                    "$and": [{k: {"$eq": v}} for k, v in filter_metadata.items()]
                }

        # $project cannot mix inclusion with exclusion, so the score is attached
        # via $addFields and the bulky embedding is dropped separately.
        pipeline = [
            {"$vectorSearch": stage},
            {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
            {"$project": {"_id": 0, "embedding": 0}},
        ]

        results: List[ScoredChunk] = []
        for doc in self.coll.aggregate(pipeline):
            score = doc.pop("score")
            chunk_id = doc.pop("chunk_id")
            text = doc.pop("text")
            metadata = {k: v for k, v in doc.items() if k in METADATA_FIELDS}
            results.append(ScoredChunk(
                chunk_id=chunk_id, text=text, metadata=metadata, score=float(score),
            ))
        return results

    # -- Lookup (used to verify that citations resolve) ----------------------

    def get_chunk(self, chunk_id: str) -> Optional[Dict]:
        """Fetch one chunk by chunk_id. Returns None if it does not exist."""
        return self.coll.find_one({"chunk_id": chunk_id}, {"_id": 0, "embedding": 0})

    @property
    def size(self) -> int:
        return self.coll.count_documents({})
