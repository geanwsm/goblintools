"""Unit tests for structured quality gates and HTML render."""

from goblintools.structured.quality import (
    clean_cell_text,
    drop_footer_rows,
    first_row_is_header,
    has_itemish_header,
    prepare_rows_for_output,
    score_table,
)
from goblintools.structured.render import table_to_html, to_full_md
from goblintools.structured.models import StructuredDocument, TableBlock, TableQuality


def test_clean_cell_split_word_and_number():
    assert clean_cell_text("ESTIMATIV A") == "ESTIMATIVA"
    assert "99.068,71" in clean_cell_text("R$99.068,7 1")


def test_drop_footer_rows():
    rows = [
        ["ITEM", "DESCRIÇÃO", "QTD", "VALOR"],
        ["1.", "Serviço", "12", "10"],
        ["VALOR TOTAL R$ 100", "", "", ""],
        ["Atenção: seguir esta ordem", "", "", ""],
    ]
    out = drop_footer_rows(rows)
    assert len(out) == 2
    assert out[1][0] == "1."


def test_first_row_is_header_false_for_data():
    assert first_row_is_header([["1.", "Serviço", "12"]]) is False
    assert first_row_is_header([["ITEM", "DESCRIÇÃO", "QTD"]]) is True


def test_has_itemish_header():
    assert has_itemish_header(
        [["ITEM", "DESCRIÇÃO", "QTD", "UND"], ["1", "x", "10", "UN"]]
    )
    assert has_itemish_header(
        [["Código", "Serviço", "Unid", "Qtd"], ["1", "Escavação", "M3", "12"]]
    )
    assert has_itemish_header(
        [["LOTE", "DESCRIÇÃO DETALHADA", "VALOR"], ["1", "Caminhao", "1000"]]
    )
    assert not has_itemish_header([["A", "B", "C"], ["1", "2", "3"]])


def test_score_ok_for_leilao_lote_desc():
    rows = [
        ["LOTE", "DESCRIÇÃO", "VALOR MÍNIMO"],
        ["1", "Veiculo utilitario placa ABC1D23", "R$ 10.000,00"],
        ["2", "Motocicleta 150cc placa XYZ9A88", "R$ 3.000,00"],
        ["3", "Caminhao basculante placa QWE4R56", "R$ 40.000,00"],
    ]
    q = score_table(rows)
    assert q.has_itemish_header
    assert q.ok_for_items
    assert q.has_usable_tables


def test_score_ok_for_dense_headerless_obra():
    rows = [
        [f"7.{i}", f"ED-{i}", f"ABRAÇADEIRA TIPO D DETALHADA ITEM {i}", "UN", str(10 + i)]
        for i in range(1, 12)
    ]
    q = score_table(rows)
    assert q.ok_for_items
    assert q.n_data_rows >= 8


def test_score_ok_for_items():
    rows = [
        ["ITEM", "DESCRIÇÃO", "UND", "QUANT", "VALOR UNT.", "VALOR TOTAL"],
        ["1.", "Serviço clínico", "SERV", "12", "R$ 16.511,45", "R$ 198.137,40"],
        ["2.", "Peças", "EST", "01", "R$ 99.068,71", "R$ 99.068,71"],
    ]
    q = score_table(rows)
    assert q.has_itemish_header
    assert q.n_data_rows == 2
    assert q.qty_parse_rate >= 0.5
    assert q.value_parse_rate >= 0.5
    assert q.ok_for_items


def test_score_rejects_bare_currency_values():
    rows = [
        ["ITEM", "DESCRIÇÃO", "UND", "QUANT", "VALOR UNT.", "VALOR TOTAL"],
        ["1.", "Serviço clínico", "SERV", "12", "R$", "R$"],
        ["2.", "Peças", "EST", "01", "R$", "R$"],
    ]
    q = score_table(rows)
    assert q.has_itemish_header
    assert q.has_value_column
    assert q.value_parse_rate < 0.5
    assert not q.ok_for_items


def test_table_to_html_contains_table_and_cells():
    html = table_to_html(
        [["ITEM", "DESCRIÇÃO", "QTD"], ["1", "Notebook", "10"]]
    )
    assert html.startswith("<table>")
    assert "<td>ITEM</td>" in html
    assert "<td>Notebook</td>" in html
    assert html.endswith("</table>")


def test_to_full_md_includes_comment_and_html():
    quality = TableQuality(
        meaningful=True,
        has_itemish_header=True,
        n_data_rows=1,
        qty_parse_rate=1.0,
        value_parse_rate=1.0,
        ok_for_items=True,
    )
    doc = StructuredDocument(
        path="x.pdf",
        tables=[
            TableBlock(
                index=0,
                source="pdf",
                page=26,
                rows=[["ITEM", "DESCRIÇÃO", "QTD"], ["1", "A", "2"]],
                quality=quality,
            )
        ],
        prose="Introdução",
        ok_for_items=True,
    )
    md = to_full_md(doc)
    assert "Introdução" in md
    assert "<!-- table source=pdf page=26 index=0 -->" in md
    assert "<table>" in md


def test_prepare_rows_for_output_pipeline():
    rows = [
        ["ITEM", "DESCRIÇÃO", "QTD"],
        ["1.", "Serviço", "12"],
        ["VALOR TOTAL R$ 1", "", ""],
    ]
    out = prepare_rows_for_output(rows)
    assert len(out) == 2
