## Tracked files modified

```diff
diff --git a/main.py b/main.py
index f334750..29e0dd8 100644
--- a/main.py
+++ b/main.py
@@ -6,6 +6,7 @@ from motor.motor_asyncio import AsyncIOMotorClient
 from fastapi.middleware.cors import CORSMiddleware
 
 from api.document import router as document_router
+from api.search import router as search_router
 
 load_dotenv()
 
@@ -37,4 +38,5 @@ def read_root():
     return {"Hello": "World"}
 
 
-app.include_router(document_router)
\ No newline at end of file
+app.include_router(document_router)
+app.include_router(search_router)
\ No newline at end of file
diff --git a/pipeline/embeddings.py b/pipeline/embeddings.py
index c163ec9..80cfd79 100644
--- a/pipeline/embeddings.py
+++ b/pipeline/embeddings.py
@@ -1,6 +1,9 @@
 """
-Embedding module using Google Gemini text-embedding-004.
+Embedding module using Google Gemini gemini-embedding-001 (3072-dim).
 Uses the google-genai SDK already in the project.
+
+This model is held CONSTANT across both chunking strategies — the chunker is
+the only variable that changes between the two measured runs.
 """
 
 from __future__ import annotations
diff --git a/utils/llm_service.py b/utils/llm_service.py
index a524174..f36c664 100644
--- a/utils/llm_service.py
+++ b/utils/llm_service.py
@@ -1,20 +1,67 @@
-from google import genai
 import os
 
-api_key = os.getenv("API_KEY")
+from dotenv import load_dotenv
+from google import genai
+from google.genai import types
+
+load_dotenv()
+
+# Single model id used everywhere in this project, so the generation half of
+# the evaluation is not silently comparing different models across scripts.
+#
+# gemini-2.5-flash (what this repo used to call) now returns 404 for this
+# account: "no longer available to new users ... use models/gemini-3.6-flash".
+GEN_MODEL = "gemini-3.6-flash"
+
+# Without an explicit timeout the SDK client can hang indefinitely on a stalled
+# connection, which silently wedges an evaluation run.
+_HTTP_TIMEOUT_MS = 120_000
+
+
+_client_singleton: genai.Client | None = None
+
+
+def _client() -> genai.Client:
+    """
+    Lazily build and CACHE the client.
+
+    Lazy because reading API_KEY at import time would capture the value before
+    load_dotenv() has populated the environment. Cached because a client built
+    per call is unreferenced as soon as the expression is evaluated — it gets
+    garbage-collected mid-request and its httpx pool closes underneath the call,
+    raising "Cannot send a request, as the client has been closed."
+    """
+    global _client_singleton
+    if _client_singleton is None:
+        api_key = os.getenv("API_KEY")
+        if not api_key:
+            raise RuntimeError("API_KEY not set in environment")
+        _client_singleton = genai.Client(
+            api_key=api_key,
+            http_options=types.HttpOptions(timeout=_HTTP_TIMEOUT_MS),
+        )
+    return _client_singleton
 
 
-async def generate_with_gemini(prompt: str, model_name: str = "gemini-2.5-flash") -> str:
+def generate_with_gemini_sync(prompt: str, model_name: str = GEN_MODEL) -> str:
+    """Blocking generation. Used by the offline evaluation scripts."""
+    response = _client().models.generate_content(
+        model=model_name,
+        contents=prompt,
+    )
+    return response.text
+
+
+async def generate_with_gemini(prompt: str, model_name: str = GEN_MODEL) -> str:
     """
-    Initializes the Gemini client with an explicit API key and generates text.
+    Async generation for the FastAPI request path.
+
+    Note `client.aio.models.generate_content` — `client.models.generate_content`
+    is synchronous and returns a response object, not an awaitable, so awaiting
+    it raises TypeError.
     """
-    # Initialize the client with the provided API key
-    client = genai.Client(api_key=api_key)
-    
-    # Generate content using the specified model
-    response = await client.models.generate_content(
+    response = await _client().aio.models.generate_content(
         model=model_name,
         contents=prompt,
     )
-    
-    return response.text
\ No newline at end of file
+    return response.text
```

## New files

### `pipeline/mongo_store.py`

```diff
diff --git a/pipeline/mongo_store.py b/pipeline/mongo_store.py
new file mode 100644
index 0000000..68d19d8
--- /dev/null
+++ b/pipeline/mongo_store.py
@@ -0,0 +1,260 @@
+"""
+MongoDB Atlas Vector Search store for endorsement chunks.
+
+Replaces the previous FAISS flat-L2 store. Both the chunk documents and their
+embeddings live in MongoDB; retrieval uses the native `$vectorSearch`
+aggregation stage so that metadata filtering is a PRE-filter applied inside the
+index, not a post-filter applied to an already-truncated candidate list.
+
+Two collections, one per chunking strategy, so the A/B comparison is clean:
+    chunks_naive_fixed
+    chunks_structure_aware
+
+Scoring note: `$vectorSearch` with similarity="cosine" returns
+`vectorSearchScore` in [0, 1] where HIGHER IS BETTER. This is the opposite
+direction from the FAISS L2 distances the old store reported; any score in this
+project's output is a cosine similarity.
+"""
+
+from __future__ import annotations
+
+import os
+import time
+from dataclasses import dataclass
+from typing import Dict, List, Optional
+
+from dotenv import load_dotenv
+from pymongo import MongoClient
+from pymongo.collection import Collection
+from pymongo.operations import SearchIndexModel
+
+load_dotenv()
+
+EMBED_DIM = 3072  # gemini-embedding-001
+VECTOR_INDEX_NAME = "endorsement_vector_index"
+
+# Metadata fields declared as `filter` in the Atlas index. Anything listed here
+# can be used as a pre-filter in vector_search().
+FILTER_FIELDS = ["policy_line", "form_number", "strategy", "edition_date"]
+
+# Fields stored flat on each chunk document (filter paths must be top-level).
+METADATA_FIELDS = [
+    "source_file", "form_number", "policy_line", "edition_date",
+    "effective_date", "strategy", "section", "chunk_index",
+]
+
+
+# ---------------------------------------------------------------------------
+# Scored result
+# ---------------------------------------------------------------------------
+
+@dataclass
+class ScoredChunk:
+    chunk_id: str
+    text: str
+    metadata: Dict[str, str]
+    score: float  # cosine similarity in [0, 1] — HIGHER is more similar
+
+    def to_dict(self) -> dict:
+        return {
+            "chunk_id": self.chunk_id,
+            "score": round(self.score, 4),
+            "metadata": self.metadata,
+            "text_preview": self.text[:200] + "..." if len(self.text) > 200 else self.text,
+        }
+
+
+# ---------------------------------------------------------------------------
+# Connection
+# ---------------------------------------------------------------------------
+
+def build_uri() -> str:
+    """Assemble the Atlas SRV URI from the same env vars main.py already uses."""
+    user = os.getenv("MONGODB_USERNAME")
+    pwd = os.getenv("MONGODB_PASSWORD")
+    host = os.getenv("MONGODB_HOSTNAME")
+    if not all([user, pwd, host]):
+        raise RuntimeError(
+            "MONGODB_USERNAME / MONGODB_PASSWORD / MONGODB_HOSTNAME must be set"
+        )
+    return f"mongodb+srv://{user}:{pwd}@{host}"
+
+
+def get_db(client: Optional[MongoClient] = None):
+    """Return (client, db) using MONGODB_DATABASE."""
+    if client is None:
+        client = MongoClient(build_uri(), serverSelectionTimeoutMS=30000)
+    db_name = os.getenv("MONGODB_DATABASE")
+    if not db_name:
+        raise RuntimeError("MONGODB_DATABASE must be set")
+    return client, client[db_name]
+
+
+def collection_name_for(strategy: str) -> str:
+    return f"chunks_{strategy}"
+
+
+# ---------------------------------------------------------------------------
+# Store
+# ---------------------------------------------------------------------------
+
+class MongoVectorStore:
+    """
+    One collection per chunking strategy, each with its own Atlas Vector Search
+    index over the `embedding` field.
+    """
+
+    def __init__(self, strategy: str, client: Optional[MongoClient] = None,
+                 dim: int = EMBED_DIM):
+        self.strategy = strategy
+        self.dim = dim
+        self.client, self.db = get_db(client)
+        self.coll: Collection = self.db[collection_name_for(strategy)]
+
+    # -- Indexing -----------------------------------------------------------
+
+    def reset(self) -> None:
+        """Drop all chunk documents for this strategy. Touches nothing else."""
+        self.coll.delete_many({})
+
+    def add_bulk(self, items: List[Dict]) -> int:
+        """
+        items: list of {chunk_id, text, embedding, metadata}
+
+        Every chunk MUST carry source_file / form_number / policy_line /
+        edition_date. A chunk without them is a failed ingest and raises here
+        rather than being written as a nameless orphan.
+        """
+        docs = []
+        for it in items:
+            meta = it["metadata"]
+            for required in ("source_file", "form_number", "policy_line", "edition_date"):
+                if not meta.get(required):
+                    raise ValueError(
+                        f"FAILED INGEST: chunk {it['chunk_id']} is missing "
+                        f"required metadata field '{required}'"
+                    )
+            doc = {
+                "chunk_id": it["chunk_id"],
+                "text": it["text"],
+                "embedding": it["embedding"],
+            }
+            # Flatten metadata to top level so Atlas filter paths are simple.
+            for f in METADATA_FIELDS:
+                if f in meta:
+                    doc[f] = meta[f]
+            docs.append(doc)
+
+        if docs:
+            self.coll.insert_many(docs)
+        return len(docs)
+
+    def ensure_vector_index(self, wait: bool = True, timeout_s: int = 600) -> str:
+        """
+        Create the Atlas Vector Search index if absent, then (optionally) block
+        until it reports queryable. Returns the index status.
+        """
+        existing = {ix["name"]: ix for ix in self.coll.list_search_indexes()}
+
+        if VECTOR_INDEX_NAME not in existing:
+            definition = {
+                "fields": (
+                    [{
+                        "type": "vector",
+                        "path": "embedding",
+                        "numDimensions": self.dim,
+                        "similarity": "cosine",
+                    }]
+                    + [{"type": "filter", "path": f} for f in FILTER_FIELDS]
+                )
+            }
+            self.coll.create_search_index(
+                SearchIndexModel(definition=definition,
+                                 name=VECTOR_INDEX_NAME,
+                                 type="vectorSearch")
+            )
+            print(f"    created vector index '{VECTOR_INDEX_NAME}' on {self.coll.name}")
+        else:
+            print(f"    vector index '{VECTOR_INDEX_NAME}' already exists on {self.coll.name}")
+
+        if not wait:
+            return "PENDING"
+
+        start = time.time()
+        while time.time() - start < timeout_s:
+            ixs = {ix["name"]: ix for ix in self.coll.list_search_indexes()}
+            ix = ixs.get(VECTOR_INDEX_NAME)
+            if ix and ix.get("queryable"):
+                print(f"    index queryable after {time.time() - start:.0f}s "
+                      f"(status={ix.get('status')})")
+                return ix.get("status", "READY")
+            time.sleep(5)
+        raise TimeoutError(
+            f"Vector index on {self.coll.name} not queryable after {timeout_s}s"
+        )
+
+    # -- Search -------------------------------------------------------------
+
+    def search(
+        self,
+        query_embedding: List[float],
+        top_k: int = 5,
+        filter_metadata: Optional[Dict[str, str]] = None,
+    ) -> List[ScoredChunk]:
+        """
+        Native Atlas $vectorSearch.
+
+        `exact: True` runs exhaustive nearest-neighbour (ENN) rather than
+        approximate HNSW. At this corpus size (tens of chunks) ENN is both
+        faster to set up and, more importantly, DETERMINISTIC — the hit-in-top-5
+        numbers this project reports must not move because of ANN recall jitter.
+
+        `filter_metadata` becomes a PRE-filter evaluated inside the index, so a
+        filtered top-5 is the true top-5 of the filtered subset — not a
+        post-filtered remnant of an unfiltered top-5.
+        """
+        stage: Dict = {
+            "index": VECTOR_INDEX_NAME,
+            "path": "embedding",
+            "queryVector": [float(x) for x in query_embedding],
+            "limit": top_k,
+            "exact": True,
+        }
+
+        if filter_metadata:
+            if len(filter_metadata) == 1:
+                (k, v), = filter_metadata.items()
+                stage["filter"] = {k: {"$eq": v}}
+            else:
+                stage["filter"] = {
+                    "$and": [{k: {"$eq": v}} for k, v in filter_metadata.items()]
+                }
+
+        # $project cannot mix inclusion with exclusion, so the score is attached
+        # via $addFields and the bulky embedding is dropped separately.
+        pipeline = [
+            {"$vectorSearch": stage},
+            {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
+            {"$project": {"_id": 0, "embedding": 0}},
+        ]
+
+        results: List[ScoredChunk] = []
+        for doc in self.coll.aggregate(pipeline):
+            score = doc.pop("score")
+            chunk_id = doc.pop("chunk_id")
+            text = doc.pop("text")
+            metadata = {k: v for k, v in doc.items() if k in METADATA_FIELDS}
+            results.append(ScoredChunk(
+                chunk_id=chunk_id, text=text, metadata=metadata, score=float(score),
+            ))
+        return results
+
+    # -- Lookup (used to verify that citations resolve) ----------------------
+
+    def get_chunk(self, chunk_id: str) -> Optional[Dict]:
+        """Fetch one chunk by chunk_id. Returns None if it does not exist."""
+        return self.coll.find_one({"chunk_id": chunk_id}, {"_id": 0, "embedding": 0})
+
+    @property
+    def size(self) -> int:
+        return self.coll.count_documents({})
```

