"""Tests for PDF table extraction helpers and TextExtractor opt-in."""

import os
import tempfile

import pytest
from pypdf import PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib import colors

from goblintools import TextExtractor, table_to_markdown, extract_pdf_tables
from goblintools.table_extractor import (
    format_tables_for_page,
    is_meaningful_table,
    merge_continuation_rows,
    normalize_table_rows,
    drop_empty_rows,
)


def test_table_to_markdown_basic():
    md = table_to_markdown(
        [
            ["Item", "Qtd"],
            ["Notebook", "10"],
            [None, ""],
        ]
    )
    assert md.startswith("| Item | Qtd |")
    assert "| --- | --- |" in md
    assert "| Notebook | 10 |" in md
    assert "|  |  |" in md


def test_table_to_markdown_escapes_pipes():
    md = table_to_markdown([["A|B", "C"], ["1", "2"]])
    assert "A\\|B" in md


def test_table_to_markdown_empty():
    assert table_to_markdown([]) == ""


def test_format_tables_for_page_marker():
    block = format_tables_for_page(
        [{"page": 3, "index": 0, "rows": [["H1", "H2"], ["a", "b"]]}],
        page=3,
    )
    assert "<!-- table page=3 index=0 -->" in block
    assert "| H1 | H2 |" in block


def _make_pdf_with_table(path: str) -> None:
    doc = SimpleDocTemplate(path, pagesize=letter)
    data = [
        ["Item", "Descricao", "Qtd"],
        ["1", "Notebook i7", "10"],
        ["2", "Mouse", "20"],
    ]
    table = Table(data, colWidths=[50, 200, 50])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]
        )
    )
    doc.build([table])


def _make_plain_pdf(path: str, text: str = "Hello plain PDF") -> None:
    c = canvas.Canvas(path, pagesize=letter)
    c.drawString(72, 720, text)
    c.save()


@pytest.fixture
def pdf_with_table():
    try:
        import reportlab  # noqa: F401
    except ImportError:
        pytest.skip("reportlab required to generate table PDF fixture")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "table.pdf")
        _make_pdf_with_table(path)
        yield path


@pytest.fixture
def plain_pdf():
    try:
        import reportlab  # noqa: F401
    except ImportError:
        # Fallback: empty-ish PDF via pypdf only (no table)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with open(path, "wb") as out:
            writer.write(out)
        try:
            yield path
        finally:
            os.unlink(path)
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "plain.pdf")
        _make_plain_pdf(path)
        yield path


def test_extract_tables_false_unchanged(plain_pdf):
    off = TextExtractor(extract_tables=False).extract_from_file(plain_pdf)
    on_ctor = TextExtractor()  # default False
    default = on_ctor.extract_from_file(plain_pdf)
    assert off == default
    assert "<!-- table page=" not in off


def test_extract_tables_true_embeds_markdown(pdf_with_table):
    pytest.importorskip("pdfplumber")
    result = TextExtractor(extract_tables=True).extract_from_file(pdf_with_table)
    assert "file_path_pwd:" in result
    tables = extract_pdf_tables(pdf_with_table)
    if not tables:
        pytest.skip("pdfplumber found no tables in generated fixture")
    assert "<!-- table page=" in result
    assert "|" in result


def test_extract_tables_from_pdf_helper(pdf_with_table):
    pytest.importorskip("pdfplumber")
    tables = TextExtractor().extract_tables_from_pdf(pdf_with_table)
    if not tables:
        pytest.skip("pdfplumber found no tables in generated fixture")
    assert tables[0]["page"] == 1
    assert "rows" in tables[0]


def test_unsupported_table_format():
    with pytest.raises(ValueError, match="table_format"):
        TextExtractor(table_format="json")


def test_is_meaningful_table_rejects_single_column():
    assert not is_meaningful_table([["só texto"], ["mais texto"], ["ainda"]])


def test_is_meaningful_table_accepts_grid():
    assert is_meaningful_table(
        [
            ["Item", "Qtd", "Valor"],
            ["1", "10", "100"],
            ["2", "5", "50"],
        ]
    )


def test_is_meaningful_table_rejects_word_split_prose():
    rows = [
        ["OB", "JETO: Co", "ntratação de empresa especializadae"],
        ["em construção civil parae", "xecução dos", "serviços remanescentes"],
        ["e complementares dae", "obra dee", "dificação do estacionamento"],
        ["ampliação do 1º pavimento", ", reformae adequ", "ação das instalações da"],
    ]
    assert not is_meaningful_table(rows)


def test_is_meaningful_table_keeps_grid_despite_small_sample_split():
    assert is_meaningful_table(
        [
            ["Item", "Qtd", "Valor"],
            ["1", "10", "100"],
        ]
    )


def test_drop_empty_rows():
    assert drop_empty_rows([["a", "b"], ["", None], ["c", "d"]]) == [
        ["a", "b"],
        ["c", "d"],
    ]


