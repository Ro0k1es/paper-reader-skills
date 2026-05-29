#!/usr/bin/env python3
"""Extract complete composite figures from a PDF without saving text pages as figures.

This script is deliberately conservative. It never falls back from "no visual
objects" to a full text page unless `--allow-text-only` is explicitly supplied.
That is the key safeguard against outputs where "Fig. 1" is actually a Results
page containing an in-text reference like "Fig. 1a,b".

Recommended workflow:
    python3 scripts/find_figures.py paper.pdf --json --out figure_index.json
    python3 scripts/extract_figures.py paper.pdf --figures fig1,fig2,fig3 --crop --debug

If a crop is incomplete, rerun with `--full-page` on the verified visual page.
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
REFERENCE_PANEL_RE = re.compile(r"^\s*[a-z](?:\s*[,;:/–—-]|\b)", re.IGNORECASE)
REFERENCE_WORD_RE = re.compile(
    r"^\s*(?:,|;|and|or|to|in|from|for|of|with|shows?|showed|showing|depicts?|illustrates?|indicates?|suggests?)\b",
    re.IGNORECASE,
)
CAPTION_SEPARATOR_RE = re.compile(r"^\s*(?:[\.:\|]|[–—-])")

MIN_VISUAL_FRACTION = 0.015
MIN_WEAK_VISUAL_FRACTION = 0.006
LARGE_CLUSTER_PAGE_FRACTION = 0.012  # at least 1.2% from large clusters


@dataclass
class Caption:
    key: str
    raw_label: str
    page_index: int
    bbox: Any
    text: str
    reason: str
    recommended_page_index: int | None
    recommended_visual_fraction: float
    recommended_visual_count: int
    status: str
    score: float


@dataclass
class FigureJob:
    page_index: int
    out_name: str
    figure_key: str | None = None
    caption: Caption | None = None
    status: str = "manual-page"


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


def parse_list(spec: str | None) -> list[str]:
    if not spec:
        return []
    return [x.strip() for x in spec.split(",") if x.strip()]


def parse_pages(spec: str | None, total_pages: int) -> list[int]:
    if not spec:
        return []
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
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


def safe_rect(fitz: Any, values: Iterable[float]) -> Any | None:
    try:
        rect = fitz.Rect(*values)
    except Exception:
        return None
    if rect.is_empty or rect.is_infinite or rect.width <= 0 or rect.height <= 0:
        return None
    return rect


def expand_rect(fitz: Any, rect: Any, margin: float, page_rect: Any) -> Any:
    out = fitz.Rect(rect.x0 - margin, rect.y0 - margin, rect.x1 + margin, rect.y1 + margin)
    return out & page_rect


def union_rects(fitz: Any, rects: list[Any]) -> Any | None:
    if not rects:
        return None
    out = fitz.Rect(rects[0])
    for rect in rects[1:]:
        out |= rect
    return out


def classify_caption_line(text: str) -> dict[str, str] | None:
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
            items.append({"text": text, "rect": rect})
    return items


def span_rects(fitz: Any, page: Any) -> list[Any]:
    rects: list[Any] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not clean_text(span.get("text", "")):
                    continue
                rect = safe_rect(fitz, span.get("bbox", [0, 0, 0, 0]))
                if rect:
                    rects.append(rect)
    return rects


def visual_rects(fitz: Any, page: Any) -> list[Any]:
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
        if rect.width < 3 or rect.height < 3:
            continue
        if rect.width * rect.height < max(36, page_area * 0.0001):
            continue
        rects.append(rect)
    return rects


def cluster_rects(fitz: Any, rects: list[Any], gap: float = 20) -> list[Any]:
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
        if _has_large_visual_cluster(fitz, doc.load_page(caption_page)):
            return same[0], same[1], same[2], "caption-page-ok"
        else:
            return None, same[1], same[2], "caption-page-only-scattered-visuals"

    safe_candidates = []
    for idx, frac, count in candidates:
        keys = caption_keys_by_page.get(idx, set())
        if idx != caption_page and keys and target_key not in keys:
            continue
        safe_candidates.append((idx, frac, count))

    good = [c for c in safe_candidates if visual_status(c[1], c[2])]
    if good:
        scored: list[tuple[float, int, int]] = []
        for idx, frac, count in good:
            lc = _large_cluster_fraction(fitz, doc.load_page(idx))
            scored.append((frac + lc * 2, idx, count))
        scored.sort(reverse=True)
        best_score, best_idx, best_count = scored[0]
        if _has_large_visual_cluster(fitz, doc.load_page(best_idx)):
            return best_idx, best_score, best_count, "nearby-visual-page"
        else:
            return None, best_score, best_count, "nearby-page-only-scattered-visuals"

    best = max(candidates, key=lambda c: (c[1], c[2]))
    return None, best[1], best[2], "text-only-or-low-visual"


def _large_cluster_fraction(fitz: Any, page: Any) -> float:
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
    return _large_cluster_fraction(fitz, page) >= LARGE_CLUSTER_PAGE_FRACTION


def find_captions(fitz: Any, doc: Any, scan_nearby: int) -> list[Caption]:
    raw_caps: list[dict[str, Any]] = []
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        for item in line_items(fitz, page):
            cap = classify_caption_line(item["text"])
            if not cap:
                continue
            raw_caps.append({"page_index": page_index, "item": item, "cap": cap})

    caption_keys_by_page: dict[int, set[str]] = {}
    for item in raw_caps:
        caption_keys_by_page.setdefault(item["page_index"], set()).add(item["cap"]["key"])

    captions: list[Caption] = []
    for raw in raw_caps:
        page_index = raw["page_index"]
        item = raw["item"]
        cap = raw["cap"]
        rec_page, rec_frac, rec_count, rec_reason = choose_recommended_page(
            fitz, doc, page_index, scan_nearby, cap["key"], caption_keys_by_page
        )
        if rec_page is None:
            status = "warning:text-only-caption-page"
        elif rec_page == page_index:
            status = "ok:caption-page-has-visuals"
        else:
            status = "warning:caption-page-low-visuals-using-nearby-page"
        score = (4 if cap["reason"] == "caption-separator" else 2) + min(6.0, rec_frac * 100) + (1 if rec_page == page_index else 0)
        captions.append(
            Caption(
                key=cap["key"],
                raw_label=cap["raw_label"],
                page_index=page_index,
                bbox=item["rect"],
                text=item["text"],
                reason=cap["reason"],
                recommended_page_index=rec_page,
                recommended_visual_fraction=rec_frac,
                recommended_visual_count=rec_count,
                status=status,
                score=score,
            )
        )

    best: dict[str, Caption] = {}
    for cap in captions:
        if cap.key not in best or cap.score > best[cap.key].score:
            best[cap.key] = cap
    return sorted(best.values(), key=lambda c: (c.page_index, c.key))

def is_caption_rect(rect: Any, captions: list[Caption], page_index: int) -> bool:
    for cap in captions:
        if cap.page_index != page_index:
            continue
        if rect.intersects(cap.bbox):
            return True
    return False


def rect_in_band(rect: Any, y0: float, y1: float) -> bool:
    center_y = (rect.y0 + rect.y1) / 2
    return y0 <= center_y <= y1


def vertical_band_for_caption(
    fitz: Any,
    page: Any,
    cap: Caption | None,
    captions: list[Caption],
    all_visuals: list[Any],
) -> tuple[float, float, str]:
    """Determine figure band using visual-object density, not a hard threshold.

    Compares visual area above vs below the caption line. The side with more
    visual content wins. Only falls back to a position tiebreaker when both
    sides contain negligible visual content.
    """
    page_rect = page.rect
    if cap is None:
        return 0.0, page_rect.height, "no-caption"
    page_caps = sorted(
        [c for c in captions if c.page_index == cap.page_index], key=lambda c: c.bbox.y0
    )
    idx = page_caps.index(cap) if cap in page_caps else -1
    prev_bottom = 0.0
    next_top = page_rect.height
    if idx > 0:
        prev_bottom = page_caps[idx - 1].bbox.y1
    if 0 <= idx < len(page_caps) - 1:
        next_top = page_caps[idx + 1].bbox.y0

    area_above = sum(
        r.width * r.height
        for r in all_visuals
        if prev_bottom <= (r.y0 + r.y1) / 2 < cap.bbox.y0
    )
    area_below = sum(
        r.width * r.height
        for r in all_visuals
        if cap.bbox.y0 <= (r.y0 + r.y1) / 2 < next_top
    )
    min_delta = page_rect.width * page_rect.height * 0.005  # 0.5% of page

    if area_above > area_below + min_delta:
        return (
            max(0.0, prev_bottom),
            max(0.0, cap.bbox.y0),
            "caption-below(density-above={:.1f}%)".format(area_above / (page_rect.width * page_rect.height) * 100),
        )
    elif area_below > area_above + min_delta:
        return (
            min(page_rect.height, cap.bbox.y1),
            min(page_rect.height, next_top),
            "caption-above(density-below={:.1f}%)".format(area_below / (page_rect.width * page_rect.height) * 100),
        )
    # Tie: use caption vertical position as tiebreaker
    if cap.bbox.y0 > page_rect.height * 0.5:
        return max(0.0, prev_bottom), max(0.0, cap.bbox.y0), "caption-below(tiebreaker)"
    else:
        return min(page_rect.height, cap.bbox.y1), min(page_rect.height, next_top), "caption-above(tiebreaker)"


def smart_crop_rect(
    fitz: Any,
    page: Any,
    page_index: int,
    cap: Caption | None,
    captions: list[Caption],
    margin: float,
    allow_text_only: bool,
) -> tuple[Any | None, str, float, int]:
    page_rect = page.rect
    frac, count = visual_score(fitz, page)
    if not visual_status(frac, count) and not allow_text_only:
        return None, "skip:text-only-or-low-visual-page", frac, count

    all_visuals = visual_rects(fitz, page)
    if not all_visuals:
        if allow_text_only:
            return page_rect, "forced-full-page:text-only", frac, count
        return None, "skip:no-visual-objects", frac, count

    y0, y1, band_reason = vertical_band_for_caption(fitz, page, cap, captions, all_visuals) if cap and cap.page_index == page_index else (0.0, page_rect.height, "visual-page")
    visuals = [r for r in all_visuals if rect_in_band(r, y0, y1) and not is_caption_rect(r, captions, page_index)]
    if not visuals:
        visuals = [r for r in all_visuals if not is_caption_rect(r, captions, page_index)]
    if not visuals:
        return None, f"skip:no-visuals-after-caption-filter:{band_reason}", frac, count

    clusters = cluster_rects(fitz, visuals, gap=max(16, page_rect.width * 0.035))
    page_area = page_rect.width * page_rect.height
    # Drop obvious small decorative clusters but keep separated panels.
    large = [c for c in clusters if c.width * c.height > page_area * 0.002 or (c.width > page_rect.width * 0.12 and c.height > page_rect.height * 0.04)]
    if large:
        clusters = large
    visual_union = union_rects(fitz, clusters)
    if visual_union is None:
        return None, f"skip:empty-visual-union:{band_reason}", frac, count

    # Include nearby labels, legends, axis text, and panel letters only after a
    # genuine visual union exists. This prevents text-only pages from becoming figures.
    expanded = expand_rect(fitz, visual_union, margin=48, page_rect=page_rect)
    nearby_text: list[Any] = []
    for rect in span_rects(fitz, page):
        if is_caption_rect(rect, captions, page_index):
            continue
        if not rect_in_band(rect, y0, y1):
            continue
        if rect.intersects(expanded) or expanded.contains(rect.tl) or expanded.contains(rect.br):
            nearby_text.append(rect)

    final_rect = union_rects(fitz, clusters + nearby_text) or visual_union
    final_rect = expand_rect(fitz, final_rect, margin=margin, page_rect=page_rect)
    if final_rect.width < page_rect.width * 0.12 or final_rect.height < page_rect.height * 0.08:
        return None, f"skip:suspiciously-small-crop:{band_reason}", frac, count

    # Trim figure legend text from the bottom of the crop.
    # Figure legends are dense blocks of long text lines (not axis labels / panel letters).
    trimmed, trim_reason = trim_figure_legend(fitz, page, final_rect)
    if trimmed is not None:
        return trimmed, f"smart:{band_reason}+{trim_reason}", frac, count
    return final_rect, f"smart:{band_reason}", frac, count


def trim_figure_legend(
    fitz: Any, page: Any, crop_rect: Any
) -> tuple[Any | None, str]:
    """Remove figure legend text from the bottom of a crop.

    Figure legends differ from in-figure labels (axis text, panel letters,
    color-bar annotations) in three ways:
      1. They consist of long text lines (>30 chars typical).
      2. They form a dense text block with low visual-object density.
      3. They sit at the very bottom of the figure, below all panels.

    Returns (trimmed_rect, reason) or (None, reason) if no trimming needed.
    """
    page_rect = page.rect
    crop_w = crop_rect.width
    crop_h = crop_rect.height

    # Only inspect the bottom 35% of the crop.
    inspect_top = crop_rect.y0 + crop_h * 0.65
    inspect_rect = fitz.Rect(crop_rect.x0, inspect_top, crop_rect.x1, crop_rect.y1)

    text_spans = span_rects(fitz, page)
    visual_objs = visual_rects(fitz, page)

    # Divide into horizontal strips (~8% of crop height each).
    strip_h = max(12, crop_h * 0.08)
    num_strips = max(1, int((crop_rect.y1 - inspect_top) / strip_h))

    strips: list[dict[str, Any]] = []
    for i in range(num_strips):
        y0 = inspect_top + i * strip_h
        y1 = min(crop_rect.y1, y0 + strip_h)
        strip_area = crop_w * (y1 - y0)

        # Text metrics in this strip
        strip_texts = [
            s
            for s in text_spans
            if s.intersects(fitz.Rect(crop_rect.x0, y0, crop_rect.x1, y1))
        ]
        text_area = sum(
            min(s.x1, crop_rect.x1) - max(s.x0, crop_rect.x0)
            for s in strip_texts
            if s.x1 > crop_rect.x0 and s.x0 < crop_rect.x1
        ) * 6  # approximate height per span
        text_area_ratio = min(0.99, text_area / max(strip_area, 1))
        avg_text_width = (
            sum(min(s.width, crop_w) for s in strip_texts) / max(len(strip_texts), 1)
            if strip_texts
            else 0
        )

        # Visual metrics
        strip_visuals = [
            v
            for v in visual_objs
            if v.intersects(fitz.Rect(crop_rect.x0, y0, crop_rect.x1, y1))
        ]
        visual_area = sum(
            min(v.x1, crop_rect.x1) - max(v.x0, crop_rect.x0)
            for v in strip_visuals
            if v.x1 > crop_rect.x0 and v.x0 < crop_rect.x1
        ) * min(v.height for v in strip_visuals) if strip_visuals else 0
        visual_ratio = visual_area / max(strip_area, 1)

        strips.append(
            {
                "y0": y0,
                "y1": y1,
                "text_ratio": text_area_ratio,
                "avg_text_w": avg_text_width,
                "visual_ratio": visual_ratio,
                "n_text": len(strip_texts),
            }
        )

    if not strips:
        return None, "no-legend-trim(strips-empty)"

    # A strip is legend-like when:
    #   - text area > 18% of strip
    #   - average text span width > 30% of crop width (long lines, not axis labels)
    #   - visual object area < 8%
    def _legend_like(s: dict) -> bool:
        return (
            s["text_ratio"] > 0.18
            and s["avg_text_w"] > crop_w * 0.30
            and s["visual_ratio"] < 0.08
        )

    # Find the bottommost strip that is NOT legend-like.
    trim_at = crop_rect.y1
    consecutive_legend = 0
    for s in reversed(strips):
        if _legend_like(s):
            consecutive_legend += 1
        else:
            if consecutive_legend >= 2:
                trim_at = s["y1"]
            break

    # If the entire inspected zone is legend-like, trim to inspect_top.
    if consecutive_legend >= 2 and trim_at == crop_rect.y1:
        # All strips are legend-like; trim to where legend starts.
        first_legend = None
        for s in strips:
            if _legend_like(s):
                first_legend = s["y0"]
                break
        if first_legend is not None:
            trim_at = first_legend

    if trim_at < crop_rect.y1 - crop_h * 0.05:
        # Don't trim if it removes less than 5% of the crop height.
        return None, "no-legend-trim(too-small)"

    trimmed_rect = fitz.Rect(crop_rect.x0, crop_rect.y0, crop_rect.x1, trim_at)
    # Safety: trimmed crop must still be a reasonable figure size.
    if trimmed_rect.height < page_rect.height * 0.06:
        return None, "no-legend-trim(would-destroy-figure)"

    pct_removed = (crop_rect.y1 - trim_at) / crop_h * 100
    return trimmed_rect, f"legend-trim({pct_removed:.0f}%)"


def draw_debug_box(fitz: Any, page: Any, rect: Any, out_path: Path, zoom: float) -> None:
    tmp = fitz.open()
    tmp.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    p = tmp[0]
    p.draw_rect(rect, color=(1, 0, 0), width=2)
    pix = p.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    pix.save(str(out_path))
    tmp.close()


# ── panel detection ──────────────────────────────────────────────

PANEL_LABEL_RE = re.compile(r"^\s*([a-h])\s*$")  # single-panel letters only


def detect_panel_labels(
    fitz: Any,
    page: Any,
    crop_rect: Any,
) -> list[dict[str, Any]]:
    """Find isolated panel-label letters (a, b, c, ...) inside a figure crop.

    A genuine panel label is:
      - a single lowercase letter (a–h)
      - rendered in a small, often bold, font
      - positioned near the top-left of its corresponding visual cluster
      - NOT part of a longer word or sentence
    """
    page_rect = page.rect
    crop_w = crop_rect.width
    crop_h = crop_rect.height
    candidates: list[dict[str, Any]] = []

    try:
        text_dict = page.get_text("dict", clip=crop_rect)
    except Exception:
        return candidates

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            # Collect the full line text and look for isolated single letters.
            line_text = "".join(s.get("text", "") for s in spans).strip()
            m = PANEL_LABEL_RE.match(line_text)
            if not m:
                continue
            label = m.group(1)
            # Compute bounding box of the line.
            bboxes = [safe_rect(fitz, s["bbox"]) for s in spans]
            bboxes = [b for b in bboxes if b is not None]
            if not bboxes:
                continue
            bbox = union_rects(fitz, bboxes) if len(bboxes) > 1 else bboxes[0]

            # Heuristic: panel labels are usually bold and small.
            font_size = max(s.get("size", 0) for s in spans)
            is_bold = any("Bold" in (s.get("font", "") or "") for s in spans)
            # Panel labels should not be too large relative to the crop.
            rel_x = (bbox.x0 - crop_rect.x0) / crop_w if crop_w else 0
            rel_y = (bbox.y0 - crop_rect.y0) / crop_h if crop_h else 0

            candidates.append(
                {
                    "label": label,
                    "bbox": [round(bbox.x0, 1), round(bbox.y0, 1), round(bbox.x1, 1), round(bbox.y1, 1)],
                    "font_size": round(font_size, 1),
                    "is_bold": is_bold,
                    "rel_x": round(rel_x, 3),
                    "rel_y": round(rel_y, 3),
                }
            )

    # Deduplicate: keep the best-scored candidate for each label.
    best: dict[str, dict[str, Any]] = {}
    for c in candidates:
        score = 0
        if c["is_bold"]:
            score += 3
        # Prefer labels in the upper portion of the figure (<40% from top).
        if c["rel_y"] < 0.40:
            score += 2
        # Penalize very large font (likely a heading).
        if c["font_size"] < 14:
            score += 1
        c["_score"] = score
        if c["label"] not in best or score > best[c["label"]]["_score"]:
            best[c["label"]] = c

    # Remove internal scores.
    for v in best.values():
        del v["_score"]

    # Sort by expected reading order: top-to-bottom, left-to-right.
    return sorted(best.values(), key=lambda c: (c["rel_y"], c["rel_x"]))


def extract_panels_from_labels(
    fitz: Any,
    page: Any,
    crop_rect: Any,
    labels: list[dict[str, Any]],
    margin: float = 6,
) -> list[dict[str, Any]]:
    """Partition the figure crop into panels guided by label positions.

    Each panel gets a bounding box that extends from its label to just before
    the next label (or to the crop edge for the last panel). Panels are split
    primarily by vertical gaps between their label rows.
    """
    if len(labels) < 2:
        return []

    panels: list[dict[str, Any]] = []
    n = len(labels)

    for i, lab in enumerate(labels):
        label_bbox = fitz.Rect(*lab["bbox"])
        # The panel starts at its label's top-left (with margin).
        x0 = max(crop_rect.x0, label_bbox.x0 - margin)
        y0 = max(crop_rect.y0, label_bbox.y0 - margin)

        if i + 1 < n:
            next_lab = labels[i + 1]
            next_bbox = fitz.Rect(*next_lab["bbox"])
            # If the next label is on a significantly lower row, split vertically.
            if next_bbox.y0 - label_bbox.y1 > crop_rect.height * 0.03:
                y1 = next_bbox.y0 - margin
                x1 = crop_rect.x1
            else:
                # Same row — split horizontally.
                x1 = next_bbox.x0 - margin
                y1 = crop_rect.y1
        else:
            x1 = crop_rect.x1
            y1 = crop_rect.y1

        panel_rect = fitz.Rect(x0, y0, x1, y1) & crop_rect
        if panel_rect.width > 20 and panel_rect.height > 20:
            panels.append(
                {
                    "panel": lab["label"],
                    "bbox": [round(panel_rect.x0, 1), round(panel_rect.y0, 1), round(panel_rect.x1, 1), round(panel_rect.y1, 1)],
                    "status": "ok",
                }
            )

    return panels


# ── contact sheet ────────────────────────────────────────────────


def build_contact_sheet(
    out_dir: Path,
    manifest: list[dict[str, Any]],
    columns: int = 4,
    thumb_w: int = 320,
) -> Path | None:
    """Create a contact-sheet PNG of all extracted figures for quick QC.

    Each thumbnail is labeled with the figure name and status.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None  # Pillow not installed; skip gracefully

    saved = [m for m in manifest if m.get("status") == "saved" and m.get("path")]
    if not saved:
        return None

    rows = (len(saved) + columns - 1) // columns
    label_h = 28
    cell_w = thumb_w
    cell_h = int(thumb_w * 0.75)  # 4:3 aspect ratio

    canvas = Image.new("RGB", (columns * cell_w, rows * (cell_h + label_h)), (245, 245, 245))
    draw = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except Exception:
        font = ImageFont.load_default()

    for i, m in enumerate(saved):
        row = i // columns
        col = i % columns
        x = col * cell_w
        y = row * (cell_h + label_h)

        # Label
        name = m.get("name", "?")
        reason = m.get("crop_reason", "")
        label = f"{name}  [{reason[:60]}]"
        draw.rectangle([x, y, x + cell_w, y + label_h], fill=(220, 220, 220))
        draw.text((x + 4, y + 4), label, fill=(40, 40, 40), font=font)

        # Thumbnail
        try:
            img = Image.open(m["path"])
            img.thumbnail((cell_w - 8, cell_h - 8), Image.LANCZOS)
            px = x + (cell_w - img.width) // 2
            py = y + label_h + (cell_h - img.height) // 2
            canvas.paste(img, (px, py))
        except Exception:
            draw.text((x + 10, y + label_h + 10), "(load error)", fill=(200, 0, 0), font=font)

    out_path = out_dir / "figure_contact_sheet.png"
    canvas.save(str(out_path), quality=85)
    return out_path