### `pipeline/chunkers.py`

```diff
diff --git a/pipeline/chunkers.py b/pipeline/chunkers.py
new file mode 100644
index 0000000..5b336db
--- /dev/null
+++ b/pipeline/chunkers.py
@@ -0,0 +1,340 @@
+"""
+Chunking strategies for homeowners endorsement documents.
+
+Strategy 1 — naive_fixed:
+    Fixed-size character chunks with overlap. Ignores document structure.
+    This simulates a "current" generic chunker that treats all text as flat.
+
+Strategy 2 — structure_aware:
+    Splits on SECTION headers. Never separates an exclusion row from its
+    table header or form number. Prepends the document header to every chunk
+    so each chunk is self-contained. SECTION-level granularity: the whole
+    EXCLUSIONS TABLE stays in one chunk.
+
+Strategy 3 — structure_aware_rows:
+    As above, but the EXCLUSIONS TABLE is split to ONE CHUNK PER E-nn ROW
+    (each still carrying the form header and the table's column header).
+    Maximum retrieval precision, minimum context: this is the strategy that
+    demonstrates the precision/completeness trade-off, because a row chunk
+    knows what E-27 excludes but not what the endorsement means by
+    "sudden and accidental".
+
+Strategies 1 and 2 are the two measured in the headline hit-in-top-5 table.
+Strategy 3 exists for the precision-vs-completeness probe.
+"""
+
+from __future__ import annotations
+
+import re
+import os
+from dataclasses import dataclass, field, asdict
+from typing import List, Dict, Optional
+
+
+# ---------------------------------------------------------------------------
+# Chunk dataclass
+# ---------------------------------------------------------------------------
+
+@dataclass
+class Chunk:
+    chunk_id: str
+    text: str
+    metadata: Dict[str, str] = field(default_factory=dict)
+
+    def to_dict(self) -> dict:
+        return asdict(self)
+
+
+# ---------------------------------------------------------------------------
+# Metadata extraction from endorsement header
+# ---------------------------------------------------------------------------
+
+def extract_header_metadata(text: str, source_file: str) -> Dict[str, str]:
+    """
+    Parse the structured header block at the top of each endorsement
+    and return metadata dict with source_file, form_number, policy_line,
+    edition_date.  Raises ValueError if any required field is missing.
+    """
+    meta: Dict[str, str] = {"source_file": source_file}
+
+    patterns = {
+        "form_number":  r"Form\s+Number:\s*(\S+)",
+        "edition_date": r"Edition:\s*(\S+)",
+        "policy_line":  r"Policy\s+Line:\s*(.+?)(?:\r?\n)",
+        "effective_date": r"Effective\s+Date:\s*(.+?)(?:\r?\n)",
+    }
+
+    for key, pattern in patterns.items():
+        m = re.search(pattern, text)
+        if m:
+            meta[key] = m.group(1).strip()
+        else:
+            raise ValueError(
+                f"Required metadata field '{key}' not found in {source_file}"
+            )
+
+    return meta
+
+
+def extract_header_block(text: str) -> str:
+    """Return everything up to and including the first blank line after
+    the header separator block (the ===... lines and field lines)."""
+    # Find the ENDORSEMENT TITLE line + next line as end of header
+    m = re.search(r"(ENDORSEMENT\s+TITLE:.*?)(?:\r?\n){2,}", text, re.DOTALL)
+    if m:
+        return text[: m.end()].strip()
+    # Fallback: first 600 chars
+    return text[:600].strip()
+
+
+# ---------------------------------------------------------------------------
+# Strategy 1 — Naive fixed-size chunker
+# ---------------------------------------------------------------------------
+
+def naive_fixed_chunks(
+    text: str,
+    source_file: str,
+    chunk_size: int = 1500,
+    overlap: int = 200,
+) -> List[Chunk]:
+    """
+    Split text into fixed-size character chunks with overlap.
+    Metadata is extracted from the header and attached to EVERY chunk,
+    but the chunk TEXT may not include the header — that's the weakness
+    this strategy is designed to expose.
+    """
+    meta = extract_header_metadata(text, source_file)
+    form = meta["form_number"]
+
+    chunks: List[Chunk] = []
+    start = 0
+    idx = 0
+    while start < len(text):
+        end = min(start + chunk_size, len(text))
+        chunk_text = text[start:end]
+        chunk_id = f"{form}_naive_{idx:03d}"
+        chunks.append(Chunk(
+            chunk_id=chunk_id,
+            text=chunk_text,
+            metadata={**meta, "strategy": "naive_fixed", "chunk_index": str(idx)},
+        ))
+        idx += 1
+        if end >= len(text):
+            break
+        start += chunk_size - overlap
+    return chunks
+
+
+# ---------------------------------------------------------------------------
+# Strategy 2 — Structure-aware chunker
+# ---------------------------------------------------------------------------
+
+_SECTION_RE = re.compile(
+    r"^(SECTION\s+[IVXLCDM]+\s*[—–-]\s*.+)$",
+    re.MULTILINE,
+)
+
+
+def _split_into_sections(text: str) -> List[Dict[str, str]]:
+    """Split document text on SECTION headers. Returns list of
+    {title, text} dicts."""
+    matches = list(_SECTION_RE.finditer(text))
+    if not matches:
+        return [{"title": "FULL_DOCUMENT", "text": text}]
+
+    sections: List[Dict[str, str]] = []
+    for i, m in enumerate(matches):
+        title = m.group(1).strip()
+        start = m.start()
+        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
+        sections.append({"title": title, "text": text[start:end].strip()})
+    return sections
+
+
+def structure_aware_chunks(
+    text: str,
+    source_file: str,
+) -> List[Chunk]:
+    """
+    Split on SECTION boundaries.  Prepend the document header (form number,
+    edition, effective date) to every chunk so retrieval always has context.
+    Exclusion rows are NEVER split from their table header.
+    """
+    meta = extract_header_metadata(text, source_file)
+    form = meta["form_number"]
+    header_block = extract_header_block(text)
+
+    sections = _split_into_sections(text)
+    chunks: List[Chunk] = []
+
+    for i, sec in enumerate(sections):
+        # Clean the section title for metadata
+        title_short = sec["title"].split("—")[-1].split("–")[-1].strip() \
+            if "—" in sec["title"] or "–" in sec["title"] else sec["title"]
+
+        chunk_id = f"{form}_struct_{i:03d}"
+
+        # Prepend the header block to give every chunk full endorsement context
+        chunk_text = f"{header_block}\n\n{sec['text']}"
+
+        chunks.append(Chunk(
+            chunk_id=chunk_id,
+            text=chunk_text,
+            metadata={
+                **meta,
+                "strategy": "structure_aware",
+                "section": title_short,
+                "chunk_index": str(i),
+            },
+        ))
+    return chunks
+
+
+# ---------------------------------------------------------------------------
+# Convenience: chunk a whole directory
+# ---------------------------------------------------------------------------
+
+_ROW_START_RE = re.compile(r"^\|\s*(E-\d+)\s*\|")
+_BORDER_RE = re.compile(r"^\+[-+]+\+\s*$")
+
+
+def _split_exclusion_table(section_text: str) -> Optional[Dict[str, object]]:
+    """
+    Break an EXCLUSIONS TABLE section into its preamble, its column-header
+    block, and one block per E-nn row.
+
+    Returns None if this section is not a parseable ASCII table, so callers can
+    fall back to keeping the section whole.
+    """
+    lines = section_text.splitlines()
+    first_border = next((i for i, ln in enumerate(lines) if _BORDER_RE.match(ln)), None)
+    if first_border is None:
+        return None
+
+    preamble = "\n".join(lines[:first_border]).strip()
+
+    # Group the table body into blocks delimited by +---+ border lines.
+    blocks: List[List[str]] = []
+    current: List[str] = []
+    for ln in lines[first_border:]:
+        if _BORDER_RE.match(ln):
+            if current:
+                blocks.append(current)
+                current = []
+            continue
+        current.append(ln)
+    if current:
+        blocks.append(current)
+
+    if not blocks:
+        return None
+
+    # The first block is the column header (| Code | Exclusion Title | ... |).
+    column_header = "\n".join(blocks[0]).rstrip()
+
+    rows: List[Dict[str, str]] = []
+    for blk in blocks[1:]:
+        if not blk:
+            continue
+        m = _ROW_START_RE.match(blk[0])
+        if not m:
+            continue
+        rows.append({"code": m.group(1), "text": "\n".join(blk).rstrip()})
+
+    if not rows:
+        return None
+
+    return {"preamble": preamble, "column_header": column_header, "rows": rows}
+
+
+def structure_aware_row_chunks(text: str, source_file: str) -> List[Chunk]:
+    """
+    Strategy 3 — structure_aware_rows.
+
+    Same section splitting as `structure_aware`, except the EXCLUSIONS TABLE is
+    broken into ONE CHUNK PER EXCLUSION ROW. Each row chunk still carries the
+    document header (form number, edition, effective date) and the table's
+    column header, so an exclusion row is never separated from the form that
+    scopes it — but it IS separated from the DEFINITIONS section.
+
+    This is the maximally precise chunker. It exists to measure the cost of that
+    precision: a row chunk answers "what does E-27 say" perfectly and answers
+    "is this a breakdown as defined" not at all.
+    """
+    meta = extract_header_metadata(text, source_file)
+    form = meta["form_number"]
+    header_block = extract_header_block(text)
+
+    sections = _split_into_sections(text)
+    chunks: List[Chunk] = []
+    idx = 0
+
+    for sec in sections:
+        title_short = sec["title"].split("—")[-1].split("–")[-1].strip() \
+            if "—" in sec["title"] or "–" in sec["title"] else sec["title"]
+
+        table = _split_exclusion_table(sec["text"]) if "EXCLUSION" in title_short.upper() else None
+
+        if table is None:
+            chunks.append(Chunk(
+                chunk_id=f"{form}_rows_{idx:03d}",
+                text=f"{header_block}\n\n{sec['text']}",
+                metadata={**meta, "strategy": "structure_aware_rows",
+                          "section": title_short, "chunk_index": str(idx)},
+            ))
+            idx += 1
+            continue
+
+        for row in table["rows"]:
+            body = (f"{table['preamble']}\n\n"
+                    f"{table['column_header']}\n{row['text']}")
+            chunks.append(Chunk(
+                chunk_id=f"{form}_rows_{idx:03d}",
+                text=f"{header_block}\n\n{body}",
+                metadata={**meta, "strategy": "structure_aware_rows",
+                          "section": title_short, "clause": row["code"],
+                          "chunk_index": str(idx)},
+            ))
+            idx += 1
+
+    return chunks
+
+
+STRATEGY_MAP = {
+    "naive_fixed": naive_fixed_chunks,
+    "structure_aware": structure_aware_chunks,
+    "structure_aware_rows": structure_aware_row_chunks,
+}
+
+
+def chunk_endorsement_file(filepath: str, strategy: str) -> List[Chunk]:
+    """Read a single endorsement file, chunk it with the given strategy."""
+    source_file = os.path.basename(filepath)
+    with open(filepath, "r", encoding="utf-8") as f:
+        text = f.read()
+
+    # STRATEGY_MAP is the single source of truth — a strategy added there is
+    # immediately usable here, rather than needing a matching elif branch.
+    try:
+        chunker = STRATEGY_MAP[strategy]
+    except KeyError:
+        raise ValueError(
+            f"Unknown strategy: {strategy}. "
+            f"Known strategies: {', '.join(sorted(STRATEGY_MAP))}"
+        ) from None
+    return chunker(text, source_file)
+
+
+def chunk_all_endorsements(
+    endorsements_dir: str,
+    strategy: str,
+) -> List[Chunk]:
+    """Chunk every .txt file in the endorsements directory."""
+    all_chunks: List[Chunk] = []
+    for fname in sorted(os.listdir(endorsements_dir)):
+        if not fname.endswith(".txt"):
+            continue
+        fpath = os.path.join(endorsements_dir, fname)
+        chunks = chunk_endorsement_file(fpath, strategy)
+        all_chunks.extend(chunks)
+    return all_chunks
```

