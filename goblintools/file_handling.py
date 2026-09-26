import os
import shutil
import zipfile
import patoolib
from pathlib import Path
import logging
import tempfile
import rarfile
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional, Callable, Union

from goblintools.config import ArchiveLimits
from goblintools.retry import retry_with_backoff
from goblintools.log_policy import _set_suppress_warnings, log_warning

logger = logging.getLogger(__name__)

class FileValidator:
    # Extensions supported by TextExtractor (kept in sync with parser.py)
    PARSEABLE_EXTENSIONS = frozenset({
        '.pdf', '.docx', '.txt', '.pptx', '.html', '.odt', '.rtf',
        '.csv', '.xml', '.xlsx', '.xlsm', '.xls', '.ods', '.dbf',
        '.jpg', '.jpeg', '.png', '.tif', '.tiff',
    })

    # ZIP-based office formats: documents for the parser, never archives to expand.
    OFFICE_CONTAINER_EXTENSIONS = frozenset({'.docx', '.xlsx', '.xlsm', '.pptx', '.odt', '.ods'})

    @classmethod
    def office_container_extension(cls, file_path: str) -> Optional[str]:
        """Office extension for a ZIP-based office file (by suffix or content), else None."""
        suffix = Path(file_path).suffix.lower()
        if suffix in cls.OFFICE_CONTAINER_EXTENSIONS:
            return suffix
        detected = cls.detect_extension_from_magic(file_path)
        if detected in cls.OFFICE_CONTAINER_EXTENSIONS:
            return detected
        return None

    @staticmethod
    def is_empty(file_path: str) -> bool:
        """Check if file is empty and optionally delete it."""
        try:
            return os.path.getsize(file_path) == 0
        except (FileNotFoundError, PermissionError) as e:
            logger.info(f"Error checking file {file_path}: {e}")
            return False

    @staticmethod
    def is_archive(file_path: str) -> bool:
        """Check if file is a supported archive format."""
        return patoolib.is_archive(file_path)

    @classmethod
    def is_parseable_document(cls, file_path: str) -> bool:
        """Check if file is a document format that can be parsed directly (no extraction)."""
        return Path(file_path).suffix.lower() in cls.PARSEABLE_EXTENSIONS

    @staticmethod
    def is_zip_by_magic(file_path: str) -> bool:
        """True if file starts with ZIP signature (PK..). Used for Case A fallback."""
        try:
            with open(file_path, 'rb') as f:
                return f.read(4).startswith(b'PK\x03\x04')
        except (OSError, IOError):
            return False

    @staticmethod
    def detect_extension_from_magic(file_path: str) -> Optional[str]:
        """Detect file type from content (extensionless PDF, RTF, OOXML in ZIP, etc.)."""
        try:
            with open(file_path, 'rb') as f:
                sniff = f.read(64)
            header = sniff[:8]
            if sniff.startswith(b'%PDF'):
                return '.pdf'
            if sniff.startswith(b'\xff\xd8\xff'):
                return '.jpg'
            if sniff.startswith(b'\x89PNG\r\n\x1a\n'):
                return '.png'
            if sniff.startswith((b'II*\x00', b'MM\x00*')):
                return '.tif'
            stripped = sniff.lstrip()
            if stripped.startswith(b'{\\rtf'):
                return '.rtf'
            # Office Open XML (ZIP): extensionless docx/xlsx/pptx inside archives
            if header.startswith(b'PK\x03\x04'):
                try:
                    with zipfile.ZipFile(file_path, 'r') as zf:
                        names = set(zf.namelist())
                    if 'word/document.xml' in names:
                        return '.docx'
                    if 'xl/workbook.xml' in names:
                        return '.xlsx'
                    if 'ppt/presentation.xml' in names:
                        return '.pptx'
                except (zipfile.BadZipFile, OSError):
                    pass
            return None
        except (OSError, IOError):
            return None


_COPY_CHUNK = 1024 * 1024