def build_jobs(doc: Any, args: argparse.Namespace, captions: list[Caption]) -> list[FigureJob]:
    pages = parse_pages(args.pages, doc.page_count)
    names = parse_list(args.names)
    figures = [normalize_figure_key(x) or x for x in parse_list(args.figures)]

    jobs: list[FigureJob] = []
    if figures and not pages:
        by_key = {cap.key: cap for cap in captions}
        for fig in figures:
            cap = by_key.get(fig)
            if cap is None:
                print(f"  [WARN] Could not find a true caption anchor for {fig}; skipping instead of guessing a text page", file=sys.stderr)
                continue
            if cap.recommended_page_index is None:
                print(f"  [WARN] Caption found for {fig} on page {cap.page_index + 1}, but no visual figure page passed validation — NOT saving. Use --pages to manually specify.", file=sys.stderr)
                # Do NOT fall back to the caption text page. A text page saved as a
                # figure is worse than a missing figure.
                continue
            jobs.append(FigureJob(page_index=cap.recommended_page_index, out_name=fig.replace(" ", ""), figure_key=fig, caption=cap, status=cap.status))
        if names:
            if len(names) != len(jobs):
                raise ValueError("Number of --names must match located --figures")
            for job, name in zip(jobs, names):
                job.out_name = name
        return jobs

    if not pages:
        raise ValueError("Provide --pages or --figures")

    if not names:
        if figures and len(figures) == len(pages):
            names = [f.replace(" ", "") for f in figures]
        else:
            names = [f"fig_page{p + 1}" for p in pages]
    if len(names) != len(pages):
        raise ValueError(f"Number of names ({len(names)}) must match number of pages ({len(pages)})")

    for i, (page_index, name) in enumerate(zip(pages, names)):
        fig_key = figures[i] if i < len(figures) else None
        cap = None
        target = normalize_figure_key(fig_key)
        if target:
            cap = next((c for c in captions if c.key == target), None)
        elif not fig_key:
            same = [c for c in captions if c.page_index == page_index]
            if same:
                cap = sorted(same, key=lambda c: c.bbox.y0)[-1]
        jobs.append(FigureJob(page_index=page_index, out_name=name, figure_key=fig_key, caption=cap))
    return jobs


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract figure images from a PDF without saving text pages as figures.")
    parser.add_argument("pdf", help="Path to input PDF")
    parser.add_argument("--pages", help="1-based page numbers with verified figures, e.g. 7,9,13")
    parser.add_argument("--figures", help="Figure labels to locate by true captions, e.g. fig1,fig2,'extended data fig 1'")
    parser.add_argument("--names", help="Output names, comma-separated, e.g. fig1,fig2,fig3")
    parser.add_argument("--out-dir", help="Output directory. Defaults to <pdf_stem>_assets/")
    parser.add_argument("--crop", action="store_true", help="Use smart visual-content crop. Recommended.")
    parser.add_argument("--full-page", action="store_true", help="Render the verified visual page as a full page")
    parser.add_argument("--allow-text-only", action="store_true", help="Dangerous: allow text-only pages to be saved. Off by default.")
    parser.add_argument("--zoom", type=float, default=2.4, help="Render zoom factor. Default: 2.4")
    parser.add_argument("--margin", type=float, default=12.0, help="Crop margin in PDF points. Default: 12")
    parser.add_argument("--scan-nearby", type=int, default=2, help="Search +/-N pages for visual page when caption page is low-visual. Default: 2")
    parser.add_argument("--debug", action="store_true", help="Save debug images with red crop boxes")
    parser.add_argument("--manifest", help="Optional JSON manifest path")
    args = parser.parse_args()

    try:
        import fitz
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

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else pdf_path.with_name(pdf_path.stem + "_assets")
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    captions = find_captions(fitz, doc, scan_nearby=max(0, args.scan_nearby))
    try:
        jobs = build_jobs(doc, args, captions)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        doc.close()
        return 2

    matrix = fitz.Matrix(args.zoom, args.zoom)
    manifest: list[dict[str, Any]] = []

    for job in jobs:
        page = doc.load_page(job.page_index)
        page_frac, page_count = visual_score(fitz, page)

        if args.full_page:
            if not visual_status(page_frac, page_count) and not args.allow_text_only:
                clip = None
                reason = "skip:full-page-target-is-text-only-or-low-visual"
            else:
                clip = page.rect
                reason = "full-page:verified-visual-page"
        elif args.crop:
            clip, reason, page_frac, page_count = smart_crop_rect(
                fitz,
                page,
                job.page_index,
                job.caption,
                captions,
                margin=args.margin,
                allow_text_only=args.allow_text_only,
            )
        else:
            if not visual_status(page_frac, page_count) and not args.allow_text_only:
                clip = None
                reason = "skip:target-page-is-text-only-or-low-visual"
            else:
                clip = page.rect
                reason = "full-page-default:verified-visual-page"

        item: dict[str, Any] = {
            "name": job.out_name,
            "page": job.page_index + 1,
            "figure_key": job.figure_key,
            "caption_detected": job.caption.key if job.caption else None,
            "caption_page": job.caption.page_index + 1 if job.caption else None,
            "caption_status": job.caption.status if job.caption else job.status,
            "visual_fraction": round(page_frac, 4),
            "visual_object_count": page_count,
            "crop_reason": reason,
        }

        if clip is None:
            item.update({"status": "skipped", "path": None, "clip": None, "pixels": None, "debug_path": None})
            manifest.append(item)
            print(f"  [SKIP] {job.out_name} <- page {job.page_index + 1} [{reason}] visual_fraction={page_frac:.4f}, objects={page_count}", file=sys.stderr)
            continue

        pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        out_path = out_dir / f"{job.out_name}.png"
        pix.save(str(out_path))

        debug_path = None
        if args.debug:
            debug_path = out_dir / f"{job.out_name}_debug_box.png"
            draw_debug_box(fitz, page, clip, debug_path, args.zoom)

        item.update(
            {
                "status": "saved",
                "path": str(out_path),
                "clip": [round(clip.x0, 2), round(clip.y0, 2), round(clip.x1, 2), round(clip.y1, 2)],
                "pixels": [pix.width, pix.height],
                "debug_path": str(debug_path) if debug_path else None,
            }
        )

        # ── optional panel extraction ──
        panels = []
        if args.crop and clip != page.rect:
            labels = detect_panel_labels(fitz, page, clip)
            if len(labels) >= 2:
                panel_entries = extract_panels_from_labels(fitz, page, clip, labels)
            else:
                panel_entries = []
            for p in panel_entries:
                p_rect = fitz.Rect(*p["bbox"])
                # Render each panel at the same zoom factor.
                p_pix = page.get_pixmap(matrix=matrix, clip=p_rect, alpha=False)
                p_path = out_dir / f"{job.out_name}_panel_{p['panel']}.png"
                p_pix.save(str(p_path))
                p["path"] = str(p_path)
                p["pixels"] = [p_pix.width, p_pix.height]
                panels.append(p)
                print(f"    panel_{p['panel']}.png ({p_pix.width}x{p_pix.height})", file=sys.stderr)
        item["panels"] = panels

        manifest.append(item)
        print(
            f"  {job.out_name}.png <- page {job.page_index + 1} [{reason}] ({pix.width}x{pix.height}) visual_fraction={page_frac:.4f}, objects={page_count}"
        )
        if job.caption:
            print(f"    caption page {job.caption.page_index + 1}: {job.caption.raw_label} | {job.caption.text[:120]}")

    # ── contact sheet ──
    contact_path = build_contact_sheet(out_dir, manifest)
    if contact_path:
        print(f"Contact sheet saved to: {contact_path}", file=sys.stderr)

    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else out_dir / "figure_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    saved = sum(1 for x in manifest if x.get("status") == "saved")
    skipped = sum(1 for x in manifest if x.get("status") == "skipped")
    print(f"\nSaved {saved} figure(s), skipped {skipped} suspicious target(s). Output: {out_dir}")
    print(f"Manifest saved to: {manifest_path}")
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
