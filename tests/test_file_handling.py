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


# --- Archive limits (0.12.0) ---------------------------------------------------

import io
import tarfile

from goblintools import ArchiveLimits, ExtractionBudget
from goblintools.file_handling import _stream_members


def _make_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return str(path)


def _files_under(root):
    return sorted(
        os.path.relpath(os.path.join(dirpath, name), root)
        for dirpath, _, names in os.walk(root)
        for name in names
    )


def test_zip_member_over_per_member_limit_is_skipped(tmp_path):
    """A member bigger than max_member_bytes is left out and the partial file removed."""
    archive = _make_zip(tmp_path / "a.zip", [("small.txt", b"x" * 10), ("big.txt", b"y" * 3000)])
    budget = ExtractionBudget(ArchiveLimits(max_member_bytes=1000))

    assert ArchiveHandler.extract(archive, str(tmp_path / "out"), budget=budget)

    assert _files_under(tmp_path / "out") == ["small.txt"]
    assert any("big.txt" in reason for reason in budget.skipped)


def test_zip_total_limit_stops_remaining_members(tmp_path):
    """Once the total cap is hit, the current and later members are not extracted."""
    archive = _make_zip(tmp_path / "a.zip", [(f"m{i}.txt", b"z" * 600) for i in range(3)])
    budget = ExtractionBudget(ArchiveLimits(max_total_bytes=1000))

    ArchiveHandler.extract(archive, str(tmp_path / "out"), budget=budget)

    assert _files_under(tmp_path / "out") == ["m0.txt"]
    assert any("total" in reason for reason in budget.skipped)


def test_zip_member_count_limit(tmp_path):
    """Only max_members entries are extracted; the rest are reported."""
    archive = _make_zip(tmp_path / "a.zip", [(f"m{i}.txt", b"1") for i in range(5)])
    budget = ExtractionBudget(ArchiveLimits(max_members=2))

    ArchiveHandler.extract(archive, str(tmp_path / "out"), budget=budget)

    assert len(_files_under(tmp_path / "out")) == 2
    assert any("member limit" in reason for reason in budget.skipped)


def test_zip_members_escaping_the_root_are_skipped(tmp_path):
    """Zip-slip style names (../, absolute) never land outside the destination."""
    archive = _make_zip(tmp_path / "a.zip", [("../evil.txt", b"e"), ("/abs.txt", b"a"), ("ok.txt", b"o")])
    budget = ExtractionBudget()
    out = tmp_path / "out"

    ArchiveHandler.extract(archive, str(out), budget=budget)

    assert _files_under(out) == ["ok.txt"]
    assert not (tmp_path / "evil.txt").exists()
    assert len([r for r in budget.skipped if "escapes" in r]) == 2


def test_nested_zip_beyond_max_depth_is_not_extracted(tmp_path):
    """Archives nested deeper than max_depth stay closed and are reported."""
    inner3 = _make_zip(tmp_path / "level3.zip", [("deep.txt", b"deep")])
    inner2 = _make_zip(tmp_path / "level2.zip", [("level3.zip", open(inner3, "rb").read()), ("mid.txt", b"m")])
    outer = _make_zip(tmp_path / "level1.zip", [("level2.zip", open(inner2, "rb").read())])
    budget = ExtractionBudget(ArchiveLimits(max_depth=2))
    out = tmp_path / "out"

    assert FileManager.extract_files_recursive(outer, str(out), budget=budget)

    files = _files_under(out)
    assert "mid.txt" in files
    assert "deep.txt" not in files
    assert any("level3.zip" in r and "nesting" in r for r in budget.skipped)


def test_budget_is_shared_across_nested_archives(tmp_path):
    """Caps apply to the whole tree, not to each archive separately."""
    inner = _make_zip(tmp_path / "inner.zip", [("b.txt", b"b"), ("c.txt", b"c")])
    outer = _make_zip(tmp_path / "outer.zip", [("inner.zip", open(inner, "rb").read()), ("a.txt", b"a")])
    budget = ExtractionBudget(ArchiveLimits(max_members=3))
    out = tmp_path / "out"

    FileManager.extract_files_recursive(outer, str(out), budget=budget)

    extracted = [f for f in _files_under(out) if f.endswith(".txt")]
    assert len(extracted) == 2
    assert budget.members == 3


