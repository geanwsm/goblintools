import csv
import logging
import os
import unicodedata
from typing import Callable, Dict, List, Optional, Set, Tuple
from pathlib import Path
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from striprtf.striprtf import rtf_to_text
from dbfread import DBF
from pptx import Presentation
from pypdf import PdfReader, PdfWriter
from pypdf.generic import IndirectObject

from goblintools.pypdf_workarounds import apply_pypdf_extraction_workarounds

apply_pypdf_extraction_workarounds()
import openpyxl
import xlrd
from odf import text, teletype
from odf.opendocument import load
from odf.text import P
import docx
from goblintools.ocr_parser import OCRProcessor
from goblintools.config import GoblinConfig, OCRConfig
from goblintools.log_policy import _set_suppress_warnings, log_warning
from goblintools.file_handling import FileValidator
from goblintools.table_extractor import (
    extract_pdf_tables,
    format_tables_for_page,
    tables_by_page,
)

logger = logging.getLogger(__name__)


def _has_meaningful_text(text: str) -> bool:
    """True if *text* has at least one letter, number, punctuation, symbol, or mark.

    Corrupt or blank PDFs often yield only whitespace, zero-width spaces (U+200B), or
    other format/control characters that survive :meth:`str.strip` but carry no content.
    """
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "N", "P", "S", "M"):
            return True
    return False


