#!/usr/bin/env python3
"""Extract text from a PDF into Markdown for paper-reading workflows.

Backends:
- pypdf: lightweight text extraction
- pymupdf: often better layout handling if installed as `fitz`

Usage:
    python3 scripts/extract_pdf_text.py paper.pdf --out paper_extracted_text.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from typing import Iterable, Optional


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


def extract_with_pypdf(pdf_path: Path, pages_spec: Optional[str]) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("pypdf is not installed. Try: python3 -m pip install pypdf") from exc

    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    selected = parse_pages(pages_spec, total)
    chunks: list[str] = []
    for idx in selected:
        text = reader.pages[idx].extract_text() or ""
        chunks.append(f"\n\n## Page {idx + 1}\n\n{text.strip()}\n")
    return "".join(chunks), total


def extract_with_pymupdf(pdf_path: Path, pages_spec: Optional[str]) -> tuple[str, int]:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF is not installed. Try: python3 -m pip install pymupdf") from exc

    doc = fitz.open(str(pdf_path))
    total = doc.page_count
    selected = parse_pages(pages_spec, total)
    chunks: list[str] = []
    for idx in selected:
        page = doc.load_page(idx)
        text = page.get_text("text") or ""
        chunks.append(f"\n\n## Page {idx + 1}\n\n{text.strip()}\n")
    doc.close()
    return "".join(chunks), total


def extract_auto(pdf_path: Path, pages_spec: Optional[str]) -> tuple[str, int, str]:
    errors: list[str] = []
    for backend, func in (("pymupdf", extract_with_pymupdf), ("pypdf", extract_with_pypdf)):
        try:
            text, total = func(pdf_path, pages_spec)
            if text.strip():
                return text, total, backend
            errors.append(f"{backend}: extracted empty text")
        except Exception as exc:
            errors.append(f"{backend}: {exc}")
    raise RuntimeError("All extraction backends failed:\n" + "\n".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PDF text into a Markdown file.")
    parser.add_argument("pdf", help="Path to input PDF")
    parser.add_argument("--out", help="Output Markdown path. Defaults to <pdf_stem>_extracted_text.md")
    parser.add_argument("--pages", help="1-based pages, e.g. 1-5,8,10-12. Defaults to all pages")
    parser.add_argument("--backend", choices=["auto", "pypdf", "pymupdf"], default="auto")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 2
    if pdf_path.suffix.lower() != ".pdf":
        print(f"ERROR: Input is not a .pdf file: {pdf_path}", file=sys.stderr)
        return 2

    out_path = Path(args.out).expanduser().resolve() if args.out else pdf_path.with_name(pdf_path.stem + "_extracted_text.md")

    if args.backend == "auto":
        text, total_pages, backend_used = extract_auto(pdf_path, args.pages)
    elif args.backend == "pypdf":
        text, total_pages = extract_with_pypdf(pdf_path, args.pages)
        backend_used = "pypdf"
    else:
        text, total_pages = extract_with_pymupdf(pdf_path, args.pages)
        backend_used = "pymupdf"

    header = (
        f"# Extracted PDF Text\n\n"
        f"- Source PDF: `{pdf_path}`\n"
        f"- Extracted at: `{dt.datetime.now().isoformat(timespec='seconds')}`\n"
        f"- Backend: `{backend_used}`\n"
        f"- Total pages: **{total_pages}**\n"
        f"- Page selection: `{args.pages or 'all'}`\n\n"
        f"---\n"
    )
    out_path.write_text(header + text, encoding="utf-8")
    print(f"Extracted text saved to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
