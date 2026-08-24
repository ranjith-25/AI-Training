"""
Chunking strategies for homeowners endorsement documents.

Strategy 1 — naive_fixed:
    Fixed-size character chunks with overlap. Ignores document structure.
    This simulates a "current" generic chunker that treats all text as flat.

Strategy 2 — structure_aware:
    Splits on SECTION headers. Never separates an exclusion row from its
    table header or form number. Prepends the document header to every chunk
    so each chunk is self-contained. SECTION-level granularity: the whole
    EXCLUSIONS TABLE stays in one chunk.

Strategy 3 — structure_aware_rows:
    As above, but the EXCLUSIONS TABLE is split to ONE CHUNK PER E-nn ROW
    (each still carrying the form header and the table's column header).
    Maximum retrieval precision, minimum context: this is the strategy that
    demonstrates the precision/completeness trade-off, because a row chunk
    knows what E-27 excludes but not what the endorsement means by
    "sudden and accidental".

Strategies 1 and 2 are the two measured in the headline hit-in-top-5 table.
Strategy 3 exists for the precision-vs-completeness probe.
"""

from __future__ import annotations

import re
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    chunk_id: str
    text: str
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Metadata extraction from endorsement header
# ---------------------------------------------------------------------------

def extract_header_metadata(text: str, source_file: str) -> Dict[str, str]:
    """
    Parse the structured header block at the top of each endorsement
    and return metadata dict with source_file, form_number, policy_line,
    edition_date.  Raises ValueError if any required field is missing.
    """
    meta: Dict[str, str] = {"source_file": source_file}

    patterns = {
        "form_number":  r"Form\s+Number:\s*(\S+)",
        "edition_date": r"Edition:\s*(\S+)",
        "policy_line":  r"Policy\s+Line:\s*(.+?)(?:\r?\n)",
        "effective_date": r"Effective\s+Date:\s*(.+?)(?:\r?\n)",
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, text)
        if m:
            meta[key] = m.group(1).strip()
        else:
            raise ValueError(
                f"Required metadata field '{key}' not found in {source_file}"
            )

    return meta


def extract_header_block(text: str) -> str:
    """Return everything up to and including the first blank line after
    the header separator block (the ===... lines and field lines)."""
    # Find the ENDORSEMENT TITLE line + next line as end of header
    m = re.search(r"(ENDORSEMENT\s+TITLE:.*?)(?:\r?\n){2,}", text, re.DOTALL)
    if m:
        return text[: m.end()].strip()
    # Fallback: first 600 chars
    return text[:600].strip()


# ---------------------------------------------------------------------------
# Strategy 1 — Naive fixed-size chunker
# ---------------------------------------------------------------------------

def naive_fixed_chunks(
    text: str,
    source_file: str,
    chunk_size: int = 1500,
    overlap: int = 200,
) -> List[Chunk]:
    """
    Split text into fixed-size character chunks with overlap.
    Metadata is extracted from the header and attached to EVERY chunk,
    but the chunk TEXT may not include the header — that's the weakness
    this strategy is designed to expose.
    """
    meta = extract_header_metadata(text, source_file)
    form = meta["form_number"]

    chunks: List[Chunk] = []
    start = 0
    idx = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk_text = text[start:end]
        chunk_id = f"{form}_naive_{idx:03d}"
        chunks.append(Chunk(
            chunk_id=chunk_id,
            text=chunk_text,
            metadata={**meta, "strategy": "naive_fixed", "chunk_index": str(idx)},
        ))
        idx += 1
        if end >= len(text):
            break
        start += chunk_size - overlap
    return chunks


# ---------------------------------------------------------------------------
# Strategy 2 — Structure-aware chunker
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(
    r"^(SECTION\s+[IVXLCDM]+\s*[—–-]\s*.+)$",
    re.MULTILINE,
)