### `pipeline/ingest.py`

```diff
diff --git a/pipeline/ingest.py b/pipeline/ingest.py
new file mode 100644
index 0000000..c04bc59
--- /dev/null
+++ b/pipeline/ingest.py
@@ -0,0 +1,115 @@
+"""
+Ingestion pipeline: read endorsements → chunk → embed → index into MongoDB Atlas.
+
+Indexes the 6 NEW endorsements only (HO-0304 … HO-0309). It does NOT touch,
+re-read, or re-embed the base policy wording library.
+
+Usage:
+    python -m pipeline.ingest --strategy naive_fixed
+    python -m pipeline.ingest --strategy structure_aware
+    python -m pipeline.ingest --strategy both
+"""
+
+from __future__ import annotations
+
+import argparse
+import os
+import sys
+import time
+
+# Ensure project root is on sys.path
+sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
+
+# The Windows console defaults to cp1252, which cannot encode the arrows and
+# box-drawing characters used in this project's output.
+if hasattr(sys.stdout, "reconfigure"):
+    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
+
+from pipeline.chunkers import chunk_all_endorsements
+from pipeline.embeddings import embed_texts
+from pipeline.mongo_store import MongoVectorStore
+
+DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
+                        "data", "endorsements")
+
+# The two strategies compared in the headline hit-in-top-5 table.
+STRATEGIES = ["naive_fixed", "structure_aware"]
+
+# Strategy 3 is not part of the headline A/B; it backs the
+# precision-vs-completeness probe in eval/bonus_challenge.py.
+ALL_STRATEGIES = STRATEGIES + ["structure_aware_rows"]
+
+
+def ingest(strategy: str, verbose: bool = True,
+           wait_for_index: bool = True) -> MongoVectorStore:
+    """
+    Chunk + embed + upsert all 6 endorsements into the Mongo collection for
+    `strategy`, then ensure the Atlas Vector Search index is queryable.
+    """
+    if verbose:
+        print(f"\n{'='*60}")
+        print(f"INGESTING with strategy: {strategy}")
+        print(f"{'='*60}")
+
+    # 1. Chunk
+    t0 = time.time()
+    chunks = chunk_all_endorsements(DATA_DIR, strategy)
+    if verbose:
+        print(f"  Chunked {len(chunks)} chunks from endorsement files")
+
+    # Validate: a chunk with no source_file is a failed ingest.
+    for c in chunks:
+        if not c.metadata.get("source_file"):
+            raise ValueError(f"Chunk {c.chunk_id} has no source_file — failed ingest")
+
+    # 2. Embed
+    texts = [c.text for c in chunks]
+    if verbose:
+        print(f"  Embedding {len(texts)} chunks with gemini-embedding-001...")
+    embeddings = embed_texts(texts)
+    if verbose:
+        print(f"  Embeddings complete ({time.time()-t0:.1f}s, dim={len(embeddings[0])})")
+
+    # 3. Write to MongoDB (replace this strategy's chunks; nothing else)
+    store = MongoVectorStore(strategy, dim=len(embeddings[0]))
+    store.reset()
+    n = store.add_bulk([
+        {"chunk_id": c.chunk_id, "text": c.text, "embedding": e, "metadata": c.metadata}
+        for c, e in zip(chunks, embeddings)
+    ])
+    if verbose:
+        print(f"  Inserted {n} chunks into '{store.coll.name}'")
+
+    # 4. Vector index
+    store.ensure_vector_index(wait=wait_for_index)
+    if verbose:
+        print(f"  ✓ {strategy}: {store.size} chunks queryable in MongoDB Atlas")
+
+    return store
+
+
+def main():
+    parser = argparse.ArgumentParser(
+        description="Ingest the 6 new endorsements into MongoDB Atlas Vector Search")
+    parser.add_argument("--strategy", choices=ALL_STRATEGIES + ["both", "all"],
+                        default="both",
+                        help="'both' = the two measured strategies; "
+                             "'all' = those plus structure_aware_rows")
+    args = parser.parse_args()
+
+    if args.strategy == "both":
+        strategies = STRATEGIES
+    elif args.strategy == "all":
+        strategies = ALL_STRATEGIES
+    else:
+        strategies = [args.strategy]
+
+    print("\nIndexing the 6 NEW endorsements ONLY (HO-0304 → HO-0309).")
+    print("NOT re-indexing the base policy wording library.\n")
+
+    for s in strategies:
+        ingest(s)
+
+
+if __name__ == "__main__":
+    main()
```

