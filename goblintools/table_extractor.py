"""PDF table extraction helpers (pdfplumber) and Markdown formatting."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from goblintools.log_policy import log_warning

logger = logging.getLogger(__name__)

Cell = Optional[str]
TableRows = List[List[Cell]]

# Default quality gate: drop bordered text blocks mistaken for tables.
DEFAULT_MIN_COLS = 2
DEFAULT_MIN_ROWS = 2
DEFAULT_MIN_FILLED_CELLS = 4


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(text.split())
    return text.replace("|", "\\|")


def _pad_row(row: Sequence[Any], width: int) -> List[str]:
    cells = [_cell_text(c) for c in row]
    if len(cells) < width:
        cells = cells + [""] * (width - len(cells))
    return cells[:width]


def _row_width(rows: Sequence[Sequence[Any]]) -> int:
    return max((len(row) for row in rows), default=0)


def _filled_count(row: Sequence[Any]) -> int:
    return sum(1 for c in row if _cell_text(c))


def drop_empty_rows(rows: Sequence[Sequence[Any]]) -> TableRows:
    """Remove rows where every cell is empty/None."""
    cleaned: TableRows = []
    for row in rows:
        if any(_cell_text(c) for c in row):
            cleaned.append([None if c is None else str(c) for c in row])
    return cleaned


def collapse_header_rows(rows: Sequence[Sequence[Any]], *, max_header_rows: int = 6) -> TableRows:
    """Merge leading sparse rows into a single header when they look like split headers.

    Joins consecutive sparse leading rows column-wise. Stops *before* the first
    row that looks like data (e.g. item number in the first cell).
    """
    if not rows:
        return []
    width = _row_width(rows)
    if width == 0:
        return []

    padded = [_pad_row(r, width) for r in rows]
    if len(padded) < 2:
        return [[c or None for c in padded[0]]]

    def looks_like_data(row: List[str]) -> bool:
        first = row[0].replace(" ", "") if row[0] else ""
        if first and re.match(r"^\d+([.\)]\d*)*\.?$", first):
            return True
        # Valor TOTAL / footer-style rows are not header fragments
        joined = " ".join(row).lower()
        if joined.startswith("valor total") or joined.startswith("atenção"):
            return True
        return False

    sparse_limit = max(3, width // 2)
    # Number of leading rows to merge into one header (at least the first).
    merge_count = 1
    while merge_count < min(len(padded), max_header_rows):
        nxt = padded[merge_count]
        if looks_like_data(nxt):
            break
        if _filled_count(nxt) <= sparse_limit:
            merge_count += 1
            continue
        # Dense non-data row after sparse fragments → final header line.
        merge_count += 1
        break

    if merge_count <= 1:
        return [[c or None for c in row] for row in padded]

    merged = [""] * width
    for i in range(merge_count):
        for col, cell in enumerate(padded[i]):
            if not cell:
                continue
            merged[col] = f"{merged[col]} {cell}".strip() if merged[col] else cell

    body = padded[merge_count:]
    return [[c or None for c in merged]] + [[c or None for c in row] for row in body]


def merge_continuation_rows(rows: Sequence[Sequence[Any]]) -> TableRows:
    """Join rows that continue a previous item (empty leading key columns).

    If a row has an empty first cell and at least one filled later cell, append
    its text into the previous row's matching columns (space-separated).
    """
    if not rows:
        return []
    width = _row_width(rows)
    if width == 0:
        return []

    padded = [_pad_row(r, width) for r in rows]
    out: List[List[str]] = []
    for row in padded:
        is_continuation = (not row[0]) and _filled_count(row) >= 1 and out
        if is_continuation:
            prev = out[-1]
            for col in range(width):
                if not row[col]:
                    continue
                prev[col] = f"{prev[col]} {row[col]}".strip() if prev[col] else row[col]
        else:
            out.append(list(row))
    return [[c or None for c in row] for row in out]


def is_meaningful_table(
    rows: Sequence[Sequence[Any]],
    *,
    min_cols: int = DEFAULT_MIN_COLS,
    min_rows: int = DEFAULT_MIN_ROWS,
    min_filled_cells: int = DEFAULT_MIN_FILLED_CELLS,
) -> bool:
    """Return True if the matrix looks like a real data table, not a text box."""
    if not rows:
        return False
    width = _row_width(rows)
    if width < min_cols:
        return False
    non_empty_rows = [r for r in rows if any(_cell_text(c) for c in r)]
    if len(non_empty_rows) < min_rows:
        return False
    filled = sum(_filled_count(r) for r in non_empty_rows)
    if filled < min_filled_cells:
        return False
    # At least one row must use 2+ columns (rejects single-column text blocks
    # that were padded or rare 1-col leftovers after cleanup).
    if not any(_filled_count(r) >= 2 for r in non_empty_rows):
        return False
    return True


def normalize_table_rows(
    rows: Sequence[Sequence[Any]],
    *,
    collapse_headers: bool = True,
    merge_continuations: bool = True,
) -> TableRows:
    """Clean empty rows, optionally collapse split headers and join continuations."""
    cleaned = drop_empty_rows(rows)
    if not cleaned:
        return []
    if collapse_headers:
        cleaned = collapse_header_rows(cleaned)
    if merge_continuations:
        cleaned = merge_continuation_rows(cleaned)
    return drop_empty_rows(cleaned)


def table_to_markdown(rows: Sequence[Sequence[Any]], *, header: bool = True) -> str:
    """Convert a matrix of cells to a GitHub-flavored Markdown table.

    The first row is treated as the header when ``header`` is True and there is
    at least one row. Empty / ``None`` cells become empty strings.
    """
    if not rows:
        return ""

    normalized: List[List[str]] = [
        [_normalize_cell(cell) for cell in row] for row in rows
    ]
    width = max((len(row) for row in normalized), default=0)
    if width == 0:
        return ""

    def pad(row: List[str]) -> List[str]:
        return row + [""] * (width - len(row))

    normalized = [pad(row) for row in normalized]

    if header:
        header_row = normalized[0]
        body = normalized[1:]
    else:
        header_row = [f"Col {i + 1}" for i in range(width)]
        body = normalized

    lines = [
        "| " + " | ".join(header_row) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


# Dual strategies: bordered grids (lattice) vs whitespace columns (stream).
_TABLE_STRATEGIES: List[Dict[str, str]] = [
    {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
    {"vertical_strategy": "text", "horizontal_strategy": "text"},
    {},  # pdfplumber defaults
]


def _rows_signature(rows: TableRows) -> Tuple[int, int, str]:
    """Cheap fingerprint to dedupe lattice vs stream duplicates."""
    width = _row_width(rows)
    n = len(rows)
    sample = ""
    if rows:
        sample = "|".join(_cell_text(c) for c in (rows[0][:4] if rows[0] else []))
        if len(rows) > 1:
            sample += "||" + "|".join(
                _cell_text(c) for c in (rows[1][:4] if rows[1] else [])
            )
    return width, n, sample[:200]


def _extract_page_tables_dual(page: Any) -> List[TableRows]:
    """Run multiple pdfplumber strategies; keep unique meaningful matrices."""
    seen: set = set()
    out: List[TableRows] = []
    for settings in _TABLE_STRATEGIES:
        try:
            if settings:
                page_tables = page.extract_tables(table_settings=settings) or []
            else:
                page_tables = page.extract_tables() or []
        except Exception:
            continue
        for raw in page_tables:
            if not raw:
                continue
            rows: TableRows = [
                [None if cell is None else str(cell) for cell in row]
                for row in raw
            ]
            sig = _rows_signature(rows)
            if sig in seen:
                continue
            seen.add(sig)
            out.append(rows)
    return out


def extract_pdf_tables(
    pdf_path: str,
    *,
    max_pages: Optional[int] = None,
    min_cols: int = DEFAULT_MIN_COLS,
    min_rows: int = DEFAULT_MIN_ROWS,
    min_filled_cells: int = DEFAULT_MIN_FILLED_CELLS,
    normalize: bool = True,
    dual_strategy: bool = True,
) -> List[Dict[str, Union[int, TableRows]]]:
    """Extract tables from a native PDF with pdfplumber.

    Applies quality filtering (drops 1-column text boxes / tiny fragments) and
    optional normalization (empty-row drop, header collapse, continuation merge).
    When ``dual_strategy`` is True, runs lattice + stream + default extractors
    and dedupes results (better recall on editais BR).

    Returns a list of dicts: ``{"page": 1-based int, "index": int, "rows": matrix}``.
    ``index`` is the post-filter order on that page.
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError(
            "pdfplumber is required for table extraction. "
            "Install it with: pip install pdfplumber"
        ) from e

    tables: List[Dict[str, Union[int, TableRows]]] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            limit = page_count if max_pages is None else min(page_count, max_pages)
            for page_idx in range(limit):
                page = pdf.pages[page_idx]
                try:
                    if dual_strategy:
                        candidates = _extract_page_tables_dual(page)
                    else:
                        raw_list = page.extract_tables() or []
                        candidates = [
                            [
                                [None if cell is None else str(cell) for cell in row]
                                for row in raw
                            ]
                            for raw in raw_list
                            if raw
                        ]
                except Exception as e:
                    log_warning(
                        logger,
                        f"Table extraction failed on page {page_idx + 1} of {pdf_path}: {e}",
                    )
                    continue
                kept_on_page = 0
                for rows in candidates:
                    if normalize:
                        rows = normalize_table_rows(rows)
                    if not is_meaningful_table(
                        rows,
                        min_cols=min_cols,
                        min_rows=min_rows,
                        min_filled_cells=min_filled_cells,
                    ):
                        continue
                    tables.append(
                        {
                            "page": page_idx + 1,
                            "index": kept_on_page,
                            "rows": rows,
                        }
                    )
                    kept_on_page += 1
    except Exception as e:
        logger.error(f"Failed to extract tables from {pdf_path}: {e}")
        return []

    return tables


def tables_by_page(
    tables: Sequence[Dict[str, Any]],
) -> Dict[int, List[Dict[str, Any]]]:
    """Group extracted table dicts by 1-based page number."""
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for table in tables:
        page = int(table["page"])
        grouped.setdefault(page, []).append(table)
    return grouped


def format_tables_for_page(
    page_tables: Sequence[Dict[str, Any]],
    *,
    page: int,
    table_format: str = "markdown",
) -> str:
    """Render all tables for one page as a single text block."""
    if table_format != "markdown":
        raise ValueError(
            f"Unsupported table_format={table_format!r}; only 'markdown' is supported"
        )
    blocks: List[str] = []
    for table in page_tables:
        rows = table.get("rows") or []
        md = table_to_markdown(rows)
        if not md:
            continue
        index = int(table.get("index", 0))
        blocks.append(f"<!-- table page={page} index={index} -->\n{md}")
    return "\n\n".join(blocks)
