"""
Retrieval metric definitions.

DEFINED BEFORE THE RUN, applied identically to both chunking strategies.
Nothing in here is allowed to be loosened after seeing results — that would turn
the hit-rate into a measurement of the metric author rather than of the chunker.

Two metrics are recorded for every question:

  LOOSE  (form-level)   — at least one of the top-5 chunks carries the expected
                          form_number. This is the metric the previous FAISS
                          run used. It cannot distinguish E-17 from E-18 inside
                          the same endorsement, so it flatters any chunker that
                          merely lands in the right document.

  STRICT (form+clause)  — at least one of the top-5 chunks carries the expected
                          form_number AND contains the specific clause the
                          answer lives in. This is the headline number, because
                          the whole point of the exercise is whether an
                          exclusion row stays attached to the form that scopes
                          it.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

_EXCL_CODE_RE = re.compile(r"\bE-\d+\b")
_SECTION_RE = re.compile(r"SECTION\s+[IVXLCDM]+")

# Header-field questions map to the literal label that must appear in the chunk.
_HEADER_LABELS = {
    "effective date": "Effective Date:",
    "policy line": "Policy Line:",
    "form number": "Form Number:",
    "edition": "Edition:",
}


def clause_locator(expected_clause: str) -> Tuple[str, str]:
    """
    Turn a gold `expected_clause` string into a deterministic (kind, needle)
    test that can be applied to chunk text.

      "SECTION IV — EXCLUSIONS TABLE, E-17" -> ("exclusion_code", "E-17")
      "SECTION I — SCOPE AND PURPOSE"       -> ("section", "SECTION I")
      "Header — Effective Date"             -> ("header_field", "Effective Date:")
    """
    code = _EXCL_CODE_RE.search(expected_clause)
    if code:
        return ("exclusion_code", code.group(0))

    if expected_clause.strip().upper().startswith("HEADER"):
        tail = re.split(r"[—–-]", expected_clause, maxsplit=1)[-1].strip().lower()
        for key, label in _HEADER_LABELS.items():
            if key in tail:
                return ("header_field", label)
        raise ValueError(f"Unrecognised header clause: {expected_clause!r}")

    sec = _SECTION_RE.search(expected_clause)
    if sec:
        return ("section", sec.group(0))

    raise ValueError(f"Cannot build a clause locator for: {expected_clause!r}")


def chunk_satisfies_clause(chunk_text: str, expected_clause: str) -> bool:
    """Does this chunk's text actually contain the clause the answer lives in?"""
    kind, needle = clause_locator(expected_clause)
    if kind == "exclusion_code":
        # Word-boundary match so E-17 does not match E-170.
        return re.search(rf"\b{re.escape(needle)}\b", chunk_text) is not None
    return needle in chunk_text


def expected_forms(gold_q: Dict) -> List[str]:
    """Gold answers may name more than one form (Q5 spans HO-0308 and HO-0309)."""
    return [f.strip() for f in gold_q["expected_form"].split(",") if f.strip()]


def score_question(gold_q: Dict, top_chunks: List) -> Dict:
    """
    Evaluate one question's retrieved list. `top_chunks` is a list of
    ScoredChunk. Returns the full per-question record — every rank is kept so
    the record can be printed rather than summarised.
    """
    forms = expected_forms(gold_q)
    clause = gold_q["expected_clause"]

    per_rank = []
    loose_hit = False
    strict_hit = False
    strict_rank = None
    forms_covered = set()

    for i, sc in enumerate(top_chunks):
        form_ok = sc.metadata.get("form_number") in forms
        clause_ok = chunk_satisfies_clause(sc.text, clause)
        if form_ok:
            loose_hit = True
            forms_covered.add(sc.metadata.get("form_number"))
        if form_ok and clause_ok:
            if not strict_hit:
                strict_rank = i + 1
            strict_hit = True

        per_rank.append({
            "rank": i + 1,
            "chunk_id": sc.chunk_id,
            "form_number": sc.metadata.get("form_number"),
            "policy_line": sc.metadata.get("policy_line"),
            "section": sc.metadata.get("section", ""),
            "score": round(sc.score, 4),
            "form_match": form_ok,
            "clause_match": clause_ok,
        })

    return {
        "question_id": gold_q["id"],
        "question": gold_q["question"],
        "expected_form": gold_q["expected_form"],
        "expected_clause": clause,
        "clause_locator": list(clause_locator(clause)),
        "loose_hit": loose_hit,
        "strict_hit": strict_hit,
        "strict_first_rank": strict_rank,
        "forms_covered": sorted(forms_covered),
        "all_expected_forms_covered": set(forms) == forms_covered,
        "top5": per_rank,
    }