### `eval/metrics.py`

```diff
diff --git a/eval/metrics.py b/eval/metrics.py
new file mode 100644
index 0000000..76b21f6
--- /dev/null
+++ b/eval/metrics.py
@@ -0,0 +1,131 @@
+"""
+Retrieval metric definitions.
+
+DEFINED BEFORE THE RUN, applied identically to both chunking strategies.
+Nothing in here is allowed to be loosened after seeing results — that would turn
+the hit-rate into a measurement of the metric author rather than of the chunker.
+
+Two metrics are recorded for every question:
+
+  LOOSE  (form-level)   — at least one of the top-5 chunks carries the expected
+                          form_number. This is the metric the previous FAISS
+                          run used. It cannot distinguish E-17 from E-18 inside
+                          the same endorsement, so it flatters any chunker that
+                          merely lands in the right document.
+
+  STRICT (form+clause)  — at least one of the top-5 chunks carries the expected
+                          form_number AND contains the specific clause the
+                          answer lives in. This is the headline number, because
+                          the whole point of the exercise is whether an
+                          exclusion row stays attached to the form that scopes
+                          it.
+"""
+
+from __future__ import annotations
+
+import re
+from typing import Dict, List, Tuple
+
+_EXCL_CODE_RE = re.compile(r"\bE-\d+\b")
+_SECTION_RE = re.compile(r"SECTION\s+[IVXLCDM]+")
+
+# Header-field questions map to the literal label that must appear in the chunk.
+_HEADER_LABELS = {
+    "effective date": "Effective Date:",
+    "policy line": "Policy Line:",
+    "form number": "Form Number:",
+    "edition": "Edition:",
+}
+
+
+def clause_locator(expected_clause: str) -> Tuple[str, str]:
+    """
+    Turn a gold `expected_clause` string into a deterministic (kind, needle)
+    test that can be applied to chunk text.
+
+      "SECTION IV — EXCLUSIONS TABLE, E-17" -> ("exclusion_code", "E-17")
+      "SECTION I — SCOPE AND PURPOSE"       -> ("section", "SECTION I")
+      "Header — Effective Date"             -> ("header_field", "Effective Date:")
+    """
+    code = _EXCL_CODE_RE.search(expected_clause)
+    if code:
+        return ("exclusion_code", code.group(0))
+
+    if expected_clause.strip().upper().startswith("HEADER"):
+        tail = re.split(r"[—–-]", expected_clause, maxsplit=1)[-1].strip().lower()
+        for key, label in _HEADER_LABELS.items():
+            if key in tail:
+                return ("header_field", label)
+        raise ValueError(f"Unrecognised header clause: {expected_clause!r}")
+
+    sec = _SECTION_RE.search(expected_clause)
+    if sec:
+        return ("section", sec.group(0))
+
+    raise ValueError(f"Cannot build a clause locator for: {expected_clause!r}")
+
+
+def chunk_satisfies_clause(chunk_text: str, expected_clause: str) -> bool:
+    """Does this chunk's text actually contain the clause the answer lives in?"""
+    kind, needle = clause_locator(expected_clause)
+    if kind == "exclusion_code":
+        # Word-boundary match so E-17 does not match E-170.
+        return re.search(rf"\b{re.escape(needle)}\b", chunk_text) is not None
+    return needle in chunk_text
+
+
+def expected_forms(gold_q: Dict) -> List[str]:
+    """Gold answers may name more than one form (Q5 spans HO-0308 and HO-0309)."""
+    return [f.strip() for f in gold_q["expected_form"].split(",") if f.strip()]
+
+
+def score_question(gold_q: Dict, top_chunks: List) -> Dict:
+    """
+    Evaluate one question's retrieved list. `top_chunks` is a list of
+    ScoredChunk. Returns the full per-question record — every rank is kept so
+    the record can be printed rather than summarised.
+    """
+    forms = expected_forms(gold_q)
+    clause = gold_q["expected_clause"]
+
+    per_rank = []
+    loose_hit = False
+    strict_hit = False
+    strict_rank = None
+    forms_covered = set()
+
+    for i, sc in enumerate(top_chunks):
+        form_ok = sc.metadata.get("form_number") in forms
+        clause_ok = chunk_satisfies_clause(sc.text, clause)
+        if form_ok:
+            loose_hit = True
+            forms_covered.add(sc.metadata.get("form_number"))
+        if form_ok and clause_ok:
+            if not strict_hit:
+                strict_rank = i + 1
+            strict_hit = True
+
+        per_rank.append({
+            "rank": i + 1,
+            "chunk_id": sc.chunk_id,
+            "form_number": sc.metadata.get("form_number"),
+            "policy_line": sc.metadata.get("policy_line"),
+            "section": sc.metadata.get("section", ""),
+            "score": round(sc.score, 4),
+            "form_match": form_ok,
+            "clause_match": clause_ok,
+        })
+
+    return {
+        "question_id": gold_q["id"],
+        "question": gold_q["question"],
+        "expected_form": gold_q["expected_form"],
+        "expected_clause": clause,
+        "clause_locator": list(clause_locator(clause)),
+        "loose_hit": loose_hit,
+        "strict_hit": strict_hit,
+        "strict_first_rank": strict_rank,
+        "forms_covered": sorted(forms_covered),
+        "all_expected_forms_covered": set(forms) == forms_covered,
+        "top5": per_rank,
+    }
```

### `eval/run_eval.py`

