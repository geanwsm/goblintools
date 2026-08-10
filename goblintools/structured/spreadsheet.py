"""XLSX / XLSM / CSV structured table extraction."""

from __future__ import annotations

import csv
import logging
from typing import Any, List, Optional

from goblintools.table_extractor import normalize_table_rows
from goblintools.structured.models import TableBlock
from goblintools.structured.quality import prepare_rows_for_output, score_table

logger = logging.getLogger(__name__)


def _matrix_from_rows(raw_rows: List[List[Any]]) -> List[List[Optional[str]]]:
    matrix: List[List[Optional[str]]] = []
    for row in raw_rows:
        matrix.append([None if c is None else str(c) for c in row])
    return matrix


def _to_blocks(
    matrices: List[tuple],
    *,
    source: str,
) -> List[TableBlock]:
    """``matrices`` is a list of ``(sheet_name|None, rows)``."""
    blocks: List[TableBlock] = []
    index = 0
    for sheet, raw in matrices:
        rows = normalize_table_rows(_matrix_from_rows(raw))
        rows = prepare_rows_for_output(rows)
        if not rows:
            continue
        quality = score_table(rows)
        if not quality.meaningful:
            continue
        blocks.append(
            TableBlock(
                page=None,
                index=index,
                source=source,
                sheet=sheet,
                rows=rows,
                quality=quality,
            )
        )
        index += 1
    return blocks


def extract_xlsx_table_blocks(path: str) -> List[TableBlock]:
    """Extract one table per worksheet from XLSX/XLSM."""
    try:
        import openpyxl
    except ImportError as e:
        raise ImportError(
            "openpyxl is required for XLSX structured extraction"
        ) from e

    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        logger.error("Failed to open workbook %s: %s", path, e)
        return []

    matrices = []
    try:
        for sheet in wb.worksheets:
            raw_rows: List[List[Any]] = []
            for row in sheet.iter_rows(values_only=True):
                if row is None:
                    continue
                values = list(row)
                if not any(v is not None and str(v).strip() for v in values):
                    continue
                raw_rows.append(values)
            if raw_rows:
                matrices.append((sheet.title, raw_rows))
    finally:
        wb.close()

    return _to_blocks(matrices, source="xlsx")


def extract_csv_table_blocks(path: str) -> List[TableBlock]:
    """Extract a single table from a CSV file."""
    raw_rows: List[List[Any]] = []
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=encoding, newline="") as fh:
                reader = csv.reader(fh)
                raw_rows = [list(row) for row in reader if any(c.strip() for c in row)]
            break
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.error("Failed to read CSV %s: %s", path, e)
            return []
    if not raw_rows:
        return []
    return _to_blocks([(None, raw_rows)], source="csv")
