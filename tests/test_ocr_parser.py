"""Tests for OCRProcessor (with mocked Tesseract/AWS)."""
import pytest
from unittest.mock import patch, MagicMock

from goblintools import OCRConfig
from goblintools.ocr_parser import OCRProcessor


def test_ocr_processor_fallback_when_aws_creds_missing(caplog):
    """Test that OCRProcessor falls back to local when use_aws=True but credentials missing."""
    config = OCRConfig(use_aws=True, aws_access_key=None, aws_secret_key=None)
    processor = OCRProcessor(config)

    assert processor.use_aws is False
    assert "falling back to local" in caplog.text.lower() or "credentials not found" in caplog.text.lower()


def test_ocr_processor_uses_aws_when_creds_provided():
    """Test that OCRProcessor uses AWS when credentials are provided."""
    config = OCRConfig(
        use_aws=True,
        aws_access_key="test-key",
        aws_secret_key="test-secret"
    )
    processor = OCRProcessor(config)
    assert processor.use_aws is True


def test_ocr_processor_local_by_default():
    """Test that OCRProcessor uses local when use_aws=False."""
    config = OCRConfig(use_aws=False)
    processor = OCRProcessor(config)
    assert processor.use_aws is False


# --- Textract credentials, retry and page size (0.12.0) -------------------

import numpy as np
from botocore.exceptions import ClientError

from goblintools.ocr_parser import _encode_for_textract


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "DetectDocumentText")


def _lines_response(*lines):
    return {"Blocks": [{"BlockType": "LINE", "Text": line} for line in lines]}


def _processor_with_client(client):
    processor = OCRProcessor(OCRConfig(use_aws=True, use_default_aws_credentials=True))
    processor._textract_client = client
    return processor


def test_default_credentials_option_enables_textract_without_keys():
    """use_default_aws_credentials lets Textract run on the boto3 chain (e.g. an ECS task role)."""
    processor = OCRProcessor(OCRConfig(use_aws=True, use_default_aws_credentials=True))
    assert processor.use_aws is True


def test_default_credentials_client_is_built_without_explicit_keys():
    """With the default chain no key kwargs are passed, so boto3 resolves the role itself."""
    processor = OCRProcessor(OCRConfig(use_aws=True, use_default_aws_credentials=True, aws_region="sa-east-1"))
    with patch("goblintools.ocr_parser.boto3.client") as client:
        processor.textract_client
    assert client.call_args.args == ("textract",)
    assert client.call_args.kwargs["region_name"] == "sa-east-1"
    assert "aws_access_key_id" not in client.call_args.kwargs


def test_explicit_keys_and_session_token_reach_boto3():
    """Temporary credentials need the session token or every Textract call is rejected."""
    config = OCRConfig(use_aws=True, aws_access_key="AKIA", aws_secret_key="SECRET", aws_session_token="TOKEN")
    with patch("goblintools.ocr_parser.boto3.client") as client:
        OCRProcessor(config).textract_client
    kwargs = client.call_args.kwargs
    assert kwargs["aws_access_key_id"] == "AKIA"
    assert kwargs["aws_secret_access_key"] == "SECRET"
    assert kwargs["aws_session_token"] == "TOKEN"


@patch("goblintools.retry.time.sleep")
def test_throttling_is_retried_then_succeeds(_sleep):
    """A throttled page is retried instead of silently returning an empty page."""
    client = MagicMock()
    client.detect_document_text.side_effect = [_client_error("ThrottlingException"), _lines_response("EDITAL")]
    processor = _processor_with_client(client)

    text = processor._process_page_aws(np.zeros((20, 20, 3), dtype=np.uint8))

    assert text == "EDITAL"
    assert client.detect_document_text.call_count == 2


@patch("goblintools.retry.time.sleep")
def test_transient_error_exhausted_returns_empty_with_warning(_sleep, caplog):
    """After the retries run out the page is skipped with a warning, never an exception."""
    client = MagicMock()
    client.detect_document_text.side_effect = _client_error("ProvisionedThroughputExceededException")
    processor = _processor_with_client(client)

    text = processor._process_page_aws(np.zeros((20, 20, 3), dtype=np.uint8))

    assert text == ""
    assert client.detect_document_text.call_count == 4
    assert "unavailable" in caplog.text.lower()


