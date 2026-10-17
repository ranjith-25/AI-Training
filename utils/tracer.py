"""
Structured JSONL trace writer for the claims-assistant RAG system.

Every /rag/ask call produces one trace record containing all fields needed
to replay the request:

    trace_id, timestamp, question, strategy, filter, retrieved_chunks,
    prompt_version (SHA-256 of system prompt), model, model_params,
    raw_output, refused, latency_ms

PII redaction
-------------
_redact() strips claimant identifiers BEFORE the trace dict is serialised
to JSONL.  Patterns removed:
  - CLM-\\d+        (claim numbers)
  - POL-\\w+-\\d+    (policy numbers)
  - SSN-like        (\\d{3}-\\d{2}-\\d{4})
  - US phone        (\\d{3}[-.\\s]\\d{3}[-.\\s]\\d{4})
  - Names after "claimant:" or "insured:" (best-effort)

Traces are written to data/traces.jsonl (append-only).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACES_PATH = os.path.join(PROJECT_ROOT, "data", "traces.jsonl")

# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------

_PII_PATTERNS = [
    # Claim numbers: CLM-2024-88431
    (re.compile(r"CLM-\d[\w-]*", re.IGNORECASE), "[REDACTED-CLAIM]"),
    # Policy numbers: POL-HO-2024-55102
    (re.compile(r"POL-[\w-]+\d+", re.IGNORECASE), "[REDACTED-POLICY]"),
    # SSN: 123-45-6789
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    # US phone: 555-123-4567 or 555.123.4567
    (re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED-PHONE]"),
    # Names after "claimant:" or "insured:" (best-effort, first+last)
    (re.compile(r"(?:claimant|insured)\s*:\s*[A-Z][a-z]+\s+[A-Z][a-z]+",
                re.IGNORECASE), "[REDACTED-NAME]"),
]


def _redact(text: str) -> str:
    """Strip PII patterns from text BEFORE writing to the trace file."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Prompt version hash
# ---------------------------------------------------------------------------

def prompt_version_hash(prompt_text: str) -> str:
    """SHA-256 of the system prompt, truncated to 12 hex chars."""
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Trace record
# ---------------------------------------------------------------------------

def _extract_decision(raw_output: str, refused: bool) -> str:
    if refused:
        return "REFUSED"
    lower = raw_output.lower()
    if re.search(r"\b(denied|exclusion|excluded|not covered)\b", lower):
        return "DENIED"
    if re.search(r"\b(partial|partially covered|disallowed)\b", lower):
        return "PARTIAL"
    if re.search(r"\b(is covered|fully covered|covered according)\b", lower):
        return "COVERED"
    return "INFORMATIONAL"

def _has_exclusion_chunk(retrieved_chunks: List[Dict[str, Any]]) -> bool:
    for c in retrieved_chunks:
        sec = str(c.get("section", "")).upper()
        if "EXCLUSION" in sec:
            return True
    return False

def _extract_cited_exclusion(raw_output: str) -> Optional[str]:
    m = re.search(r"\b(E-\d{2}|HO-\d{4}\s+E-\d{2}|Exclusion\s+\d+)\b", raw_output, re.IGNORECASE)
    return m.group(1).upper() if m else None

def build_trace(
    question: str,
    strategy: str,
    filter_metadata: Dict[str, str],
    retrieved_chunks: List[Dict[str, Any]],
    prompt_text: str,
    model: str,
    model_params: Optional[Dict[str, Any]],
    raw_output: str,
    refused: bool,
    latency_ms: float,
) -> Dict[str, Any]:
    """Build a trace dict with a fresh trace_id, redacted fields, and structured decision indexing."""
    cleaned_output = _redact(raw_output or "")
    return {
        "trace_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": _redact(question),
        "strategy": strategy,
        "filter": filter_metadata,
        "retrieved_chunks": [
            {
                "chunk_id": c.get("chunk_id", ""),
                "score": round(c.get("score", 0.0), 4),
                "form_number": c.get("form_number", ""),
                "section": c.get("section", ""),
            }
            for c in retrieved_chunks
        ],
        "prompt_version": prompt_version_hash(prompt_text),
        "model": model,
        "model_params": model_params or {"temperature": "default", "top_p": "default"},
        "raw_output": cleaned_output,
        "coverage_decision": _extract_decision(cleaned_output, refused),
        "has_exclusion_in_context": _has_exclusion_chunk(retrieved_chunks),
        "cited_exclusion": _extract_cited_exclusion(cleaned_output),
        "refused": refused,
        "latency_ms": round(latency_ms, 1),
    }


def log_trace(trace: Dict[str, Any], path: str = TRACES_PATH) -> str:
    """Append one trace record to the JSONL file. Returns the trace_id."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(trace, ensure_ascii=False) + "\n")
    return trace["trace_id"]


def load_traces(path: str = TRACES_PATH) -> List[Dict[str, Any]]:
    """Load all traces from the JSONL file."""
    if not os.path.exists(path):
        return []
    traces = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                traces.append(json.loads(line))
    return traces
