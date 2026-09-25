"""Tests for extract_from_bytes (bytes-in, text + reports out)."""
import io
import zipfile
from unittest.mock import patch

import pytest
from PIL import Image

import goblintools
from goblintools import (
    ArchiveLimits,
    BytesExtractionResult,
    ExtractionReport,
    GoblinConfig,
    extract_from_bytes,
)
from goblintools.extraction_report import PageExtraction


def _pdf_bytes(text):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, text)
    pdf.save()
    return buffer.getvalue()


def _docx_bytes(text):
    import docx

    document = docx.Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buffer.getvalue()


def test_pdf_bytes_return_text_and_a_report():
    """A native PDF given as bytes comes back as text with its extraction report."""
    result = extract_from_bytes(_pdf_bytes("EDITAL DE LICITACAO"), "edital.pdf")

    assert "EDITAL DE LICITACAO" in result.text
    assert result.reports
    assert result.overall_status == "clean"


def test_docx_bytes_return_text():
    """DOCX bytes are parsed like a DOCX file."""
    result = extract_from_bytes(_docx_bytes("Declaração de habilitação"), "declaracao.docx")

    assert "Declaração de habilitação" in result.text


@patch("goblintools.ocr_parser.OCRProcessor.extract_text_from_image", return_value="ATESTADO")
def test_image_bytes_are_ocrd_when_enabled(_ocr):
    """PNG bytes go through OCR when ocr_images is on."""
    result = extract_from_bytes(_png_bytes(), "atestado.png", ocr_handler=True, ocr_images=True)

    assert "ATESTADO" in result.text


@patch("goblintools.ocr_parser.OCRProcessor.extract_text_from_image", return_value="FOTO")
def test_zip_with_mixed_members_combines_text_and_reports(_ocr):
    """A ZIP of PDF + DOCX + image yields every member's text and one report per member."""
    data = _zip_bytes([
        ("edital.pdf", _pdf_bytes("EDITAL UM")),
        ("anexo.docx", _docx_bytes("ANEXO DOIS")),
        ("foto.png", _png_bytes()),
    ])

    result = extract_from_bytes(data, "pacote.zip", ocr_handler=True, ocr_images=True)

    assert "EDITAL UM" in result.text
    assert "ANEXO DOIS" in result.text
    assert "FOTO" in result.text
    assert {"edital.pdf", "foto.png"} <= set(result.reports)


def test_zip_over_a_tight_limit_reports_skipped_members():
    """Members beyond the configured caps are listed in skipped, never raised."""
    config = GoblinConfig(archive_limits=ArchiveLimits(max_member_bytes=1000))
    data = _zip_bytes([("small.txt", b"texto pequeno"), ("big.txt", b"x" * 5000)])

    result = extract_from_bytes(data, "pacote.zip", config=config)

    assert "texto pequeno" in result.text
    assert any("big.txt" in reason for reason in result.skipped)


def test_empty_bytes_return_an_empty_result():
    """No bytes, nothing to do."""
    result = extract_from_bytes(b"", "vazio.pdf")

    assert result == BytesExtractionResult()


def test_bytes_over_max_file_size_are_skipped():
    """Inputs larger than max_file_size are refused with a reason."""
    config = GoblinConfig(max_file_size=10)

    result = extract_from_bytes(b"x" * 11, "grande.txt", config=config)

    assert result.text == ""
    assert any("max_file_size" in reason for reason in result.skipped)


@pytest.mark.parametrize("filename", ["../../edital.pdf", "", "..", "C:\\temp\\edital.pdf"])
def test_hostile_or_empty_filenames_are_sanitised(filename):
    """Path-like names never escape the temp dir and still extract."""
    result = extract_from_bytes(_pdf_bytes("CONTEUDO"), filename)

    assert "CONTEUDO" in result.text


def test_random_bytes_return_empty_without_raising():
    """Unknown content yields empty text, not an exception."""
    result = extract_from_bytes(b"\x00\x01garbage\xff" * 10, "arquivo.bin")

    assert result.text == ""


def test_overall_status_is_the_worst_report():
    """One corrupt member makes the whole result corrupt-or-partial, never clean."""
    clean = ExtractionReport(path="a.pdf", pages=[PageExtraction(index=0)])
    corrupt = ExtractionReport(path="b.pdf", overall_status="corrupt_unrecoverable")

    result = BytesExtractionResult(reports={"a.pdf": clean, "b.pdf": corrupt})

    assert result.overall_status == "corrupt_unrecoverable"


def test_new_names_are_exported():
    """The 0.12.0 public API is re-exported from the package root."""
    for name in ("extract_from_bytes", "BytesExtractionResult", "ArchiveLimits", "ExtractionBudget"):
        assert name in goblintools.__all__
