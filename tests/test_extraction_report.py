"""Tests for ExtractionReport status roll-up."""
from goblintools.extraction_report import (
    CLEAN, CORRUPT_UNRECOVERABLE, RECOVERED,
    OVERALL_CLEAN, OVERALL_CORRUPT_UNRECOVERABLE, OVERALL_PARTIALLY_RECOVERED,
    ExtractionReport, PageExtraction,
)


def _report(*statuses):
    r = ExtractionReport(path="x.pdf")
    r.pages = [PageExtraction(index=i, status=s) for i, s in enumerate(statuses)]
    r.recompute_overall()
    return r


def test_all_clean():
    assert _report(CLEAN, CLEAN).overall_status == OVERALL_CLEAN


def test_some_recovered():
    assert _report(CLEAN, RECOVERED).overall_status == OVERALL_PARTIALLY_RECOVERED


def test_all_corrupt():
    assert _report(CORRUPT_UNRECOVERABLE, CORRUPT_UNRECOVERABLE).overall_status == OVERALL_CORRUPT_UNRECOVERABLE


def test_mixed_corrupt_and_clean_is_partial():
    assert _report(CLEAN, CORRUPT_UNRECOVERABLE).overall_status == OVERALL_PARTIALLY_RECOVERED


def test_is_clean_property():
    assert _report(CLEAN).is_clean is True
    assert _report(RECOVERED).is_clean is False


def test_demote_when_all_pages_ocr_recovered():
    """A fully scanned PDF re-read end to end by OCR is clean, not partial."""
    r = ExtractionReport(path="scan.pdf")
    r.pages = [PageExtraction(index=i, status=RECOVERED, engine="ocr") for i in range(3)]
    r.recompute_overall()
    assert r.overall_status == OVERALL_PARTIALLY_RECOVERED
    assert r.demote_to_clean_if_fully_ocr_recovered() is True
    assert r.overall_status == OVERALL_CLEAN
    assert r.used_ocr is True


def test_no_demote_when_a_page_stayed_corrupt():
    r = ExtractionReport(path="scan.pdf")
    r.pages = [
        PageExtraction(index=0, status=RECOVERED, engine="ocr"),
        PageExtraction(index=1, status=CORRUPT_UNRECOVERABLE),
    ]
    r.recompute_overall()
    assert r.demote_to_clean_if_fully_ocr_recovered() is False
    assert r.overall_status == OVERALL_PARTIALLY_RECOVERED


def test_no_demote_when_recovery_engine_is_not_ocr():
    r = ExtractionReport(path="x.pdf")
    r.pages = [
        PageExtraction(index=0, status=CLEAN),
        PageExtraction(index=1, status=RECOVERED, engine="poppler"),
    ]
    r.recompute_overall()
    assert r.demote_to_clean_if_fully_ocr_recovered() is False


def test_no_demote_when_already_clean():
    r = ExtractionReport(path="x.pdf")
    r.pages = [PageExtraction(index=0, status=CLEAN)]
    r.recompute_overall()
    assert r.demote_to_clean_if_fully_ocr_recovered() is False
