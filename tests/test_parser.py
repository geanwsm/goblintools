"""Tests for TextExtractor."""
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pypdf import PdfWriter

from goblintools import TextExtractor
from goblintools.file_handling import FileValidator
from goblintools.parser import (
    _SUBST_CIPHER_MIDCAP_HARD,
    _is_cipher_token,
    _letter_ratio,
    _looks_like_encoded_glyphs,
    _looks_like_glued_words,
    _looks_like_substitution_cipher,
    _normalize_for_detection,
    _substitution_cipher_score,
    _text_layer_looks_broken,
)

_FIX = Path(__file__).parent / "fixtures" / "text_layer"


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


def test_looks_like_encoded_glyphs_detects_glyph_name_tokens():
    """pypdf's raw glyph-name fallback (e.g. "/143 /j0") is flagged as garbage.

    Seen in production: a font /Differences array with non-standard glyph names
    and no /ToUnicode CMap makes pypdf emit the raw PDF name object per character
    instead of the real text (bidding 19286810's edital1787272733.pdf).
    """
    garbage = " ".join(f"/{i}" for i in range(20))
    assert _looks_like_encoded_glyphs(garbage) is True


def test_looks_like_encoded_glyphs_detects_cid_markers():
    """pdfminer/pdfplumber's "(cid:143)" fallback notation is flagged as garbage
    (same root cause as the /name tokens, different library's raw-code notation)."""
    garbage = "".join(f"(cid:{i})" for i in range(20))
    assert _looks_like_encoded_glyphs(garbage) is True


def test_looks_like_encoded_glyphs_detects_low_letter_ratio():
    """Byte-substitution garbage (glyph codes mapped into punctuation/digit ASCII
    ranges) is flagged via overall letter density even without any /name or
    (cid:) token — some pdfminer fallback paths produce this instead of raising."""
    garbage = "#$%&'()*+,-./0123456789:;<=>?@" * 3
    assert _looks_like_encoded_glyphs(garbage) is True


def test_looks_like_encoded_glyphs_false_for_real_text():
    """Ordinary prose is never flagged, even with digits/punctuation mixed in."""
    real = (
        "AVISO DE DISPENSA ELETRONICA DE LICITACAO. PROCESSO ADMINISTRATIVO "
        "no 102/2026, objeto: aquisicao de material de escritorio para a "
        "prefeitura municipal, valor estimado R$ 12.345,67, prazo de entrega "
        "de 30 dias uteis contados da assinatura do contrato."
    )
    assert _looks_like_encoded_glyphs(real) is False


def test_looks_like_encoded_glyphs_false_for_short_text():
    """Below the minimum length/token thresholds, don't flag — ratios are too
    noisy on short strings to be a reliable signal."""
    assert _looks_like_encoded_glyphs("/1 /2 /3") is False
    assert _looks_like_encoded_glyphs("") is False


def test_letter_ratio_all_letters():
    assert _letter_ratio("abcde") == 1.0


def test_letter_ratio_no_letters():
    assert _letter_ratio("12345") == 0.0


def test_letter_ratio_empty_text():
    assert _letter_ratio("") == 0.0


def test_recover_glyph_garbage_pages_uses_pdfplumber_when_it_recovers_real_text():
    """When pdfplumber's own extraction of a garbage page is real text, prefer it
    over pypdf's glyph-name output and skip OCR entirely (cheaper than OCR, and
    pdfplumber is already a goblintools dependency)."""
    extractor = TextExtractor()
    garbage_page = " ".join(f"/{i}" for i in range(20))
    with patch.object(
        extractor,
        "_pdfplumber_page_text",
        return_value={0: "Texto real recuperado do pdfplumber."},
    ):
        result = extractor._recover_glyph_garbage_pages("fake.pdf", [garbage_page], set())
    assert result[0] == "Texto real recuperado do pdfplumber."


