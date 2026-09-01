
# GoblinTools

**GoblinTools** is a Python library designed for text extraction, archive handling, OCR integration, and text cleaning. It supports a wide range of file formats and offers both local and cloud-based OCR options.

---

## Overview

GoblinTools provides a unified toolkit for extracting text from documents (PDF, DOCX, XLSX, etc.), handling archives (ZIP, RAR, 7z, 30+ formats), and cleaning text with Brazilian Portuguese support. OCR can use local Tesseract or AWS Textract. When AWS credentials are missing, the library falls back to local Tesseract with a warning. For native PDFs, optional **table extraction** (via [pdfplumber](https://pypi.org/project/pdfplumber/)) can detect grids and embed them as Markdown or return structured row matrices — useful for editais, planilhas orçamentárias, and similar bidding documents.

### Architecture

```
goblintools/
├── parser.py             # TextExtractor - main extraction, format parsers, broken-text-layer recovery
├── table_extractor.py    # PDF table detection (pdfplumber), Markdown, quality filters
├── structured/           # Parallel StructuredExtractor (HTML tables + quality gate)
├── pypdf_workarounds.py  # PyPDF monkey-patches (indirect font metrics, optional stream cap)
├── ptbr_words.py         # Embedded PT-BR wordlist helpers (broken-text-layer corroboration)
├── extraction_report.py  # ExtractionReport / PageExtraction - per-page extraction provenance
├── file_handling.py      # FileManager, ArchiveHandler, FileValidator
├── text_cleaner.py       # TextCleaner - clean_text, remove_text_noise, stopwords
├── config.py             # GoblinConfig, OCRConfig
├── log_policy.py         # configure() - library warning verbosity
├── ocr_parser.py         # OCRProcessor - Tesseract / AWS Textract
├── retry.py              # retry_with_backoff utility
└── data/                 # palavras.txt.gz (PT-BR wordlist, MPL-2.0) + its LICENSE
```

### Processing Flow

1. **Text extraction**: File → parser by extension; if unknown or **no extension**, magic-byte sniffing (PDF, RTF, Office Open XML) → extracted text (with `file_path_pwd` tag)
2. **Folder extraction**: Each file’s tag uses the **path relative to the folder** (as inside a zip), e.g. `edital/arquivo.pdf` not the full filesystem path
3. **PDF text**: [pypdf](https://pypi.org/project/pypdf/) (≥ 6.15.0) with built-in workarounds for common producer bugs (e.g. font widths as indirect references). The reader tries the file as-is, merges text from an internal resave when some pages fail, then uses plain and layout extraction modes. Pages whose text turns out to be a **broken text layer** — glyph-code garbage *or* a per-glyph substitution cipher that still looks word-shaped (see below) — are retried with **pdfplumber → poppler `pdftotext` → per-page OCR**. A per-call `TextExtractor.last_extraction_report` records what happened to each page. **Optional OCR** (`ocr_handler=True`): full-document OCR when the PDF has images but almost no text or the whole text layer is a cipher, or **per-page OCR** for pages the text engines cannot decode (requires Poppler for `pdf2image` and Tesseract for local OCR)
4. **PDF tables** (opt-in on `TextExtractor`): with `extract_tables=True`, [pdfplumber](https://pypi.org/project/pdfplumber/) detects tables on each page; the library filters one-column text boxes, collapses split headers, merges continuation rows, and appends Markdown tables after that page’s text (or returns structured matrices via `extract_tables_from_pdf`)
5. **Structured extraction** (parallel API): `StructuredExtractor` extracts item-oriented tables from PDF / XLSX / CSV / DOCX into matrices + quality scores, and can render MinerU-compatible `full.md` with HTML `<table>` blocks — **without changing** plain `TextExtractor` behaviour
6. **Archive extraction**: Format handler → extract to temp → flatten with stable names (extensionless entries preserved) → optionally remove source. Misnamed archives (e.g. `.zip` that is a PDF) use **magic-byte fallbacks**

---

## Installation

```bash
pip install goblintools
```

## Requirements

- **Python**: 3.9 or newer
- **pypdf**: 6.15.0 or newer (declared in package metadata; used for PDF text extraction)
- **pdfplumber**: Used for optional PDF table detection (`extract_tables=True` / `extract_tables_from_pdf`) and as a recovery engine for broken text layers
- **Tesseract OCR**: Required for local OCR support ([Installation Guide](https://github.com/tesseract-ocr/tesseract))
  - **Portuguese Language Pack**: Install `tesseract-ocr-por` for Portuguese text recognition
- **Poppler**: Used by `pdf2image` (OCR) **and** by the `pdftotext` recovery step for broken text layers; install `poppler-utils` (Debian/Ubuntu) or your OS equivalent. If `pdftotext` is not on `PATH`, that recovery step is skipped with a warning.
- **AWS Credentials**: Required for AWS Textract cloud OCR
- **Embedded PT-BR wordlist**: `goblintools/data/palavras.txt.gz` (≈ 265k words) ships with the package and is used only to corroborate broken-text-layer detection. It is derived from [pythonprobr/palavras](https://github.com/pythonprobr/palavras) and distributed under the **MPL-2.0** (see `goblintools/data/palavras.LICENSE`); the rest of goblintools stays MIT.

### PDF extraction notes

- Importing `TextExtractor` applies **pypdf workarounds** once (idempotent): on older pypdf, safer `/Widths` / `space_width` handling; on pypdf 6.15+ the stock text extractor is kept (legacy monkey-patches would break RTL helpers) and only light font-width guards are applied.
- If your pypdf build exposes `MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH` on `pypdf.filters`, the library increases that limit slightly so very large but legitimate content streams can still be decoded; if the attribute is missing (some forks or versions), that step is skipped automatically.
- For scanned PDFs or pages with no usable text layer, enable **`TextExtractor(ocr_handler=True)`** and install Poppler + Tesseract.
- Table extraction targets **native (digital) PDFs** with a text layer and visible table structure. Scanned pages need OCR first; table detection from scans (Textract TABLES / img2table) is not wired yet.
- **Broken font encoding (non-scanned PDFs)**: some PDFs use a font `/Encoding` with a `/Differences` array mapping character codes to non-standard glyph names, with no working `/ToUnicode` CMap. Neither pypdf nor pdfminer can recover real characters from that — the mapping genuinely isn't in the file, even though the PDF renders correctly in any viewer (rendering uses the embedded font program directly, not this metadata). The library detects the resulting garbage in three ways:
  - pypdf's raw `/143`-style glyph-name tokens and pdfminer/pdfplumber's `(cid:143)` markers;
  - a per-glyph substitution where codes land on ASCII punctuation/digits (low letter ratio);
  - a **per-glyph substitution cipher** that keeps word shape and letter/digit categories (e.g. `couvocnrónto`, `LICITAgAO`, `CaberÆ`, `R$ í47.200,04`) — flagged by the per-window rate of cipher-shaped tokens (a mid-word lowercase→UPPERCASE transition that is not two real words glued together), corroborated by a low PT-BR dictionary hit-rate. Clean prose editais sit at ~0; this class used to pass silently as "valid text". Heuristic — heavily tabular budgets and documents whose extracted text contains large non-prose sections (ad-tracking blobs, embedded XML, space-stripped tables) can also trip it; those are reported as low-confidence rather than silently trusted.

  Affected pages are retried with **pdfplumber → poppler `pdftotext`**, and **per-page OCR** only when the corruption is severe (page cipher rate well past the threshold, or unambiguous `/143`/`(cid:N)` garbage) so a false positive on a table page is not re-OCR'd. Whatever the outcome, `extract_from_file` still returns the best text it has (partially-readable content is not lost) and never replaces good text with an inferior engine's output; the result is summarised on `TextExtractor.last_extraction_report` (see below). Without an `ocr_handler`, unrecoverable pages come back as-is with a `text-layer confidence` warning logged.
- **`TextExtractor.last_extraction_report`**: after each `extract_from_file` on a **PDF**, this holds an `ExtractionReport` — `overall_status` is `clean` / `partially_recovered` / `corrupt_unrecoverable`, and `pages` lists the per-page `status` and the `engine` that produced each page (`pypdf` / `pdfplumber` / `poppler` / `ocr`). It is `None` before the first call and for non-PDF inputs, and reflects **only the last call** — do not share one `TextExtractor` across threads if you rely on it. Downstream consumers should treat a non-`clean` status as "do not trust extracted numbers" (write `null` / needs-review rather than a confident `0`).
- **`TextExtractor.last_extraction_reports`**: after `extract_from_folder`, a `dict` mapping each processed **PDF**'s relative path (same string as its `file_path_pwd` tag) to its `ExtractionReport`. Use this instead of `last_extraction_report` when processing a folder, since the single-value attribute would only reflect the last file.

---

## System Dependencies

### Archive Support
For complete archive format support, install these system tools (required by `patoolib`):

| OS | Command |
|----|---------|
| **Debian/Ubuntu** | `sudo apt install unrar p7zip-full p7zip-rar` |
| **Arch Linux** | `sudo pacman -S unrar p7zip` |
| **macOS** | `brew install unrar p7zip` |

### Tesseract OCR with Portuguese Support

| OS | Command |
|----|---------|
| **Debian/Ubuntu** | `sudo apt install tesseract-ocr tesseract-ocr-por` |
| **Arch Linux** | `sudo pacman -S tesseract tesseract-data-por` |
| **macOS** | `brew install tesseract tesseract-lang` |
| **Windows** | Download from [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) and select Portuguese during installation |

---

## Key Features

- **Broad File Support**: Extract text from 20+ document, spreadsheet, and presentation formats
- **Archive Handling**: Supports `.zip`, `.rar`, `.7z`, `.tar`, `.gz`, and 30+ more formats
- **OCR Integration**: Local Tesseract or cloud AWS Textract support
- **Text Cleaning**: `clean_text` (accent folding via unidecode, optional stopwords); `remove_text_noise` (spacing / repeated dots only, **preserves Unicode**)
- **Portuguese OCR**: Optimized for Brazilian Portuguese documents with Tesseract
- **Batch Processing**: Parallel archive extraction
- **File Management**: Comprehensive file/directory operations
- **File Path Tagging**: Automatically includes file path metadata in extracted text (relative paths for folder extraction)
- **Extensionless files**: PDFs and other types without a filename extension are detected from content
- **Robust PDF pipeline**: PyPDF workarounds, resave merge for partial failures, optional targeted OCR for stubborn pages when an OCR handler is configured
- **PDF table extraction**: Opt-in pdfplumber detection with quality filters; embed Markdown in extracted text or get structured `rows` matrices
- **Structured extraction**: Parallel `StructuredExtractor` for PDF/XLSX/CSV/DOCX → HTML `full.md` + `ok_for_items` quality gate (plain text path unchanged)
- **Quiet logs**: Optional suppression of GoblinTools warning logs via `configure()` or constructor flags

---

## Quick Start

### Basic Text Extraction

```python
from goblintools import TextExtractor

extractor = TextExtractor()
text = extractor.extract_from_file("document.pdf")
print(text[:200] + "..." if text else "No text extracted")

# Output includes file path tag:
# file_path_pwd:"document.pdf"
# [extracted text content...]
```

### PDF table extraction

By default, PDF text is flattened (columns lost). Enable tables with `extract_tables=True` to append Markdown tables after each page’s text, or call `extract_tables_from_pdf` for structured data.

```python
from goblintools import TextExtractor, extract_pdf_tables, table_to_markdown

# 1) Embed Markdown tables in the usual str output (good for LLM / RAG)
extractor = TextExtractor(extract_tables=True)  # table_format="markdown"
text = extractor.extract_from_file("edital.pdf")
# file_path_pwd:"edital.pdf"
# ...page text...
#
# <!-- table page=26 index=0 -->
# | ITEM | DESCRIÇÃO | CÓDIGO | UND. | QUANT | VALOR . UNT. | VALOR TOTAL |
# | --- | --- | --- | --- | --- | --- | --- |
# | 1. | ... | ... | SERV | 12 | R$ 16.511,45 | R$ 198.137,40 |

# 2) Structured matrices (page is 1-based)
tables = extractor.extract_tables_from_pdf("edital.pdf")
# [{"page": 26, "index": 0, "rows": [["ITEM", ...], ["1.", ...], ...]}, ...]

# Optional page limit for large editais
tables = extractor.extract_tables_from_pdf("edital.pdf", max_pages=40)

# Helpers also importable directly
tables = extract_pdf_tables("edital.pdf")
print(table_to_markdown(tables[0]["rows"]))
```

What the pipeline does before returning tables:

- Drops likely false positives (e.g. single-column bordered text)
- Removes empty rows, collapses split headers, merges continuation lines into the previous item row

`extract_tables=False` (default) leaves PDF extraction unchanged.

Dev helper at the repo root:

```bash
python scripts/dev_extract_tables.py --max-pages 40
python scripts/dev_extract_tables.py --all-pages
```

### Structured extraction (parallel to plain text)

Use this when you need **HTML tables** and an item-quality gate (e.g. bidding item pipelines). It does **not** alter `TextExtractor.extract_from_file`.

```python
from goblintools import StructuredExtractor
# or: from goblintools.structured import StructuredExtractor

ext = StructuredExtractor()
doc = ext.extract_from_file("edital.pdf")
# doc.tables -> List[TableBlock] with rows + quality
# doc.ok_for_items -> True when at least one table looks like ITEM/DESCRIÇÃO/QTD

if doc.ok_for_items:
    md = ext.to_full_md(doc)          # prose + <table>...</table>
    ext.write_full_md(doc, "out/full.md")

# Also: .xlsx / .xlsm / .csv / .docx
docs = ext.extract_from_folder("prepared/")
```

Supported suffixes: `.pdf`, `.docx`, `.xlsx`, `.xlsm`, `.csv`. Other formats return an empty document with `ok_for_items=False`.

```bash
python scripts/dev_structured_extract.py --max-pages 40
python scripts/dev_structured_extract.py path/to/file.xlsx --write-md /tmp/out
```

### OCR-Enabled Extraction

```python
# Local OCR with Tesseract
extractor = TextExtractor(ocr_handler=True)
text = extractor.extract_from_file("scanned_document.pdf")
# Output: file_path_pwd:"scanned_document.pdf" [OCR extracted text]

# AWS Textract OCR
extractor = TextExtractor(
    ocr_handler=True,
    use_aws=True,
    aws_access_key="your-key",
    aws_secret_key="your-secret",
    aws_region="us-east-1"
)
text = extractor.extract_from_file("document.pdf")
# Output: file_path_pwd:"document.pdf" [AWS Textract extracted text]
```

### Configuration Management

```python
from goblintools import GoblinConfig, OCRConfig, TextExtractor

# Create config programmatically
config = GoblinConfig(
    max_file_size=50 * 1024 * 1024,  # 50MB limit
    ocr=OCRConfig(
        use_aws=True,
        aws_access_key="your-key",
        aws_secret_key="your-secret",
        aws_region="us-west-2",
        tesseract_lang="por"  # Portuguese OCR (default)
    )
)

# Use config with extractor
extractor = TextExtractor(ocr_handler=True, config=config)

# Save config to file
config.to_file("goblin_config.json")

# Load config from file
config = GoblinConfig.from_file("goblin_config.json")
extractor = TextExtractor(ocr_handler=True, config=config)
```

**Example config file (`goblin_config.json`):**
```json
{
  "max_file_size": 52428800,
  "ocr": {
    "use_aws": false,
    "aws_access_key": null,
    "aws_secret_key": null,
    "aws_region": "us-east-1",
    "tesseract_lang": "por"
  }
}
```

**Supported Tesseract Languages:**
- `"por"` - Portuguese (default)
- `"eng"` - English
- `"spa"` - Spanish
- `"por+eng"` - Portuguese + English (multi-language)
- See [Tesseract documentation](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html) for more languages

### Warning logs (library-only)

GoblinTools can hide its own `warning` log lines (errors and third-party libraries such as **patool** are unchanged):

```python
import goblintools
from goblintools import TextExtractor, FileManager

goblintools.configure(suppress_warnings=True)

# Or per component:
extractor = TextExtractor(suppress_warnings=True)
file_manager = FileManager(suppress_warnings=True)
```

Passing `suppress_warnings=False` turns warnings back on. If you omit the argument on `TextExtractor()`, the current setting is left unchanged (so a prior `configure()` call still applies).

### Advanced Features

```python
# Extract from entire folder (respects max_file_size limit)
# Each file's tag uses the path RELATIVE to folder_path (stable layout after zip extract)
text = extractor.extract_from_folder("/path/to/documents")
# Example: file_path_pwd:"edital/anexo_1" ...  file_path_pwd:"anexo.pdf" ...

# Check if PDF needs OCR
if extractor.pdf_needs_ocr("document.pdf"):
    print("This PDF requires OCR processing")

# Validate installation
status = extractor.validate_installation()
print(f"Tesseract available: {status['tesseract']}")
if 'aws_textract' in status:
    print(f"AWS Textract available: {status['aws_textract']}")

# Add custom file parser
def custom_parser(file_path):
    # Your custom extraction logic
    return "extracted text"

extractor.add_parser('.custom', custom_parser)
text = extractor.extract_from_file("file.custom")
# Output: file_path_pwd:"file.custom" extracted text

# Direct OCR processing with config
from goblintools import OCRProcessor, OCRConfig

ocr_config = OCRConfig(use_aws=True, aws_access_key="key", aws_secret_key="secret")
ocr = OCRProcessor(ocr_config)
text = ocr.extract_text_from_pdf("scanned.pdf")
```

---

### File Path Tagging

All extracted text automatically includes the file path as metadata using the `file_path_pwd` tag:

```python
# Single file extraction — tag uses the path you pass in
text = extractor.extract_from_file("document.pdf")
# file_path_pwd:"document.pdf"

# Folder extraction — tag uses path relative to the folder (like inside a zip)
text = extractor.extract_from_folder("/cache/bidding_123")
# file_path_pwd:"edital/anexo_1"
# file_path_pwd:"docs/planilha.xlsx"
```

**File Path Tagging Features:**
- **Automatic tagging**: Every extracted text includes a source path in the tag
- **Folder mode**: Relative paths only (not the full `/cache/...` prefix), so tags stay stable across machines
- **Extensionless names**: Files like `anexo_1` (PDF without extension) are still parsed when content matches known types
- **Consistent format**: `file_path_pwd:"path/to/file"` prefix for easy parsing

---

Example helper script (repo root):

```bash
python scripts/extract_zip_and_text.py path/to/archive.zip [--ocr] [--suppress-warnings] [--work-dir DIR]
```

---

### Archive Extraction

```python
from goblintools import FileManager, FileValidator, ArchiveHandler

# Single archive extraction (handles nested archives); class methods work unchanged
FileManager.extract_files_recursive("archive.zip", "output_folder")

# Or construct FileManager if you need suppress_warnings=True for the session
fm = FileManager(suppress_warnings=True)
# fm.extract_files_recursive(...)  # same APIs as on the class

# Parallel batch extraction
results = FileManager.batch_extract(["file1.zip", "file2.rar"], "output_folder")
print(f"Extraction results: {results}")  # [True, False, ...]

# Batch extraction with progress tracking
def progress_callback(current, total):
    print(f"Progress: {current}/{total} ({current/total*100:.1f}%)")

results = FileManager.batch_extract(
    ["file1.zip", "file2.rar", "file3.7z"],
    "output_folder",
    progress_callback=progress_callback
)

# File validation
if FileValidator.is_archive("file.zip"):
    print("File is a supported archive")

if FileValidator.is_empty("file.txt"):
    print("File is empty")

# Direct archive handling (remove_source=True deletes archive after extraction)
ArchiveHandler.extract("archive.7z", "output")
ArchiveHandler.extract("archive.7z", "output", remove_source=False)  # Keep source

# Add custom archive format
ArchiveHandler.add_format('.custom', lambda f, d: custom_extract(f, d))

# File operations with conflict resolution
FileManager.move_file("source.txt", "destination.txt")  # Auto-renames if exists
FileManager.delete_folder("temp_folder")
FileManager.move_files("folder_path")  # Flatten: relative paths → safe names (e.g. edital/arquivo.pdf → edital_arquivo.pdf); extensionless entries keep the basename
```

---

### Text Cleaning

```python
from goblintools import TextCleaner

# Default Portuguese stopwords
cleaner = TextCleaner()
raw_text = "Isso é um Teste com Acentos!"

# Basic cleaning (remove accents)
clean = cleaner.clean_text(raw_text)
# Output: "Isso e um Teste com Acentos!"

# Full cleaning (lowercase + remove stopwords)
clean = cleaner.clean_text(raw_text, lowercase=True, remove_stopwords=True)
# Output: "teste acentos"

# Custom stopwords
custom_cleaner = TextCleaner(custom_stopwords=['custom', 'words'])
clean = custom_cleaner.remove_stopwords("custom text with words")
# Output: "text with"

# Portuguese text processing example
portuguese_text = "Este é um documento em português com acentuação!"
clean_pt = cleaner.clean_text(portuguese_text, lowercase=True, remove_stopwords=True)
# Output: "documento portugues acentuacao"

# Light noise removal only (collapse whitespace, strip runs of dots) — keeps accents
noisy = "São   Paulo...  centro"
cleaner.remove_text_noise(noisy)
# Output: "São Paulo centro"
```

**`clean_text` vs `remove_text_noise`**

| | `clean_text` | `remove_text_noise` |
|---|----------------|---------------------|
| Repeated dots / extra spaces | Yes | Yes |
| Accent handling | ASCII fold (`unidecode`) | **Unchanged** (keeps ç, ã, etc.) |
| Stopwords / lowercase | Optional | No |

---

## Brazilian Portuguese Support

GoblinTools is optimized for Brazilian Portuguese users:

```python
from goblintools import TextExtractor, TextCleaner, OCRConfig

# Portuguese OCR configuration
config = OCRConfig(
    tesseract_lang="por",  # Portuguese language
    use_aws=False  # Use local Tesseract
)

# Extract Portuguese documents
extractor = TextExtractor(ocr_handler=True, config=config)
text = extractor.extract_from_file("documento_brasileiro.pdf")

# Clean Portuguese text (removes Portuguese stopwords)
cleaner = TextCleaner()  # Uses Portuguese stopwords by default
clean_text = cleaner.clean_text(
    "Este é um texto em português com acentos!",
    lowercase=True,
    remove_stopwords=True
)
print(clean_text)  # Output: "texto portugues acentos"

# Multi-language OCR (Portuguese + English)
multi_config = OCRConfig(tesseract_lang="por+eng")
extractor_multi = TextExtractor(ocr_handler=True, config=multi_config)
```

**Portuguese Features:**
- Default Portuguese stopwords (400+ words)
- Portuguese Tesseract OCR support
- Accent removal with `unidecode`
- Brazilian document format support

---

## Supported Formats

### Documents
`.pdf`, `.docx`, `.odt`, `.rtf`, `.txt`, `.csv`, `.xml`, `.html`

### Spreadsheets
`.xlsx`, `.xls`, `.ods`, `.dbf`

### Presentations
`.pptx`

### Archives
`.zip`, `.rar`, `.7z`, `.tar`, `.gz`, `.bz2`, `.iso`, `.deb`, `.rpm`, `.jar`, `.war`, `.ear`, `.cbz`, `.cbr`, `.cb7`, `.tgz`, `.txz`, `.cbt`, `.udf`, `.ace`, `.cba`, `.arj`, `.cab`, `.chm`, `.cpio`, `.dms`, `.lha`, `.lzh`, `.lzma`, `.lzo`, `.xz`, `.zst`, `.zoo`, `.adf`, `.alz`, `.arc`, `.shn`, `.rz`, `.lrz`, `.a`, `.Z`

---

## API Reference

### configure (module-level)
Import from the package: `from goblintools import configure` (or `import goblintools; goblintools.configure(...)`).

- `configure(suppress_warnings=None)` - If `True` / `False`, sets whether GoblinTools emits warning logs for the process. Omit or pass `None` for no change.

### TextExtractor
- `__init__(ocr_handler=False, use_aws=False, aws_access_key=None, aws_secret_key=None, aws_region='us-east-1', config=None, suppress_warnings=None, extract_tables=False, table_format='markdown')` - `suppress_warnings`: if `True`/`False`, updates library warning policy; if `None`, leaves current setting (e.g. from `configure()`). `extract_tables`: when `True`, detect PDF tables with pdfplumber and embed them in the text. `table_format`: currently only `"markdown"`
- `extract_from_file(file_path, display_path=None)` - Extract text from single file. Returns `str` with `file_path_pwd` tag; optional `display_path` overrides the tag path. For PDFs, populates `last_extraction_report`
- `extract_from_folder(folder_path)` - Extract text from all files in folder (recursively). Tags use **paths relative to** `folder_path`. Populates `last_extraction_reports` (dict keyed by relative path) for PDFs
- `last_extraction_report` / `last_extraction_reports` - `ExtractionReport` provenance for the last PDF (`extract_from_file`) / all PDFs of the last folder run (`extract_from_folder`)
- `extract_tables_from_pdf(file_path, max_pages=None)` - Return a list of `{"page", "index", "rows"}` from a PDF (does not require `extract_tables=True` on the constructor)
- `pdf_needs_ocr(pdf_path)` - Check if PDF requires OCR processing
- `add_parser(extension, parser_func)` - Add custom parser for file extension
- `validate_installation()` - Check if dependencies are properly installed

**Output Format:**
- Always returns `str` (string) with extracted text
- Each file's text is prefixed with a `file_path_pwd:"…"` tag (relative path for folder extraction)
- Multiple files are joined with blank lines between segments
- With `extract_tables=True`, Markdown tables are appended per page after markers like `<!-- table page=N index=M -->`

### Table helpers
Import from the package: `from goblintools import extract_pdf_tables, table_to_markdown, is_meaningful_table, normalize_table_rows`.

- `extract_pdf_tables(pdf_path, max_pages=None, ...)` - Detect and normalize tables; returns `[{"page", "index", "rows"}, ...]`
- `table_to_markdown(rows, header=True)` - Convert a cell matrix to a GitHub-flavored Markdown table
- `normalize_table_rows(rows)` - Drop empty rows, collapse split headers, merge continuation rows
- `is_meaningful_table(rows)` - Quality gate used to drop one-column / tiny fragments

### FileManager
- `__init__(suppress_warnings=None)` - If `True`/`False`, sets library warning suppression for the process
- `extract_files_recursive(archive_path, output_path)` - Extract archive recursively
- `batch_extract(archive_list, output_path, progress_callback=None)` - Extract multiple archives with optional progress tracking
- `move_file(source, destination)` - Move/rename file with conflict resolution and type safety
- `delete_folder(folder_path)` - Delete folder and contents
- `delete_if_empty(file_path)` - Delete file if empty
- `move_files(folder_path)` - Flatten directory structure and normalize filenames

### FileValidator
- `is_empty(file_path)` - Check if file is empty
- `is_archive(file_path)` - Check if file is a supported archive format
- `is_parseable_document(file_path)` - Known document extension (pdf, docx, …)
- `is_zip_by_magic(file_path)` - ZIP signature sniff (misnamed PDF-as-ZIP handling)
- `detect_extension_from_magic(file_path)` - Infer `.pdf`, `.rtf`, `.docx`, `.xlsx`, `.pptx` from content when the filename has no/wrong extension

### ArchiveHandler
- `extract(file_path, destination, remove_source=True)` - Extract archive with collision avoidance. When `remove_source=True` (default), deletes the archive after extraction; set to `False` to keep it.
- `add_format(extension, handler)` - Add support for new archive formats

### TextCleaner
- `__init__(custom_stopwords=None)` - Initialize with custom stopwords (defaults to Portuguese)
- `clean_text(text, lowercase=False, remove_stopwords=False)` - Normalize whitespace and dots, apply `unidecode`, optional lowercase and Portuguese stopword removal
- `remove_text_noise(text)` - Collapse repeated whitespace and strip runs of dots (`..`, `...`); **does not** transliterate accents (use when you need to keep UTF-8 as-is)
- `remove_stopwords(text)` - Remove stopwords from text

### OCRProcessor
- `__init__(config)` - Initialize OCR processor with OCRConfig
- `extract_text_from_pdf(pdf_path)` - Extract text from PDF using OCR

### GoblinConfig
- `__init__(max_file_size=104857600, ocr=None)` - Initialize configuration
- `from_file(config_path)` - Load configuration from JSON file
- `to_file(config_path)` - Save configuration to JSON file
- `default()` - Create default configuration

### OCRConfig
- `__init__(use_aws=False, aws_access_key=None, aws_secret_key=None, aws_region='us-east-1', tesseract_lang='por')` - Initialize OCR configuration
  - `tesseract_lang`: Language for Tesseract OCR (`'por'` for Portuguese, `'eng'` for English, `'por+eng'` for both)
---

## Scripts de Teste

Run tests locally with pytest:

```bash
# Activate venv first
source .venv/bin/activate  # or: .venv\Scripts\activate on Windows

# Run all tests
pytest tests/ -v

# Or
python -m pytest tests/ -v
```

---

## Estrutura do Projeto

```
goblintools/
├── goblintools/           # Package
│   ├── __init__.py
│   ├── parser.py          # TextExtractor
│   ├── table_extractor.py # PDF tables (pdfplumber)
│   ├── structured/        # StructuredExtractor (parallel API)
│   ├── file_handling.py   # FileManager, ArchiveHandler, FileValidator
│   ├── text_cleaner.py    # TextCleaner
│   ├── config.py          # GoblinConfig, OCRConfig
│   ├── log_policy.py      # configure()
│   ├── ocr_parser.py      # OCRProcessor
│   └── retry.py           # retry_with_backoff
├── scripts/               # dev_extract_tables.py, dev_structured_extract.py, ...
├── tests/                 # Pytest tests
│   ├── conftest.py
│   ├── test_structured/
│   └── test_*.py
├── pytest.ini
├── pyproject.toml
└── requirements.txt
```

---

## Troubleshooting

### "Tesseract is not installed or it's not in your PATH"

Install Tesseract and the Portuguese language pack. See [System Dependencies](#system-dependencies) for OS-specific commands.

### "AWS credentials not found; falling back to local Tesseract OCR"

You set `use_aws=True` but did not provide `aws_access_key` and `aws_secret_key` in `OCRConfig`. The library falls back to local Tesseract. To use AWS Textract, pass credentials explicitly in config.

### "No parser available for file extension"

The file format is not supported, or the file has no extension and content could not be identified. PDFs, RTF, and Office Open XML (ZIP) are sniffed automatically; use `extractor.add_parser('.ext', your_parser_func)` for other types.

### Archive extraction fails for RAR/7z

Install system tools: `unrar` and `p7zip`. See [Archive Support](#archive-support).

### Extracted PDF text is garbled — looks like `/143 /120 /108 ...` or `(cid:143)(cid:16)...`, or is readable-looking but not real words

The PDF's font maps character codes to custom glyph names with no working `/ToUnicode` CMap, so pypdf/pdfminer can't recover real characters (see [Broken font encoding](#pdf-extraction-notes) above). This includes the subtle case where the output *keeps word shape* (`couvocnrónto`, `R$ í47.200,04`) — the library now flags it via the rate of internal lowercase→UPPERCASE transitions plus a PT-BR dictionary check. It retries with **pdfplumber → poppler `pdftotext` → OCR** — pass `ocr_handler=True` (and `use_aws=True` with credentials, or install Tesseract + `tesseract-ocr-por` locally) so the fallback has somewhere to go. Without an OCR handler these pages come back unreadable; check `extractor.last_extraction_report.overall_status` (`corrupt_unrecoverable` / `partially_recovered`) and treat non-`clean` results as untrusted. There is no way to decode them from the text layer alone.

---

## Escopo e Limites

- **In scope**: Text extraction from documents, spreadsheets, presentations; optional native-PDF table extraction (pdfplumber → Markdown / row matrices); parallel `StructuredExtractor` (PDF/XLSX/CSV/DOCX → HTML `full.md` + quality); broken-text-layer detection + recovery (pdfplumber → poppler → OCR) with a per-call `ExtractionReport`; archive handling; OCR (Tesseract, AWS Textract); text cleaning (Portuguese-focused); file operations.
- **Out of scope**: Real-time streaming, document conversion to other formats, indexing/search, web scraping. OCR requires Tesseract (local) or AWS credentials (cloud). Table extraction from pure scans (Textract TABLES / img2table) is not included yet. Acting on `last_extraction_report` (e.g. writing `null` instead of a wrong value) is the consumer's responsibility.

---

## Release highlights (0.10.1)

- **Fewer substitution-cipher false positives**: engineering / Petrobras tenders tripped the heuristic on CamelCase alloy codes (`ENiCrFe`, `ERNiCrMo`) and on repeated jargon / brand names (`AltoQi`) in BOQ reference columns. The detector now (a) excludes `E`/`ER`-prefixed alloy designations, and (b) skips any sliding window whose "cipher-shaped" tokens are dominated by a few verbatim repeats — a real per-glyph cipher never emits the same token twice. Production flag rate on random editais dropped from ~3% to ~1%, with clean-prose editais still at ~0 and every known-corrupt document still detected.

## Release highlights (0.10.0)

- **Per-glyph substitution cipher detection**: a broken font `/Differences` map can produce text that keeps word shape and letter/digit categories (`EÍesentadas`, `couvocnrónto`, `R$ í47.200,04`, `Lei nº 14.í33`) — so `_has_meaningful_text` and `_looks_like_encoded_glyphs` both passed and the garbage was indexed as valid. New `_looks_like_substitution_cipher` flags it via the sliding-window rate of internal lowercase→UPPERCASE transitions (a 119-doc clean corpus peaked at 0.0025; corrupted docs run 0.05–0.28), corroborated by a low hit-rate against an **embedded PT-BR wordlist** (`goblintools/data/palavras.txt.gz`, MPL-2.0).
- **Recovery chain gains poppler**: broken pages are now retried **pdfplumber → poppler `pdftotext` → per-page OCR** (poppler via the `pdftotext` binary — no new Python build dependency; skipped with a warning if absent).
- **`TextExtractor.last_extraction_report`** (single file) / **`last_extraction_reports`** (dict, after `extract_from_folder`): an `ExtractionReport` records `overall_status` (`clean` / `partially_recovered` / `corrupt_unrecoverable`) and per-page `status` + `engine`. `extract_from_file` still returns `str` and its output for clean PDFs is unchanged; downstream consumers can now distinguish "trustworthy text" from "recovered / still corrupt" instead of silently storing wrong numbers.
- **Whole-document safety net** also triggers on a whole-document substitution cipher (previously only blank text or `/143`/`(cid:N)` garbage).

### Earlier (0.9.3)

- **Broken font encoding detection**: PDFs whose font `/Encoding /Differences` maps to non-standard glyph names with no `/ToUnicode` CMap used to silently pass through as "successfully extracted" garbage (pypdf's raw `/143`-style tokens counted as meaningful text). `_looks_like_encoded_glyphs` heuristic catches this — plus pdfminer's `(cid:143)` notation and a substitution-cipher variant (codes mapped into ASCII punctuation/digits, detectable via overall letter density) — and retries affected pages with pdfplumber, then per-page OCR when `ocr_handler` is configured.
- **OCR fallback no longer gated on `has_images`**: the whole-document OCR fallback used to only trigger when pypdf found an `/Image` XObject on the page. Broken font encoding has nothing to do with embedded images (it affects native, non-scanned text), so the gate was dropped — OCR now triggers on any unreadable extraction result, image or not.

### Earlier (0.8.0)

- **StructuredExtractor**: New parallel API (`goblintools.structured`) for item-oriented tables from PDF, XLSX/XLSM, CSV, and DOCX. Renders MinerU-compatible HTML `<table>` `full.md` and exposes `ok_for_items` quality gate. Does **not** change `TextExtractor` defaults or plain-text output.
- **Quality**: Footer/note row drop, light cell cleanup, itemish header detection (ITEM/DESCRIÇÃO/QTD), qty/value parse rates.

### Earlier (0.7.8)

- **PDF tables**: Opt-in `TextExtractor(extract_tables=True)` embeds Markdown tables in extracted text; `extract_tables_from_pdf` / `extract_pdf_tables` return structured `rows`. Quality filters drop single-column text boxes; headers and continuation rows are normalized.
- **Dependency**: `pdfplumber` declared for table detection on native PDFs.

### Earlier (0.7.6)

- **PyPDF reliability**: Runtime fixes for `IndirectObject` font metrics and related `extract_text()` failures on real-world editais; compatible with pypdf versions that omit `MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH` on `pypdf.filters`.
- **PDF extraction flow**: Read original PDF first, merge with an internal resave when needed, try multiple extraction modes, optional per-page OCR for gaps when `ocr_handler=True`.
- **Python**: Minimum version remains 3.9; `pypdf>=6.15.0` is required.

---

## License

MIT License
