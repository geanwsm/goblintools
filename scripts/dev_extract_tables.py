#!/usr/bin/env python3
"""
Dev helper: detect tables in root editais (or given PDFs) with pdfplumber.

Usage:
  python scripts/dev_extract_tables.py
  python scripts/dev_extract_tables.py --max-pages 30
  python scripts/dev_extract_tables.py path/to/file.pdf --sample-merge
  python scripts/dev_extract_tables.py --max-pages 20 --out /tmp/tables_report.txt
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goblintools import TextExtractor
from goblintools.table_extractor import table_to_markdown


def _default_pdfs() -> list[Path]:
    return sorted(PROJECT_ROOT.glob("*.pdf"))


def _shape(rows: list) -> str:
    if not rows:
        return "0x0"
    cols = max((len(r) for r in rows), default=0)
    return f"{len(rows)}x{cols}"


def report_tables(pdf: Path, *, max_pages: int | None) -> list[dict]:
    extractor = TextExtractor()
    print(f"\n=== {pdf.name} ===")
    tables = extractor.extract_tables_from_pdf(str(pdf), max_pages=max_pages)
    print(f"tables found: {len(tables)}" + (f" (first {max_pages} pages)" if max_pages else ""))

    by_page = Counter(int(t["page"]) for t in tables)
    if by_page:
        dist = ", ".join(f"p{p}:{c}" for p, c in sorted(by_page.items()))
        print(f"by page: {dist}")

    for t in tables:
        rows = t["rows"]
        print(f"\n--- table page={t['page']} index={t['index']} shape={_shape(rows)} ---")
        print(table_to_markdown(rows))

    return tables


def sample_merge(pdf: Path, *, max_pages: int | None) -> str:
    """Run extract_from_file with tables; optionally trim to pages that have tables."""
    extractor = TextExtractor(extract_tables=True)
    # Full extract can be heavy; for large editais we still extract all pages
    # via pypdf + tables for max_pages only is not exposed — so we call helpers.
    from goblintools.table_extractor import (
        extract_pdf_tables,
        format_tables_for_page,
        tables_by_page,
    )
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    limit = len(reader.pages) if max_pages is None else min(len(reader.pages), max_pages)
    page_texts: list[str] = []
    for i in range(limit):
        try:
            page_texts.append(reader.pages[i].extract_text(extraction_mode="layout") or "")
        except Exception:
            try:
                page_texts.append(reader.pages[i].extract_text() or "")
            except Exception:
                page_texts.append("")

    tables = extract_pdf_tables(str(pdf), max_pages=limit)
    by_page = tables_by_page(tables)
    chunks: list[str] = []
    for i, text in enumerate(page_texts):
        page = i + 1
        block = format_tables_for_page(
            by_page.get(page, []), page=page, table_format=extractor.table_format
        )
        if block:
            base = text.rstrip()
            chunks.append(f"{base}\n\n{block}" if base else block)
        else:
            chunks.append(text)
    return "\n".join(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PDF table extraction on editais")
    parser.add_argument("pdfs", nargs="*", type=Path, help="PDF paths (default: *.pdf at repo root)")
    parser.add_argument("--max-pages", type=int, default=40, help="Limit pages scanned (default 40)")
    parser.add_argument(
        "--all-pages",
        action="store_true",
        help="Scan all pages (ignores --max-pages)",
    )
    parser.add_argument(
        "--sample-merge",
        action="store_true",
        help="Also build merged text+markdown preview for pages with tables",
    )
    parser.add_argument("--out", type=Path, help="Write merge preview to this file")
    args = parser.parse_args()

    pdfs = args.pdfs or _default_pdfs()
    if not pdfs:
        print("No PDFs found.", file=sys.stderr)
        return 1

    max_pages = None if args.all_pages else args.max_pages
    total = 0
    for pdf in pdfs:
        if not pdf.exists():
            print(f"Missing: {pdf}", file=sys.stderr)
            continue
        tables = report_tables(pdf.resolve(), max_pages=max_pages)
        total += len(tables)
        if args.sample_merge or args.out:
            merged = sample_merge(pdf.resolve(), max_pages=max_pages)
            if args.sample_merge:
                print("\n=== merge (full) ===")
                print(merged)
            if args.out:
                out_path = args.out
                if len(pdfs) > 1:
                    out_path = args.out.with_name(
                        f"{args.out.stem}_{pdf.stem[:40]}{args.out.suffix}"
                    )
                out_path.write_text(merged, encoding="utf-8")
                print(f"wrote {out_path} ({len(merged)} chars)")

    print(f"\nDone. Total tables across files: {total}")
    return 0 if total > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