def test_recover_glyph_garbage_pages_falls_back_to_ocr_when_pdfplumber_also_garbled():
    """When pdfplumber's output is itself garbled (e.g. (cid:N) markers, the same
    /ToUnicode-less font defeating a second library), fall through to the
    configured OCR handler for that specific page."""
    extractor = TextExtractor()
    extractor.ocr_handler = MagicMock()
    extractor.ocr_handler.extract_text_from_pdf_page_indices.return_value = {
        0: "Texto real vindo do OCR."
    }
    garbage_page = " ".join(f"/{i}" for i in range(20))
    plumber_garbage = "".join(f"(cid:{i})" for i in range(20))
    with patch.object(extractor, "_pdfplumber_page_text", return_value={0: plumber_garbage}), \
         patch.object(extractor, "_poppler_page_text", return_value={0: plumber_garbage}):
        result = extractor._recover_glyph_garbage_pages("fake.pdf", [garbage_page], set())
    assert result[0] == "Texto real vindo do OCR."
    extractor.ocr_handler.extract_text_from_pdf_page_indices.assert_called_once_with(
        "fake.pdf", [0]
    )


def test_recover_glyph_garbage_pages_tries_poppler_between_plumber_and_ocr():
    """poppler pdftotext is a third independent engine tried before OCR."""
    extractor = TextExtractor()
    extractor.ocr_handler = MagicMock()
    garbage = " ".join(f"/{i}" for i in range(20))
    with patch.object(extractor, "_pdfplumber_page_text", return_value={0: garbage}), \
         patch.object(
             extractor,
             "_poppler_page_text",
             return_value={0: "Texto real recuperado pelo poppler nesta pagina."},
         ):
        result = extractor._recover_glyph_garbage_pages("fake.pdf", [garbage], set())
    assert result[0].startswith("Texto real recuperado pelo poppler")
    extractor.ocr_handler.extract_text_from_pdf_page_indices.assert_not_called()


def test_recover_glyph_garbage_pages_noop_when_no_garbage():
    """Pages with real text are left untouched; pdfplumber/OCR are never invoked."""
    extractor = TextExtractor()
    extractor.ocr_handler = MagicMock()
    page_texts = ["Texto normal de um edital de licitacao publica."]
    with patch.object(extractor, "_pdfplumber_page_text") as mock_plumber:
        result = extractor._recover_glyph_garbage_pages("fake.pdf", page_texts, set())
    mock_plumber.assert_not_called()
    extractor.ocr_handler.extract_text_from_pdf_page_indices.assert_not_called()
    assert result == page_texts


def test_recover_glyph_garbage_pages_skips_already_failed_indices():
    """Pages already recorded as pypdf read-failures (skip_indices) are left to
    the earlier OCR-on-failure path and not re-checked here."""
    extractor = TextExtractor()
    garbage_page = " ".join(f"/{i}" for i in range(20))
    with patch.object(extractor, "_pdfplumber_page_text") as mock_plumber:
        result = extractor._recover_glyph_garbage_pages("fake.pdf", [garbage_page], {0})
    mock_plumber.assert_not_called()
    assert result == [garbage_page]


# --- per-glyph substitution cipher detection ---------------------------------


def test_is_cipher_token():
    assert _is_cipher_token("soQdaJHP")
    assert _is_cipher_token("LICITAgAO")
    assert _is_cipher_token("automáƟca")        # ti -> Ɵ (U+019F) substitution
    assert not _is_cipher_token("PREGÃO")
    assert not _is_cipher_token("edital")
    assert not _is_cipher_token("CNPJ")
    assert not _is_cipher_token("kW")           # too short
    assert not _is_cipher_token("unRolo")       # unit glued to a real word (layout)
    assert not _is_cipher_token("kgChapa")


def test_is_cipher_token_excludes_alloy_codes():
    """AWS/ASME welding filler-metal codes (common in engineering tenders) are
    CamelCase but not corruption."""
    for code in ("ENiCrFe", "ERNiCrMo", "ENiCrCoMo", "ERNiCrCoMo", "ENiCu"):
        assert not _is_cipher_token(code)


