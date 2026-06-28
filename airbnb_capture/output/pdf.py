from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

PAGE_SIZES_IN = {
    "a4": (8.27, 11.69),
    "letter": (8.5, 11.0),
}


@dataclass(frozen=True)
class PdfOptions:
    page_size: str = "a4"
    dpi: int = 150
    margin: int = 40
    gap_search: int = 950
    gap_search_down: int = 500
    bubble_pad: int = 36
    merge_gap: int = 80
    text_pad: int = 14
    min_gap: int = 10
    ink_threshold: float = 0.008
    blank_threshold: float = 0.04
    max_analysis_width: int = 420
    analysis_width_fraction: float = 0.68
    quality: int = 82
    debug: bool = False


@dataclass(frozen=True)
class SlicePlan:
    cuts: list[int]
    max_slice_h: int
    scale: float
    page_px: tuple[int, int]
    content_px: tuple[int, int]
    protected_blocks: list[tuple[int, int]]
    text_blocks: list[tuple[int, int]]


def page_pixels(page_size: str, dpi: int) -> tuple[int, int]:
    w_in, h_in = PAGE_SIZES_IN[page_size]
    return round(w_in * dpi), round(h_in * dpi)


def estimate_background(gray: Image.Image) -> int:
    w, h = gray.size
    box = max(8, min(w, h) // 80)
    corners = [
        gray.crop((0, 0, box, box)),
        gray.crop((w - box, 0, w, box)),
        gray.crop((0, h - box, box, h)),
        gray.crop((w - box, h - box, w, h)),
    ]
    return int(max(ImageStat.Stat(c).mean[0] for c in corners))


def row_ink_scores(img: Image.Image, max_analysis_width: int) -> list[float]:
    src_w, src_h = img.size
    analysis_w = min(max_analysis_width, src_w)
    analysis = img.resize((analysis_w, src_h), Image.Resampling.BILINEAR) if analysis_w != src_w else img.copy()

    gray = analysis.convert("L")
    bg_img = Image.new("L", gray.size, estimate_background(gray))
    diff = ImageChops.difference(gray, bg_img)
    diff_mask = diff.point(lambda p: 255 if p > 4 else 0, mode="1")
    dark_mask = gray.point(lambda p: 255 if p < 238 else 0, mode="1")
    mask = ImageChops.logical_or(diff_mask, dark_mask)

    pix = mask.load()
    scores: list[float] = []
    for y in range(src_h):
        count = 0
        for x in range(analysis_w):
            if pix[x, y]:
                count += 1
        scores.append(count / analysis_w)
    analysis.close()
    return scores


def analysis_image(img: Image.Image, options: PdfOptions) -> Image.Image:
    """Use the chat column for page-break analysis, but render the full image."""
    if options.analysis_width_fraction <= 0 or options.analysis_width_fraction >= 1:
        return img.copy()
    analysis_w = max(1, round(img.width * options.analysis_width_fraction))
    return img.crop((0, 0, analysis_w, img.height))


def content_blocks(content: list[bool], merge_gap: int) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    i = 0
    n = len(content)
    while i < n:
        if not content[i]:
            i += 1
            continue
        start = i
        end = i + 1
        i += 1
        gap_start: int | None = None
        while i < n:
            if content[i]:
                if gap_start is not None and i - gap_start > merge_gap:
                    break
                gap_start = None
                end = i + 1
            elif gap_start is None:
                gap_start = i
            i += 1
        blocks.append((start, end))
    return blocks


def protect_blocks(blocks: list[tuple[int, int]], image_h: int, pad: int) -> list[tuple[int, int]]:
    if not blocks:
        return []

    protected: list[tuple[int, int]] = []
    for idx, (start, end) in enumerate(blocks):
        prev_end = blocks[idx - 1][1] if idx > 0 else 0
        next_start = blocks[idx + 1][0] if idx + 1 < len(blocks) else image_h
        padded_start = max(0, start - pad)
        padded_end = min(image_h, end + pad)

        if idx > 0:
            padded_start = max(padded_start, (prev_end + start) // 2)
        if idx + 1 < len(blocks):
            padded_end = min(padded_end, (end + next_start) // 2)

        if protected and padded_start < protected[-1][1]:
            prev_start, prev_end = protected[-1]
            protected[-1] = (prev_start, max(prev_end, padded_end))
        else:
            protected.append((padded_start, padded_end))
    return protected


def safe_rows_from_blocks(image_h: int, blocks: list[tuple[int, int]]) -> list[bool]:
    safe = [True] * image_h
    for start, end in blocks:
        for row in range(max(0, start), min(image_h, end)):
            safe[row] = False
    return safe


def safe_bands(safe: list[bool], lo: int, hi: int, min_gap: int) -> list[tuple[int, int]]:
    bands: list[tuple[int, int]] = []
    i = max(0, lo)
    hi = min(len(safe), hi)
    while i < hi:
        if not safe[i]:
            i += 1
            continue
        start = i
        while i < hi and safe[i]:
            i += 1
        end = i
        if end - start >= min_gap:
            bands.append((start, end))
    return bands


def row_safe_bands_from_scores(
    scores: list[float],
    lo: int,
    hi: int,
    max_score: float,
    min_gap: int,
) -> list[tuple[int, int]]:
    bands: list[tuple[int, int]] = []
    i = max(0, lo)
    hi = min(len(scores), hi)
    while i < hi:
        if scores[i] > max_score:
            i += 1
            continue
        start = i
        while i < hi and scores[i] <= max_score:
            i += 1
        end = i
        if end - start >= min_gap:
            bands.append((start, end))
    return bands


def choose_best_band(bands: list[tuple[int, int]], target: int) -> int:
    for start, end in bands:
        if start <= target < end:
            return target

    def band_key(band: tuple[int, int]) -> tuple[int, int]:
        centre = (band[0] + band[1]) // 2
        earlier_penalty = 20 if centre < target else 0
        return (abs(target - centre) + earlier_penalty, -centre)

    best = min(bands, key=band_key)
    return (best[0] + best[1]) // 2


def choose_cut(
    y: int,
    image_h: int,
    max_slice_h: int,
    safe: list[bool],
    scores: list[float],
    options: PdfOptions,
) -> int:
    target = min(image_h, y + max_slice_h)
    if target >= image_h:
        return image_h
    if image_h - target <= options.gap_search_down:
        return image_h

    min_slice_h = max(300, int(max_slice_h * 0.45))
    lo = max(y + min_slice_h, target - options.gap_search)
    hi = min(image_h, target + options.gap_search_down)

    bands = safe_bands(safe, lo, hi, options.min_gap)
    if bands:
        return max(y + 1, choose_best_band(bands, target))

    text_gap_min = max(4, min(options.min_gap, 10))
    blank_bands = row_safe_bands_from_scores(scores, lo, hi, options.ink_threshold * 0.5, text_gap_min)
    if blank_bands:
        return max(y + 1, choose_best_band(blank_bands, target))

    loose_bands = row_safe_bands_from_scores(scores, lo, hi, options.blank_threshold, text_gap_min)
    if loose_bands:
        return max(y + 1, choose_best_band(loose_bands, target))

    # Prefer an earlier/later low-ink row over a page-fill row through a bubble.
    nearby_low_ink = [
        row for row in range(lo, hi)
        if scores[row] <= options.blank_threshold
    ]
    if nearby_low_ink:
        return max(y + 1, min(nearby_low_ink, key=lambda row: (abs(target - row), -row)))

    best_row = lo
    best_cost = math.inf
    for row in range(lo, hi):
        distance_penalty = abs(target - row) / max(1, options.gap_search)
        cost = scores[row] * 10.0 + distance_penalty
        if cost < best_cost:
            best_cost = cost
            best_row = row
    return max(y + 1, best_row)


def plan_slices(img: Image.Image, options: PdfOptions) -> SlicePlan:
    page_w, page_h = page_pixels(options.page_size, options.dpi)
    content_w = page_w - 2 * options.margin
    content_h = page_h - 2 * options.margin
    if content_w <= 0 or content_h <= 0:
        raise ValueError("PDF margin is too large for page size/DPI")

    src_w, src_h = img.size
    scale = content_w / src_w
    max_slice_h = max(1, int(content_h / scale))

    analysis = analysis_image(img, options)
    scores = row_ink_scores(analysis, options.max_analysis_width)
    analysis.close()
    content_rows = [score >= options.ink_threshold for score in scores]
    blocks = content_blocks(content_rows, options.merge_gap)
    protected_blocks = protect_blocks(blocks, src_h, options.bubble_pad)
    text_blocks = protect_blocks(content_blocks(content_rows, 2), src_h, options.text_pad)
    safe = safe_rows_from_blocks(src_h, protected_blocks)

    cuts = [0]
    y = 0
    while y < src_h:
        nxt = choose_cut(y, src_h, max_slice_h, safe, scores, options)
        if nxt <= y:
            nxt = min(src_h, y + max_slice_h)
        cuts.append(nxt)
        y = nxt

    return SlicePlan(
        cuts=cuts,
        max_slice_h=max_slice_h,
        scale=scale,
        page_px=(page_w, page_h),
        content_px=(content_w, content_h),
        protected_blocks=protected_blocks,
        text_blocks=text_blocks,
    )


def make_pdf_pages(img: Image.Image, plan: SlicePlan, margin: int) -> list[Image.Image]:
    page_w, page_h = plan.page_px
    content_w, content_h = plan.content_px
    pages: list[Image.Image] = []

    for top, bottom in zip(plan.cuts, plan.cuts[1:]):
        if bottom <= top:
            continue
        slice_img = img.crop((0, top, img.width, bottom)).convert("RGB")
        scaled_h = round(slice_img.height * plan.scale)
        page_scale = plan.scale
        if scaled_h > content_h:
            page_scale = min(content_w / slice_img.width, content_h / slice_img.height)
            scaled_h = round(slice_img.height * page_scale)
            scaled_w = round(slice_img.width * page_scale)
        else:
            scaled_w = content_w

        if scaled_w != slice_img.width or scaled_h != slice_img.height:
            slice_img = slice_img.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)

        page = Image.new("RGB", (page_w, page_h), "white")
        x = margin + (content_w - scaled_w) // 2
        page.paste(slice_img, (x, margin))
        pages.append(page)
        slice_img.close()

    return pages


def save_pdf(image: Image.Image, path: Path, options: PdfOptions | None = None) -> Path:
    options = options or PdfOptions()
    path.parent.mkdir(parents=True, exist_ok=True)
    img = image.convert("RGB")
    plan = plan_slices(img, options)
    pages = make_pdf_pages(img, plan, options.margin)
    if not pages:
        raise RuntimeError("PDF export produced no pages")

    first, rest = pages[0], pages[1:]
    first.save(
        path,
        "PDF",
        save_all=True,
        append_images=rest,
        resolution=options.dpi,
        quality=options.quality,
    )
    for page in pages:
        page.close()

    if options.debug:
        path.with_suffix(".cuts.json").write_text(
            json.dumps(
                {
                    "output": str(path),
                    "source_size": list(img.size),
                    "page_pixels": list(plan.page_px),
                    "content_pixels": list(plan.content_px),
                    "scale": plan.scale,
                    "analysis_width_fraction": options.analysis_width_fraction,
                    "blank_threshold": options.blank_threshold,
                    "max_slice_h": plan.max_slice_h,
                    "cuts": plan.cuts,
                    "protected_blocks": plan.protected_blocks,
                    "text_blocks": plan.text_blocks,
                    "pages": len(plan.cuts) - 1,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    img.close()
    return path
