"""Tests for TextExtractor."""
import logging
import os
import tempfile

import pytest
from pypdf import PdfWriter

from goblintools import TextExtractor
from goblintools.file_handling import FileValidator


def test_extract_from_file_txt(sample_txt_file):
    """Test extraction from TXT file."""
    extractor = TextExtractor()
    result = extractor.extract_from_file(sample_txt_file)
    assert "file_path_pwd:" in result
    assert "Hello world" in result or "test file" in result


def test_extract_from_file_csv(sample_csv_file):
    """Test extraction from CSV file."""
    extractor = TextExtractor()
    result = extractor.extract_from_file(sample_csv_file)
    assert "file_path_pwd:" in result
    assert "col1" in result or "val1" in result


def test_extract_from_file_not_found():
    """Test extraction from non-existent file."""
    extractor = TextExtractor()
    result = extractor.extract_from_file("/nonexistent/file.txt")
    assert result == ""


def test_extract_from_file_unsupported_format():
    """Test extraction from unsupported format returns empty."""
    with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False) as f:
        path = f.name
    try:
        extractor = TextExtractor()
        result = extractor.extract_from_file(path)
        assert result == ""
    finally:
        os.unlink(path)


def test_add_parser():
    """Test custom parser registration."""
    extractor = TextExtractor()

    def custom_parser(path):
        return "custom content"

    extractor.add_parser('.custom', custom_parser)

    with tempfile.NamedTemporaryFile(suffix='.custom', delete=False) as f:
        path = f.name
    try:
        result = extractor.extract_from_file(path)
        assert "custom content" in result
        assert "file_path_pwd:" in result
    finally:
        os.unlink(path)


def test_extract_from_file_no_file_path_when_whitespace_only():
    """Corrupt/blank docs may yield only whitespace; do not emit file_path_pwd without real text."""
    extractor = TextExtractor()

    def whitespace_parser(path):
        return "   \n\t  "

    extractor.add_parser('.ws', whitespace_parser)
    with tempfile.NamedTemporaryFile(suffix='.ws', delete=False) as f:
        path = f.name
    try:
        assert extractor.extract_from_file(path) == ""
    finally:
        os.unlink(path)


def test_extract_from_file_no_file_path_when_only_zero_width_and_format_chars():
    """PyPDF on damaged PDFs can return ZWSP/format chars that str.strip() does not remove."""
    extractor = TextExtractor()

    def invisible_parser(path):
        return "\u200b\u200c\u2060\ufeff"

    extractor.add_parser('.inv', invisible_parser)
    with tempfile.NamedTemporaryFile(suffix='.inv', delete=False) as f:
        path = f.name
    try:
        assert extractor.extract_from_file(path) == ""
    finally:
        os.unlink(path)


def test_extract_from_file_extensionless_pdf(caplog):
    """PDF without extension: magic bytes route to PDF parser (not 'no parser' warning)."""
    fd, path = tempfile.mkstemp(suffix="")
    os.close(fd)
    try:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with open(path, "wb") as f:
            writer.write(f)
        assert FileValidator.detect_extension_from_magic(path) == ".pdf"
        extractor = TextExtractor()
        with caplog.at_level(logging.WARNING):
            result = extractor.extract_from_file(path)
        assert "No parser available" not in caplog.text
        # Blank page yields no extractable text — no file_path_pwd without content
        assert result == ""
    finally:
        os.unlink(path)


def test_extract_from_folder_uses_relative_path():
    """Test that file_path_pwd uses path relative to folder (as inside zip)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        edital_dir = os.path.join(tmpdir, "edital")
        os.makedirs(edital_dir)
        arquivo_path = os.path.join(edital_dir, "arquivo.txt")
        with open(arquivo_path, "w") as f:
            f.write("content from edital/arquivo")
        extractor = TextExtractor()
        result = extractor.extract_from_folder(tmpdir)
        assert 'file_path_pwd:"edital/arquivo.txt"' in result
        assert "content from edital/arquivo" in result


def test_pypdf_workarounds_idempotent():
    from goblintools.pypdf_workarounds import apply_pypdf_extraction_workarounds

    apply_pypdf_extraction_workarounds()
    apply_pypdf_extraction_workarounds()


def test_pypdf_workarounds_tolerates_int_cmap_and_bad_encoding():
    """On legacy pypdf, patched get_display_str must accept int cmap values."""
    from goblintools.pypdf_workarounds import (
        apply_pypdf_extraction_workarounds,
        _uses_legacy_rtl_api,
    )
    import pypdf._text_extraction as te
    import pytest

    apply_pypdf_extraction_workarounds(force=True)
    if not _uses_legacy_rtl_api():
        pytest.skip("legacy get_display_str patch not applied on modern pypdf")

    class BadFont:
        character_map = {"A": 65, "B": "B"}
        space_width = 250
        encoding = object()
        def text_width(self, x):
            return 10 * len(x)

    text, rtl, widths = te.get_display_str(
        "",
        [1, 0, 0, 1, 0, 0],
        [1, 0, 0, 1, 0, 0],
        None,
        BadFont(),
        "AB",
        12,
        False,
        None,
    )
    assert "A" in text and "B" in text
    assert widths > 0

    out, is_str = te.get_text_operands(
        [b"AB"],
        [1, 0, 0, 1, 0, 0],
        [1, 0, 0, 1, 0, 0],
        BadFont(),
        (0, 90, 180, 270),
    )
    assert is_str is False
    assert isinstance(out, str)


def test_pypdf_workarounds_modern_keeps_stock_display_str():
    """On pypdf 6.15+, workarounds must not break stock extraction with str RTL consts."""
    from goblintools.pypdf_workarounds import (
        apply_pypdf_extraction_workarounds,
        _uses_legacy_rtl_api,
    )
    import pypdf._text_extraction as te
    import pytest

    apply_pypdf_extraction_workarounds(force=True)
    if _uses_legacy_rtl_api():
        pytest.skip("modern path not used on this pypdf")

    assert isinstance(te.CUSTOM_RTL_MIN, str)
    # Stock function still present and callable with a minimal fake font
    class Font:
        character_map = {}
        space_width = 250
        space_char = " "
        def get_text_width(self, text):
            return 10 * len(text)

    text, rtl, widths = te.get_display_str(
        "",
        [1, 0, 0, 1, 0, 0],
        [1, 0, 0, 1, 0, 0],
        None,
        Font(),
        "AB",
        12,
        False,
        None,
    )
    assert text == "AB"
    assert widths > 0


def test_merge_page_texts_prefers_primary_then_secondary():
    """_merge_page_texts keeps primary when present and fills blanks from secondary."""
    extractor = TextExtractor()
    merged, still_empty = extractor._merge_page_texts(["a", "", "c"], ["x", "b", ""])
    assert merged == ["a", "b", "c"]
    assert still_empty == set()


def test_validate_installation():
    """Test validate_installation returns dict with tesseract key."""
    extractor = TextExtractor()
    result = extractor.validate_installation()
    assert isinstance(result, dict)
    assert 'tesseract' in result
    assert result['tesseract'] in (True, False)