def _split_into_sections(text: str) -> List[Dict[str, str]]:
    """Split document text on SECTION headers. Returns list of
    {title, text} dicts."""
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return [{"title": "FULL_DOCUMENT", "text": text}]

    sections: List[Dict[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append({"title": title, "text": text[start:end].strip()})
    return sections


def structure_aware_chunks(
    text: str,
    source_file: str,
) -> List[Chunk]:
    """
    Split on SECTION boundaries.  Prepend the document header (form number,
    edition, effective date) to every chunk so retrieval always has context.
    Exclusion rows are NEVER split from their table header.
    """
    meta = extract_header_metadata(text, source_file)
    form = meta["form_number"]
    header_block = extract_header_block(text)

    sections = _split_into_sections(text)
    chunks: List[Chunk] = []

    for i, sec in enumerate(sections):
        # Clean the section title for metadata
        title_short = sec["title"].split("—")[-1].split("–")[-1].strip() \
            if "—" in sec["title"] or "–" in sec["title"] else sec["title"]

        chunk_id = f"{form}_struct_{i:03d}"

        # Prepend the header block to give every chunk full endorsement context
        chunk_text = f"{header_block}\n\n{sec['text']}"

        chunks.append(Chunk(
            chunk_id=chunk_id,
            text=chunk_text,
            metadata={
                **meta,
                "strategy": "structure_aware",
                "section": title_short,
                "chunk_index": str(i),
            },
        ))
    return chunks


# ---------------------------------------------------------------------------
# Convenience: chunk a whole directory
# ---------------------------------------------------------------------------

_ROW_START_RE = re.compile(r"^\|\s*(E-\d+)\s*\|")
_BORDER_RE = re.compile(r"^\+[-+]+\+\s*$")


def _split_exclusion_table(section_text: str) -> Optional[Dict[str, object]]:
    """
    Break an EXCLUSIONS TABLE section into its preamble, its column-header
    block, and one block per E-nn row.

    Returns None if this section is not a parseable ASCII table, so callers can
    fall back to keeping the section whole.
    """
    lines = section_text.splitlines()
    first_border = next((i for i, ln in enumerate(lines) if _BORDER_RE.match(ln)), None)
    if first_border is None:
        return None

    preamble = "\n".join(lines[:first_border]).strip()

    # Group the table body into blocks delimited by +---+ border lines.
    blocks: List[List[str]] = []
    current: List[str] = []
    for ln in lines[first_border:]:
        if _BORDER_RE.match(ln):
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(ln)
    if current:
        blocks.append(current)

    if not blocks:
        return None

    # The first block is the column header (| Code | Exclusion Title | ... |).
    column_header = "\n".join(blocks[0]).rstrip()

    rows: List[Dict[str, str]] = []
    for blk in blocks[1:]:
        if not blk:
            continue
        m = _ROW_START_RE.match(blk[0])
        if not m:
            continue
        rows.append({"code": m.group(1), "text": "\n".join(blk).rstrip()})

    if not rows:
        return None

    return {"preamble": preamble, "column_header": column_header, "rows": rows}


def structure_aware_row_chunks(text: str, source_file: str) -> List[Chunk]:
    """
    Strategy 3 — structure_aware_rows.

    Same section splitting as `structure_aware`, except the EXCLUSIONS TABLE is
    broken into ONE CHUNK PER EXCLUSION ROW. Each row chunk still carries the
    document header (form number, edition, effective date) and the table's
    column header, so an exclusion row is never separated from the form that
    scopes it — but it IS separated from the DEFINITIONS section.

    This is the maximally precise chunker. It exists to measure the cost of that
    precision: a row chunk answers "what does E-27 say" perfectly and answers
    "is this a breakdown as defined" not at all.
    """
    meta = extract_header_metadata(text, source_file)
    form = meta["form_number"]
    header_block = extract_header_block(text)

    sections = _split_into_sections(text)
    chunks: List[Chunk] = []
    idx = 0

    for sec in sections:
        title_short = sec["title"].split("—")[-1].split("–")[-1].strip() \
            if "—" in sec["title"] or "–" in sec["title"] else sec["title"]

        table = _split_exclusion_table(sec["text"]) if "EXCLUSION" in title_short.upper() else None

        if table is None:
            chunks.append(Chunk(
                chunk_id=f"{form}_rows_{idx:03d}",
                text=f"{header_block}\n\n{sec['text']}",
                metadata={**meta, "strategy": "structure_aware_rows",
                          "section": title_short, "chunk_index": str(idx)},
            ))
            idx += 1
            continue

        for row in table["rows"]:
            body = (f"{table['preamble']}\n\n"
                    f"{table['column_header']}\n{row['text']}")
            chunks.append(Chunk(
                chunk_id=f"{form}_rows_{idx:03d}",
                text=f"{header_block}\n\n{body}",
                metadata={**meta, "strategy": "structure_aware_rows",
                          "section": title_short, "clause": row["code"],
                          "chunk_index": str(idx)},
            ))
            idx += 1

    return chunks


STRATEGY_MAP = {
    "naive_fixed": naive_fixed_chunks,
    "structure_aware": structure_aware_chunks,
    "structure_aware_rows": structure_aware_row_chunks,
}


def chunk_endorsement_file(filepath: str, strategy: str) -> List[Chunk]:
    """Read a single endorsement file, chunk it with the given strategy."""
    source_file = os.path.basename(filepath)
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    # STRATEGY_MAP is the single source of truth — a strategy added there is
    # immediately usable here, rather than needing a matching elif branch.
    try:
        chunker = STRATEGY_MAP[strategy]
    except KeyError:
        raise ValueError(
            f"Unknown strategy: {strategy}. "
            f"Known strategies: {', '.join(sorted(STRATEGY_MAP))}"
        ) from None
    return chunker(text, source_file)


def chunk_all_endorsements(
    endorsements_dir: str,
    strategy: str,
) -> List[Chunk]:
    """Chunk every .txt file in the endorsements directory."""
    all_chunks: List[Chunk] = []
    for fname in sorted(os.listdir(endorsements_dir)):
        if not fname.endswith(".txt"):
            continue
        fpath = os.path.join(endorsements_dir, fname)
        chunks = chunk_endorsement_file(fpath, strategy)
        all_chunks.extend(chunks)
    return all_chunks
