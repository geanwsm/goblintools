"""Provenance / confidence report for the most recent PDF text extraction."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

# per-page status
CLEAN = "clean"
RECOVERED = "recovered"
CORRUPT_UNRECOVERABLE = "corrupt_unrecoverable"

# overall status
OVERALL_CLEAN = "clean"
OVERALL_PARTIALLY_RECOVERED = "partially_recovered"
OVERALL_CORRUPT_UNRECOVERABLE = "corrupt_unrecoverable"


@dataclass
class PageExtraction:
    """How one PDF page's text was obtained and whether it looks trustworthy."""

    index: int                      # 0-based page index
    status: str = CLEAN
    engine: str = "pypdf"          # pypdf | pdfplumber | poppler | ocr | ""
    broken_before: bool = False    # text layer looked broken before recovery


@dataclass
class ExtractionReport:
    """Confidence summary for the last ``TextExtractor.extract_from_file`` call.

    Populated only for PDF inputs. Reflects **only the last call** on the
    extractor instance — do not share a ``TextExtractor`` across threads if you
    rely on this attribute.
    """

    path: str
    pages: List[PageExtraction] = field(default_factory=list)
    overall_status: str = OVERALL_CLEAN
    used_ocr: bool = False

    @property
    def is_clean(self) -> bool:
        return self.overall_status == OVERALL_CLEAN

    def recompute_overall(self) -> None:
        statuses = {p.status for p in self.pages}
        if not statuses:
            self.overall_status = OVERALL_CLEAN
        elif CORRUPT_UNRECOVERABLE in statuses:
            self.overall_status = (
                OVERALL_PARTIALLY_RECOVERED
                if statuses & {CLEAN, RECOVERED}
                else OVERALL_CORRUPT_UNRECOVERABLE
            )
        elif RECOVERED in statuses:
            self.overall_status = OVERALL_PARTIALLY_RECOVERED
        else:
            self.overall_status = OVERALL_CLEAN
        if any(p.engine == "ocr" for p in self.pages):
            self.used_ocr = True

    def demote_to_clean_if_fully_ocr_recovered(self) -> bool:
        """A fully scanned PDF that OCR re-read end to end is trustworthy, not
        ``partially_recovered``. If every non-clean page ended ``RECOVERED`` via
        ``ocr`` and none stayed ``CORRUPT_UNRECOVERABLE``, set ``overall_status``
        back to :data:`OVERALL_CLEAN` (``used_ocr`` still records that OCR ran).
        Returns ``True`` when the status was demoted."""
        if self.overall_status == OVERALL_CLEAN:
            return False
        non_clean = [p for p in self.pages if p.status != CLEAN]
        if not non_clean:
            return False
        if any(p.status == CORRUPT_UNRECOVERABLE for p in self.pages):
            return False
        if all(p.status == RECOVERED and p.engine == "ocr" for p in non_clean):
            self.overall_status = OVERALL_CLEAN
            return True
        return False
