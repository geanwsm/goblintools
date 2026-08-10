"""Render structured tables as HTML compatible with item parsers."""

from __future__ import annotations

import html
from typing import Any, List, Optional, Sequence

from goblintools.structured.models import StructuredDocument, TableBlock


def _cell_html(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value).strip())


def table_to_html(
    rows: Sequence[Sequence[Any]],
    *,
    first_row_is_header: bool = True,
) -> str:
    """Convert a cell matrix to a single ``<table>`` HTML block.

    Uses ``<td>`` for every cell (including header) so consumers that regex
    ``<t[dh]>`` keep working; header semantics stay in the first row.
    """
    if not rows:
        return ""

    width = max((len(r) for r in rows), default=0)
    if width == 0:
        return ""

    lines: List[str] = ["<table>"]
    for i, row in enumerate(rows):
        cells = [(_cell_html(c) if c is not None else "") for c in row]
        if len(cells) < width:
            cells.extend([""] * (width - len(cells)))
        # first_row_is_header reserved for callers; output stays <td> uniformly
        _ = first_row_is_header, i
        lines.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _table_comment(block: TableBlock) -> str:
    parts = [f"source={block.source}", f"index={block.index}"]
    if block.page is not None:
        parts.insert(1, f"page={block.page}")
    if block.sheet:
        parts.append(f"sheet={block.sheet}")
    return f"<!-- table {' '.join(parts)} -->"


def to_full_md(doc: StructuredDocument) -> str:
    """Build a MinerU-compatible ``full.md`` string (HTML tables + prose)."""
    blocks: List[str] = []
    prose = (doc.prose or "").strip()
    if prose:
        blocks.append(prose)

    for table in doc.tables:
        html_table = table_to_html(
            table.rows,
            first_row_is_header=table.quality.first_row_is_header,
        )
        if not html_table:
            continue
        blocks.append(f"{_table_comment(table)}\n{html_table}")

    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")
