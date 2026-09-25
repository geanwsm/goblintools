"""High-level bytes-in API: raw bytes (+ original filename) to text and reports."""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from goblintools.config import GoblinConfig
from goblintools.extraction_report import (
    OVERALL_CLEAN,
    OVERALL_CORRUPT_UNRECOVERABLE,
    OVERALL_PARTIALLY_RECOVERED,
    ExtractionReport,
)
from goblintools.file_handling import ExtractionBudget, FileManager
from goblintools.parser import TextExtractor

logger = logging.getLogger(__name__)

_SEVERITY = {
    OVERALL_CLEAN: 0,
    OVERALL_PARTIALLY_RECOVERED: 1,
    OVERALL_CORRUPT_UNRECOVERABLE: 2,
}


@dataclass
class BytesExtractionResult:
    """Outcome of :func:`extract_from_bytes`.

    ``reports`` is keyed like the ``file_path_pwd`` tags (member path for
    archives, the sanitised filename otherwise); ``skipped`` lists what the
    archive limits or size cap left out, with the reason.
    """

    text: str = ""
    reports: Dict[str, ExtractionReport] = field(default_factory=dict)
    skipped: List[str] = field(default_factory=list)

    @property
    def overall_status(self) -> str:
        """Worst ``overall_status`` among the reports (``clean`` when there are none)."""
        worst = OVERALL_CLEAN
        for report in self.reports.values():
            if _SEVERITY.get(report.overall_status, 0) > _SEVERITY[worst]:
                worst = report.overall_status
        return worst


def _safe_filename(filename: Optional[str]) -> str:
    """Basename only: the caller's name is kept for extension/tagging, never as a path."""
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    return name if name not in ("", ".", "..") else "document"


def extract_from_bytes(
    data: bytes,
    filename: str,
    *,
    config: Optional[GoblinConfig] = None,
    ocr_handler: bool = False,
    ocr_images: bool = False,
    suppress_warnings: Optional[bool] = None,
) -> BytesExtractionResult:
    """Extract text from in-memory bytes (document, image or archive).

    Writes to a private temp dir, expands archives under ``config.archive_limits``
    and reads every member with a fresh :class:`TextExtractor` (safe to call from
    several threads). OCR settings come from ``config.ocr``. Never raises: errors
    are logged and yield an empty or partial result.
    """
    result = BytesExtractionResult()
    if not data:
        return result

    config = config or GoblinConfig.default()
    name = _safe_filename(filename)
    if len(data) > config.max_file_size:
        result.skipped.append(f"{name}: larger than max_file_size ({config.max_file_size} bytes)")
        return result

    budget = ExtractionBudget(config.archive_limits)
    try:
        extractor = TextExtractor(
            ocr_handler=ocr_handler,
            ocr_images=ocr_images,
            config=config,
            suppress_warnings=suppress_warnings,
        )
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = os.path.join(tmp, "in")
            output_dir = os.path.join(tmp, "out")
            os.makedirs(source_dir)
            source = os.path.join(source_dir, name)
            with open(source, "wb") as f:
                f.write(data)

            if FileManager.extract_files_recursive(source, output_dir, budget=budget):
                result.text = extractor.extract_from_folder(output_dir)
                result.reports = dict(extractor.last_extraction_reports)
            else:
                result.text = extractor.extract_from_file(source, display_path=name)
                if extractor.last_extraction_report is not None:
                    result.reports[name] = extractor.last_extraction_report
    except Exception as e:
        logger.error(f"Error extracting text from bytes ({name}): {type(e).__name__}: {e}")

    result.skipped.extend(budget.skipped)
    return result