def test_normalize_for_detection_folds_ligatures_and_strips_urls():
    """The cipher detector inspects NFKC-folded, URL-free text."""
    assert _normalize_for_detection("classiﬁcado") == "classificado"
    assert _normalize_for_detection("2º item") == "2o item"
    out = _normalize_for_detection("veja https://x.com/AbCdEf e www.y.com/Zz aqui")
    assert "http" not in out and "www." not in out
    assert out.split() == ["veja", "e", "aqui"]


def test_looks_like_glued_words_true_for_no_space_extraction():
    """A no-space PDF extraction concatenates real words into one CamelCase token."""
    for glued in (
        "TenhamcontrariadoaLegislaçãoetermosdopresente",
        "InstruçõesNormativas",
        "ModelodeProposta",
        "NotadaPropostaFinanceira",
        "MinutadoTermodeRegistrodePreços",
        "JacquelinedeSouzaMonteiro",
        "bateriasparareceptorGNSS",
    ):
        assert _looks_like_glued_words(glued) is True


def test_looks_like_glued_words_false_for_cipher_tokens():
    """A per-glyph cipher deforms a single word; it must not read as glued words —
    including substitutions that produce a capital accented vowel (``tempeÍatura``)."""
    for tok in (
        "contrataÉo", "coMPoslçAo", "rttumrcrpAL", "manHdo", "parHcipação",
        "tempeÍatura", "refeÍência", "EÍesentadas", "couvocnrónto",
        "idenHﬁcada", "santanadoaçaraU", "soQdaJHP", "LICITAgAO",
    ):
        assert _looks_like_glued_words(tok) is False


def test_substitution_cipher_ignores_no_space_extraction():
    """A table-of-contents / signature block extracted without spaces is not a cipher."""
    text = (
        "AnexoI ModelodeProposta AnexoII MinutadoTermodeRegistrodePreços "
        "AnexoIII ModelodePedidodeCompra AnexoIV ModelodoTermodeAdesão "
        "AnexoV JacquelinedeSouzaMonteiro SecretariaMunicipaldeMeioAmbiente "
        "NotadaPropostaFinanceira NotadaPropostaTécnica "
        "SerádesclassificadooProjetoquenãoatenderàsexigênciasdoTermo "
    ) * 12
    assert _looks_like_substitution_cipher(text) is False
    assert _substitution_cipher_score(text) < 0.035


def test_substitution_cipher_strips_price_research_url_tokens():
    """A Banco de Preços receipt: the noise tokens live inside a URL query string
    (bidding 19332500) and must be stripped before scoring."""
    text = (
        "Extrato de fontes utilizadas neste relatório de pesquisa de preços "
        "para composição do valor estimado da contratação conforme a "
        "Instrução Normativa aplicável ao presente processo administrativo. "
        "http://www.bancodeprecos.com.br/CertificadoAutenticidade?"
        "token=v6iHJuY%252f2NgclBvPbBa6v6i5phDA2CXDD1IfOl17n%252f8qHU8nPtm6WA "
    ) * 20
    assert _looks_like_substitution_cipher(text) is False


def test_substitution_cipher_ignores_repeated_jargon():
    """A window whose 'cipher-shaped' tokens are one identifier repeated verbatim
    (a per-glyph cipher never repeats a token) is not flagged."""
    text = ("Tabela de Referencia Categoria Codigo Descricao Item Quantidade "
            "Unidade Observacao AltoQi " * 30)
    assert _looks_like_substitution_cipher(text) is False
    assert _substitution_cipher_score(text) == 0.0


def test_substitution_cipher_engineering_welding_spec_not_flagged():
    """Real Petrobras welding spec: alloy codes + English process names, no cipher."""
    text = (
        "A soldagem de revestimento pelo processo FCAW com protecao gasosa e "
        "permitida somente com aprovacao previa. Os consumiveis ENiCrFe ERNiCrMo "
        "ENiCrCoMo ERNiFeCr e ENiMo devem atender a norma aplicavel. O gas de "
        "protecao no processo GMAW deve ser argonio puro ou argonio com CO2. "
    ) * 12
    assert _looks_like_substitution_cipher(text) is False