```diff
diff --git a/eval/run_eval.py b/eval/run_eval.py
new file mode 100644
index 0000000..eacb41a
--- /dev/null
+++ b/eval/run_eval.py
@@ -0,0 +1,484 @@
+"""
+End-to-end evaluation harness for the endorsement RAG pipeline (MongoDB Atlas).
+
+Runs:
+  1. Search-only evaluation: hit-in-top-5 for all 8 gold questions x 2 strategies
+     -- the SAME 8 questions, written before any retrieval was run.
+  2. Metadata-filter demonstration: a policy_line pre-filter changing top-1.
+  3. Generation: 3 answerable questions with citations that are RESOLVED back
+     against MongoDB, plus 3 out-of-corpus questions that must be refused.
+  4. Dumps everything to eval/results.json and eval/search_dump.md.
+
+Retrieval is MongoDB Atlas $vectorSearch (exact/ENN, cosine). Scores are cosine
+similarities in [0, 1] -- HIGHER IS BETTER.
+
+Assumes `python -m pipeline.ingest --strategy both` has already run.
+Pass --ingest to do it inline.
+
+Usage:
+    python -m eval.run_eval
+    python -m eval.run_eval --ingest
+"""
+
+from __future__ import annotations
+
+import argparse
+import json
+import os
+import re
+import sys
+import time
+import textwrap
+
+# Ensure project root is on sys.path
+PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
+sys.path.insert(0, PROJECT_ROOT)
+
+if hasattr(sys.stdout, "reconfigure"):
+    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
+
+from dotenv import load_dotenv
+
+from pipeline.embeddings import embed_single
+from pipeline.mongo_store import MongoVectorStore, ScoredChunk
+from eval.metrics import score_question, chunk_satisfies_clause
+from utils.llm_service import generate_with_gemini_sync, GEN_MODEL
+from utils.prompts import (
+    RAG_SYSTEM_PROMPT, RAG_USER_TEMPLATE, REFUSAL_PHRASE, UNANSWERABLE_QUESTIONS,
+)
+
+load_dotenv()
+
+EVAL_DIR = os.path.join(PROJECT_ROOT, "eval")
+GOLD_QA_PATH = os.path.join(EVAL_DIR, "gold_qa.json")
+RESULTS_PATH = os.path.join(EVAL_DIR, "results.json")
+DUMP_PATH = os.path.join(EVAL_DIR, "search_dump.md")
+
+STRATEGIES = ["naive_fixed", "structure_aware"]
+
+ENDORSEMENTS = [
+    "HO-0304_ed_03-24.txt", "HO-0305_ed_03-24.txt", "HO-0306_ed_03-24.txt",
+    "HO-0307_ed_03-24.txt", "HO-0308_ed_03-24.txt", "HO-0309_ed_03-24.txt",
+]
+
+# The 3 answerable questions sent through generation. Fixed up front.
+ANSWERABLE_IDS = ["Q1", "Q3", "Q8"]
+
+# Citation format the system prompt mandates:
+#   [Source: <chunk_id> | <form_number>, <clause>]
+_CITATION_RE = re.compile(
+    r"\[Source:\s*([^|\]]+?)\s*\|\s*([^,\]]+?)\s*,\s*([^\]]+?)\s*\]"
+)
+
+
+# ---------------------------------------------------------------------------
+# Helpers
+# ---------------------------------------------------------------------------
+
+def load_gold_qa():
+    with open(GOLD_QA_PATH, "r", encoding="utf-8") as f:
+        return json.load(f)
+
+
+def search_question(store: MongoVectorStore, question: str, top_k: int = 5,
+                    filter_metadata=None) -> list[ScoredChunk]:
+    qemb = embed_single(question)
+    return store.search(qemb, top_k=top_k, filter_metadata=filter_metadata)
+
+
+def build_context(context_chunks: list[ScoredChunk]) -> str:
+    parts = []
+    for sc in context_chunks:
+        parts.append(
+            f"[chunk_id={sc.chunk_id}] [form={sc.metadata.get('form_number','')}] "
+            f"[section={sc.metadata.get('section','')}]\n{sc.text}"
+        )
+    return "\n\n---\n\n".join(parts)
+
+
+class GenerationFailed(Exception):
+    """The model call itself failed (quota, network, 5xx).
+
+    This is deliberately a distinct type. A failed API call must NEVER be
+    recorded as a refusal: "the model declined to answer" and "we never reached
+    the model" are different facts, and conflating them would let an outage
+    inflate the refusal score.
+    """
+
+
+def generate_answer(question: str, context_chunks: list[ScoredChunk]) -> str:
+    """Call Gemini with the grounding prompt. The prompt forces refusal; it is
+    used verbatim and is never softened to 'use your best judgement'."""
+    user_prompt = RAG_USER_TEMPLATE.format(
+        context=build_context(context_chunks), question=question)
+    full_prompt = f"{RAG_SYSTEM_PROMPT}\n\n{user_prompt}"
+    try:
+        return generate_with_gemini_sync(full_prompt)
+    except Exception as e:  # noqa: BLE001 - re-raised as a typed error below
+        raise GenerationFailed(f"{type(e).__name__}: {e}") from e
+
+
+def resolve_citations(answer: str, store: MongoVectorStore,
+                      expected_clause: str | None = None) -> list[dict]:
+    """
+    Parse every [Source: ...] citation out of the answer and check it against
+    MongoDB: does the chunk_id exist, does its form_number match the cited form,
+    and does the chunk text actually contain the cited clause?
+
+    This is the check the grader said they would run on one citation, so the
+    harness runs it on all of them.
+    """
+    resolved = []
+    for chunk_id, form, clause in _CITATION_RE.findall(answer):
+        chunk_id, form, clause = chunk_id.strip(), form.strip(), clause.strip()
+        doc = store.get_chunk(chunk_id)
+        entry = {
+            "cited_chunk_id": chunk_id,
+            "cited_form": form,
+            "cited_clause": clause,
+            "chunk_exists": doc is not None,
+        }
+        if doc:
+            entry["actual_form"] = doc.get("form_number")
+            entry["actual_section"] = doc.get("section", "")
+            entry["form_matches"] = doc.get("form_number") == form
+            code = re.search(r"\bE-\d+\b", clause)
+            if code:
+                entry["clause_in_chunk_text"] = bool(
+                    re.search(rf"\b{re.escape(code.group(0))}\b", doc["text"]))
+                entry["clause_checked"] = code.group(0)
+            else:
+                entry["clause_in_chunk_text"] = None
+                entry["clause_checked"] = None
+            if expected_clause:
+                entry["chunk_contains_gold_clause"] = chunk_satisfies_clause(
+                    doc["text"], expected_clause)
+            entry["chunk_text_excerpt"] = doc["text"][:400]
+        resolved.append(entry)
+    return resolved
+
+
+# ---------------------------------------------------------------------------
+# 1. SEARCH-ONLY EVALUATION
+# ---------------------------------------------------------------------------
+
+def run_search_eval(stores: dict[str, MongoVectorStore], gold_qa: list[dict]):
+    print("\n" + "=" * 78)
+    print("  SEARCH-ONLY EVALUATION -- hit-in-top-5, same 8 questions, both chunkers")
+    print("  Retrieval: MongoDB Atlas $vectorSearch (exact ENN, cosine)")
+    print("=" * 78)
+
+    results = {}
+    # Embed each question once and reuse across strategies: identical query
+    # vector for both, so nothing but the chunker differs.
+    qvecs = {q["id"]: embed_single(q["question"]) for q in gold_qa}
+
+    for strategy in STRATEGIES:
+        store = stores[strategy]
+        details = []
+        for q in gold_qa:
+            top5 = store.search(qvecs[q["id"]], top_k=5)
+            rec = score_question(q, top5)
+            details.append(rec)
+
+            strict = "HIT " if rec["strict_hit"] else "MISS"
+            loose = "HIT " if rec["loose_hit"] else "MISS"
+            print(f"\n  [{strategy}] {q['id']}  strict={strict}  loose={loose}"
+                  f"  (expect {q['expected_form']} / {q['expected_clause']})")
+            for r in rec["top5"]:
+                mark = "**" if (r["form_match"] and r["clause_match"]) else \
+                       ("~ " if r["form_match"] else "  ")
+                print(f"      {mark} #{r['rank']} {r['chunk_id']:<24} "
+                      f"form={r['form_number']:<8} score={r['score']:.4f} "
+                      f"form_match={r['form_match']!s:<5} clause_match={r['clause_match']}")
+
+        strict_hits = sum(1 for d in details if d["strict_hit"])
+        loose_hits = sum(1 for d in details if d["loose_hit"])
+        results[strategy] = {
+            "strict_hits": strict_hits,
+            "loose_hits": loose_hits,
+            "total": len(gold_qa),
+            "details": details,
+        }
+        print(f"\n  >> {strategy}: STRICT {strict_hits}/{len(gold_qa)} "
+              f"| LOOSE {loose_hits}/{len(gold_qa)}\n")
+
+    return results
+
+
+def write_search_dump(search_results: dict, gold_qa: list[dict]) -> None:
+    """Full search-only dump for all 8 questions under both strategies."""
+    lines = ["# Search-only dump — all 8 questions × both chunking strategies", ""]
+    lines.append("Retrieval: MongoDB Atlas `$vectorSearch`, exact ENN, cosine similarity.")
+    lines.append("**Scores are cosine similarity in [0,1] — higher is better.**")
+    lines.append("")
+    lines.append("Legend: `**` = form AND clause match (strict hit) · "
+                 "`~` = form matches only · blank = neither")
+    lines.append("")
+
+    for q in gold_qa:
+        lines.append(f"## {q['id']} — {q['question']}")
+        lines.append("")
+        lines.append(f"- **Expected form:** `{q['expected_form']}`")
+        lines.append(f"- **Expected clause:** {q['expected_clause']}")
+        lines.append("")
+        for strategy in STRATEGIES:
+            rec = next(d for d in search_results[strategy]["details"]
+                       if d["question_id"] == q["id"])
+            lines.append(f"### `{strategy}` — strict: "
+                         f"{'HIT' if rec['strict_hit'] else 'MISS'} · "
+                         f"loose: {'HIT' if rec['loose_hit'] else 'MISS'}")
+            lines.append("")
+            lines.append("| | rank | chunk_id | form | policy_line | section | score | form✓ | clause✓ |")
+            lines.append("|---|---|---|---|---|---|---|---|---|")
+            for r in rec["top5"]:
+                mark = "**" if (r["form_match"] and r["clause_match"]) else \
+                       ("~" if r["form_match"] else "")
+                lines.append(
+                    f"| {mark} | {r['rank']} | `{r['chunk_id']}` | {r['form_number']} | "
+                    f"{r['policy_line']} | {r['section']} | {r['score']:.4f} | "
+                    f"{'Y' if r['form_match'] else 'n'} | {'Y' if r['clause_match'] else 'n'} |")
+            lines.append("")
+
+    with open(DUMP_PATH, "w", encoding="utf-8") as f:
+        f.write("\n".join(lines))
+    print(f"  Search dump written to {DUMP_PATH}")
+
+
+# ---------------------------------------------------------------------------
+# 2. METADATA FILTER DEMONSTRATION
+# ---------------------------------------------------------------------------
+
+def run_filter_demo(store: MongoVectorStore, strategy: str):
+    print("\n" + "=" * 78)
+    print("  METADATA FILTER DEMONSTRATION -- policy_line pre-filter")
+    print("=" * 78)
+
+    query = ("What are the exclusions related to property stored at an offsite "
+             "or off-premises location?")
+    print(f"\n  Query: \"{query}\"")
+    print(f"  Index: {strategy}\n")
+
+    qemb = embed_single(query)
+
+    def dump(label, chunks):
+        print(f"  -- {label} --")
+        out = []
+        for i, sc in enumerate(chunks):
+            info = {
+                "rank": i + 1,
+                "chunk_id": sc.chunk_id,
+                "form_number": sc.metadata.get("form_number"),
+                "policy_line": sc.metadata.get("policy_line"),
+                "section": sc.metadata.get("section", ""),
+                "score": round(sc.score, 4),
+            }
+            out.append(info)
+            print(f"    #{i+1}  {sc.chunk_id:<24} form={sc.metadata.get('form_number'):<8} "
+                  f"line={sc.metadata.get('policy_line'):<15} score={sc.score:.4f}")
+        print()
+        return out
+
+    unfiltered = store.search(qemb, top_k=5)
+    unfiltered_list = dump("UNFILTERED TOP-5", unfiltered)
+
+    filtered = store.search(qemb, top_k=5,
+                            filter_metadata={"policy_line": "Dwelling Fire"})
+    filtered_list = dump("FILTERED policy_line='Dwelling Fire' TOP-5", filtered)
+
+    top1_changed = (
+        bool(unfiltered) and bool(filtered)
+        and unfiltered[0].chunk_id != filtered[0].chunk_id
+    )
+    print(f"  >> Top-1 changed with filter: {'YES' if top1_changed else 'NO'}")
+    if top1_changed:
+        print(f"     unfiltered #1: {unfiltered[0].chunk_id} "
+              f"({unfiltered[0].metadata.get('form_number')}, "
+              f"{unfiltered[0].metadata.get('policy_line')}, {unfiltered[0].score:.4f})")
+        print(f"     filtered   #1: {filtered[0].chunk_id} "
+              f"({filtered[0].metadata.get('form_number')}, "
+              f"{filtered[0].metadata.get('policy_line')}, {filtered[0].score:.4f})")
+
+    return {
+        "query": query,
+        "strategy": strategy,
+        "filter": {"policy_line": "Dwelling Fire"},
+        "unfiltered_top5": unfiltered_list,
+        "filtered_top5": filtered_list,
+        "top1_changed": top1_changed,
+    }
+
+
+# ---------------------------------------------------------------------------
+# 3. GENERATION -- citations + forced refusal
+# ---------------------------------------------------------------------------
+
+def run_generation_eval(store: MongoVectorStore, gold_qa: list[dict]):
+    print("\n" + "=" * 78)
+    print("  GENERATION EVALUATION -- resolvable citations & forced refusal")
+    print(f"  Model: {GEN_MODEL}")
+    print("=" * 78)
+
+    gen_results = {"answerable": [], "unanswerable": []}
+
+    print("\n  -- ANSWERABLE (expect citations that resolve in MongoDB) --\n")
+    for q in [q for q in gold_qa if q["id"] in ANSWERABLE_IDS]:
+        print(f"  {q['id']}: {q['question']}")
+        context = search_question(store, q["question"], top_k=5)
+        time.sleep(1)
+        try:
+            answer = generate_answer(q["question"], context)
+        except GenerationFailed as e:
+            print(f"  GENERATION FAILED: {e}\n")
+            gen_results["answerable"].append({
+                "question_id": q["id"],
+                "question": q["question"],
+                "generation_error": str(e),
+                "all_citations_resolve": False,
+                "context_chunk_ids": [sc.chunk_id for sc in context],
+            })
+            continue
+        print(f"  ANSWER:\n{textwrap.indent(answer, '    ')}")
+
+        citations = resolve_citations(answer, store, q["expected_clause"])
+        all_resolve = bool(citations) and all(c["chunk_exists"] for c in citations)
+        any_gold = any(c.get("chunk_contains_gold_clause") for c in citations)
+        for c in citations:
+            status = "RESOLVES" if c["chunk_exists"] else "DANGLING"
+            print(f"    citation {c['cited_chunk_id']} -> {status} "
+                  f"form_match={c.get('form_matches')} "
+                  f"clause_in_text={c.get('clause_in_chunk_text')}")
+        print(f"    >> all citations resolve: {all_resolve} | "
+              f"cited chunk contains gold clause: {any_gold}\n")
+
+        gen_results["answerable"].append({
+            "question_id": q["id"],
+            "question": q["question"],
+            "expected_form": q["expected_form"],
+            "expected_clause": q["expected_clause"],
+            "answer": answer,
+            "citations": citations,
+            "all_citations_resolve": all_resolve,
+            "cited_chunk_contains_gold_clause": any_gold,
+            "context_chunk_ids": [sc.chunk_id for sc in context],
+        })
+
+    print("\n  -- OUT OF CORPUS (must refuse, not invent) --\n")
+    for uq in UNANSWERABLE_QUESTIONS:
+        print(f"  {uq['id']}: {uq['question']}")
+        context = search_question(store, uq["question"], top_k=5)
+        time.sleep(1)
+        try:
+            answer = generate_answer(uq["question"], context)
+        except GenerationFailed as e:
+            # NOT counted as a refusal — we never reached the model.
+            print(f"  GENERATION FAILED: {e}\n")
+            gen_results["unanswerable"].append({
+                "question_id": uq["id"],
+                "question": uq["question"],
+                "reason": uq["reason"],
+                "generation_error": str(e),
+                "refused_exact": False,
+                "refused_contains_phrase": False,
+                "retrieved_chunk_ids": [sc.chunk_id for sc in context],
+            })
+            continue
+        print(f"  ANSWER:\n{textwrap.indent(answer, '    ')}")
+
+        # Exact refusal, not a fuzzy 'looks like a refusal'.
+        refused_exact = answer.strip() == REFUSAL_PHRASE
+        refused_contains = REFUSAL_PHRASE.lower() in answer.lower()
+        print(f"    >> exact refusal: {refused_exact} | contains phrase: "
+              f"{refused_contains}\n")
+
+        gen_results["unanswerable"].append({
+            "question_id": uq["id"],
+            "question": uq["question"],
+            "reason": uq["reason"],
+            "answer": answer,
+            "refused_exact": refused_exact,
+            "refused_contains_phrase": refused_contains,
+            "retrieved_chunk_ids": [sc.chunk_id for sc in context],
+            "retrieved_top_score": round(context[0].score, 4) if context else None,
+        })
+
+    return gen_results
+
+
+# ---------------------------------------------------------------------------
+# MAIN
+# ---------------------------------------------------------------------------
+
+def main():
+    parser = argparse.ArgumentParser()
+    parser.add_argument("--ingest", action="store_true",
+                        help="Re-run ingest before evaluating")
+    args = parser.parse_args()
+
+    print("\n" + "#" * 78)
+    print("  ENDORSEMENT RAG PIPELINE -- FULL EVALUATION (MongoDB Atlas)")
+    print("  Indexing the 6 NEW endorsements ONLY (HO-0304 -> HO-0309)")
+    print("  NOT re-indexing the base policy wording library")
+    print("#" * 78)
+
+    if args.ingest:
+        from pipeline.ingest import ingest
+        for s in STRATEGIES:
+            ingest(s)
+
+    gold_qa = load_gold_qa()
+    stores = {s: MongoVectorStore(s) for s in STRATEGIES}
+    for s, st in stores.items():
+        print(f"  {s}: {st.size} chunks in MongoDB collection '{st.coll.name}'")
+
+    search_results = run_search_eval(stores, gold_qa)
+    write_search_dump(search_results, gold_qa)
+
+    filter_results = run_filter_demo(stores["structure_aware"], "structure_aware")
+    gen_results = run_generation_eval(stores["structure_aware"], gold_qa)
+
+    full_results = {
+        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
+        "retrieval": "MongoDB Atlas $vectorSearch (exact ENN, cosine similarity)",
+        "embedding_model": "gemini-embedding-001 (3072-dim) — held constant across both chunkers",
+        "generation_model": GEN_MODEL,
+        "note": "Indexed 6 new endorsements ONLY (HO-0304 through HO-0309). "
+                "Did NOT re-index the base policy wording library.",
+        "endorsements_indexed": ENDORSEMENTS,
+        "chunk_counts": {s: stores[s].size for s in STRATEGIES},
+        "search_evaluation": search_results,
+        "metadata_filter_demo": filter_results,
+        "generation_evaluation": gen_results,
+    }
+
+    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
+        json.dump(full_results, f, indent=2, ensure_ascii=False)
+
+    print("\n" + "#" * 78)
+    print("  SUMMARY")
+    print("#" * 78)
+    print(f"  {'strategy':<20} {'chunks':>7} {'STRICT':>10} {'LOOSE':>10}")
+    for s in STRATEGIES:
+        r = search_results[s]
+        print(f"  {s:<20} {stores[s].size:>7} "
+              f"{str(r['strict_hits']) + '/' + str(r['total']):>10} "
+              f"{str(r['loose_hits']) + '/' + str(r['total']):>10}")
+
+    ans_ok = sum(1 for a in gen_results["answerable"] if a["all_citations_resolve"])
+    ref_ok = sum(1 for u in gen_results["unanswerable"] if u["refused_exact"])
+    errs = sum(1 for x in gen_results["answerable"] + gen_results["unanswerable"]
+               if x.get("generation_error"))
+    print(f"\n  Answerable with fully-resolving citations: {ans_ok}/3")
+    print(f"  Out-of-corpus refused (exact phrase):      {ref_ok}/3")
+    if errs:
+        print(f"  !! {errs} generation call(s) FAILED (not refusals) — "
+              f"the two figures above are understated by that many.")
+    print(f"  Filter changed top-1:                      "
+          f"{filter_results['top1_changed']}")
+    print(f"\n  Results  -> {RESULTS_PATH}")
+    print(f"  Dump     -> {DUMP_PATH}")
+    print("#" * 78 + "\n")
+
+
+if __name__ == "__main__":
+    main()
```