class ExtractionBudget:
    """Running totals for one (possibly nested) archive extraction.

    Pass the same instance through nested calls so the caps in
    :class:`ArchiveLimits` apply to the whole tree, not per archive. Anything
    left out is recorded in ``skipped`` with the reason; nothing is raised.
    """

    def __init__(self, limits: Optional[ArchiveLimits] = None):
        self.limits = limits or ArchiveLimits()
        self.members = 0
        self.total_bytes = 0
        self.skipped: List[str] = []
        self.exhausted = False

    def snapshot(self):
        return (self.members, self.total_bytes, len(self.skipped), self.exhausted)

    def restore(self, snapshot) -> None:
        """Roll back to ``snapshot`` so a retried extraction does not double count."""
        self.members, self.total_bytes, skipped_count, self.exhausted = snapshot
        del self.skipped[skipped_count:]

    def skip(self, reason: str) -> None:
        self.skipped.append(reason)
        log_warning(logger, f"Archive extraction limit: {reason}")

    def admit_member(self, name: str) -> bool:
        """False once the member cap (or an earlier total-size stop) is reached."""
        if self.exhausted:
            return False
        if self.members >= self.limits.max_members:
            self.skip(
                f"{name}: member limit ({self.limits.max_members}) reached; remaining members skipped"
            )
            self.exhausted = True
            return False
        return True

    def over_limit(self, name: str, member_bytes: int) -> Optional[str]:
        """Reason to drop a member of ``member_bytes`` bytes (read so far), else None."""
        if member_bytes > self.limits.max_member_bytes:
            return f"{name}: exceeds the per-member limit ({self.limits.max_member_bytes} bytes)"
        if self.total_bytes + member_bytes > self.limits.max_total_bytes:
            self.exhausted = True
            return (
                f"{name}: total extracted size limit ({self.limits.max_total_bytes} bytes) "
                "reached; remaining members skipped"
            )
        return None

    def commit(self, member_bytes: int) -> None:
        self.members += 1
        self.total_bytes += member_bytes


def _safe_member_path(root: str, name: str) -> Optional[str]:
    """Destination for an archive member, or None when the name escapes ``root``.

    Member names are attacker-controlled: absolute paths, drive letters and
    ``..`` segments are rejected, and the resolved path must stay under root.
    """
    normalized = name.replace('\\', '/')
    if normalized.startswith('/'):
        return None
    parts = [part for part in normalized.split('/') if part not in ('', '.')]
    if not parts or '..' in parts or (len(parts[0]) >= 2 and parts[0][1] == ':'):
        return None
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, *parts))
    if os.path.commonpath([root_real, target]) != root_real:
        return None
    return target


