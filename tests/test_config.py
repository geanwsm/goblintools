"""Tests for GoblinConfig and OCRConfig."""
import json
import tempfile
from pathlib import Path

import pytest
from goblintools import GoblinConfig, OCRConfig


def test_ocr_config_defaults():
    """Test OCRConfig default values."""
    config = OCRConfig()
    assert config.use_aws is False
    assert config.aws_access_key is None
    assert config.aws_secret_key is None
    assert config.aws_region == 'us-east-1'
    assert config.tesseract_lang == 'por'


def test_goblin_config_default():
    """Test GoblinConfig.default()."""
    config = GoblinConfig.default()
    assert config.max_file_size == 100 * 1024 * 1024
    assert config.ocr is not None
    assert config.ocr.use_aws is False


def test_config_to_file_and_from_file():
    """Test saving and loading config from JSON file."""
    config = GoblinConfig(max_file_size=50 * 1024 * 1024)
    config.ocr = OCRConfig(use_aws=False, tesseract_lang='eng')

    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        path = f.name

    try:
        config.to_file(path)
        loaded = GoblinConfig.from_file(path)
        assert loaded.max_file_size == config.max_file_size
        assert loaded.ocr.tesseract_lang == 'eng'
    finally:
        Path(path).unlink(missing_ok=True)


def test_config_from_file_not_found():
    """Test from_file raises when file does not exist."""
    with pytest.raises(FileNotFoundError):
        GoblinConfig.from_file("/nonexistent/path/config.json")


def test_ocr_config_new_fields_default_off():
    """0.12.0 fields are neutral by default: no token, no default credential chain."""
    config = OCRConfig()
    assert config.aws_session_token is None
    assert config.use_default_aws_credentials is False


def test_ocr_config_positional_construction_still_works():
    """TextExtractor builds OCRConfig positionally; new fields must not shift the old ones."""
    config = OCRConfig(True, "key", "secret", "sa-east-1")
    assert (config.use_aws, config.aws_access_key, config.aws_secret_key, config.aws_region) == (True, "key", "secret", "sa-east-1")
    assert config.tesseract_lang == "por"
