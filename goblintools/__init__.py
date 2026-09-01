from .file_handling import FileValidator, ArchiveHandler, FileManager
from .parser import TextExtractor
from .text_cleaner import TextCleaner
from .config import GoblinConfig, OCRConfig
from .ocr_parser import OCRProcessor
from .log_policy import configure
from .table_extractor import (
    extract_pdf_tables,
    table_to_markdown,
    is_meaningful_table,
    normalize_table_rows,
)
from .structured import StructuredDocument, StructuredExtractor
from .extraction_report import ExtractionReport, PageExtraction

__all__ = [
    'FileValidator', 'ArchiveHandler', 'FileManager',
    'TextExtractor', 'TextCleaner', 'GoblinConfig', 'OCRConfig', 'OCRProcessor',
    'configure', 'extract_pdf_tables', 'table_to_markdown',
    'is_meaningful_table', 'normalize_table_rows',
    'StructuredExtractor', 'StructuredDocument',
    'ExtractionReport', 'PageExtraction',
]
__version__ = '0.10.1'
