"""Item-oriented quality gates and row/cell cleanup for structured tables."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from goblintools.table_extractor import is_meaningful_table
from goblintools.structured.models import TableQuality

Cell = Optional[str]
TableRows = List[List[Cell]]


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _row_width(rows: Sequence[Sequence[Any]]) -> int:
    return max((len(row) for row in rows), default=0)


def _pad_row(row: Sequence[Any], width: int) -> List[str]:
    cells = [_cell_text(c) for c in row]
    if len(cells) < width:
        cells = cells + [""] * (width - len(cells))
    return cells[:width]


def _filled_count(row: Sequence[Any]) -> int:
    return sum(1 for c in row if _cell_text(c))

_HEADER_ITEM = re.compile(
    r"\b(?:item|itens|n[º°o]\.?|núm(?:ero)?|c[oó]d(?:igo)?)\b",
    re.I,
)
_HEADER_DESC = re.compile(
    r"\b(descri|especifica|produto|nome do|discrimin|servi[cç]o|material)",
    re.I,
)
_HEADER_QTD = re.compile(r"\b(qtd\.?e?|quant)\b", re.I)
_HEADER_UND = re.compile(
    r"\b(unid(?:ade)?|und\.?|u\.?\s*m\.?)\b",
    re.I,
)
_HEADER_OBRA = re.compile(
    r"\b(c[oó]digo|sinapi|composi[cç]|insumo|servi[cç]o|especific)\b",
    re.I,
)
_HEADER_LOTE = re.compile(r"\b(?:lote|lotes)\b", re.I)
_HEADER_VALOR = re.compile(
    r"\bvalor\b|pre[cç]o|v\.?\s*unit|v\.?\s*total|lance",
    re.I,
)

_DATA_FIRST = re.compile(r"^\d+([.\)]\d*)*\.?$")
_FOOTER_ROW = re.compile(
    r"^\s*(valor\s*total|aten[cç][aã]o\b|total\s*geral|subtotal)\b",
    re.I,
)
# Split letter groups inside a short token: "ESTIMATIV A" -> "ESTIMATIVA"
_SPLIT_WORD = re.compile(
    r"\b([A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç]{2,})\s+([A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç])\b"
)
# Broken BR money / decimals: "99.068,7 1" -> "99.068,71"
_SPLIT_NUMBER = re.compile(r"(\d[0-9.]*,\d+)\s+(\d)\b")
_QTY_LIKE = re.compile(
    r"^[\d]+([.,]\d+)?$|"
    r"^R\$\s*[\d.]+,\d{2}$|"
    r"^[\d.]+,\d{2}$",
    re.I,
)
_BARE_CURRENCY = re.compile(r"^R\$\s*$", re.I)
_NUMERIC_LOOSE = re.compile(r"[\d]+([.,]\d+)?")
_UND_CELL = re.compile(
    r"^(UN|UND|UNID|M2|M²|M3|M³|KG|T|VB|GLB|HR|H|CJ|JG|PÇ|PC|M|ML|L)$",
    re.I,
)


def looks_like_data_row(row: Sequence[Any]) -> bool:
    """True when the first cell looks like an item number."""
    if not row:
        return False
    first = _cell_text(row[0]).replace(" ", "")
    return bool(first and _DATA_FIRST.match(first))


def first_row_is_header(rows: Sequence[Sequence[Any]]) -> bool:
    """Return False when the first row already looks like data."""
    if not rows:
        return True
    return not looks_like_data_row(rows[0])


def drop_footer_rows(rows: Sequence[Sequence[Any]]) -> TableRows:
    """Remove total / attention / single-span note rows from the body."""
    if not rows:
        return []
    width = _row_width(rows)
    padded = [_pad_row(r, width) for r in rows]
    out: TableRows = []
    for i, row in enumerate(padded):
        joined = " ".join(c for c in row if c).strip()
        if not joined:
            continue
        # Keep a real header even if it mentions "valor"
        if i == 0 and not looks_like_data_row(row):
            out.append([c or None for c in row])
            continue
        if _FOOTER_ROW.match(joined):
            continue
        filled = _filled_count(row)
        # Prose note absorbed as one long cell + empty columns
        if filled == 1 and len(joined) > 80 and looks_like_data_row(row) is False:
            if i > 0:
                continue
        out.append([c or None for c in row])
    return out


def clean_cell_text(value: Any) -> str:
    """Light cleanup for OCR/layout split tokens inside a cell."""
    text = _cell_text(value)
    if not text:
        return ""
    # Fix split decimals before collapsing other spaces
    prev = None
    while prev != text:
        prev = text
        text = _SPLIT_NUMBER.sub(r"\1\2", text)
    # Collapse "ESTIMATIV A" / "ESTIMATI VA" style splits (short second part)
    prev = None
    while prev != text:
        prev = text
        text = _SPLIT_WORD.sub(r"\1\2", text)
    return " ".join(text.split())


def clean_table_rows(rows: Sequence[Sequence[Any]]) -> TableRows:
    """Apply cell cleanup across the matrix."""
    cleaned: TableRows = []
    for row in rows:
        cleaned.append([clean_cell_text(c) or None for c in row])
    return cleaned


def _header_cells(rows: Sequence[Sequence[Any]]) -> List[str]:
    if not rows:
        return []
    if first_row_is_header(rows):
        return [_cell_text(c) for c in rows[0]]
    return []


def has_itemish_header(rows: Sequence[Sequence[Any]]) -> bool:
    """True when header looks like an item catalog (Portuguese / obra)."""
    cells = _header_cells(rows)
    if not cells:
        return False
    joined = " | ".join(cells)
    has_item = bool(_HEADER_ITEM.search(joined))
    has_desc = bool(_HEADER_DESC.search(joined))
    has_qtd = bool(_HEADER_QTD.search(joined))
    has_und = bool(_HEADER_UND.search(joined))
    has_obra = bool(_HEADER_OBRA.search(joined))
    has_lote = bool(_HEADER_LOTE.search(joined))
    if has_item and has_desc and has_qtd:
        return True
    # Planilha/SINAPI: código/serviço + und + qtd (ITEM word optional).
    if (has_item or has_obra) and has_und and has_qtd:
        return True
    if has_desc and has_und and has_qtd:
        return True
    # Leilão / hasta: LOTE + DESCRIÇÃO (qty often implicit = 1).
    if has_lote and has_desc:
        return True
    if has_lote and has_item:
        return True
    return False


def looks_like_dense_obra(rows: Sequence[Sequence[Any]]) -> bool:
    """Headerless BOQ: many rows with unit + quantity cells."""
    if len(rows) < 6:
        return False
    width = _row_width(rows)
    if width < 3:
        return False
    sample = list(rows[: min(40, len(rows))])
    und_hits = 0
    qty_hits = 0
    long_desc = 0
    for row in sample:
        cells = [_cell_text(c) for c in _pad_row(row, width)]
        if any(_UND_CELL.match(c) for c in cells):
            und_hits += 1
        if any(_QTY_LIKE.match(c) and not c.upper().startswith("R$") for c in cells):
            qty_hits += 1
        if max((len(c) for c in cells), default=0) >= 12:
            long_desc += 1
    n = len(sample)
    return (
        und_hits >= max(3, n // 4)
        and qty_hits >= max(3, n // 3)
        and long_desc >= max(3, n // 3)
    )


def _column_indexes(header: Sequence[str]) -> Tuple[Optional[int], List[int]]:
    qty_idx: Optional[int] = None
    value_idxs: List[int] = []
    for i, cell in enumerate(header):
        if qty_idx is None and _HEADER_QTD.search(cell):
            qty_idx = i
        if _HEADER_VALOR.search(cell):
            value_idxs.append(i)
    return qty_idx, value_idxs


def _parseable_qty(cell: str) -> bool:
    text = cell.strip()
    if not text or _BARE_CURRENCY.match(text):
        return False
    # Strip currency / unit noise
    cleaned = re.sub(r"^R\$\s*", "", text, flags=re.I).strip()
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        float(cleaned)
        return True
    except ValueError:
        return bool(_NUMERIC_LOOSE.search(text) and not _BARE_CURRENCY.match(text))


def _parseable_value(cell: str) -> bool:
    text = cell.strip()
    if not text or _BARE_CURRENCY.match(text):
        return False
    return _parseable_qty(text)


def score_table(rows: Sequence[Sequence[Any]]) -> TableQuality:
    """Compute item-extraction quality for a normalized table matrix."""
    working = drop_footer_rows(rows)
    working = clean_table_rows(working)
    meaningful = is_meaningful_table(working)
    header_ok = first_row_is_header(working)
    itemish = has_itemish_header(working) if header_ok else False
    dense_obra = (not itemish) and looks_like_dense_obra(working)

    data_rows: TableRows
    if header_ok and working:
        data_rows = working[1:]
        header = [_cell_text(c) for c in working[0]]
    else:
        data_rows = list(working)
        header = []

    # Drop empty data rows
    data_rows = [r for r in data_rows if any(_cell_text(c) for c in r)]
    n_data = len(data_rows)

    qty_idx, value_idxs = _column_indexes(header) if header else (None, [])
    has_value_col = bool(value_idxs)

    if n_data == 0:
        qty_rate = 0.0
        value_rate = 1.0 if not has_value_col else 0.0
    else:
        if qty_idx is None:
            # Fall back: second-to-last numeric-ish column heuristic
            qty_hits = 0
            for row in data_rows:
                cells = [_cell_text(c) for c in row]
                # try any cell that looks like a plain quantity
                if any(_parseable_qty(c) and not c.upper().startswith("R$") for c in cells[1:] or cells):
                    qty_hits += 1
            qty_rate = qty_hits / n_data
        else:
            hits = 0
            for row in data_rows:
                cells = [_cell_text(c) for c in _pad_row(row, max(qty_idx + 1, _row_width(data_rows)))]
                if qty_idx < len(cells) and _parseable_qty(cells[qty_idx]):
                    hits += 1
            qty_rate = hits / n_data

        if not has_value_col:
            value_rate = 1.0
        else:
            hits = 0
            width = _row_width(data_rows)
            for row in data_rows:
                cells = [_cell_text(c) for c in _pad_row(row, width)]
                if any(
                    idx < len(cells) and _parseable_value(cells[idx])
                    for idx in value_idxs
                ):
                    hits += 1
            value_rate = hits / n_data

    # Leilão tables often have no parseable qty column — treat as qty_rate=1.
    leilao_header = False
    if header:
        joined_h = " | ".join(header)
        leilao_header = bool(
            _HEADER_LOTE.search(joined_h)
            and (_HEADER_DESC.search(joined_h) or _HEADER_ITEM.search(joined_h))
        )
    effective_qty = 1.0 if leilao_header and qty_idx is None else qty_rate

    ok = bool(
        meaningful
        and (itemish or dense_obra)
        and n_data >= 1
        and effective_qty >= 0.5
        and (not has_value_col or value_rate >= 0.5 or leilao_header)
    )
    # Soft signal: meaningful grid with enough rows even if itemish gate fails.
    usable = bool(meaningful and n_data >= 3)

    return TableQuality(
        meaningful=meaningful,
        has_itemish_header=itemish or dense_obra,
        n_data_rows=n_data,
        qty_parse_rate=round(effective_qty if leilao_header and qty_idx is None else qty_rate, 3),
        value_parse_rate=round(value_rate, 3),
        ok_for_items=ok,
        has_value_column=has_value_col,
        first_row_is_header=header_ok,
        has_usable_tables=usable,
    )


def prepare_rows_for_output(rows: Sequence[Sequence[Any]]) -> TableRows:
    """Footer drop + cell cleanup used before scoring/render."""
    return clean_table_rows(drop_footer_rows(rows))
