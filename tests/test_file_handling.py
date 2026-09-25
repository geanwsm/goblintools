"""Tests for FileValidator, ArchiveHandler, FileManager."""
import os
import tempfile
import zipfile
from pathlib import Path

import pytest
from goblintools import FileValidator, ArchiveHandler, FileManager


def test_file_validator_is_empty():
    """Test is_empty for empty and non-empty files."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        empty_path = f.name
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"content")
        non_empty_path = f.name

    try:
        assert FileValidator.is_empty(empty_path) is True
        assert FileValidator.is_empty(non_empty_path) is False
    finally:
        os.unlink(empty_path)
        os.unlink(non_empty_path)


def test_file_validator_is_archive():
    """Test is_archive for zip files."""
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as f:
        zip_path = f.name
    try:
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("test.txt", "content")
        assert FileValidator.is_archive(zip_path) is True
    finally:
        os.unlink(zip_path)


def test_file_validator_is_archive_false():
    """Test is_archive returns False for non-archives."""
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
        f.write(b"not an archive")
        path = f.name
    try:
        assert FileValidator.is_archive(path) is False
    finally:
        os.unlink(path)


def test_is_zip_by_magic(temp_dir):
    """Test is_zip_by_magic for zip, pdf, txt files."""
    zip_path = os.path.join(temp_dir, "test.zip")
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("x.txt", "content")
    assert FileValidator.is_zip_by_magic(zip_path) is True

    pdf_path = os.path.join(temp_dir, "test.pdf")
    with open(pdf_path, 'wb') as f:
        f.write(b'%PDF-1.4\ncontent')
    assert FileValidator.is_zip_by_magic(pdf_path) is False

    txt_path = os.path.join(temp_dir, "test.txt")
    with open(txt_path, 'w') as f:
        f.write("plain text")
    assert FileValidator.is_zip_by_magic(txt_path) is False


def test_detect_extension_from_magic(temp_dir):
    """Test detect_extension_from_magic returns .pdf for PDF, None for others."""
    pdf_path = os.path.join(temp_dir, "test.pdf")
    with open(pdf_path, 'wb') as f:
        f.write(b'%PDF-1.4\ncontent')
    assert FileValidator.detect_extension_from_magic(pdf_path) == '.pdf'

    zip_path = os.path.join(temp_dir, "test.zip")
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("x.txt", "content")
    assert FileValidator.detect_extension_from_magic(zip_path) is None

    txt_path = os.path.join(temp_dir, "test.txt")
    with open(txt_path, 'w') as f:
        f.write("plain text")
    assert FileValidator.detect_extension_from_magic(txt_path) is None

    no_ext = os.path.join(temp_dir, "anexo_1")
    with open(no_ext, 'wb') as f:
        f.write(b'%PDF-1.4\n% fake pdf header for magic sniff')
    assert FileValidator.detect_extension_from_magic(no_ext) == '.pdf'


def test_extract_files_recursive_pdf_named_as_zip(temp_dir):
    """Case B fallback: .zip file with PDF content."""
    zip_path = os.path.join(temp_dir, "arquivo.zip")
    with open(zip_path, 'wb') as f:
        f.write(b'%PDF-1.4\nfake pdf content')
    dest = os.path.join(temp_dir, "out")
    os.makedirs(dest, exist_ok=True)
    result = FileManager.extract_files_recursive(zip_path, dest)
    assert result is True
    # Should have copied as .pdf
    pdf_files = list(Path(dest).glob("*.pdf"))
    assert len(pdf_files) == 1
    assert pdf_files[0].name == "arquivo.pdf"


def test_extract_files_recursive_zip_named_as_pdf(temp_dir):
    """Case A fallback: .pdf file with ZIP content."""
    pdf_path = os.path.join(temp_dir, "edital.pdf")
    with zipfile.ZipFile(pdf_path, 'w') as zf:
        zf.writestr("inner.txt", "hello from zip")
    dest = os.path.join(temp_dir, "out")
    os.makedirs(dest, exist_ok=True)
    result = FileManager.extract_files_recursive(pdf_path, dest)
    assert result is True
    inner = os.path.join(dest, "inner.txt")
    assert os.path.exists(inner)
    with open(inner) as f:
        assert f.read() == "hello from zip"


def test_archive_handler_extract_zip(temp_dir):
    """Test ArchiveHandler.extract for zip file (default: remove source)."""
    zip_path = os.path.join(temp_dir, "test.zip")
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("inner.txt", "hello")

    dest = os.path.join(temp_dir, "extracted")
    os.makedirs(dest, exist_ok=True)

    result = ArchiveHandler.extract(zip_path, dest)
    assert result is True
    assert not os.path.exists(zip_path)  # Source removed by default
    extracted_file = os.path.join(dest, "inner.txt")
    assert os.path.exists(extracted_file)
    with open(extracted_file) as f:
        assert f.read() == "hello"


def test_archive_handler_extract_keep_source(temp_dir):
    """Test ArchiveHandler.extract with remove_source=False keeps archive."""
    zip_path = os.path.join(temp_dir, "keep.zip")
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("data.txt", "content")

    dest = os.path.join(temp_dir, "out")
    os.makedirs(dest, exist_ok=True)

    result = ArchiveHandler.extract(zip_path, dest, remove_source=False)
    assert result is True
    assert os.path.exists(zip_path)  # Source kept
    assert os.path.exists(os.path.join(dest, "data.txt"))


def test_file_manager_delete_if_empty():
    """Test delete_if_empty."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        path = f.name
    try:
        assert FileManager.delete_if_empty(path) is True
        assert not os.path.exists(path)
    except Exception:
        if os.path.exists(path):
            os.unlink(path)
        raise