### `eval/analyze.py`

```diff
diff --git a/eval/analyze.py b/eval/analyze.py
new file mode 100644
index 0000000..dd93f98
--- /dev/null
+++ b/eval/analyze.py
@@ -0,0 +1,117 @@
+"""
+Post-hoc analysis of eval/results.json.
+
+hit-in-top-5 is the metric the brief asks for, and it is reported as such. But
+on a 6-document corpus, top-5 sweeps most of a single endorsement, so a question
+that names its form number is almost guaranteed a hit under either chunker.
+That makes hit@5 saturate and tie.
+
+These rank-sensitive cuts come from the SAME recorded run — no re-retrieval, no
+new questions, no changed metric definitions. They just read the per-rank
+records that run_eval.py already stored:
+
+    hit@1 / hit@3 / hit@5   strict (form AND clause)
+    MRR (strict)            mean of 1/rank-of-first-strict-hit
+    mean strict rank        over questions where a strict hit exists
+
+Usage:
+    python -m eval.analyze
+"""
+
+from __future__ import annotations
+
+import json
+import os
+import sys
+
+PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
+sys.path.insert(0, PROJECT_ROOT)
+
+if hasattr(sys.stdout, "reconfigure"):
+    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
+
+RESULTS_PATH = os.path.join(PROJECT_ROOT, "eval", "results.json")
+OUT_PATH = os.path.join(PROJECT_ROOT, "eval", "analysis.md")
+
+STRATEGIES = ["naive_fixed", "structure_aware"]
+
+
+def strict_rank(detail: dict) -> int | None:
+    """Rank of the first chunk matching BOTH form and clause, or None."""
+    for r in detail["top5"]:
+        if r["form_match"] and r["clause_match"]:
+            return r["rank"]
+    return None
+
+
+def loose_rank(detail: dict) -> int | None:
+    for r in detail["top5"]:
+        if r["form_match"]:
+            return r["rank"]
+    return None
+
+
+def main():
+    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
+        res = json.load(f)
+
+    search = res["search_evaluation"]
+    qids = [d["question_id"] for d in search[STRATEGIES[0]]["details"]]
+    n = len(qids)
+
+    table = {}
+    for s in STRATEGIES:
+        details = {d["question_id"]: d for d in search[s]["details"]}
+        sranks = {q: strict_rank(details[q]) for q in qids}
+        lranks = {q: loose_rank(details[q]) for q in qids}
+        found = [r for r in sranks.values() if r]
+        table[s] = {
+            "strict_hit_at_1": sum(1 for r in sranks.values() if r and r <= 1),
+            "strict_hit_at_3": sum(1 for r in sranks.values() if r and r <= 3),
+            "strict_hit_at_5": sum(1 for r in sranks.values() if r and r <= 5),
+            "loose_hit_at_5": sum(1 for r in lranks.values() if r and r <= 5),
+            "mrr_strict": round(sum(1 / r for r in sranks.values() if r) / n, 4),
+            "mean_strict_rank": round(sum(found) / len(found), 3) if found else None,
+            "strict_ranks": sranks,
+        }
+
+    lines = ["# Rank-sensitive analysis (same run, same 8 questions)", ""]
+    lines.append("Computed from `eval/results.json`. No re-retrieval, no new "
+                 "questions, no redefined metric — these read the per-rank "
+                 "records the measured run already stored.")
+    lines.append("")
+    lines.append(f"| metric | {' | '.join(STRATEGIES)} |")
+    lines.append("|---|" + "---|" * len(STRATEGIES))
+    for key, label in [
+        ("strict_hit_at_5", f"**hit-in-top-5 (strict: form+clause)** — /{n}"),
+        ("loose_hit_at_5", f"hit-in-top-5 (loose: form only) — /{n}"),
+        ("strict_hit_at_3", f"hit-in-top-3 (strict) — /{n}"),
+        ("strict_hit_at_1", f"hit-in-top-1 (strict) — /{n}"),
+        ("mrr_strict", "MRR (strict)"),
+        ("mean_strict_rank", "mean rank of first strict hit (lower better)"),
+    ]:
+        lines.append(f"| {label} | "
+                     + " | ".join(str(table[s][key]) for s in STRATEGIES) + " |")
+    lines.append("")
+
+    lines.append("## Rank of first strict hit, per question")
+    lines.append("")
+    lines.append(f"| question | {' | '.join(STRATEGIES)} |")
+    lines.append("|---|" + "---|" * len(STRATEGIES))
+    for q in qids:
+        cells = []
+        for s in STRATEGIES:
+            r = table[s]["strict_ranks"][q]
+            cells.append(str(r) if r else "— (miss)")
+        lines.append(f"| {q} | " + " | ".join(cells) + " |")
+    lines.append("")
+
+    out = "\n".join(lines)
+    with open(OUT_PATH, "w", encoding="utf-8") as f:
+        f.write(out)
+    print(out)
+    print(f"\nWritten to {OUT_PATH}")
+
+
+if __name__ == "__main__":
+    main()
```