@patch("goblintools.retry.time.sleep")
def test_definitive_error_is_not_retried(sleep):
    """AccessDenied will never succeed: one call, logged, empty page."""
    client = MagicMock()
    client.detect_document_text.side_effect = _client_error("AccessDeniedException")
    processor = _processor_with_client(client)

    text = processor._process_page_aws(np.zeros((20, 20, 3), dtype=np.uint8))

    assert text == ""
    assert client.detect_document_text.call_count == 1
    sleep.assert_not_called()


def test_credentials_never_appear_in_logs(caplog):
    """Secrets passed in OCRConfig must not leak through warning or error logs."""
    config = OCRConfig(use_aws=True, aws_access_key="AKIAXXSECRET", aws_secret_key="VERYSECRET", aws_session_token="TOKENSECRET")
    processor = OCRProcessor(config)
    client = MagicMock()
    client.detect_document_text.side_effect = _client_error("AccessDeniedException")
    processor._textract_client = client

    processor._process_page_aws(np.zeros((20, 20, 3), dtype=np.uint8))

    for secret in ("AKIAXXSECRET", "VERYSECRET", "TOKENSECRET"):
        assert secret not in caplog.text


def test_encode_for_textract_fits_under_the_cap():
    """A page too large at full quality is re-encoded smaller instead of being rejected."""
    rng = np.random.default_rng(0)
    noisy = rng.integers(0, 255, size=(600, 600, 3), dtype=np.uint8)
    full = len(_encode_for_textract(noisy, max_bytes=10**9))

    encoded = _encode_for_textract(noisy, max_bytes=full // 2)

    assert encoded is not None
    assert len(encoded) <= full // 2


def test_encode_for_textract_gives_up_when_it_cannot_fit():
    """If no quality/scale fits the synchronous limit the page is reported as skipped."""
    rng = np.random.default_rng(1)
    noisy = rng.integers(0, 255, size=(200, 200, 3), dtype=np.uint8)
    assert _encode_for_textract(noisy, max_bytes=10) is None


def test_extract_text_from_image_uses_textract_when_enabled():
    """The public image entry point dispatches to Textract when AWS is effective."""
    client = MagicMock()
    client.detect_document_text.return_value = _lines_response("CERTIDÃO", "NEGATIVA")
    processor = _processor_with_client(client)

    assert processor.extract_text_from_image(np.zeros((20, 20, 3), dtype=np.uint8)) == "CERTIDÃO\nNEGATIVA"


# --- Page-by-page PDF OCR and single retry layer (0.12.1) ---------------------

import tracemalloc

import goblintools.ocr_parser as ocr_module

A4_150DPI = (1754, 1240, 3)


def _page_image():
    return np.ones(A4_150DPI, dtype=np.uint8)


def _aws_processor(**config_overrides):
    config = OCRConfig(use_aws=True, use_default_aws_credentials=True, **config_overrides)
    processor = OCRProcessor(config)
    processor._process_page_aws = MagicMock(side_effect=lambda image: "pagina")
    return processor


def test_pdf_ocr_rasterizes_one_page_at_a_time(monkeypatch):
    """Each page is converted on its own, so memory holds one page image at a time."""
    calls = []

    def fake_convert(path, **kwargs):
        calls.append(kwargs)
        return [_page_image()]

    monkeypatch.setattr(ocr_module, "convert_from_path", fake_convert)
    monkeypatch.setattr(ocr_module, "_pdf_page_count", lambda path: 3)
    processor = _aws_processor()

    pages = processor.extract_text_from_pdf_by_pages("scan.pdf")

    assert pages == ["pagina", "pagina", "pagina"]
    assert [(c["first_page"], c["last_page"]) for c in calls] == [(1, 1), (2, 2), (3, 3)]
    assert all(c["dpi"] == 200 for c in calls)


def test_pdf_ocr_dpi_is_configurable(monkeypatch):
    """pdf_ocr_dpi controls rasterization resolution."""
    calls = []
    monkeypatch.setattr(ocr_module, "convert_from_path", lambda path, **kw: calls.append(kw) or [_page_image()])
    monkeypatch.setattr(ocr_module, "_pdf_page_count", lambda path: 1)

    _aws_processor(pdf_ocr_dpi=100).extract_text_from_pdf("scan.pdf")

    assert calls[0]["dpi"] == 100


def test_pdf_ocr_has_no_page_cap_by_default(monkeypatch):
    """Without max_ocr_pages every page is OCR'd, as before 0.12.1."""
    calls = []
    monkeypatch.setattr(ocr_module, "convert_from_path", lambda path, **kw: calls.append(kw) or [_page_image()])
    monkeypatch.setattr(ocr_module, "_pdf_page_count", lambda path: 150)
    processor = _aws_processor()
    processor._process_page_aws = lambda image: "pagina"

    assert len(processor.extract_text_from_pdf_by_pages("scan.pdf")) == 150
    assert len(calls) == 150


def test_pdf_ocr_stops_at_the_page_cap(monkeypatch, caplog):
    """Pages beyond max_ocr_pages are not rasterized and the cut is logged."""
    calls = []
    monkeypatch.setattr(ocr_module, "convert_from_path", lambda path, **kw: calls.append(kw) or [_page_image()])
    monkeypatch.setattr(ocr_module, "_pdf_page_count", lambda path: 5)

    pages = _aws_processor(max_ocr_pages=2).extract_text_from_pdf_by_pages("scan.pdf")

    assert len(pages) == 2
    assert len(calls) == 2
    assert "max_ocr_pages" in caplog.text


def test_pdf_ocr_probes_pages_when_the_count_is_unknown(monkeypatch):
    """Without a page count, pages are converted until pdf2image returns nothing."""
    monkeypatch.setattr(ocr_module, "_pdf_page_count", lambda path: 0)
    monkeypatch.setattr(
        ocr_module,
        "convert_from_path",
        lambda path, **kw: [_page_image()] if kw["first_page"] <= 2 else [],
    )

    assert _aws_processor().extract_text_from_pdf_by_pages("scan.pdf") == ["pagina", "pagina"]


def test_whole_document_text_keeps_the_previous_join(monkeypatch):
    """extract_text_from_pdf still joins pages with a space, as before."""
    monkeypatch.setattr(ocr_module, "convert_from_path", lambda path, **kw: [_page_image()])
    monkeypatch.setattr(ocr_module, "_pdf_page_count", lambda path: 2)

    assert _aws_processor().extract_text_from_pdf("scan.pdf") == "pagina pagina"


def test_local_ocr_processes_pages_in_worker_sized_batches(monkeypatch):
    """Tesseract keeps its parallelism but only holds one batch of page images."""
    calls = []
    monkeypatch.setattr(ocr_module, "convert_from_path", lambda path, **kw: calls.append(kw) or [_page_image()])
    monkeypatch.setattr(ocr_module, "_pdf_page_count", lambda path: 5)
    monkeypatch.setattr(ocr_module.multiprocessing, "cpu_count", lambda: 2)
    batches = []

    class _FakePool:
        def __init__(self, processes):
            self.processes = processes

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def map(self, func, items):
            batches.append(len(items))
            return ["local" for _ in items]

    monkeypatch.setattr(ocr_module.multiprocessing, "Pool", _FakePool)
    processor = OCRProcessor(OCRConfig(use_aws=False))

    pages = processor.extract_text_from_pdf_by_pages("scan.pdf")

    assert pages == ["local"] * 5
    assert batches == [2, 2, 1]
    assert len(calls) == 5


def test_pdf_ocr_memory_peak_stays_low_for_a_50_page_scan(monkeypatch):
    """A 50-page scan never holds more than a couple of page images (< 300 MB peak)."""

    def fake_convert(path, **kwargs):
        if "first_page" not in kwargs:
            return [_page_image() for _ in range(50)]
        return [_page_image()]

    monkeypatch.setattr(ocr_module, "convert_from_path", fake_convert)
    monkeypatch.setattr(ocr_module, "_pdf_page_count", lambda path: 50)
    processor = _aws_processor()
    # A MagicMock would keep every page in call_args_list; use a plain function.
    processor._process_page_aws = lambda image: "pagina"

    tracemalloc.start()
    try:
        processor.extract_text_from_pdf("scan.pdf")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 300 * 1024 * 1024


def test_textract_client_disables_botocore_retries():
    """Retries live in goblintools only, so throttling does not multiply calls per page."""
    processor = OCRProcessor(OCRConfig(use_aws=True, use_default_aws_credentials=True))
    with patch("goblintools.ocr_parser.boto3.client") as client:
        processor.textract_client
    boto_config = client.call_args.kwargs["config"]
    assert boto_config.retries == {"total_max_attempts": 1}
    assert boto_config.connect_timeout == 10
    assert boto_config.read_timeout == 60
