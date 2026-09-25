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
    client.assert_called_once_with("textract", region_name="sa-east-1")


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
