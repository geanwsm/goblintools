"""OCR → table reconstruction for scanned / low-text PDFs.

Uses pdf2image + Tesseract word boxes (local) or Textract TABLES (AWS).
Opt-in via ``StructuredExtractor(ocr_tables=True)`` — does not change
``TextExtractor`` defaults.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from goblintools.structured.models import TableBlock
from goblintools.structured.quality import prepare_rows_for_output, score_table
from goblintools.table_extractor import normalize_table_rows

logger = logging.getLogger(__name__)

Cell = Optional[str]
TableRows = List[List[Cell]]

_MIN_TEXT_CHARS_PER_PAGE = 40
_Y_TOLERANCE_RATIO = 0.015  # relative to page height for row clustering


def pdf_text_density(pdf_path: str, *, max_pages: Optional[int] = None) -> Tuple[int, int, float]:
    """Return ``(pages, total_chars, chars_per_page)`` via pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return 0, 0, 0.0
    try:
        reader = PdfReader(pdf_path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("pdf_text_density failed for %s: %s", pdf_path, exc)
        return 0, 0, 0.0
    n = len(reader.pages)
    limit = n if max_pages is None else min(n, max_pages)
    total = 0
    for i in range(limit):
        try:
            total += len((reader.pages[i].extract_text() or "").strip())
        except Exception:  # noqa: BLE001
            continue
    cpp = total / limit if limit else 0.0
    return limit, total, cpp


def is_low_text_pdf(pdf_path: str, *, max_pages: Optional[int] = None) -> bool:
    """True when average text per page looks like a scan / image PDF."""
    pages, _total, cpp = pdf_text_density(pdf_path, max_pages=max_pages)
    if pages == 0:
        return True
    return cpp < _MIN_TEXT_CHARS_PER_PAGE


def _cluster_rows(
    words: Sequence[Dict[str, Any]], page_height: float,
) -> List[List[Dict[str, Any]]]:
    """Group OCR words into rows by vertical position."""
    if not words:
        return []
    tol = max(8.0, page_height * _Y_TOLERANCE_RATIO)
    ordered = sorted(words, key=lambda w: (w["top"], w["left"]))
    rows: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_top: Optional[float] = None
    for w in ordered:
        if current_top is None or abs(w["top"] - current_top) <= tol:
            current.append(w)
            if current_top is None:
                current_top = float(w["top"])
            else:
                current_top = (current_top + float(w["top"])) / 2.0
        else:
            rows.append(sorted(current, key=lambda x: x["left"]))
            current = [w]
            current_top = float(w["top"])
    if current:
        rows.append(sorted(current, key=lambda x: x["left"]))
    return rows


def _infer_column_edges(row_words: Sequence[Sequence[Dict[str, Any]]], n_hint: int = 0) -> List[float]:
    """Infer vertical column split points from word left edges."""
    lefts: List[float] = []
    for row in row_words:
        for w in row:
            lefts.append(float(w["left"]))
    if not lefts:
        return [0.0]
    lefts.sort()
    # Gap clustering: large gaps between consecutive left edges → new column.
    gaps: List[Tuple[float, float]] = []
    for a, b in zip(lefts, lefts[1:]):
        gaps.append((b - a, (a + b) / 2.0))
    gaps.sort(reverse=True)
    # Keep up to 7 largest gaps that are significant.
    splits = [0.0]
    for gap, mid in gaps[:8]:
        if gap < 25:
            break
        splits.append(mid)
    splits = sorted(set(splits))
    if n_hint and len(splits) > n_hint:
        splits = splits[:n_hint]
    return splits or [0.0]


def _words_to_matrix(words: Sequence[Dict[str, Any]], page_height: float) -> TableRows:
    row_groups = _cluster_rows(words, page_height)
    if len(row_groups) < 2:
        return []
    edges = _infer_column_edges(row_groups)
    n_cols = max(2, len(edges))

    def col_index(left: float) -> int:
        idx = 0
        for i, edge in enumerate(edges):
            if left >= edge:
                idx = i
        return min(idx, n_cols - 1)

    matrix: TableRows = []
    for group in row_groups:
        cells = [""] * n_cols
        for w in group:
            ci = col_index(float(w["left"]))
            text = str(w.get("text") or "").strip()
            if not text:
                continue
            cells[ci] = f"{cells[ci]} {text}".strip() if cells[ci] else text
        if any(cells):
            matrix.append([c or None for c in cells])
    return matrix


def _tesseract_page_table(image: Any) -> TableRows:
    """Build a table matrix from one page image via Tesseract word boxes."""
    import numpy as np
    import pytesseract

    arr = np.array(image)
    height = arr.shape[0]
    data = pytesseract.image_to_data(arr, lang="por+eng", output_type=pytesseract.Output.DICT)
    words: List[Dict[str, Any]] = []
    n = len(data.get("text") or [])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if not text or conf < 30:
            continue
        words.append(
            {
                "text": text,
                "left": float(data["left"][i]),
                "top": float(data["top"][i]),
                "width": float(data["width"][i]),
                "height": float(data["height"][i]),
            }
        )
    return _words_to_matrix(words, float(height))


def _textract_page_tables(image: Any, ocr_processor: Any) -> List[TableRows]:
    """Extract TABLE blocks via Textract AnalyzeDocument when AWS is active."""
    import cv2
    import numpy as np

    if not getattr(ocr_processor, "use_aws", False):
        return []
    client = ocr_processor.textract_client
    if client is None:
        return []
    arr = np.array(image)
    if len(arr.shape) == 2:
        bgr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    else:
        bgr = arr
    _, encoded = cv2.imencode(".jpg", bgr)
    try:
        response = client.analyze_document(
            Document={"Bytes": encoded.tobytes()},
            FeatureTypes=["TABLES"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Textract TABLES failed: %s", exc)
        return []

    blocks = {b["Id"]: b for b in response.get("Blocks") or []}
    tables: List[TableRows] = []
    for block in response.get("Blocks") or []:
        if block.get("BlockType") != "TABLE":
            continue
        cells_by_pos: Dict[Tuple[int, int], str] = {}
        max_r, max_c = 0, 0
        for rel in block.get("Relationships") or []:
            if rel.get("Type") != "CHILD":
                continue
            for cid in rel.get("Ids") or []:
                cell = blocks.get(cid)
                if not cell or cell.get("BlockType") != "CELL":
                    continue
                row_i = int(cell.get("RowIndex") or 1)
                col_i = int(cell.get("ColumnIndex") or 1)
                max_r = max(max_r, row_i)
                max_c = max(max_c, col_i)
                texts: List[str] = []
                for crel in cell.get("Relationships") or []:
                    if crel.get("Type") != "CHILD":
                        continue
                    for wid in crel.get("Ids") or []:
                        wb = blocks.get(wid)
                        if wb and wb.get("BlockType") == "WORD":
                            texts.append(wb.get("Text") or "")
                cells_by_pos[(row_i, col_i)] = " ".join(texts).strip()
        if max_r < 2 or max_c < 2:
            continue
        matrix: TableRows = []
        for r in range(1, max_r + 1):
            matrix.append(
                [cells_by_pos.get((r, c)) or None for c in range(1, max_c + 1)]
            )
        tables.append(matrix)
    return tables


def extract_ocr_table_blocks(
    pdf_path: str,
    *,
    max_pages: Optional[int] = None,
    ocr_processor: Any = None,
    force: bool = False,
) -> List[TableBlock]:
    """Rasterize PDF pages and rebuild tables via OCR.

    Skips when the PDF already has a dense text layer unless ``force=True``.
    """
    if not force and not is_low_text_pdf(pdf_path, max_pages=max_pages):
        return []

    try:
        from pdf2image import convert_from_path
    except ImportError:
        logger.warning("pdf2image not available — OCR tables skipped for %s", pdf_path)
        return []

    try:
        kwargs: Dict[str, Any] = {}
        if max_pages is not None:
            kwargs["last_page"] = max_pages
        images = convert_from_path(pdf_path, dpi=200, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR rasterize failed for %s: %s", pdf_path, exc)
        return []

    blocks: List[TableBlock] = []
    idx = 0
    for page_i, image in enumerate(images, start=1):
        matrices: List[TableRows] = []
        if ocr_processor is not None and getattr(ocr_processor, "use_aws", False):
            matrices = _textract_page_tables(image, ocr_processor)
        if not matrices:
            try:
                matrix = _tesseract_page_table(image)
                if matrix:
                    matrices = [matrix]
            except Exception as exc:  # noqa: BLE001
                logger.debug("Tesseract table failed page %d: %s", page_i, exc)
                continue
        for matrix in matrices:
            rows = prepare_rows_for_output(matrix)
            rows = normalize_table_rows(rows)
            rows = prepare_rows_for_output(rows)
            quality = score_table(rows)
            if not quality.meaningful and quality.n_data_rows < 3:
                continue
            blocks.append(
                TableBlock(
                    page=page_i,
                    index=idx,
                    source="ocr",
                    sheet=None,
                    rows=rows,
                    quality=quality,
                )
            )
            idx += 1
    return blocks