def _stream_members(archive, destination: str, budget: ExtractionBudget) -> None:
    """Extract ``archive`` member by member, counting the bytes actually read.

    Works with any object exposing ``infolist()`` / ``open(member)`` /
    ``member.is_dir()`` (``zipfile.ZipFile``, ``rarfile.RarFile``). Header sizes
    are never trusted: caps are checked against decompressed bytes, so a bomb
    with a lying header is cut at the limit. Symlinks are never created.
    """
    for member in archive.infolist():
        if member.is_dir():
            continue
        name = member.filename
        if not budget.admit_member(name):
            break
        target = _safe_member_path(destination, name)
        if target is None:
            budget.skip(f"{name}: path escapes the extraction root; member skipped")
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        written = 0
        reason = None
        with archive.open(member) as source, open(target, 'wb') as sink:
            while True:
                chunk = source.read(_COPY_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                reason = budget.over_limit(name, written)
                if reason:
                    break
                sink.write(chunk)
        if reason:
            os.remove(target)
            budget.skip(reason)
            if budget.exhausted:
                break
            continue
        budget.commit(written)


def _enforce_budget_on_tree(root: str, budget: ExtractionBudget) -> None:
    """Apply the budget to files a tool already extracted (patool formats).

    This cannot stop a hostile archive from filling the temp dir during
    extraction; it keeps the excess out of the destination. Symlinks are dropped.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if os.path.islink(path):
                os.remove(path)
                budget.skip(f"{rel}: symlink not extracted")
                continue
            if not budget.admit_member(rel):
                os.remove(path)
                continue
            size = os.path.getsize(path)
            reason = budget.over_limit(rel, size)
            if reason:
                os.remove(path)
                budget.skip(reason)
                continue
            budget.commit(size)


def _move_tree(source_root: str, destination: str) -> None:
    """Move every file under ``source_root`` into ``destination``, renaming on collision."""
    for root, _, files in os.walk(source_root):
        for name in files:
            src_file = os.path.join(root, name)
            rel_path = os.path.relpath(src_file, source_root)
            dest_file = os.path.join(destination, rel_path)

            base, ext = os.path.splitext(dest_file)
            counter = 1
            while os.path.exists(dest_file):
                dest_file = f"{base}_{counter}{ext}"
                counter += 1

            os.makedirs(os.path.dirname(dest_file), exist_ok=True)
            shutil.move(src_file, dest_file)


# Formats extracted member by member under the budget (unless overridden via add_format).
_STREAMED_OPENERS: Dict[str, Callable] = {
    '.zip': zipfile.ZipFile,
    '.jar': zipfile.ZipFile,
    '.cbz': zipfile.ZipFile,
    '.war': zipfile.ZipFile,
    '.ear': zipfile.ZipFile,
    '.rar': rarfile.RarFile,
    '.cbr': rarfile.RarFile,
}


class ArchiveHandler:
    _SUPPORTED_FORMATS: Dict[str, Callable] = {
        # ZIP formats
        '.zip': lambda f, d: zipfile.ZipFile(f).extractall(d),
        '.jar': lambda f, d: zipfile.ZipFile(f).extractall(d),
        '.cbz': lambda f, d: zipfile.ZipFile(f).extractall(d),
        '.war': lambda f, d: zipfile.ZipFile(f).extractall(d),
        '.ear': lambda f, d: zipfile.ZipFile(f).extractall(d),
        
        # RAR formats
        '.rar': lambda f, d: rarfile.RarFile(f).extractall(d),
        '.cbr': lambda f, d: rarfile.RarFile(f).extractall(d),
        
        # 7-Zip
        '.7z': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.cb7': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        
        # Gzip/Bzip
        '.gz': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.bz2': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.bz3': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.tbz2': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        
        # Tar and variants
        '.tar': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.tgz': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.txz': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.cbt': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        
        # ISO formats
        '.iso': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.udf': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        
        # Package formats
        '.deb': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.rpm': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        
        # Other common formats
        '.ace': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.cba': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.arj': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.cab': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.chm': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.cpio': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.dms': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.lha': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.lzh': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.lzma': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.lzo': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.xz': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.zst': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        '.zoo': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),
        
        # Special cases
        '.adf': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),  # Amiga Disk File
        '.alz': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),  # ALZip
        '.arc': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),  # ARC
        '.shn': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),  # Shorten
        '.rz': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),   # Rzip
        '.lrz': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),  # LRzip
        '.a': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),    # Unix static library
        '.Z': lambda f, d: patoolib.extract_archive(f, outdir=d, verbosity=-1),    # Unix compress
    }

    # Extensions whose handler was replaced via add_format (not streamed).
    _custom_formats: set = set()

    @classmethod
    def add_format(cls, extension: str, handler: Callable):
        """Dynamically add support for new archive formats."""
        cls._SUPPORTED_FORMATS[extension.lower()] = handler
        cls._custom_formats.add(extension.lower())

    @classmethod
    def extract(
        cls,
        file_path: str,
        destination: str,
        remove_source: bool = True,
        limits: Optional[ArchiveLimits] = None,
        budget: Optional[ExtractionBudget] = None,
    ) -> bool:
        """Extract any supported archive format safely, avoiding file name collisions.

        Args:
            file_path: Path to the archive file.
            destination: Directory to extract contents into.
            remove_source: If True (default), delete the archive after extraction.
                           If False, keep the source archive.
            limits: Caps for this extraction (default: :class:`ArchiveLimits`).
            budget: Shared :class:`ExtractionBudget` for nested extractions;
                    takes precedence over ``limits``. Skipped members are recorded
                    in ``budget.skipped``.
        """
        if FileValidator.is_empty(file_path):
            return False
        budget = budget or ExtractionBudget(limits)
        snapshot = budget.snapshot()

        @retry_with_backoff(max_retries=3, exceptions=(OSError, RuntimeError))
        def _do_extract():
            budget.restore(snapshot)
            ext = Path(file_path).suffix.lower()
            with tempfile.TemporaryDirectory() as tmpdir:
                opener = None if ext in cls._custom_formats else _STREAMED_OPENERS.get(ext)
                if opener is not None:
                    with opener(file_path) as archive:
                        _stream_members(archive, tmpdir, budget)
                else:
                    if ext in cls._SUPPORTED_FORMATS:
                        cls._SUPPORTED_FORMATS[ext](file_path, tmpdir)
                    else:
                        patoolib.extract_archive(file_path, outdir=tmpdir, verbosity=-1)
                    _enforce_budget_on_tree(tmpdir, budget)
                _move_tree(tmpdir, destination)

            if remove_source:
                os.remove(file_path)

        try:
            _do_extract()
            return True
        except (zipfile.BadZipFile, rarfile.BadRarFile) as e:
            # Expected when file is misnamed (e.g. .zip that is PDF); fallback will handle
            logger.debug(f"Format mismatch for {file_path}: {e}")
            return False
        except Exception as e:
            logger.exception(f"Error extracting {file_path}: {e}")
            return False

    @classmethod
    def extract_zip(
        cls,
        file_path: str,
        destination: str,
        remove_source: bool = True,
        limits: Optional[ArchiveLimits] = None,
        budget: Optional[ExtractionBudget] = None,
    ) -> bool:
        """Extract file as ZIP using zipfile, regardless of extension. Used for Case A fallback."""
        if FileValidator.is_empty(file_path):
            return False
        budget = budget or ExtractionBudget(limits)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                with zipfile.ZipFile(file_path) as archive:
                    _stream_members(archive, tmpdir, budget)
                _move_tree(tmpdir, destination)
            if remove_source:
                os.remove(file_path)
            return True
        except Exception as e:
            logger.exception(f"Error extracting ZIP {file_path}: {e}")
            return False


class FileManager:
    """File extraction and cache helpers."""

    def __init__(self, suppress_warnings: Optional[bool] = None):
        if suppress_warnings is not None:
            _set_suppress_warnings(suppress_warnings)

    @staticmethod
    def delete_if_empty(file_path: str) -> bool:
        """Delete file if it's empty. Returns True if deleted or doesn't exist."""
        try:
            if os.path.getsize(file_path) == 0:
                os.remove(file_path)
                logger.info(f"Deleted empty file: {file_path}")
                return True
            return False
        except FileNotFoundError:
            return True
        except PermissionError:
            log_warning(logger, f"Permission denied deleting {file_path}")
            return False
        except Exception as e:
            logger.error(f"Error checking/deleting {file_path}: {e}")
            return False

    @staticmethod
    def delete_folder(folder_path: str) -> bool:
        """Recursively delete a folder and its contents."""
        try:
            if not os.path.exists(folder_path):
                logger.info(f"Folder not found: {folder_path}")
                return False
            shutil.rmtree(folder_path)
            return True
        except PermissionError as e:
            logger.error(f"Permission denied deleting {folder_path}: {e}")
        except Exception as e:
            logger.error(f"Error deleting {folder_path}: {e}")
        return False

    @staticmethod
    def move_file(source: Union[str, Path], destination: Union[str, Path]) -> bool:
        """Move a single file with proper error handling."""
        source_path = Path(source)
        dest_path = Path(destination)
        
        try:
            if not source_path.exists():
                logger.error(f"Source file does not exist: {source_path}")
                return False
            
            if FileValidator.is_empty(str(source_path)):
                logger.info(f"Skipping empty file: {source_path}")
                return False

            if source_path.resolve() == dest_path.resolve():
                return True

            # Ensure destination directory exists
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Handle name conflicts
            counter = 1
            original_dest = dest_path
            while dest_path.exists():
                dest_path = original_dest.parent / f"{original_dest.stem}_{counter}{original_dest.suffix}"
                counter += 1

            shutil.move(str(source_path), str(dest_path))
            return True
        except PermissionError:
            logger.error(f"Permission denied moving {source_path} to {dest_path}")
            return False
        except Exception as e:
            logger.error(f"Error moving {source_path} to {dest_path}: {e}")
            return False

    @classmethod
    def move_files(cls, folder_path: str) -> None:
        """Organize files in a directory structure."""
        if not os.path.exists(folder_path):
            logger.error(f"Directory not found: {folder_path}")
            return

        for root, _, files in os.walk(folder_path):
            if os.path.abspath(root) == os.path.abspath(folder_path):
                continue

            for file in files:
                source_path = os.path.join(root, file)
                if cls.delete_if_empty(source_path):
                    continue

                # Use relative path as base to avoid _1 when same-name files exist in
                # different folders (e.g. edital/edital.pdf vs subfolder/edital.pdf).
                # Same stem + different extensions (edital.pdf, edital.docx) never collide.
                rel_path = os.path.relpath(source_path, folder_path)
                file_ext = Path(file).suffix.lower()
                if not file_ext:
                    # Extensionless (e.g. PDF saved as "anexo_1"): use full rel path as name.
                    # Avoid endswith("") + rel_path[:-0] which would empty dest_stem.
                    dest_name = rel_path.replace(os.sep, "_")
                    dest_path = os.path.join(folder_path, dest_name)
                else:
                    dest_stem = (
                        rel_path[:-len(file_ext)]
                        if rel_path.lower().endswith(file_ext)
                        else rel_path
                    )
                    dest_stem = dest_stem.replace(os.sep, "_").rstrip("_")
                    dest_path = os.path.join(folder_path, f"{dest_stem}{file_ext}")

                cls.move_file(source_path, dest_path)

            # Remove empty directories (never remove the root folder_path itself)
            try:
                if (
                    os.path.abspath(root) != os.path.abspath(folder_path)
                    and not os.listdir(root)
                ):
                    os.rmdir(root)
            except Exception as e:
                logger.error(f"Error removing directory {root}: {e}")

    @classmethod
    def _extract_nested(cls, destination: str, budget: ExtractionBudget, depth: int) -> None:
        """Open archives found under ``destination`` one level deeper, within max_depth."""
        for root, _, files in os.walk(destination):
            for file in files:
                source = os.path.join(root, file)
                if FileValidator.office_container_extension(source):
                    continue
                if not FileValidator.is_archive(source):
                    continue
                if depth + 1 >= budget.limits.max_depth:
                    budget.skip(
                        f"{file}: nesting deeper than {budget.limits.max_depth} archive levels; not extracted"
                    )
                    continue
                cls.extract_files_recursive(source, root, budget=budget, _depth=depth + 1)

    @classmethod
    def extract_files_recursive(
        cls,
        file_path: str,
        destination: str,
        limits: Optional[ArchiveLimits] = None,
        budget: Optional[ExtractionBudget] = None,
        _depth: int = 0,
    ) -> bool:
        """Recursively extract nested archives, or copy parseable documents as-is.

        If the file is an archive: extracts (and nested archives) to destination.
        If the file is a parseable document (pdf, docx, etc.): copies to destination.
        Returns False only for unsupported formats or on error.

        ``limits`` / ``budget`` cap the whole tree (see :class:`ArchiveLimits`);
        archives nested deeper than ``max_depth`` stay closed and are recorded in
        ``budget.skipped``.
        """
        if not os.path.exists(file_path):
            return False
        budget = budget or ExtractionBudget(limits)

        # Office containers are ZIPs for patool and for the Case A fallback, but
        # they must reach the parser whole (their XML parts are not documents).
        office_ext = FileValidator.office_container_extension(file_path)
        if office_ext:
            os.makedirs(destination, exist_ok=True)
            dest_file = os.path.join(destination, f"{Path(file_path).stem}{office_ext}")
            base, ext = os.path.splitext(dest_file)
            counter = 1
            while os.path.exists(dest_file):
                dest_file = f"{base}_{counter}{ext}"
                counter += 1
            shutil.copy2(file_path, dest_file)
            return True

        if FileValidator.is_archive(file_path):
            if ArchiveHandler.extract(file_path, destination, budget=budget):
                cls._extract_nested(destination, budget, _depth)
                return True
            # Case B fallback: extraction failed (e.g. .zip that is PDF)
            actual_ext = FileValidator.detect_extension_from_magic(file_path)
            if actual_ext and actual_ext in FileValidator.PARSEABLE_EXTENSIONS:
                os.makedirs(destination, exist_ok=True)
                stem = Path(file_path).stem
                dest_file = os.path.join(destination, f"{stem}{actual_ext}")
                base, ext = os.path.splitext(dest_file)
                counter = 1
                while os.path.exists(dest_file):
                    dest_file = f"{base}_{counter}{ext}"
                    counter += 1
                shutil.copy2(file_path, dest_file)
                logger.info(f"Treating misnamed file as {actual_ext}: {file_path}")
                return True
            return False

        if FileValidator.is_parseable_document(file_path):
            # Case A fallback: .pdf (or other doc ext) that is actually ZIP
            if FileValidator.is_zip_by_magic(file_path):
                if ArchiveHandler.extract_zip(file_path, destination, budget=budget):
                    cls._extract_nested(destination, budget, _depth)
                    logger.info(f"Treating misnamed file as ZIP: {file_path}")
                    return True
            # Normal path: copy document
            os.makedirs(destination, exist_ok=True)
            dest_file = os.path.join(destination, os.path.basename(file_path))
            base, ext = os.path.splitext(dest_file)
            counter = 1
            while os.path.exists(dest_file):
                dest_file = f"{base}_{counter}{ext}"
                counter += 1
            shutil.copy2(file_path, dest_file)
            return True

        # Case B fallback when is_archive is False (e.g. patool detects PDF in .zip)
        ext = Path(file_path).suffix.lower()
        if ext in ArchiveHandler._SUPPORTED_FORMATS:
            actual_ext = FileValidator.detect_extension_from_magic(file_path)
            if actual_ext and actual_ext in FileValidator.PARSEABLE_EXTENSIONS:
                os.makedirs(destination, exist_ok=True)
                stem = Path(file_path).stem
                dest_file = os.path.join(destination, f"{stem}{actual_ext}")
                base, ext_suffix = os.path.splitext(dest_file)
                counter = 1
                while os.path.exists(dest_file):
                    dest_file = f"{base}_{counter}{ext_suffix}"
                    counter += 1
                shutil.copy2(file_path, dest_file)
                logger.info(f"Treating misnamed file as {actual_ext}: {file_path}")
                return True

        return False

    @classmethod
    def batch_extract(
        cls, 
        file_paths: List[str], 
        destination: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> List[bool]:
        """Process multiple archives in parallel with optional progress tracking."""
        if progress_callback:
            # Sequential processing with progress tracking
            results = []
            total = len(file_paths)
            
            for i, path in enumerate(file_paths):
                result = cls.extract_files_recursive(path, destination)
                results.append(result)
                progress_callback(i + 1, total)
            
            return results
        else:
            # Parallel processing
            with ThreadPoolExecutor() as executor:
                results = list(executor.map(
                    lambda path: cls.extract_files_recursive(path, destination),
                    file_paths
                ))
            return results
