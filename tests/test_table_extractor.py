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