def test_substitution_cipher_flags_corrupted_edital():
    """anexo_1 of bidding 19317659: a 40-page edital whose font /Differences map is
    broken with no /ToUnicode. Text keeps word shape so the old detector missed it."""
    text = (_FIX / "cipher_edital.txt").read_text(encoding="utf-8")
    assert _substitution_cipher_score(text) >= _SUBST_CIPHER_MIDCAP_HARD
    assert _looks_like_substitution_cipher(text) is True
    assert _text_layer_looks_broken(text) is True


def test_substitution_cipher_flags_corrupted_gazette():
    """anexo_2 of the same bidding: a Diário Oficial page with the same failure mode."""
    text = (_FIX / "cipher_gazette.txt").read_text(encoding="utf-8")
    assert _looks_like_substitution_cipher(text) is True


@pytest.mark.parametrize(
    "name", ["clean_edital_1.txt", "clean_edital_2.txt", "clean_edital_3.txt"]
)
def test_substitution_cipher_false_for_clean_editais(name):
    """Real editais indexed from production must never be flagged."""
    text = (_FIX / name).read_text(encoding="utf-8")
    assert _substitution_cipher_score(text) < 0.02
    assert _looks_like_substitution_cipher(text) is False
    assert _text_layer_looks_broken(text) is False


def test_substitution_cipher_false_for_short_text():
    assert _looks_like_substitution_cipher("Contratação de empresa. Valor R$ 10,00.") is False


def test_substitution_cipher_never_raises_on_exotic_input():
    for junk in ("", "🙂🙂🙂", "\x00\x01\x02", "日本語のテキスト " * 50):
        assert _looks_like_substitution_cipher(junk) is False
        assert _substitution_cipher_score(junk) == 0.0


# --- poppler pdftotext recovery step ----------------------------------------


def test_poppler_page_text_missing_binary(monkeypatch, caplog):
    import goblintools.parser as p

    monkeypatch.setattr(p.shutil, "which", lambda _: None)
    extractor = TextExtractor()
    with caplog.at_level(logging.WARNING):
        out = extractor._poppler_page_text("x.pdf", [0, 1])
    assert out == {}
    assert "pdftotext" in caplog.text


def test_poppler_page_text_parses_stdout(monkeypatch):
    import goblintools.parser as p

    monkeypatch.setattr(p.shutil, "which", lambda _: "/usr/bin/pdftotext")

    class FakeProc:
        returncode = 0
        stdout = "Texto da página recuperado pelo poppler.".encode("utf-8")

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr(p.subprocess, "run", fake_run)
    out = TextExtractor()._poppler_page_text("edital.pdf", [2])
    assert out == {2: "Texto da página recuperado pelo poppler."}
    assert calls[0][0] == "/usr/bin/pdftotext"
    assert "--" in calls[0] and calls[0][calls[0].index("--") + 1] == "edital.pdf"
    assert "3" in calls[0]  # 0-based idx 2 -> page 3


def test_poppler_page_text_handles_subprocess_failure(monkeypatch, caplog):
    import goblintools.parser as p

    monkeypatch.setattr(p.shutil, "which", lambda _: "/usr/bin/pdftotext")

    def boom(cmd, **kw):
        raise p.subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(p.subprocess, "run", boom)
    with caplog.at_level(logging.WARNING):
        out = TextExtractor()._poppler_page_text("x.pdf", [0])
    assert out == {}


# --- ExtractionReport wiring -----------------------------------------------


def test_last_extraction_report_none_before_first_call():
    assert TextExtractor().last_extraction_report is None