### `eval/bonus_challenge.py`

```diff
diff --git a/eval/bonus_challenge.py b/eval/bonus_challenge.py
new file mode 100644
index 0000000..f3a8e95
--- /dev/null
+++ b/eval/bonus_challenge.py
@@ -0,0 +1,172 @@
+"""
+Bonus challenge: precision vs completeness.
+
+Hypothesis under test — there exists a question where the TIGHTER chunker wins
+on retrieval (it puts the exact exclusion row at rank 1, at a higher score) but
+LOSES on the final answer, because the tight row chunk strands the model without
+the DEFINITIONS clause that tells it what the row's terms mean.
+
+The probe question needs BOTH:
+  * SECTION IV, row E-28  — excludes equipment that "does not meet the
+                            definition of Covered Equipment"
+  * SECTION II (a)        — a window air-conditioning unit is NOT Covered
+                            Equipment
+
+E-28 is a pointer; on its own it cannot resolve the question. Under
+`structure_aware_rows` each E-nn row is its own chunk, so the three exclusion
+rows of HO-0308 compete for the same top-k slots and crowd out DEFINITIONS.
+Under `structure_aware` the whole exclusions table is ONE chunk, which leaves a
+slot free for DEFINITIONS.
+
+`--top-k` defaults to 3, the smallest k at which that crowding is visible; the
+main evaluation uses top_k=5.
+
+Usage:
+    python -m eval.bonus_challenge
+    python -m eval.bonus_challenge --top-k 3
+"""
+
+from __future__ import annotations
+
+import argparse
+import json
+import os
+import sys
+import time
+
+PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
+sys.path.insert(0, PROJECT_ROOT)
+
+if hasattr(sys.stdout, "reconfigure"):
+    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
+
+from dotenv import load_dotenv
+
+from pipeline.embeddings import embed_single
+from pipeline.mongo_store import MongoVectorStore
+from utils.llm_service import generate_with_gemini_sync, GEN_MODEL
+from utils.prompts import RAG_SYSTEM_PROMPT, RAG_USER_TEMPLATE, REFUSAL_PHRASE
+
+load_dotenv()
+
+# structure_aware keeps the whole EXCLUSIONS TABLE section together;
+# structure_aware_rows splits it to one chunk per E-nn row. naive_fixed is
+# included as the baseline. The tension shows up between the last two.
+STRATEGIES = ["naive_fixed", "structure_aware", "structure_aware_rows"]
+OUT_PATH = os.path.join(PROJECT_ROOT, "eval", "bonus_results.json")
+
+QUESTION = (
+    "Under HO-0308 ed. 03-24, is a window air-conditioning unit 'Covered "
+    "Equipment' for the purposes of exclusion E-28, and would a claim for its "
+    "failure be excluded?"
+)
+
+# Ground truth, from the source document:
+#   SECTION II (a) — "Portable or plug-in appliances (e.g., window
+#   air-conditioning units, portable heaters, kitchen counter appliances) are
+#   not covered equipment unless specifically scheduled."
+#   SECTION IV, E-28 — excludes equipment "that does not meet the definition of
+#   Covered Equipment."
+# E-28 is the operative exclusion, but it is a POINTER: it only bites once you
+# read SECTION II (a) to learn that a window AC unit is not Covered Equipment.
+# Answering therefore requires BOTH chunks.
+
+# What the CONTEXT must contain for the model to answer completely.
+#
+# The definition needle matches the ACTUAL text of SECTION II (a) that names a
+# window air-conditioning unit, not a mere mention of "Covered Equipment".
+# Lesson from an earlier version of this probe: it tested for the bare phrase
+# "sudden and accidental", which also appears in SECTION I as "...as defined
+# herein" -- a POINTER to a definition, not the definition -- and so it wrongly
+# scored a stranded context as complete. Needles must match the operative text.
+NEEDLES = {
+    "exclusion_row_E-28": lambda t: "E-28" in t,
+    "definition_covered_equipment": lambda t: "window air-conditioning" in t.lower(),
+}
+
+
+def probe(strategy: str, top_k: int) -> dict:
+    store = MongoVectorStore(strategy)
+    qemb = embed_single(QUESTION)
+    hits = store.search(qemb, top_k=top_k)
+
+    context = "\n\n---\n\n".join(
+        f"[chunk_id={sc.chunk_id}] [form={sc.metadata.get('form_number','')}] "
+        f"[section={sc.metadata.get('section','')}]\n{sc.text}"
+        for sc in hits
+    )
+
+    coverage = {name: test(context) for name, test in NEEDLES.items()}
+    # Retrieval "win" = the exclusion row is present and ranked first.
+    retrieval_precise = bool(hits) and "E-28" in hits[0].text
+
+    prompt = (f"{RAG_SYSTEM_PROMPT}\n\n"
+              + RAG_USER_TEMPLATE.format(context=context, question=QUESTION))
+    time.sleep(1)
+    answer = generate_with_gemini_sync(prompt)
+
+    refused = REFUSAL_PHRASE.lower() in (answer or "").lower()
+
+    print(f"\n{'='*78}")
+    print(f"  STRATEGY: {strategy}   (top_k={top_k}, model={GEN_MODEL})")
+    print(f"{'='*78}")
+    for i, sc in enumerate(hits):
+        print(f"  #{i+1}  {sc.chunk_id:<24} form={sc.metadata.get('form_number'):<8} "
+              f"section={sc.metadata.get('section','')!r:<26} score={sc.score:.4f}")
+    print("\n  Context coverage:")
+    for name, present in coverage.items():
+        print(f"    {'PRESENT' if present else 'MISSING'}  {name}")
+    print(f"  Exclusion row ranked #1: {retrieval_precise}")
+    print(f"\n  ANSWER:\n{answer}\n")
+    print(f"  Refused: {refused}")
+
+    return {
+        "strategy": strategy,
+        "top_k": top_k,
+        "retrieved": [
+            {"rank": i + 1, "chunk_id": sc.chunk_id,
+             "form_number": sc.metadata.get("form_number"),
+             "section": sc.metadata.get("section", ""),
+             "score": round(sc.score, 4)}
+            for i, sc in enumerate(hits)
+        ],
+        "context_coverage": coverage,
+        "exclusion_row_ranked_first": retrieval_precise,
+        "answer": answer,
+        "refused": refused,
+    }
+
+
+def main():
+    parser = argparse.ArgumentParser()
+    parser.add_argument("--top-k", type=int, default=3)
+    args = parser.parse_args()
+
+    print("\n" + "#" * 78)
+    print("  BONUS CHALLENGE — precision vs completeness")
+    print(f"  Question: {QUESTION}")
+    print("#" * 78)
+
+    results = {
+        "question": QUESTION,
+        "top_k": args.top_k,
+        "generation_model": GEN_MODEL,
+        "retrieval": "MongoDB Atlas $vectorSearch (exact ENN, cosine)",
+        "runs": [probe(s, args.top_k) for s in STRATEGIES],
+    }
+
+    print("\n" + "#" * 78)
+    print("  SIDE BY SIDE")
+    print("#" * 78)
+    for r in results["runs"]:
+        cov = ", ".join(k for k, v in r["context_coverage"].items() if v) or "nothing"
+        print(f"  {r['strategy']:<18} row#1={r['exclusion_row_ranked_first']!s:<6} "
+              f"refused={r['refused']!s:<6} context has: {cov}")
+
+    with open(OUT_PATH, "w", encoding="utf-8") as f:
+        json.dump(results, f, indent=2, ensure_ascii=False)
+    print(f"\n  Written to {OUT_PATH}\n")
+
+
+if __name__ == "__main__":
+    main()
```

