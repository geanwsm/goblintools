"""PDF structured table extraction (pdfplumber via table_extractor)."""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence

from goblintools.table_extractor import extract_pdf_tables, normalize_table_rows
from goblintools.structured.models import TableBlock
from goblintools.structured.quality import prepare_rows_for_output, score_table

logger = logging.getLogger(__name__)


def _header_key(rows: Sequence[Sequence[Optional[str]]]) -> str:
    if not rows:
        return ""
    return "|".join((c or "").strip().lower() for c in rows[0][:8])


def _same_schema(a: Sequence[Sequence[Optional[str]]], b: Sequence[Sequence[Optional[str]]]) -> bool:
    if not a or not b:
        return False
    wa = max(len(r) for r in a)
    wb = max(len(r) for r in b)
    if wa != wb or wa < 2:
        return False
    # Same header text, or headerless continuation (first row looks like data).
    ka, kb = _header_key(a), _header_key(b)
    if ka and kb and ka == kb:
        return True
    # Continuation page often repeats no header — allow if widths match and
    # both have enough data rows.
    return len(a) >= 3 and len(b) >= 2 and not ka.startswith("item")


def merge_multipage_tables(blocks: List[TableBlock]) -> List[TableBlock]:
    """Concatenate consecutive same-schema tables across pages (large catalogs)."""
    if len(blocks) < 2:
        return blocks
    # Sort by page then index
    ordered = sorted(
        blocks,
        key=lambda b: (b.page if b.page is not None else 0, b.index),
    )
    merged: List[TableBlock] = []
    acc = ordered[0]
    for nxt in ordered[1:]:
        same_page = (
            acc.page is not None
            and nxt.page is not None
            and nxt.page == acc.page
        )
        consecutive = (
            acc.page is not None
            and nxt.page is not None
            and nxt.page == acc.page + 1
        )
        if (consecutive or same_page) and _same_schema(acc.rows, nxt.rows):
            # Drop repeated header on continuation.
            body = list(nxt.rows)
            if body and _header_key(acc.rows) and _header_key(body) == _header_key(acc.rows):
                body = body[1:]
            combined_rows = list(acc.rows) + body
            quality = score_table(combined_rows)
            acc = TableBlock(
                page=acc.page,
                index=acc.index,
                source=acc.source,
                sheet=None,
                rows=combined_rows,
                quality=quality,
            )
        else:
            merged.append(acc)
            acc = nxt
    merged.append(acc)
    # Re-index globally for stable comments
    for i, block in enumerate(merged):
        block.index = i
    return merged


def extract_pdf_table_blocks(
    pdf_path: str,
    *,
    max_pages: Optional[int] = None,
    merge_multipage: bool = True,
) -> List[TableBlock]:
    """Extract and score tables from a native PDF."""
    raw_tables = extract_pdf_tables(
        pdf_path,
        max_pages=max_pages,
        normalize=True,
        dual_strategy=True,
    )
    blocks: List[TableBlock] = []
    page_counters: dict = {}
    for raw in raw_tables:
        rows = prepare_rows_for_output(raw.get("rows") or [])
        if not rows:
            continue
        rows = normalize_table_rows(rows)
        rows = prepare_rows_for_output(rows)
        quality = score_table(rows)
        if not quality.meaningful:
            continue
        page = int(raw["page"])
        idx = page_counters.get(page, 0)
        page_counters[page] = idx + 1
        blocks.append(
            TableBlock(
                page=page,
                index=idx,
                source="pdf",
                sheet=None,
                rows=rows,
                quality=quality,
            )
        )
    if merge_multipage and blocks:
        blocks = merge_multipage_tables(blocks)
    return blocks
