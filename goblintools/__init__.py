from .file_handling import FileValidator, ArchiveHandler, FileManager
from .parser import TextExtractor
from .text_cleaner import TextCleaner
from .config import GoblinConfig, OCRConfig
from .ocr_parser import OCRProcessor
from .log_policy import configure

__all__ = ['FileValidator', 'ArchiveHandler', 'FileManager',
           'TextExtractor', 'TextCleaner', 'GoblinConfig', 'OCRConfig', 'OCRProcessor',
           'configure']
__version__ = '0.7.8'
