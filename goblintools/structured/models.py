"""Data contracts for structured document extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TableQuality:
    """Quality signals for item-oriented table extraction."""

    meaningful: bool
    has_itemish_header: bool
    n_data_rows: int
    qty_parse_rate: float
    value_parse_rate: float
    ok_for_items: bool
    has_value_column: bool = False
    first_row_is_header: bool = True
    # Soft gate: meaningful grid with enough rows (process may still write HTML).
    has_usable_tables: bool = False


@dataclass
class TableBlock:
    """One extracted table matrix with provenance and quality."""

    index: int
    source: str  # "pdf" | "xlsx" | "csv" | "docx" | "ocr"
    rows: List[List[Optional[str]]]
    quality: TableQuality
    page: Optional[int] = None
    sheet: Optional[str] = None


@dataclass
class StructuredDocument:
    """Structured view of a single file for item-extraction pipelines."""

    path: str
    tables: List[TableBlock] = field(default_factory=list)
    prose: str = ""
    ok_for_items: bool = False
    # True when any table is meaningful with enough rows (even if ok_for_items).
    has_usable_tables: bool = False
    # True when OCR table path was used (scan / low-text PDF).
    used_ocr_tables: bool = False
