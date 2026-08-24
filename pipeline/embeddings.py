"""
Embedding module using Google Gemini gemini-embedding-001 (3072-dim).
Uses the google-genai SDK already in the project.

This model is held CONSTANT across both chunking strategies — the chunker is
the only variable that changes between the two measured runs.
"""

from __future__ import annotations

import os
import time
from typing import List

from google import genai
from dotenv import load_dotenv

load_dotenv()

_client: genai.Client | None = None

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 3072
MAX_BATCH = 100  # API limit per request


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv("API_KEY")
        if not api_key:
            raise RuntimeError("API_KEY not set in environment")
        _client = genai.Client(api_key=api_key)
    return _client


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Embed a list of texts using Gemini text-embedding-004.
    Returns a list of 768-dim float vectors.
    Handles batching automatically.
    """
    client = _get_client()
    all_embeddings: List[List[float]] = []

    for i in range(0, len(texts), MAX_BATCH):
        batch = texts[i : i + MAX_BATCH]
        # Rate-limit safety: small pause between batches
        if i > 0:
            time.sleep(0.5)

        response = client.models.embed_content(
            model=EMBED_MODEL,
            contents=batch,
        )
        for emb in response.embeddings:
            all_embeddings.append(list(emb.values))

    return all_embeddings


def embed_single(text: str) -> List[float]: 
    """Embed a single text string."""
    return embed_texts([text])[0]