def test_last_extraction_report_none_for_non_pdf(sample_txt_file):
    ex = TextExtractor()
    ex.extract_from_file(sample_txt_file)
    assert ex.last_extraction_report is None


def _write_pdf_lines(path, text):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=letter)
    y = 750
    for line in text.splitlines():
        c.drawString(40, y, line[:110])
        y -= 11
        if y < 40:
            c.showPage()
            y = 750
    c.save()


def test_report_clean_for_plain_pdf():
    pytest.importorskip("reportlab")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name
    try:
        _write_pdf_lines(
            path,
            "Contratacao de empresa para fornecimento de material de consumo. "
            "Valor total estimado R$ 10.000,00. Pregao eletronico do tipo menor preco "
            "regido pela Lei Federal numero 14.133 de 2021. A sessao publica sera "
            "conduzida pelo pregoeiro designado pela administracao municipal.",
        )
        ex = TextExtractor()
        ex.extract_from_file(path)
        assert ex.last_extraction_report is not None
        assert ex.last_extraction_report.overall_status == "clean"
        assert ex.last_extraction_report.is_clean
    finally:
        os.unlink(path)


def test_report_flags_corrupt_pdf_without_ocr(caplog):
    """A substitution-cipher PDF with no ocr_handler: text is still returned, but the
    report says the text layer is not trustworthy and a warning is logged."""
    pytest.importorskip("reportlab")
    cipher = (_FIX / "cipher_edital.txt").read_text(encoding="utf-8")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name
    try:
        _write_pdf_lines(path, cipher)
        ex = TextExtractor()  # no ocr_handler
        with caplog.at_level(logging.WARNING), \
             patch.object(ex, "_pdfplumber_page_text", return_value={}), \
             patch.object(ex, "_poppler_page_text", return_value={}):
            out = ex.extract_from_file(path)
        assert out != ""  # best-available text still returned (no breaking change)
        assert ex.last_extraction_report.overall_status in (
            "corrupt_unrecoverable",
            "partially_recovered",
        )
        assert "text-layer confidence" in caplog.text
    finally:
        os.unlink(path)


def test_recovery_is_improvement_rejects_cosmetic_swap():
    """A page flagged only by the cipher heuristic (not /143 garbage) must NOT be
    swapped for another engine's text of similar Portuguese density — that would
    corrupt a false-positive page. It is reported corrupt instead."""
    import goblintools.parser as p
    from goblintools.extraction_report import ExtractionReport, PageExtraction

    ex = TextExtractor()  # no ocr_handler
    original = "algum texto de qualidade duvidosa mas nao glyph garbage " * 8
    cosmetic = "outro texto de qualidade parecida sem ganho real de dicionario " * 8
    report = ExtractionReport(path="x.pdf", pages=[PageExtraction(index=0)])
    with patch.object(p, "_looks_like_substitution_cipher", lambda *a, **k: True), \
         patch.object(ex, "_pdfplumber_page_text", return_value={0: cosmetic}), \
         patch.object(ex, "_poppler_page_text", return_value={0: cosmetic}):
        out = ex._recover_glyph_garbage_pages("x.pdf", [original], set(), report)
    assert out[0] == original            # not swapped
    assert report.pages[0].status == "corrupt_unrecoverable"


def test_strong_gate_skips_ocr_for_mild_cipher_flag():
    """A page whose cipher score is below the STRONG bar and that pdfplumber/poppler
    cannot fix is reported corrupt WITHOUT spending OCR (OCR of a table is worse)."""
    import goblintools.parser as p
    from goblintools.extraction_report import ExtractionReport, PageExtraction

    ex = TextExtractor()
    ex.ocr_handler = MagicMock()
    mild = "planilha orcamentaria com termos tecnicos e unidades diversas " * 8
    report = ExtractionReport(path="x.pdf", pages=[PageExtraction(index=0)])
    with patch.object(p, "_looks_like_substitution_cipher", lambda *a, **k: True), \
         patch.object(p, "_substitution_cipher_score", lambda *a, **k: 0.07), \
         patch.object(ex, "_pdfplumber_page_text", return_value={}), \
         patch.object(ex, "_poppler_page_text", return_value={}):
        ex._recover_glyph_garbage_pages("x.pdf", [mild], set(), report)
    ex.ocr_handler.extract_text_from_pdf_page_indices.assert_not_called()
    assert report.pages[0].status == "corrupt_unrecoverable"