def test_limits_argument_builds_a_budget(tmp_path):
    """Passing limits without a budget still enforces them."""
    archive = _make_zip(tmp_path / "a.zip", [(f"m{i}.txt", b"1") for i in range(4)])

    FileManager.extract_files_recursive(archive, str(tmp_path / "out"), limits=ArchiveLimits(max_members=1))

    assert len(_files_under(tmp_path / "out")) == 1


def test_tar_is_trimmed_to_the_budget_after_extraction(tmp_path):
    """Formats extracted by patool are checked after extraction; the excess is removed."""
    path = tmp_path / "a.tar"
    with tarfile.open(path, "w") as tf:
        for i in range(3):
            data = b"t" * 10
            info = tarfile.TarInfo(f"t{i}.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    budget = ExtractionBudget(ArchiveLimits(max_members=2))

    if not ArchiveHandler.extract(str(path), str(tmp_path / "out"), budget=budget):
        pytest.skip("tar extraction tool not available")

    assert len(_files_under(tmp_path / "out")) == 2
    assert any("member limit" in r for r in budget.skipped)


def test_tar_symlinks_are_dropped(tmp_path):
    """Symlinks produced by patool formats are removed instead of moved."""
    path = tmp_path / "links.tar"
    with tarfile.open(path, "w") as tf:
        data = b"real"
        info = tarfile.TarInfo("real.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("link.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
    budget = ExtractionBudget()

    if not ArchiveHandler.extract(str(path), str(tmp_path / "out"), budget=budget):
        pytest.skip("tar extraction tool not available")

    assert _files_under(tmp_path / "out") == ["real.txt"]
    assert any("symlink" in r for r in budget.skipped)


class _FakeMember:
    def __init__(self, filename, data, is_dir=False):
        self.filename = filename
        self._data = data
        self._is_dir = is_dir

    def is_dir(self):
        return self._is_dir


class _FakeArchive:
    """Duck-typed archive with the zipfile/rarfile member API (infolist/open/is_dir)."""

    def __init__(self, members):
        self._members = members

    def infolist(self):
        return list(self._members)

    def open(self, member):
        return io.BytesIO(member._data)


def test_stream_members_counts_bytes_actually_read(tmp_path):
    """The per-member cap is enforced on bytes read, whatever size a header claims (RAR path)."""
    archive = _FakeArchive([
        _FakeMember("dir/", b"", is_dir=True),
        _FakeMember("ok.txt", b"k" * 10),
        _FakeMember("bomb.txt", b"B" * 5000),
    ])
    budget = ExtractionBudget(ArchiveLimits(max_member_bytes=100))

    _stream_members(archive, str(tmp_path), budget)

    assert _files_under(tmp_path) == ["ok.txt"]
    assert budget.total_bytes == 10


def test_budget_restore_rolls_back_a_failed_attempt():
    """A retried extraction does not double count members, bytes or reasons."""
    budget = ExtractionBudget()
    snapshot = budget.snapshot()
    budget.commit(100)
    budget.skip("x: reason")

    budget.restore(snapshot)

    assert (budget.members, budget.total_bytes, budget.skipped) == (0, 0, [])


def test_misnamed_pdf_zip_respects_limits(tmp_path):
    """The .pdf-that-is-a-ZIP fallback streams members under the same budget."""
    path = tmp_path / "anexo.pdf"
    _make_zip(path, [("a.txt", b"a"), ("b.txt", b"b" * 2000)])
    budget = ExtractionBudget(ArchiveLimits(max_member_bytes=1000))

    FileManager.extract_files_recursive(str(path), str(tmp_path / "out"), budget=budget)

    assert _files_under(tmp_path / "out") == ["a.txt"]


def test_zip_extraction_unchanged_under_default_limits(tmp_path):
    """Small archives extract exactly as before with the default (high) limits."""
    archive = _make_zip(tmp_path / "a.zip", [("x/one.txt", b"1"), ("two.txt", b"2")])

    assert ArchiveHandler.extract(archive, str(tmp_path / "out"))

    assert _files_under(tmp_path / "out") == sorted([os.path.join("x", "one.txt"), "two.txt"])
    assert not os.path.exists(archive)