def test_merge_continuation_rows():
    rows = [
        ["1.", "Contratação", "SERV", "12"],
        ["", "de empresa especializada", "", ""],
        ["", "na área de saúde", "", ""],
        ["2.", "Peças", "EST", "01"],
    ]
    merged = merge_continuation_rows(rows)
    assert len(merged) == 2
    assert "empresa especializada" in (merged[0][1] or "")
    assert "área de saúde" in (merged[0][1] or "")
    assert merged[1][0] == "2."


def test_normalize_table_rows_pipeline():
    rows = [
        ["ITEM", "DESCRIÇÃO", "VALOR"],
        ["1.", "Serviço", "10"],
        ["", "complemento", ""],
        ["", "", ""],
    ]
    out = normalize_table_rows(rows)
    assert len(out) == 2
    assert out[0][0] == "ITEM"
    assert out[1][0] == "1."
    assert "complemento" in (out[1][1] or "")


def test_collapse_split_header_keeps_data():
    from goblintools.table_extractor import collapse_header_rows

    rows = [
        ["", "", "Unidade", "Marca", "", "Valor", ""],
        ["", "", "", "", "", "", "Total"],
        ["Item", "Especificação", "De Medida", "", "Quant.", "Unitário", ""],
        ["1.", "Notebook", "UN", "", "10", "100", "1000"],
    ]
    out = collapse_header_rows(rows)
    assert out[-1][0] == "1."
    assert "Item" in (out[0][0] or "")
    assert "Notebook" in (out[-1][1] or "")


def _make_blank_pdf(path, pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as out:
        writer.write(out)


def _record_page_events(monkeypatch, extraction=None):
    """Record extract/close per page, in order, to prove each page is released before the next."""
    from pdfplumber.page import Page
    import goblintools.table_extractor as table_extractor

    events = []
    original_close = Page.close
    original_extraction = table_extractor._extract_page_tables_dual

    def recording_close(self):
        events.append(("close", self.page_number))
        return original_close(self)

    def recording_extraction(page):
        events.append(("extract", page.page_number))
        return (extraction or original_extraction)(page)

    monkeypatch.setattr(Page, "close", recording_close)
    monkeypatch.setattr(table_extractor, "_extract_page_tables_dual", recording_extraction)
    return events


def test_extract_pdf_tables_closes_each_page_before_the_next(monkeypatch, tmp_path):
    pytest.importorskip("pdfplumber")
    path = str(tmp_path / "three_pages.pdf")
    _make_blank_pdf(path, 3)
    events = _record_page_events(monkeypatch)

    extract_pdf_tables(path)

    assert events[:6] == [
        ("extract", 1), ("close", 1),
        ("extract", 2), ("close", 2),
        ("extract", 3), ("close", 3),
    ]


def test_extract_pdf_tables_closes_page_when_extraction_fails(monkeypatch, tmp_path):
    pytest.importorskip("pdfplumber")
    path = str(tmp_path / "two_pages.pdf")
    _make_blank_pdf(path, 2)

    def failing_extraction(page):
        raise RuntimeError("broken page")

    events = _record_page_events(monkeypatch, extraction=failing_extraction)

    assert extract_pdf_tables(path) == []
    assert events[:4] == [("extract", 1), ("close", 1), ("extract", 2), ("close", 2)]


def _make_light_and_heavy_pdf(path):
    """Page 1: a few words. Page 2: many vector lines (a drawing-like page)."""
    c = canvas.Canvas(path, pagesize=letter)
    c.drawString(72, 720, "light page")
    c.showPage()
    for i in range(400):
        c.line(10, i, 600, i + 1)
    c.showPage()
    c.save()


def test_extract_pdf_tables_skips_page_over_content_limit(monkeypatch, tmp_path, caplog):
    pytest.importorskip("pdfplumber")
    path = str(tmp_path / "light_and_heavy.pdf")
    _make_light_and_heavy_pdf(path)
    events = _record_page_events(monkeypatch)

    with caplog.at_level("WARNING", logger="goblintools.table_extractor"):
        extract_pdf_tables(path, max_page_content_bytes=2000)

    assert ("extract", 1) in events
    assert ("extract", 2) not in events
    assert ("close", 2) in events
    assert "page 2" in caplog.text and "content" in caplog.text


def test_extract_pdf_tables_default_limit_keeps_small_pages(monkeypatch, tmp_path):
    pytest.importorskip("pdfplumber")
    path = str(tmp_path / "light_and_heavy.pdf")
    _make_light_and_heavy_pdf(path)
    events = _record_page_events(monkeypatch)

    extract_pdf_tables(path)

    assert ("extract", 1) in events and ("extract", 2) in events


def test_extract_pdf_tables_none_disables_content_limit(monkeypatch, tmp_path):
    pytest.importorskip("pdfplumber")
    path = str(tmp_path / "light_and_heavy.pdf")
    _make_light_and_heavy_pdf(path)
    events = _record_page_events(monkeypatch)

    extract_pdf_tables(path, max_page_content_bytes=None)

    assert ("extract", 2) in events


def test_default_page_content_limit_is_10_mb():
    from goblintools.table_extractor import DEFAULT_MAX_PAGE_CONTENT_BYTES

    assert DEFAULT_MAX_PAGE_CONTENT_BYTES == 10_000_000
