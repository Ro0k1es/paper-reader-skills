#!/usr/bin/env python3
"""Find true figure-caption anchors and recommended visual pages in a PDF.

The main failure mode this script prevents is treating an in-text mention such as
"Fig. 1a,b" in the Results section as the real Figure 1. The detector is
therefore line-based and conservative:

- real caption anchors usually start a line: "Fig. 1 | ...", "Fig. 1. ...",
  "Figure 1: ...", "Extended Data Fig. 1 | ...";
- in-text references such as "... (Fig. 1a,b)" or "Fig. 1 and Extended Data..."
  are rejected by default;
- each caption is paired with a visual-content score. If the caption page looks
  text-only, the script searches nearby pages for a likely visual figure page and
  reports a warning instead of pretending the text page is a figure.

Examples:
    python3 scripts/find_figures.py paper.pdf
    python3 scripts/find_figures.py paper.pdf --json --out figure_index.json
    python3 scripts/find_figures.py paper.pdf --include-references
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

FIGURE_LINE_RE = re.compile(
    r"^\s*(?P<prefix>"
    r"extended\s+data\s+fig(?:ure)?|"
    r"supp(?:lementary)?\.?\s*fig(?:ure)?|"
    r"fig(?:ure)?"
    r")\.?\s*(?P<num>\d+)(?P<tail>.*)$",
    re.IGNORECASE,
)

# Body references after the number, e.g. Fig. 1a,b, Fig. 1 and, Fig. 1 shows.
REFERENCE_PANEL_RE = re.compile(r"^\s*[a-z](?:\s*[,;:/–—-]|\b)", re.IGNORECASE)
REFERENCE_WORD_RE = re.compile(
    r"^\s*(?:,|;|and|or|to|in|from|for|of|with|shows?|showed|showing|depicts?|illustrates?|indicates?|suggests?)\b",
    re.IGNORECASE,
)
CAPTION_SEPARATOR_RE = re.compile(r"^\s*(?:[\.:\|]|[–—-])")

MIN_VISUAL_FRACTION = 0.015  # 1.5% — must be above scatter from logos/decorations
MIN_WEAK_VISUAL_FRACTION = 0.006
LARGE_CLUSTER_PAGE_FRACTION = 0.012  # visual in ≥1 large cluster must exceed this


@dataclass
class CaptionCandidate:
    key: str
    raw_label: str
    page_index: int
    bbox: list[float]
    text: str
    reason: str
    caption_page_visual_fraction: float
    caption_page_visual_count: int
    recommended_page_index: int | None
    recommended_visual_fraction: float
    recommended_visual_count: int
    status: str
    score: float


def clean_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_figure_key_from_parts(prefix: str, num: str) -> str:
    p = clean_text(prefix).lower().replace(".", "")
    p = p.replace("figure", "fig")
    p = p.replace("supplementary", "supp")
    p = re.sub(r"\s+", " ", p).strip()
    if p.startswith("extended data"):
        return f"extended data fig {num}"
    if p.startswith("supp"):
        return f"supp fig {num}"
    return f"fig {num}"


def normalize_figure_key(value: str | None) -> str | None:
    if value is None:
        return None
    s = clean_text(value).lower()
    if not s:
        return None
    s = s.replace("_", " ").replace("-", " ").replace(".", "")
    s = s.replace("figure", "fig")
    s = s.replace("supplementary", "supp")
    s = re.sub(r"\s+", " ", s).strip()
    m = re.fullmatch(r"(?:fig\s*)?(\d+)[a-z]?", s)
    if m:
        return f"fig {m.group(1)}"
    m = re.fullmatch(r"fig\s*(\d+)[a-z]?", s)
    if m:
        return f"fig {m.group(1)}"
    m = re.fullmatch(r"extended data fig\s*(\d+)[a-z]?", s)
    if m:
        return f"extended data fig {m.group(1)}"
    m = re.fullmatch(r"supp fig\s*(\d+)[a-z]?", s)
    if m:
        return f"supp fig {m.group(1)}"
    return s


def classify_caption_line(text: str) -> dict[str, str] | None:
    """Return caption metadata only for likely caption lines.

    This intentionally ignores figure labels that occur mid-sentence because
    those are almost always body-text citations.
    """
    s = clean_text(text)
    m = FIGURE_LINE_RE.match(s)
    if not m:
        return None

    prefix = m.group("prefix")
    num = m.group("num")
    tail = m.group("tail") or ""
    tail_l = tail.strip().lower()

    if REFERENCE_PANEL_RE.match(tail) or REFERENCE_WORD_RE.match(tail):
        return None

    strong_separator = bool(CAPTION_SEPARATOR_RE.match(tail))
    # Some publishers expose captions as "Fig. 1 Title...". Keep these only
    # when the tail is long enough to look like a caption title, not a reference.
    title_like = len(tail_l) >= 50 and not tail_l.lower().startswith(
        (
            "shows ", "showed ", "showing ", "depicts ", "illustrates ",
            "indicates ", "is a", "was ", "has been", "provides ", "represents ",
            "demonstrates", "highlights ", "presents ", "summarizes ", "outlines ",
            "describes ", "contains ", "displays ", "reveals ", "confirms ",
        )
    )
    if not (strong_separator or title_like):
        return None

    return {
        "key": normalize_figure_key_from_parts(prefix, num),
        "raw_label": f"{prefix} {num}",
        "reason": "caption-separator" if strong_separator else "caption-title-like",
    }


def classify_reference_in_line(text: str) -> dict[str, str] | None:
    """Return likely in-text references for diagnostics only."""
    s = clean_text(text)
    # Match labels anywhere in the line, including "(Fig. 1a,b".
    pattern = re.compile(
        r"(?P<prefix>extended\s+data\s+fig(?:ure)?|supp(?:lementary)?\.?\s*fig(?:ure)?|fig(?:ure)?)\.?\s*(?P<num>\d+)(?P<tail>[a-z,;:/–—\- ]{0,20})",
        re.IGNORECASE,
    )
    for m in pattern.finditer(s):
        before = s[: m.start()].strip()
        tail = m.group("tail") or ""
        if before and not before.endswith(("(", "[", ";", ":")):
            # Mid-sentence references are diagnostic only.
            pass
        if REFERENCE_PANEL_RE.match(tail) or REFERENCE_WORD_RE.match(tail) or before:
            return {
                "key": normalize_figure_key_from_parts(m.group("prefix"), m.group("num")),
                "raw_label": f"{m.group('prefix')} {m.group('num')}",
                "reason": "in-text-reference",
            }
    return None


def safe_rect(fitz: Any, values: Iterable[float]) -> Any | None:
    try:
        rect = fitz.Rect(*values)
    except Exception:
        return None
    if rect.is_empty or rect.is_infinite or rect.width <= 0 or rect.height <= 0:
        return None
    return rect


def line_items(fitz: Any, page: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = clean_text(" ".join(span.get("text", "") for span in line.get("spans", [])))
            if not text:
                continue
            rects = [safe_rect(fitz, span.get("bbox", [0, 0, 0, 0])) for span in line.get("spans", [])]
            rects = [r for r in rects if r is not None]
            if not rects:
                continue
            rect = fitz.Rect(rects[0])
            for r in rects[1:]:
                rect |= r
            items.append({"text": text, "bbox": [rect.x0, rect.y0, rect.x1, rect.y1]})
    return items


def visual_rects(fitz: Any, page: Any) -> list[Any]:
    """Return non-text visual objects likely to belong to figures.

    Text spans are deliberately excluded. Falling back to text spans is what
    causes text-only Results pages to be saved as "figures".
    """
    rects: list[Any] = []
    page_area = page.rect.width * page.rect.height
    for img in page.get_image_info() or []:
        rect = safe_rect(fitz, img.get("bbox", [0, 0, 0, 0]))
        if rect and rect.width * rect.height > max(64, page_area * 0.0002):
            rects.append(rect)
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None:
            continue
        rect = fitz.Rect(rect)
        # Exclude thin page rules and tiny artifacts; keep real plot/vector panels.
        if rect.width < 3 or rect.height < 3:
            continue
        if rect.width * rect.height < max(36, page_area * 0.0001):
            continue
        rects.append(rect)
    return rects


def cluster_rects(fitz: Any, rects: list[Any], gap: float = 18) -> list[Any]:
    clusters: list[Any] = []
    for rect in sorted(rects, key=lambda r: (r.y0, r.x0)):
        expanded = fitz.Rect(rect.x0 - gap, rect.y0 - gap, rect.x1 + gap, rect.y1 + gap)
        merged = False
        for i, cluster in enumerate(clusters):
            if expanded.intersects(cluster):
                clusters[i] = cluster | rect
                merged = True
                break
        if not merged:
            clusters.append(fitz.Rect(rect))
    changed = True
    while changed:
        changed = False
        out: list[Any] = []
        for rect in clusters:
            expanded = fitz.Rect(rect.x0 - gap, rect.y0 - gap, rect.x1 + gap, rect.y1 + gap)
            did = False
            for i, other in enumerate(out):
                if expanded.intersects(other):
                    out[i] = other | rect
                    did = True
                    changed = True
                    break
            if not did:
                out.append(rect)
        clusters = out
    return clusters


def visual_score(fitz: Any, page: Any) -> tuple[float, int]:
    rects = visual_rects(fitz, page)
    if not rects:
        return 0.0, 0
    clusters = cluster_rects(fitz, rects)
    page_area = page.rect.width * page.rect.height
    # Sum cluster area, capped at page area, because complex vector figures may
    # contain many overlapping small paths.
    area = min(page_area, sum(c.width * c.height for c in clusters))
    return (area / page_area if page_area else 0.0), len(rects)


def visual_status(frac: float, count: int) -> bool:
    return frac >= MIN_VISUAL_FRACTION or (frac >= MIN_WEAK_VISUAL_FRACTION and count >= 10)


def choose_recommended_page(
    fitz: Any,
    doc: Any,
    caption_page: int,
    scan_nearby: int,
    target_key: str,
    caption_keys_by_page: dict[int, set[str]],
) -> tuple[int | None, float, int, str]:
    candidates: list[tuple[int, float, int]] = []
    for idx in range(max(0, caption_page - scan_nearby), min(doc.page_count, caption_page + scan_nearby + 1)):
        page = doc.load_page(idx)
        frac, count = visual_score(fitz, page)
        candidates.append((idx, frac, count))
    if not candidates:
        return None, 0.0, 0, "no-candidate"

    same = next((c for c in candidates if c[0] == caption_page), None)
    if same and visual_status(same[1], same[2]):
        # Verify: visual must come from large coherent clusters, not scattered decorations.
        if _has_large_visual_cluster(fitz, doc.load_page(caption_page)):
            return same[0], same[1], same[2], "caption-page-ok"
        else:
            return None, same[1], same[2], "caption-page-only-scattered-visuals"

    # When the caption page is low-visual, nearby-page rescue can be useful, but
    # it is also dangerous: the nearest visual page may be a different figure.
    # Therefore never rescue to a page that already has a true caption anchor for
    # another figure. This prevents Fig. 2 from being mapped onto the visual page
    # for Fig. 1 simply because it is nearby.
    safe_candidates = []
    for idx, frac, count in candidates:
        keys = caption_keys_by_page.get(idx, set())
        if idx != caption_page and keys and target_key not in keys:
            continue
        safe_candidates.append((idx, frac, count))

    good = [c for c in safe_candidates if visual_status(c[1], c[2])]
    # Among visual candidates, prefer those with large coherent clusters.
    if good:
        scored: list[tuple[float, int, int, float]] = []
        for idx, frac, count in good:
            lc = _large_cluster_fraction(fitz, doc.load_page(idx))
            scored.append((frac + lc * 2, idx, count, frac))
        scored.sort(reverse=True)
        best_frac, best_idx, best_count, _ = scored[0]
        if _has_large_visual_cluster(fitz, doc.load_page(best_idx)):
            return best_idx, best_frac, best_count, "nearby-visual-page"
        else:
            return None, best_frac, best_count, "nearby-page-only-scattered-visuals"

    best = max(candidates, key=lambda c: (c[1], c[2]))
    return None, best[1], best[2], "text-only-or-low-visual"


def _large_cluster_fraction(fitz: Any, page: Any) -> float:
    """Fraction of page area covered by visual clusters larger than 1% of page."""
    rects = visual_rects(fitz, page)
    if not rects:
        return 0.0
    clusters = cluster_rects(fitz, rects)
    page_area = page.rect.width * page.rect.height
    large_threshold = page_area * 0.01
    large_area = sum(
        c.width * c.height for c in clusters if c.width * c.height > large_threshold
    )
    return large_area / max(page_area, 1)


def _has_large_visual_cluster(fitz: Any, page: Any) -> bool:
    """True when the page contains at least one visual cluster >1.2% of page area."""
    return _large_cluster_fraction(fitz, page) >= LARGE_CLUSTER_PAGE_FRACTION


def find_caption_candidates(pdf_path: Path, include_references: bool = False, scan_nearby: int = 2) -> list[dict[str, Any]]:
    try:
        import fitz
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF is not installed. Try: python3 -m pip install pymupdf") from exc

    doc = fitz.open(str(pdf_path))
    raw_caps: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    # Pass 1: collect all caption anchors first, so nearby-page rescue can avoid
    # visual pages that already belong to another captioned figure.
    for page_idx in range(doc.page_count):
        page = doc.load_page(page_idx)
        cap_frac, cap_count = visual_score(fitz, page)
        for line in line_items(fitz, page):
            cap = classify_caption_line(line["text"])
            if cap:
                raw_caps.append({"page_idx": page_idx, "line": line, "cap": cap, "cap_frac": cap_frac, "cap_count": cap_count})
            elif include_references:
                ref = classify_reference_in_line(line["text"])
                if ref:
                    refs.append(
                        {
                            "figure": ref["key"],
                            "kind": "reference",
                            "caption_page": None,
                            "recommended_page": None,
                            "status": "diagnostic:body-text-reference",
                            "reason": ref["reason"],
                            "page": page_idx + 1,
                            "visual_fraction": round(cap_frac, 4),
                            "visual_object_count": cap_count,
                            "caption_start": line["text"][:300],
                        }
                    )

    caption_keys_by_page: dict[int, set[str]] = {}
    for item in raw_caps:
        caption_keys_by_page.setdefault(item["page_idx"], set()).add(item["cap"]["key"])

    rows: list[CaptionCandidate] = []
    for item in raw_caps:
        page_idx = item["page_idx"]
        line = item["line"]
        cap = item["cap"]
        cap_frac = item["cap_frac"]
        cap_count = item["cap_count"]
        rec_page, rec_frac, rec_count, rec_reason = choose_recommended_page(
            fitz, doc, page_idx, scan_nearby, cap["key"], caption_keys_by_page
        )
        if rec_page is None:
            status = "warning:text-only-caption-page"
        elif rec_page == page_idx:
            status = "ok:caption-page-has-visuals"
        else:
            status = "warning:caption-page-low-visuals-using-nearby-page"
        score = 0.0
        score += 4 if cap["reason"] == "caption-separator" else 2
        score += min(6.0, rec_frac * 100)
        score += 1 if rec_page == page_idx else 0
        rows.append(
            CaptionCandidate(
                key=cap["key"],
                raw_label=cap["raw_label"],
                page_index=page_idx,
                bbox=line["bbox"],
                text=line["text"],
                reason=cap["reason"],
                caption_page_visual_fraction=cap_frac,
                caption_page_visual_count=cap_count,
                recommended_page_index=rec_page,
                recommended_visual_fraction=rec_frac,
                recommended_visual_count=rec_count,
                status=status,
                score=score,
            )
        )

    doc.close()

    best: dict[str, CaptionCandidate] = {}
    for row in rows:
        if row.key not in best or row.score > best[row.key].score:
            best[row.key] = row

    out: list[dict[str, Any]] = []
    for row in sorted(best.values(), key=lambda r: (r.page_index, r.key)):
        out.append(
            {
                "figure": row.key,
                "kind": "caption",
                "caption_page": row.page_index + 1,
                "recommended_page": row.recommended_page_index + 1 if row.recommended_page_index is not None else None,
                "status": row.status,
                "reason": row.reason,
                "caption_bbox": [round(x, 2) for x in row.bbox],
                "caption_page_visual_fraction": round(row.caption_page_visual_fraction, 4),
                "caption_page_visual_object_count": row.caption_page_visual_count,
                "recommended_visual_fraction": round(row.recommended_visual_fraction, 4),
                "recommended_visual_object_count": row.recommended_visual_count,
                "score": round(row.score, 3),
                "caption_start": row.text[:300],
            }
        )
    if include_references:
        out.extend(refs)
    return out

def main() -> int:
    parser = argparse.ArgumentParser(description="Find true figure caption pages and recommended visual pages in a PDF.")
    parser.add_argument("pdf", help="Path to input PDF")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a Markdown table")
    parser.add_argument("--out", help="Optional output path")
    parser.add_argument("--include-references", action="store_true", help="Also list likely in-text references such as Fig. 1a,b")
    parser.add_argument("--scan-nearby", type=int, default=2, help="Search +/-N pages for a visual page when the caption page is text-only. Default: 2")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 2
    if pdf_path.suffix.lower() != ".pdf":
        print(f"ERROR: Input is not a .pdf file: {pdf_path}", file=sys.stderr)
        return 2

    rows = find_caption_candidates(pdf_path, include_references=args.include_references, scan_nearby=max(0, args.scan_nearby))
    if args.json:
        output = json.dumps(rows, ensure_ascii=False, indent=2)
    else:
        lines = [
            "| figure | kind | caption_page | recommended_page | status | visual_fraction | reason | caption_start |",
            "|---|---|---:|---:|---|---:|---|---|",
        ]
        for row in rows:
            caption = (row.get("caption_start") or "").replace("|", "\\|")
            visual_fraction = row.get("recommended_visual_fraction", row.get("visual_fraction", 0))
            lines.append(
                f"| {row.get('figure')} | {row.get('kind')} | {row.get('caption_page') or row.get('page') or ''} | {row.get('recommended_page') or ''} | {row.get('status')} | {visual_fraction} | {row.get('reason')} | {caption} |"
            )
        output = "\n".join(lines) + "\n"

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.write_text(output, encoding="utf-8")
        print(f"Figure index saved to: {out_path}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