class TextExtractor:
    """Main class for handling text extraction from various file formats."""

    def __init__(
        self,
        ocr_handler=False,
        use_aws=False,
        aws_access_key=None,
        aws_secret_key=None,
        aws_region='us-east-1',
        config: Optional[GoblinConfig] = None,
        suppress_warnings: Optional[bool] = None,
        extract_tables: bool = False,
        table_format: str = "markdown",
    ):
        """
        Initialize the text extractor.

        Args:
            ocr_handler: Enable OCR for image-based PDFs
            use_aws: Use AWS Textract for OCR
            aws_access_key: AWS access key
            aws_secret_key: AWS secret key
            aws_region: AWS region
            config: GoblinConfig object (overrides other parameters)
            suppress_warnings: If True/False, sets warning suppression for the process.
                If None (default), leaves the current setting unchanged (use
                ``goblintools.configure(suppress_warnings=True)`` at startup, or pass
                ``FileManager(suppress_warnings=True)`` before extraction).
            extract_tables: When True, detect PDF tables (pdfplumber) and embed them
                in the extracted text (currently Markdown only).
            table_format: Output format for embedded tables (``"markdown"``).
        """
        self.config = config or GoblinConfig.default()

        # Override config with explicit parameters if provided
        if any([use_aws, aws_access_key, aws_secret_key, aws_region != 'us-east-1']):
            self.config.ocr = OCRConfig(use_aws, aws_access_key, aws_secret_key, aws_region)

        if suppress_warnings is not None:
            _set_suppress_warnings(suppress_warnings)

        if table_format != "markdown":
            raise ValueError(
                f"Unsupported table_format={table_format!r}; only 'markdown' is supported"
            )
        self.extract_tables = extract_tables
        self.table_format = table_format

        if ocr_handler:
            self.ocr_handler = OCRProcessor(self.config.ocr)
        else:
            self.ocr_handler = None

        self._parsers = None  # Lazy initialization

    @property
    def parsers(self) -> Dict[str, Callable]:
        """Lazy-loaded parsers dictionary"""
        if self._parsers is None:
            self._parsers = self._initialize_parsers()
        return self._parsers

    def _initialize_parsers(self) -> Dict[str, Callable]:
        """Initialize all available text extraction parsers."""
        return {
            '.pdf': self._extract_pdf,
            '.docx': self._extract_docx,
            '.txt': self._extract_txt,
            '.pptx': self._extract_pptx,
            '.html': self._extract_html,
            '.odt': self._extract_odt,
            '.rtf': self._extract_rtf,
            '.csv': self._extract_csv,
            '.xml': self._extract_xml,
            '.xlsx': self._extract_xlsx,
            '.xlsm': self._extract_xlsx,
            '.xls': self._extract_xls,
            '.ods': self._extract_ods,
            '.dbf': self._extract_dbf,
        }

    def add_parser(self, extension: str, parser_func: Callable) -> None:
        """Add or override a parser for a specific file extension."""
        self.parsers[extension.lower()] = parser_func

    def extract_from_file(self, file_path: str, display_path: Optional[str] = None) -> str:
        """
        Extract text from a single file.

        Args:
            file_path: Path to the file to extract text from
            display_path: Optional path for the file_path_pwd tag (e.g. relative path from inside archive).
                          If None, uses file_path.

        Returns:
            Extracted text as string with file_path_pwd tag at the beginning
        """
        if not os.path.exists(file_path):
            log_warning(logger, f"File not found: {file_path}")
            return ""

        file_extension = Path(file_path).suffix.lower()
        parser = self.parsers.get(file_extension)

        if not parser:
            detected = FileValidator.detect_extension_from_magic(file_path)
            if detected:
                parser = self.parsers.get(detected)
                if parser:
                    file_extension = detected

        if not parser:
            log_warning(
                logger,
                f"No parser available for file extension: {file_extension or '(none)'}",
            )
            return ""

        try:
            extracted_text = parser(file_path)
            if not extracted_text or not _has_meaningful_text(str(extracted_text)):
                return ""
            path_for_tag = display_path if display_path is not None else file_path
            return f'file_path_pwd:"{path_for_tag}"\n{extracted_text}'

        except Exception as e:
            logger.error(f"Error extracting text from {file_path}: {e}")
            return ""

    def extract_from_folder(self, folder_path: str) -> str:
        """
        Extract text from all supported files in a folder (recursively).

        Args:
            folder_path: Path to the folder to process

        Returns:
            Combined extracted text with file_path_pwd tags for each file
        """
        if not os.path.exists(folder_path):
            log_warning(logger, f"Folder not found: {folder_path}")
            return ""

        all_texts = []
        for root, _, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    if os.path.getsize(file_path) > self.config.max_file_size:
                        log_warning(logger, f"Skipping large file: {file_path}")
                        continue
                except OSError:
                    continue

                rel_path = os.path.relpath(file_path, folder_path)
                text = self.extract_from_file(file_path, display_path=rel_path)
                if text:
                    all_texts.append(text)

        return '\n\n'.join(all_texts)

    def extract_tables_from_pdf(
        self, file_path: str, *, max_pages: Optional[int] = None
    ) -> List[Dict]:
        """Extract structured tables from a PDF (pdfplumber).

        Returns a list of dicts with keys ``page`` (1-based), ``index``, and ``rows``.
        """
        return extract_pdf_tables(file_path, max_pages=max_pages)

    def pdf_needs_ocr(self, file_path: str) -> bool:
        """Check if PDF needs OCR processing"""
        try:
            with open(file_path, 'rb') as f:
                reader = PdfReader(f)
                for page in reader.pages:
                    try:
                        text = self._pypdf_try_extract_text(page)
                    except Exception:
                        continue
                    if text and not text.isspace():
                        return False
            return True
        except Exception as e:
            logger.error(f"Error checking PDF {file_path}: {e}")
            return True

    def _pypdf_try_extract_text(self, page) -> str:
        """Try plain then layout (newer pypdf); fall back to legacy extract_text()."""
        last_error: Optional[Exception] = None
        for mode in ("plain", "layout"):
            try:
                t = page.extract_text(extraction_mode=mode)
                if t:
                    return t
            except TypeError:
                break
            except Exception as e:
                last_error = e
                continue
        try:
            return page.extract_text() or ""
        except Exception:
            if last_error:
                raise last_error
            raise

    def _pdf_page_has_images(self, page) -> bool:
        resources = page.get("/Resources")
        if isinstance(resources, IndirectObject):
            resources = resources.get_object()
        if not resources or "/XObject" not in resources:
            return False
        xobject = resources["/XObject"]
        if isinstance(xobject, IndirectObject):
            xobject = xobject.get_object()
        return any(
            xobject[obj].get("/Subtype") == "/Image" for obj in xobject
        )

    def _pypdf_extract_pages(
        self, pdf_path: str
    ) -> Tuple[List[str], Set[int], bool]:
        """Per-page PyPDF text; failed indices; whether any page references images."""
        reader = PdfReader(pdf_path)
        texts: List[str] = []
        failed: Set[int] = set()
        has_images = False
        for i, page in enumerate(reader.pages):
            try:
                text = self._pypdf_try_extract_text(page)
                texts.append(text or "")
            except Exception as e:
                log_warning(logger, f"Error reading page {i} of {pdf_path}: {e}")
                texts.append("")
                failed.add(i)
            try:
                if not has_images and self._pdf_page_has_images(page):
                    has_images = True
            except Exception:
                pass
        return texts, failed, has_images

    def _merge_page_texts(
        self, primary: List[str], secondary: List[str]
    ) -> Tuple[List[str], Set[int]]:
        """Fill empty primary slots from secondary; return merged list and still-empty indices."""
        n = max(len(primary), len(secondary))
        merged = []
        for i in range(n):
            a = primary[i] if i < len(primary) else ""
            b = secondary[i] if i < len(secondary) else ""
            merged.append(a if (a and a.strip()) else (b or ""))
        failed = {i for i, t in enumerate(merged) if not (t and t.strip())}
        return merged, failed

    def _resave_pdf(self, file_path: str) -> str:
        """Resave PDF to fix potential xref/stream issues (used as second attempt)."""
        reader = PdfReader(file_path)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        output_path = Path(file_path).with_suffix(".resaved.pdf")
        with open(output_path, "wb") as f:
            writer.write(f)

        return str(output_path)

    def validate_installation(self) -> Dict[str, bool]:
        """Check if all dependencies are properly installed"""
        results = {}

        # Check Tesseract
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            results['tesseract'] = True
        except Exception:
            results['tesseract'] = False

        # Check AWS credentials (use ocr_handler.use_aws in case we fell back to local)
        if self.ocr_handler and self.ocr_handler.use_aws:
            try:
                self.ocr_handler.textract_client.list_document_analysis_jobs
                results['aws_textract'] = True
            except Exception:
                results['aws_textract'] = False

        return results

    # Individual parser methods
    def _extract_pdf(self, file_path: str) -> str:
        """Extract text with PyPDF; resave retry; per-page OCR for gaps when handler is set."""
        temp_file: Optional[str] = None
        page_texts: List[str] = []
        has_images = False
        extracted_text = ""

        def run_pypdf(path: str) -> Tuple[List[str], Set[int], bool]:
            return self._pypdf_extract_pages(path)

        try:
            try:
                page_texts, failed, has_images = run_pypdf(file_path)
            except Exception as e:
                log_warning(
                    logger,
                    f"PyPDF read failed on original {file_path}: {e}; trying resaved copy.",
                )
                page_texts, failed, has_images = [], set(), False
                temp_file = self._resave_pdf(file_path)
                page_texts, failed, has_images = run_pypdf(temp_file)
            else:
                if failed:
                    try:
                        temp_file = self._resave_pdf(file_path)
                        alt_texts, _alt_failed, alt_img = run_pypdf(temp_file)
                        page_texts, still_failed = self._merge_page_texts(
                            page_texts, alt_texts
                        )
                        has_images = has_images or alt_img
                        failed = still_failed
                    except Exception as e:
                        log_warning(
                            logger,
                            f"PyPDF resave retry failed for {file_path}: {e}",
                        )

            if failed and self.ocr_handler:
                logger.info(
                    "OCR fallback for %d page(s) of %s (PyPDF could not decode them)",
                    len(failed),
                    file_path,
                )
                ocr_by_page = self.ocr_handler.extract_text_from_pdf_page_indices(
                    file_path, sorted(failed)
                )
                for idx in failed:
                    if idx in ocr_by_page and ocr_by_page[idx]:
                        page_texts[idx] = ocr_by_page[idx]

            if self.extract_tables and page_texts:
                page_texts = self._merge_tables_into_page_texts(file_path, page_texts)

            extracted_text = "\n".join(page_texts)

        except Exception as e:
            logger.error(f"Failed to open PDF {file_path}: {e}")
            return ""
        finally:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass

        if not _has_meaningful_text(extracted_text) and has_images:
            if self.ocr_handler:
                logger.info(f"OCR required for file: {file_path}")
                return self.ocr_handler.extract_text_from_pdf(file_path)
            log_warning(
                logger,
                f"The file {file_path} requires OCR but no handler was provided.",
            )

        return extracted_text

    def _merge_tables_into_page_texts(
        self, file_path: str, page_texts: List[str]
    ) -> List[str]:
        """Append Markdown tables after each page's pypdf text."""
        try:
            tables = extract_pdf_tables(file_path, max_pages=len(page_texts))
        except ImportError as e:
            log_warning(logger, str(e))
            return page_texts
        except Exception as e:
            log_warning(logger, f"Table extraction skipped for {file_path}: {e}")
            return page_texts

        if not tables:
            return page_texts

        by_page = tables_by_page(tables)
        merged: List[str] = []
        for i, text in enumerate(page_texts):
            page_num = i + 1
            page_tables = by_page.get(page_num, [])
            if not page_tables:
                merged.append(text)
                continue
            block = format_tables_for_page(
                page_tables, page=page_num, table_format=self.table_format
            )
            if block:
                base = text.rstrip()
                merged.append(f"{base}\n\n{block}" if base else block)
            else:
                merged.append(text)
        return merged

    def _extract_docx(self, file_path: str) -> str:
        """Extract text from DOCX files."""
        try:
            doc = docx.Document(file_path)
            return ' '.join(para.text for para in doc.paragraphs if para.text)
        except Exception as e:
            logger.error(f"Error processing DOCX file {file_path}: {e}")
            return ""

    def _extract_txt(self, file_path: str) -> str:
        """Extract text from plain text files."""
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return file.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, 'r', encoding='latin-1') as file:
                    return file.read()
            except Exception as e:
                logger.error(f"Error processing TXT file {file_path}: {e}")
                return ""

    def _extract_pptx(self, file_path: str) -> str:
        """Extract text from PowerPoint files."""
        try:
            prs = Presentation(file_path)
            texts = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text:
                        texts.append(shape.text)
            return ' '.join(texts)
        except Exception as e:
            logger.error(f"Error processing PPTX file {file_path}: {e}")
            return ""

    def _extract_html(self, file_path: str) -> str:
        """Extract text from HTML files."""
        encodings = ['utf-8', 'latin-1']
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as file:
                    soup = BeautifulSoup(file.read(), 'html.parser')
                    return soup.get_text(separator=' ', strip=True)
            except UnicodeDecodeError:
                continue
            except Exception as e:
                logger.error(f"Error processing HTML file {file_path}: {e}")
                break
        return ""

    def _extract_odt(self, file_path: str) -> str:
        """Extract text from OpenDocument Text files."""
        try:
            doc = load(file_path)
            return ' '.join(
                teletype.extractText(element)
                for element in doc.getElementsByType(text.P)
            )
        except Exception as e:
            logger.error(f"Error processing ODT file {file_path}: {e}")
            return ""

    def _extract_rtf(self, file_path: str) -> str:
        """Extract text from RTF files."""
        try:
            with open(file_path, 'r') as file:
                return rtf_to_text(file.read(), errors='ignore')
        except Exception as e:
            logger.error(f"Error processing RTF file {file_path}: {e}")
            return ""

    def _extract_csv(self, file_path: str) -> str:
        """Extract text from CSV files."""
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return ' '.join(
                    ' '.join(row)
                    for row in csv.reader(file)
                    if any(field.strip() for field in row)
                )
        except Exception as e:
            logger.error(f"Error processing CSV file {file_path}: {e}")
            return ""

    def _extract_xml(self, file_path: str) -> str:
        """Extract text from XML files."""
        try:
            tree = ET.parse(file_path)
            return ' '.join(
                elem.text.strip()
                for elem in tree.iter()
                if elem.text and elem.text.strip()
            )
        except Exception as e:
            logger.error(f"Error processing XML file {file_path}: {e}")
            return ""

    def _extract_xlsx(self, file_path: str) -> str:
        """Extract evaluated text content from Excel files."""
        try:
            wb = openpyxl.load_workbook(file_path, data_only=True)
            texts = []
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for cell in row:
                        if cell is not None:
                            texts.append(str(cell))
            return ' '.join(texts)
        except Exception as e:
            logger.error(f"Error processing XLSX file {file_path}: {e}")
            return ""

    def _extract_xls(self, file_path: str) -> str:
        """Extract text from legacy Excel files."""
        try:
            book = xlrd.open_workbook(file_path, formatting_info=False)
            texts = []
            for sheet in book.sheets():
                for row_idx in range(sheet.nrows):
                    for cell in sheet.row(row_idx):
                        value = cell.value
                        if value and not str(value).startswith('='):
                            texts.append(str(value))
            return ' '.join(texts)
        except Exception as e:
            logger.error(f"Error processing XLS file {file_path}: {e}")
            return ""

    def _extract_ods(self, file_path: str) -> str:
        """Extract text from OpenDocument Spreadsheets."""
        try:
            doc = load(file_path)
            return '\n'.join(
                "".join(
                    child.data
                    for child in p.childNodes
                    if child.nodeType == child.TEXT_NODE
                )
                for p in doc.getElementsByType(P)
            )
        except Exception as e:
            logger.error(f"Error processing ODS file {file_path}: {e}")
            return ""

    def _extract_dbf(self, file_path: str) -> str:
        """Extract text from DBF database files."""
        try:
            dbf = DBF(file_path, load=True)
            return ' '.join(
                f"{key}: {value}"
                for record in dbf
                for key, value in record.items()
            )
        except Exception as e:
            logger.error(f"Error processing DBF file {file_path}: {e}")
            return ""
