#!/usr/bin/env python3
"""
Dev helper: run StructuredExtractor on root editais or given files.

Usage:
  python scripts/dev_structured_extract.py
  python scripts/dev_structured_extract.py --max-pages 40
  python scripts/dev_structured_extract.py path/to/file.pdf --write-md /tmp/out
  python scripts/dev_structured_extract.py path/to/itens.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goblintools.structured import STRUCTURED_SUFFIXES, StructuredExtractor


def _default_inputs() -> list[Path]:
    found: list[Path] = []
    for suffix in sorted(STRUCTURED_SUFFIXES):
        found.extend(PROJECT_ROOT.glob(f"*{suffix}"))
    return sorted(found)


def _shape(rows: list) -> str:
    if not rows:
        return "0x0"
    cols = max((len(r) for r in rows), default=0)
    return f"{len(rows)}x{cols}"


def report(path: Path, *, max_pages: int | None, write_md: Path | None) -> bool:
    ext = StructuredExtractor(max_pdf_pages=max_pages)
    print(f"\n=== {path.name} ===")
    doc = ext.extract_from_file(path)
    print(f"ok_for_items={doc.ok_for_items} tables={len(doc.tables)}")
    for t in doc.tables:
        q = t.quality
        loc = f"page={t.page}" if t.page is not None else f"sheet={t.sheet!r}"
        print(
            f"  [{t.source}] {loc} index={t.index} shape={_shape(t.rows)} "
            f"itemish={q.has_itemish_header} data_rows={q.n_data_rows} "
            f"qty={q.qty_parse_rate} valor={q.value_parse_rate} "
            f"ok={q.ok_for_items}"
        )
        # Preview header + first data row
        for row in t.rows[:2]:
            cells = [str(c or "")[:40] for c in row]
            print("   |", " | ".join(cells))

    if write_md is not None and doc.tables:
        dest_dir = write_md / path.stem
        dest = dest_dir / "full.md"
        if ext.write_full_md(doc, dest):
            print(f"  wrote {dest}")

    return doc.ok_for_items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Files to process")
    parser.add_argument("--max-pages", type=int, default=40, help="PDF page cap")
    parser.add_argument(
        "--all-pages", action="store_true", help="No PDF page cap"
    )
    parser.add_argument(
        "--write-md",
        type=Path,
        default=None,
        help="Directory to write full.md per file stem",
    )
    args = parser.parse_args()

    paths = list(args.paths) if args.paths else _default_inputs()
    if not paths:
        print("No input files found.", file=sys.stderr)
        return 2

    max_pages = None if args.all_pages else args.max_pages
    any_ok = False
    for path in paths:
        if not path.is_file():
            print(f"skip missing: {path}", file=sys.stderr)
            continue
        if report(path, max_pages=max_pages, write_md=args.write_md):
            any_ok = True

    return 0 if any_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
