"""Parallel structured extraction API (item-oriented tables).

Does not alter :class:`goblintools.TextExtractor` plain-text behaviour.
"""

from goblintools.structured.extractor import STRUCTURED_SUFFIXES, StructuredExtractor
from goblintools.structured.models import StructuredDocument, TableBlock, TableQuality
from goblintools.structured.render import table_to_html, to_full_md
from goblintools.structured.quality import (
    has_itemish_header,
    prepare_rows_for_output,
    score_table,
)
from goblintools.structured.ocr_tables import is_low_text_pdf

__all__ = [
    "STRUCTURED_SUFFIXES",
    "StructuredDocument",
    "StructuredExtractor",
    "TableBlock",
    "TableQuality",
    "has_itemish_header",
    "is_low_text_pdf",
    "prepare_rows_for_output",
    "score_table",
    "table_to_html",
    "to_full_md",
]
