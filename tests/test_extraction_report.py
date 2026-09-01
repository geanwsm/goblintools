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