def test_extract_pdf_whole_doc_ocr_recovery(monkeypatch):
    """Whole-document substitution cipher past the STRONG bar with an ocr_handler:
    full-doc OCR runs, every page is marked recovered, used_ocr is set. Because
    every page was re-read end to end by OCR and none stayed corrupt, the report is
    demoted back to ``clean`` (see demote_to_clean_if_fully_ocr_recovered)."""
    pytest.importorskip("reportlab")
    import goblintools.parser as p

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name
    try:
        _write_pdf_lines(
            path,
            "Contratacao de empresa para fornecimento de material de consumo conforme "
            "termo de referencia. Valor total estimado dez mil reais. Pregao eletronico.",
        )
        monkeypatch.setattr(p, "_substitution_cipher_score", lambda *a, **k: 0.25)
        ex = TextExtractor()
        ex.ocr_handler = MagicMock()
        ex.ocr_handler.extract_text_from_pdf.return_value = (
            "TEXTO LIMPO RECUPERADO VIA OCR: valor total estimado R$ 10.000,00."
        )
        out = ex.extract_from_file(path)
        assert ex.ocr_handler.extract_text_from_pdf.return_value in out
        assert "file_path_pwd:" in out
        ex.ocr_handler.extract_text_from_pdf.assert_called_once()
        rep = ex.last_extraction_report
        assert rep.used_ocr is True
        assert rep.overall_status == "clean"
        assert all(pg.engine == "ocr" for pg in rep.pages)
    finally:
        os.unlink(path)


def test_extract_from_folder_collects_per_file_reports():
    """extract_from_folder keeps a per-file report dict keyed by relative path;
    last_extraction_report alone would only reflect the last file."""
    pytest.importorskip("reportlab")
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = os.path.join(tmp, "edital.pdf")
        _write_pdf_lines(
            pdf_path,
            "Pregao eletronico para contratacao de servico de limpeza. "
            "Valor estimado cinquenta mil reais. Termo de referencia anexo.",
        )
        with open(os.path.join(tmp, "aviso.txt"), "w", encoding="utf-8") as fh:
            fh.write("aviso de licitacao publica")
        ex = TextExtractor()
        ex.extract_from_folder(tmp)
        assert "edital.pdf" in ex.last_extraction_reports
        assert ex.last_extraction_reports["edital.pdf"].overall_status == "clean"
        assert "aviso.txt" not in ex.last_extraction_reports  # non-PDF: no report


def test_recover_glyph_garbage_pages_fills_report_with_engine():
    """When poppler recovers a broken page, the report records status=recovered and
    the engine that fixed it; OCR is not consulted."""
    from goblintools.extraction_report import ExtractionReport, PageExtraction

    ex = TextExtractor()
    ex.ocr_handler = MagicMock()
    garbage = " ".join(f"/{i}" for i in range(20))
    clean = (
        "A prefeitura municipal torna publico o edital de pregao eletronico para "
        "contratacao de empresa especializada no fornecimento de material de consumo."
    )
    report = ExtractionReport(path="x.pdf", pages=[PageExtraction(index=0)])
    with patch.object(ex, "_pdfplumber_page_text", return_value={}), \
         patch.object(ex, "_poppler_page_text", return_value={0: clean}):
        ex._recover_glyph_garbage_pages("x.pdf", [garbage], set(), report)
    assert report.pages[0].status == "recovered"
    assert report.pages[0].engine == "poppler"
    assert report.pages[0].broken_before is True
    ex.ocr_handler.extract_text_from_pdf_page_indices.assert_not_called()