def test_move_files_extensionless_keeps_work_dir(temp_dir):
    """Extensionless files must not target the work dir as dest (avoids moving outside + rmdir)."""
    work = os.path.join(temp_dir, "work")
    os.makedirs(work)
    nested = os.path.join(work, "docs")
    os.makedirs(nested)
    no_ext = os.path.join(nested, "anexo_1")
    with open(no_ext, "wb") as f:
        f.write(b"%PDF-1.4\n%test")

    FileManager.move_files(work)

    assert os.path.isdir(work)
    flat = os.path.join(work, "docs_anexo_1")
    assert os.path.isfile(flat)
    with open(flat, "rb") as f:
        assert f.read().startswith(b"%PDF")


def test_move_files_root_level_file_not_renamed(temp_dir):
    """Files already at the root of the target folder must not be renamed with _1."""
    work = os.path.join(temp_dir, "work")
    os.makedirs(work)
    root_file = os.path.join(work, "edital.pdf")
    with open(root_file, "wb") as f:
        f.write(b"%PDF-1.4\n%test")

    FileManager.move_files(work)

    assert os.path.isfile(root_file), "edital.pdf must remain with its original name"
    assert not os.path.exists(os.path.join(work, "edital_1.pdf")), "_1 suffix must not be added"


def test_move_file_source_equals_destination_is_noop(temp_dir):
    """move_file with source == destination must not rename or raise."""
    path = os.path.join(temp_dir, "edital.pdf")
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n%test")

    result = FileManager.move_file(path, path)

    assert result is True
    assert os.path.isfile(path), "file must still exist at its original path"
    assert not os.path.exists(os.path.join(temp_dir, "edital_1.pdf")), "_1 suffix must not be added"


# --- Image detection (0.12.0) ------------------------------------------------

import pytest as _pytest


@_pytest.mark.parametrize(
    "header, expected",
    [
        (b"\xff\xd8\xff\xe0" + b"\x00" * 20, ".jpg"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, ".png"),
        (b"II*\x00" + b"\x00" * 20, ".tif"),
        (b"MM\x00*" + b"\x00" * 20, ".tif"),
    ],
)
def test_detect_extension_from_magic_recognises_images(tmp_path, header, expected):
    """JPEG/PNG/TIFF signatures are detected for extensionless files."""
    path = tmp_path / "sem_extensao"
    path.write_bytes(header)
    assert FileValidator.detect_extension_from_magic(str(path)) == expected


def test_images_are_parseable_documents():
    """Images are copied by FileManager like other parseable documents."""
    for suffix in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        assert suffix in FileValidator.PARSEABLE_EXTENSIONS
