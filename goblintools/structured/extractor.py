"""Facade for parallel structured extraction (does not alter TextExtractor)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable, List, Optional, Set, Union

from goblintools.structured.docx_tables import extract_docx_content
from goblintools.structured.models import StructuredDocument
from goblintools.structured.ocr_tables import extract_ocr_table_blocks
from goblintools.structured.pdf import extract_pdf_table_blocks, merge_multipage_tables
from goblintools.structured.render import to_full_md
from goblintools.structured.spreadsheet import (
    extract_csv_table_blocks,
    extract_xlsx_table_blocks,
)

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

STRUCTURED_SUFFIXES: Set[str] = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".xlsm",
    ".csv",
}


class StructuredExtractor:
    """Extract item-oriented tables without touching plain-text extraction.

    Usage::

        ext = StructuredExtractor(ocr_tables=True)
        doc = ext.extract_from_file("edital.pdf")
        if doc.ok_for_items or doc.has_usable_tables:
            ext.write_full_md(doc, "extracted/edital/full.md")
    """

    def __init__(
        self,
        *,
        max_pdf_pages: Optional[int] = None,
        ocr_tables: bool = False,
        ocr_processor: Any = None,
    ):
        self.max_pdf_pages = max_pdf_pages
        self.ocr_tables = ocr_tables
        self.ocr_processor = ocr_processor

    def extract_from_file(self, file_path: PathLike) -> StructuredDocument:
        """Extract structured tables from one file."""
        path = Path(file_path)
        suffix = path.suffix.lower()
        if not path.is_file():
            return StructuredDocument(path=str(path), ok_for_items=False)

        used_ocr = False
        try:
            if suffix == ".pdf":
                tables = extract_pdf_table_blocks(
                    str(path), max_pages=self.max_pdf_pages,
                )
                prose = ""
                # OCR path when native tables miss and ocr_tables is enabled.
                need_ocr = self.ocr_tables and (
                    not tables
                    or not any(t.quality.ok_for_items for t in tables)
                )
                if need_ocr:
                    ocr_blocks = extract_ocr_table_blocks(
                        str(path),
                        max_pages=self.max_pdf_pages,
                        ocr_processor=self.ocr_processor,
                        force=not tables,
                    )
                    if ocr_blocks:
                        used_ocr = True
                        # Prefer OCR blocks when they score better / are the only ones.
                        if not tables or any(b.quality.ok_for_items for b in ocr_blocks):
                            tables = merge_multipage_tables(ocr_blocks)
                        else:
                            tables = merge_multipage_tables(list(tables) + ocr_blocks)
            elif suffix in {".xlsx", ".xlsm"}:
                tables = extract_xlsx_table_blocks(str(path))
                prose = ""
            elif suffix == ".csv":
                tables = extract_csv_table_blocks(str(path))
                prose = ""
            elif suffix == ".docx":
                tables, prose = extract_docx_content(str(path))
            else:
                return StructuredDocument(path=str(path), ok_for_items=False)
        except Exception as e:  # noqa: BLE001 - never abort the caller
            logger.warning("Structured extraction failed for %s: %s", path, e)
            return StructuredDocument(path=str(path), ok_for_items=False)

        ok = any(t.quality.ok_for_items for t in tables)
        usable = ok or any(
            getattr(t.quality, "has_usable_tables", False) or t.quality.n_data_rows >= 3
            for t in tables
            if t.quality.meaningful
        )
        return StructuredDocument(
            path=str(path),
            tables=tables,
            prose=prose,
            ok_for_items=ok,
            has_usable_tables=usable,
            used_ocr_tables=used_ocr,
        )

    def extract_from_folder(self, folder_path: PathLike) -> List[StructuredDocument]:
        """Walk a folder and extract structured docs from supported leaves."""
        root = Path(folder_path)
        if not root.is_dir():
            return []
        docs: List[StructuredDocument] = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() not in STRUCTURED_SUFFIXES:
                    continue
                docs.append(self.extract_from_file(path))
        return docs

    def to_full_md(self, doc: StructuredDocument) -> str:
        """Render a :class:`StructuredDocument` as HTML-rich ``full.md`` text."""
        return to_full_md(doc)

    def write_full_md(self, doc: StructuredDocument, dest: PathLike) -> bool:
        """Write ``full.md`` content to ``dest``. Returns False if empty."""
        content = self.to_full_md(doc)
        if not content.strip():
            return False
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(content, encoding="utf-8")
        return True

    def supports(self, file_path: PathLike) -> bool:
        """Return True when the file suffix is handled by this extractor."""
        return Path(file_path).suffix.lower() in STRUCTURED_SUFFIXES

    def extract_ok_files(
        self, files: Iterable[PathLike]
    ) -> List[StructuredDocument]:
        """Extract only documents that pass ``ok_for_items``."""
        out: List[StructuredDocument] = []
        for file_path in files:
            if not self.supports(file_path):
                continue
            doc = self.extract_from_file(file_path)
            if doc.ok_for_items:
                out.append(doc)
        return out
