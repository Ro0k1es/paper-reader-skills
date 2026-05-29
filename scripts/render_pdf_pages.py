#!/usr/bin/env python3
"""Render selected PDF pages to PNG images for visual figure/table inspection.

Requires PyMuPDF:
    python3 -m pip install pymupdf

Usage:
    python3 scripts/render_pdf_pages.py paper.pdf --pages 1,3-5 --out-dir page_images
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional


def parse_pages(spec: Optional[str], total_pages: int) -> list[int]:
    if not spec:
        return list(range(total_pages))
    pages: set[int] = set()
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start_s, end_s = part.split('-', 1)
            start = int(start_s) if start_s else 1
            end = int(end_s) if end_s else total_pages
            for page in range(start, end + 1):
                if 1 <= page <= total_pages:
                    pages.add(page - 1)
        else:
            page = int(part)
            if 1 <= page <= total_pages:
                pages.add(page - 1)
    return sorted(pages)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render PDF pages to PNG images.")
    parser.add_argument("pdf", help="Path to input PDF")
    parser.add_argument("--pages", help="1-based pages, e.g. 1-5,8,10-12. Defaults to all pages")
    parser.add_argument("--out-dir", help="Output directory. Defaults to <pdf_stem>_page_images")
    parser.add_argument("--zoom", type=float, default=2.0, help="Render zoom factor. Default: 2.0")
    args = parser.parse_args()

    try:
        import fitz  # PyMuPDF
    except Exception:
        print("ERROR: PyMuPDF is not installed. Try: python3 -m pip install pymupdf", file=sys.stderr)
        return 2

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 2
    if pdf_path.suffix.lower() != ".pdf":
        print(f"ERROR: Input is not a .pdf file: {pdf_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else pdf_path.with_name(pdf_path.stem + "_page_images")
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    selected = parse_pages(args.pages, doc.page_count)
    matrix = fitz.Matrix(args.zoom, args.zoom)
    paths = []
    for idx in selected:
        page = doc.load_page(idx)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        out_path = out_dir / f"page_{idx + 1:03d}.png"
        pix.save(str(out_path))
        paths.append(out_path)
    doc.close()

    for path in paths:
        print(path)
    print(f"Rendered {len(paths)} page(s) to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
