"""Structure-aware chunker for the Docling PDF pipeline (spec stage 2).

Chunks are built from the typed extraction artifact items (not exported
Markdown) so item refs, page regions and table structure survive into the
indexed representation.

- Prose: grouped within a section, sentence-packed to a token budget,
  `embedding_text` prefixed with title + section breadcrumb; `display_text`
  stays verbatim.
- Tables: small tables stay intact; large ones split into row groups with
  repeated headers, plus a row-level key/value representation for exact
  parameter/value lookup (guarded by `representation_type`).
- Repeated page headers/footers are excluded from embedding text.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import tiktoken

from backend.core.config import settings
from backend.extractor.docling_pdf_extractor import (
    DoclingExtraction,
    ExtractedItem,
    SourceRegion,
)

logger = logging.getLogger(__name__)

# Kinds excluded from embedding chunks: repeated page furniture (spec:
# "Keep repeated headers/footers out of embedding text"). Page provenance
# stays available in the artifact.
_NON_INDEXED_KINDS = {"page_header", "page_footer"}

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")


@dataclass
class DoclingChunk:
    chunk_id: str
    document_id: str
    block_type: str  # "prose" | "table" | "table_row"
    representation_type: str  # "prose" | "table_full" | "table_rows" | "row_kv"
    section_path: List[str]
    citation_label: str
    display_text: str
    embedding_text: str
    lexical_text: str
    item_refs: List[str]
    regions: List[Dict] = field(default_factory=list)
    page_numbers: List[int] = field(default_factory=list)
    token_count: int = 0
    title: str = ""
    # Truncation/ordering info
    chunk_index: int = 0

    def to_dict(self) -> Dict:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "block_type": self.block_type,
            "representation_type": self.representation_type,
            "section_path": self.section_path,
            "citation_label": self.citation_label,
            "display_text": self.display_text,
            "embedding_text": self.embedding_text,
            "lexical_text": self.lexical_text,
            "item_refs": self.item_refs,
            "regions": self.regions,
            "page_numbers": self.page_numbers,
            "token_count": self.token_count,
            "title": self.title,
            "chunk_index": self.chunk_index,
        }


@dataclass
class ChunkPlan:
    chunks: List[DoclingChunk]
    omitted_chunks: int = 0  # chunks cut by max_chunks (reported, not indexed)
    skipped_sections: List[str] = field(default_factory=list)

    @property
    def total_chunks(self) -> int:
        return len(self.chunks) + self.omitted_chunks


class _Tokenizer:
    """cl100k token counting, consistent with the existing pipeline."""

    def __init__(self) -> None:
        try:
            self._enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._enc = None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is None:
            return len(text.split())
        return len(self._enc.encode(text, disallowed_special=()))


_TOKENIZER = _Tokenizer()


def _stable_chunk_id(document_id: str, ordinal: int) -> str:
    digest = hashlib.sha256(f"{document_id}|chunk|{ordinal}".encode("utf-8")).hexdigest()
    return f"chunk-{digest[:24]}"


def _citation_label(title: str, section_path: Sequence[str]) -> str:
    parts = [title] + [s for s in section_path if s]
    return " > ".join(p for p in parts if p)


def _norm_section(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def _section_skipped(section_path: Sequence[str], skip_norms: set) -> bool:
    return any(_norm_section(s) in skip_norms for s in section_path)


def _regions_payload(item: ExtractedItem) -> List[Dict]:
    return [r.to_dict() for r in item.regions]


def _merge_regions(items: Sequence[ExtractedItem]) -> Tuple[List[Dict], List[int], List[str]]:
    regions: List[Dict] = []
    pages: List[int] = []
    refs: List[str] = []
    for item in items:
        regions.extend(r.to_dict() for r in item.regions)
        for r in item.regions:
            if r.page_number not in pages:
                pages.append(r.page_number)
        if item.item_ref not in refs:
            refs.append(item.item_ref)
    return regions, pages, refs


def _split_sentences(text: str) -> List[str]:
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _pack_sentences(
    sentences: Sequence[Tuple[str, int]],  # (sentence, tokens)
    budget: int,
    overlap: int,
) -> List[List[str]]:
    """Greedily pack sentences into chunks <= budget tokens.

    Overlap carries the trailing sentences (up to `overlap` tokens) into the
    next chunk so a boundary does not orphan context. Oversized single
    sentences are word-split as a last resort.
    """
    chunks: List[List[str]] = []
    current: List[str] = []
    current_tokens = 0
    for sentence, tokens in sentences:
        if tokens > budget:
            # flush current, then hard-split the oversized sentence by words
            if current:
                chunks.append(current)
                current, current_tokens = [], 0
            words = sentence.split()
            piece: List[str] = []
            piece_tokens = 0
            for word in words:
                w_tokens = _TOKENIZER.count(word) + 1
                if piece_tokens + w_tokens > budget and piece:
                    chunks.append(piece)
                    piece, piece_tokens = [], 0
                piece.append(word)
                piece_tokens += w_tokens
            if piece:
                chunks.append(piece)
            continue
        if current_tokens + tokens > budget and current:
            chunks.append(current)
            # overlap: carry trailing sentences within the overlap budget
            carry: List[str] = []
            carry_tokens = 0
            for prev in reversed(current):
                prev_tokens = _TOKENIZER.count(prev)
                if carry_tokens + prev_tokens > overlap or carry_tokens >= budget:
                    break
                carry.insert(0, prev)
                carry_tokens += prev_tokens
            current = list(carry)
            current_tokens = carry_tokens
        current.append(sentence)
        current_tokens += tokens
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Prose chunks
# ---------------------------------------------------------------------------

def _build_prose_chunks(
    extraction: DoclingExtraction,
    items: Sequence[ExtractedItem],
    budget: int,
    overlap: int,
    skip_norms: set,
) -> List[DoclingChunk]:
    chunks: List[DoclingChunk] = []

    # Group consecutive items sharing the same section path; do not cross
    # section boundaries.
    groups: List[Tuple[List[str], List[ExtractedItem]]] = []
    for item in items:
        if item.kind in _NON_INDEXED_KINDS or item.kind in ("table", "picture"):
            continue
        if not item.verbatim_text:
            continue
        if _section_skipped(item.section_path, skip_norms):
            continue
        if groups and groups[-1][0] == item.section_path:
            groups[-1][1].append(item)
        else:
            groups.append((list(item.section_path), [item]))

    for section_path, group_items in groups:
        label = _citation_label(extraction.title, section_path)
        # sentences annotated with their source item index in the group
        sentences: List[Tuple[str, int]] = []
        for item in group_items:
            for sentence in _split_sentences(item.verbatim_text):
                if sentence:
                    sentences.append((sentence, _TOKENIZER.count(sentence)))

        if not sentences:
            continue

        sentence_texts = [s for s, _ in sentences]
        sentence_tokens = [t for _, t in sentences]
        packed = _pack_sentences(list(zip(sentence_texts, sentence_tokens)), budget, overlap)

        # map each sentence index -> item index for item_refs/regions
        sentence_item_idx: List[int] = []
        for item_i, item in enumerate(group_items):
            n = len(_split_sentences(item.verbatim_text))
            sentence_item_idx.extend([item_i] * n)

        for packed_sentences in packed:
            # find item indices for the sentences actually included
            first_sentence = packed_sentences[0]
            try:
                start_idx = sentence_texts.index(first_sentence)
            except ValueError:
                start_idx = 0
            item_indices = sorted({sentence_item_idx[start_idx + i] for i, s in enumerate(packed_sentences) if start_idx + i < len(sentence_item_idx)})
            contributing = [group_items[i] for i in item_indices]
            regions, pages, refs = _merge_regions(contributing)
            display = " ".join(packed_sentences)
            embedding = f"{label}\n\n{display}" if label else display
            lexical = " ".join(display.split())
            chunks.append(
                DoclingChunk(
                    chunk_id="",  # assigned after ordering
                    document_id=extraction.document_id,
                    block_type="prose",
                    representation_type="prose",
                    section_path=list(section_path),
                    citation_label=label,
                    display_text=display,
                    embedding_text=embedding,
                    lexical_text=lexical,
                    item_refs=refs,
                    regions=regions,
                    page_numbers=pages,
                    token_count=_TOKENIZER.count(embedding),
                    title=extraction.title,
                )
            )
    return chunks


# ---------------------------------------------------------------------------
# Table chunks
# ---------------------------------------------------------------------------

def _render_markdown_row_group(
    table,
    header_row: List[str],
    data_rows: Sequence[List[str]],
) -> str:
    lines: List[str] = []
    all_rows = [header_row] + [r for r in data_rows]
    for row in all_rows:
        cells = [str(c).replace("|", "\\|") for c in row]
        lines.append("| " + " | ".join(cells) + " |")
        if not lines[1:]:
            lines.append("|" + "---|" * len(cells))
    # ensure separator after header
    if len(lines) >= 2 and not lines[1].startswith("|---"):
        lines.insert(1, "|" + "---|" * max(len(header_row), 1))
    return "\n".join(lines)


def _union_bbox_row(
    table,
    row_idx: int,
    page_size: Optional[Dict[str, float]],
) -> Optional[Dict]:
    """Union of normalized cell bboxes for one table row, for row highlights."""
    if not page_size:
        return None
    w = float(page_size.get("width", 0.0))
    h = float(page_size.get("height", 0.0))
    if w <= 0 or h <= 0:
        return None
    boxes = [c.bbox for c in table.cells if c.start_row == row_idx and c.bbox]
    if not boxes:
        return None
    l = min(b[0] for b in boxes)
    b = min(b[1] for b in boxes)
    r = max(b[2] for b in boxes)
    t = max(b[3] for b in boxes)
    # BOTTOMLEFT -> top-left fractions
    x0, x1 = l / w, r / w
    y0, y1 = 1.0 - t / h, 1.0 - b / h
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return {"bbox_norm": [max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)]}


def _build_table_chunks(
    extraction: DoclingExtraction,
    item: ExtractedItem,
    budget: int,
    rows_per_group: int,
    min_rows_row_repr: int,
    skip_norms: set,
) -> List[DoclingChunk]:
    if item.kind != "table" or item.table is None:
        return []
    if _section_skipped(item.section_path, skip_norms):
        return []

    table = item.table
    label = _citation_label(extraction.title, item.section_path)
    table_regions = _regions_payload(item)
    page_numbers = [r["page_number"] for r in table_regions]
    page_size = extraction.pages.get(item.page_index) if item.page_index is not None else None

    header = table.rows[0] if table.rows else []
    data_rows = table.rows[1:] if len(table.rows) > 1 else []
    full_markdown = table.markdown or _render_markdown_row_group(table, header, data_rows)
    full_tokens = _TOKENIZER.count(full_markdown)

    chunks: List[DoclingChunk] = []

    def _mk(display, embedding, rep_type, regions, pages, block_type="table"):
        return DoclingChunk(
            chunk_id="",
            document_id=extraction.document_id,
            block_type=block_type,
            representation_type=rep_type,
            section_path=list(item.section_path),
            citation_label=label,
            display_text=display,
            embedding_text=embedding,
            lexical_text=" ".join(display.split()),
            item_refs=[item.item_ref],
            regions=regions,
            page_numbers=pages,
            token_count=_TOKENIZER.count(embedding),
            title=extraction.title,
        )

    if full_tokens <= budget:
        embedding = f"{label}\n\n{full_markdown}"
        chunks.append(_mk(full_markdown, embedding, "table_full", table_regions, page_numbers))
    else:
        # row-group chunks with repeated headers + breadcrumb context
        for start in range(0, len(data_rows), rows_per_group):
            group = data_rows[start : start + rows_per_group]
            rendered = _render_markdown_row_group(table, header, group)
            embedding = f"{label}\n\n{rendered}"
            chunks.append(_mk(rendered, embedding, "table_rows", table_regions, page_numbers))

    # row-level key/value representation for exact parameter/value lookup
    if len(data_rows) >= min_rows_row_repr:
        for row_idx, row in enumerate(data_rows, start=1):
            pairs = []
            for col, value in enumerate(row):
                col_name = header[col] if col < len(header) else f"col{col}"
                if value:
                    pairs.append(f"{col_name}={value}")
            if not pairs:
                continue
            display = "; ".join(pairs)
            embedding = f"{label} (specification row)\n{display}"
            region_payload = list(table_regions)
            row_bbox = _union_bbox_row(table, row_idx, page_size)
            if row_bbox and table_regions:
                region_payload = [
                    {**table_regions[0], **row_bbox, "granularity": "table_row"}
                ]
            chunks.append(
                _mk(
                    display,
                    embedding,
                    "row_kv",
                    region_payload,
                    page_numbers,
                    block_type="table_row",
                )
            )
    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_chunks(
    extraction: DoclingExtraction,
    *,
    max_chunks: Optional[int] = None,
    skip_sections: Optional[Sequence[str]] = None,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    rows_per_group: Optional[int] = None,
    min_rows_row_repr: Optional[int] = None,
) -> ChunkPlan:
    """Build the structure-aware chunk plan from an extraction artifact."""
    budget = int(chunk_size if chunk_size is not None else settings.pdf_docling_chunk_size)
    overlap = int(chunk_overlap if chunk_overlap is not None else settings.pdf_docling_chunk_overlap)
    rows_per_group = int(rows_per_group if rows_per_group is not None else settings.pdf_docling_table_rows_per_chunk)
    min_rows_row_repr = int(
        min_rows_row_repr
        if min_rows_row_repr is not None
        else getattr(settings, "pdf_docling_min_rows_row_repr", 8)
    )

    skip_norms = {_norm_section(s) for s in (skip_sections or []) if _norm_section(s)}

    chunks: List[DoclingChunk] = []

    prose_items = [i for i in extraction.items if i.kind not in ("table", "picture")]
    table_items = [i for i in extraction.items if i.kind == "table"]
    picture_items = [i for i in extraction.items if i.kind == "picture"]

    chunks.extend(_build_prose_chunks(extraction, prose_items, budget, overlap, skip_norms))
    for table_item in table_items:
        chunks.extend(
            _build_table_chunks(
                extraction, table_item, budget, rows_per_group, min_rows_row_repr, skip_norms
            )
        )

    # pictures: index captions/adjacent explanatory text where present
    for pic in picture_items:
        if not pic.verbatim_text:
            continue
        label = _citation_label(extraction.title, pic.section_path)
        chunks.append(
            DoclingChunk(
                chunk_id="",
                document_id=extraction.document_id,
                block_type="caption",
                representation_type="prose",
                section_path=list(pic.section_path),
                citation_label=label,
                display_text=pic.verbatim_text,
                embedding_text=f"{label}\n\n{pic.verbatim_text}",
                lexical_text=" ".join(pic.verbatim_text.split()),
                item_refs=[pic.item_ref],
                regions=_regions_payload(pic),
                page_numbers=[r["page_number"] for r in _regions_payload(pic)],
                token_count=_TOKENIZER.count(pic.verbatim_text),
                title=extraction.title,
            )
        )

    # deterministic order: document order of first item ref, then chunk type
    order = {i.item_ref: idx for idx, i in enumerate(extraction.items)}

    def _sort_key(chunk: DoclingChunk):
        first_ref = chunk.item_refs[0] if chunk.item_refs else ""
        return (order.get(first_ref, len(order)), chunk.representation_type)

    chunks.sort(key=_sort_key)

    skipped_sections = sorted(
        {_norm_section(s) for s in (skip_sections or []) if _norm_section(s)}
    )

    omitted = 0
    if max_chunks is not None and max_chunks > 0 and len(chunks) > max_chunks:
        omitted = len(chunks) - max_chunks
        chunks = chunks[:max_chunks]

    for idx, chunk in enumerate(chunks):
        chunk.chunk_index = idx
        chunk.chunk_id = _stable_chunk_id(extraction.document_id, idx)

    return ChunkPlan(chunks=chunks, omitted_chunks=omitted, skipped_sections=skipped_sections)