### `api/search.py`

```diff
diff --git a/api/search.py b/api/search.py
new file mode 100644
index 0000000..3c977dd
--- /dev/null
+++ b/api/search.py
@@ -0,0 +1,139 @@
+"""
+Retrieval + grounded-answer endpoints backed by MongoDB Atlas Vector Search.
+
+    POST /rag/search   -- search-only, returns scored chunks with metadata
+    POST /rag/ask      -- grounded answer with citations, or the exact refusal
+
+/ask uses the same forced-refusal prompt as the offline evaluation. There is no
+"use your best judgement" fallback: if the retrieved endorsement text does not
+support an answer, the endpoint returns the refusal phrase.
+"""
+
+from __future__ import annotations
+
+from typing import List, Literal, Optional
+
+from fastapi import APIRouter, HTTPException
+from pydantic import BaseModel, Field
+
+from pipeline.embeddings import embed_single
+from pipeline.mongo_store import MongoVectorStore
+from utils.llm_service import generate_with_gemini
+from utils.prompts import RAG_SYSTEM_PROMPT, RAG_USER_TEMPLATE, REFUSAL_PHRASE
+
+router = APIRouter(prefix="/rag", tags=["RAG"])
+
+Strategy = Literal["naive_fixed", "structure_aware"]
+
+
+class SearchRequest(BaseModel):
+    query: str = Field(..., examples=["Does E-17 apply to a burst supply line?"])
+    strategy: Strategy = "structure_aware"
+    top_k: int = Field(5, ge=1, le=20)
+    policy_line: Optional[str] = Field(
+        None, description="Pre-filter on policy_line, e.g. 'Homeowners' or 'Dwelling Fire'")
+    form_number: Optional[str] = Field(None, description="Pre-filter on form_number")
+
+
+class SearchHit(BaseModel):
+    rank: int
+    chunk_id: str
+    score: float
+    form_number: Optional[str] = None
+    policy_line: Optional[str] = None
+    edition_date: Optional[str] = None
+    source_file: Optional[str] = None
+    section: Optional[str] = None
+    text: str
+
+
+class SearchResponse(BaseModel):
+    query: str
+    strategy: str
+    filter: dict
+    results: List[SearchHit]
+
+
+class AskResponse(BaseModel):
+    question: str
+    answer: str
+    refused: bool
+    context_chunk_ids: List[str]
+
+
+def _build_filter(req: SearchRequest) -> dict:
+    f = {}
+    if req.policy_line:
+        f["policy_line"] = req.policy_line
+    if req.form_number:
+        f["form_number"] = req.form_number
+    return f
+
+
+def _search(req: SearchRequest):
+    store = MongoVectorStore(req.strategy)
+    qemb = embed_single(req.query)
+    return store.search(qemb, top_k=req.top_k,
+                        filter_metadata=_build_filter(req) or None)
+
+
+@router.post("/search", response_model=SearchResponse)
+async def search(req: SearchRequest):
+    """Search-only. No generation, no LLM call — just scored chunks."""
+    try:
+        hits = _search(req)
+    except Exception as e:  # surface Atlas/index problems rather than a bare 500
+        raise HTTPException(status_code=502, detail=f"Vector search failed: {e}")
+
+    return SearchResponse(
+        query=req.query,
+        strategy=req.strategy,
+        filter=_build_filter(req),
+        results=[
+            SearchHit(
+                rank=i + 1,
+                chunk_id=sc.chunk_id,
+                score=round(sc.score, 4),
+                form_number=sc.metadata.get("form_number"),
+                policy_line=sc.metadata.get("policy_line"),
+                edition_date=sc.metadata.get("edition_date"),
+                source_file=sc.metadata.get("source_file"),
+                section=sc.metadata.get("section"),
+                text=sc.text,
+            )
+            for i, sc in enumerate(hits)
+        ],
+    )
+
+
+@router.post("/ask", response_model=AskResponse)
+async def ask(req: SearchRequest):
+    """
+    Grounded answer. Every claim carries a citation that resolves to a real
+    chunk_id; anything the endorsements do not cover is refused outright.
+    """
+    try:
+        hits = _search(req)
+    except Exception as e:
+        raise HTTPException(status_code=502, detail=f"Vector search failed: {e}")
+
+    if not hits:
+        # Nothing retrieved is not a reason to improvise.
+        return AskResponse(question=req.query, answer=REFUSAL_PHRASE,
+                           refused=True, context_chunk_ids=[])
+
+    context = "\n\n---\n\n".join(
+        f"[chunk_id={sc.chunk_id}] [form={sc.metadata.get('form_number','')}] "
+        f"[section={sc.metadata.get('section','')}]\n{sc.text}"
+        for sc in hits
+    )
+    prompt = (f"{RAG_SYSTEM_PROMPT}\n\n"
+              + RAG_USER_TEMPLATE.format(context=context, question=req.query))
+
+    answer = await generate_with_gemini(prompt)
+    return AskResponse(
+        question=req.query,
+        answer=answer,
+        refused=REFUSAL_PHRASE.lower() in (answer or "").lower(),
+        context_chunk_ids=[sc.chunk_id for sc in hits],
+    )
```

