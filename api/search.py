"""
Retrieval + grounded-answer endpoints backed by MongoDB Atlas Vector Search.

    POST /rag/search   -- search-only, returns scored chunks with metadata
    POST /rag/ask      -- grounded answer with citations, or the exact refusal

/ask uses the same forced-refusal prompt as the offline evaluation. There is no
"use your best judgement" fallback: if the retrieved endorsement text does not
support an answer, the endpoint returns the refusal phrase.
"""

from __future__ import annotations

import time
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pipeline.embeddings import embed_single
from pipeline.mongo_store import MongoVectorStore
from utils.llm_service import generate_with_gemini, GEN_MODEL
from utils.tracer import build_trace, log_trace
from utils.prompts import RAG_SYSTEM_PROMPT, RAG_USER_TEMPLATE, REFUSAL_PHRASE

router = APIRouter(prefix="/rag", tags=["RAG"])

Strategy = Literal["naive_fixed", "structure_aware"]


class SearchRequest(BaseModel):
    query: str = Field(..., examples=["Does E-17 apply to a burst supply line?"])
    strategy: Strategy = "structure_aware"
    top_k: int = Field(5, ge=1, le=20)
    policy_line: Optional[str] = Field(
        None, description="Pre-filter on policy_line, e.g. 'Homeowners' or 'Dwelling Fire'")
    form_number: Optional[str] = Field(None, description="Pre-filter on form_number")


class SearchHit(BaseModel):
    rank: int
    chunk_id: str
    score: float
    form_number: Optional[str] = None
    policy_line: Optional[str] = None
    edition_date: Optional[str] = None
    source_file: Optional[str] = None
    section: Optional[str] = None
    text: str


class SearchResponse(BaseModel):
    query: str
    strategy: str
    filter: dict
    results: List[SearchHit]


class AskResponse(BaseModel):
    question: str
    answer: str
    refused: bool
    context_chunk_ids: List[str]


def _build_filter(req: SearchRequest) -> dict:
    f = {}
    if req.policy_line:
        f["policy_line"] = req.policy_line
    if req.form_number:
        f["form_number"] = req.form_number
    return f


def _search(req: SearchRequest):
    store = MongoVectorStore(req.strategy)
    qemb = embed_single(req.query)
    return store.search(qemb, top_k=req.top_k,
                        filter_metadata=_build_filter(req) or None)


@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    """Search-only. No generation, no LLM call — just scored chunks."""
    try:
        hits = _search(req)
    except Exception as e:  # surface Atlas/index problems rather than a bare 500
        raise HTTPException(status_code=502, detail=f"Vector search failed: {e}")

    return SearchResponse(
        query=req.query,
        strategy=req.strategy,
        filter=_build_filter(req),
        results=[
            SearchHit(
                rank=i + 1,
                chunk_id=sc.chunk_id,
                score=round(sc.score, 4),
                form_number=sc.metadata.get("form_number"),
                policy_line=sc.metadata.get("policy_line"),
                edition_date=sc.metadata.get("edition_date"),
                source_file=sc.metadata.get("source_file"),
                section=sc.metadata.get("section"),
                text=sc.text,
            )
            for i, sc in enumerate(hits)
        ],
    )


@router.post("/ask", response_model=AskResponse)
async def ask(req: SearchRequest):
    """
    Grounded answer. Every claim carries a citation that resolves to a real
    chunk_id; anything the endorsements do not cover is refused outright.
    Every call is traced to data/traces.jsonl with full provenance.
    """
    t0 = time.perf_counter()

    try:
        hits = _search(req)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Vector search failed: {e}")

    if not hits:
        # Nothing retrieved is not a reason to improvise.
        elapsed_ms = (time.perf_counter() - t0) * 1000
        trace = build_trace(
            question=req.query, strategy=req.strategy,
            filter_metadata=_build_filter(req),
            retrieved_chunks=[], prompt_text=RAG_SYSTEM_PROMPT,
            model=GEN_MODEL, model_params=None,
            raw_output=REFUSAL_PHRASE, refused=True, latency_ms=elapsed_ms,
        )
        log_trace(trace)
        return AskResponse(question=req.query, answer=REFUSAL_PHRASE,
                           refused=True, context_chunk_ids=[])

    context = "\n\n---\n\n".join(
        f"[chunk_id={sc.chunk_id}] [form={sc.metadata.get('form_number','')}] "
        f"[section={sc.metadata.get('section','')}]\n{sc.text}"
        for sc in hits
    )
    prompt = (f"{RAG_SYSTEM_PROMPT}\n\n"
              + RAG_USER_TEMPLATE.format(context=context, question=req.query))

    answer = await generate_with_gemini(prompt)
    refused = REFUSAL_PHRASE.lower() in (answer or "").lower()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # --- Trace logging (PII redacted before write) ---
    trace = build_trace(
        question=req.query,
        strategy=req.strategy,
        filter_metadata=_build_filter(req),
        retrieved_chunks=[
            {
                "chunk_id": sc.chunk_id,
                "score": sc.score,
                "form_number": sc.metadata.get("form_number", ""),
                "section": sc.metadata.get("section", ""),
            }
            for sc in hits
        ],
        prompt_text=RAG_SYSTEM_PROMPT,
        model=GEN_MODEL,
        model_params=None,
        raw_output=answer,
        refused=refused,
        latency_ms=elapsed_ms,
    )
    log_trace(trace)

    return AskResponse(
        question=req.query,
        answer=answer,
        refused=refused,
        context_chunk_ids=[sc.chunk_id for sc in hits],
    )
