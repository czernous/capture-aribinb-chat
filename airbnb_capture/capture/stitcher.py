from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops, ImageStat
from selenium import webdriver

from ..config import STRIP_OVERLAP_PX, STRIP_PAUSE_S, log
from ..dom.js import scroll_height, scroll_top, set_scroll
from ..dom.overlays import hide_overlays
from ..tmp import ensure_tmp

def wait_for_paint(driver: webdriver.Chrome) -> None:
    """Wait for the browser to finish painting before taking a screenshot."""
    time.sleep(STRIP_PAUSE_S)
    try:
        driver.execute_async_script(
            "const done = arguments[arguments.length-1];"
            "requestAnimationFrame(() => requestAnimationFrame(done));"
        )
    except Exception:
        pass


def take_strip(
    driver: webdriver.Chrome,
    tmp_path: Path,
    px_left: int,
    px_top: int,
    px_right: int,
    px_bottom: int,
) -> Image.Image:
    """
    Screenshot → crop → close file handle → delete temp file → return Image.
    Closing before deleting is required on Windows to release the file lock.
    """
    driver.save_screenshot(str(tmp_path))
    with Image.open(tmp_path) as raw:
        left = max(0, min(raw.width, px_left))
        top = max(0, min(raw.height, px_top))
        right = max(left + 1, min(raw.width, px_right))
        bottom = max(top + 1, min(raw.height, px_bottom))
        crop = raw.crop((left, top, right, bottom)).copy()
    tmp_path.unlink(missing_ok=True)
    return crop


def _estimate_background(gray: Image.Image) -> int:
    w, h = gray.size
    box = max(6, min(w, h) // 60)
    corners = [
        gray.crop((0, 0, box, box)),
        gray.crop((w - box, 0, w, box)),
        gray.crop((0, h - box, box, h)),
        gray.crop((w - box, h - box, w, h)),
    ]
    return int(max(ImageStat.Stat(c).mean[0] for c in corners))


def _row_ink_scores(img: Image.Image, max_width: int = 360) -> list[float]:
    w, h = img.size
    analysis_w = min(max_width, w)
    if analysis_w != w:
        analysis = img.resize((analysis_w, h), Image.Resampling.BILINEAR)
    else:
        analysis = img

    gray = analysis.convert("L")
    bg = _estimate_background(gray)
    diff = ImageChops.difference(gray, Image.new("L", gray.size, bg))
    diff_mask = diff.point(lambda p: 255 if p > 8 else 0, mode="1")
    dark_mask = gray.point(lambda p: 255 if p < 238 else 0, mode="1")
    mask = ImageChops.logical_or(diff_mask, dark_mask)

    pix = mask.load()
    scores: list[float] = []
    for y in range(h):
        ink = 0
        for x in range(analysis_w):
            if pix[x, y]:
                ink += 1
        scores.append(ink / analysis_w)
    return scores


def choose_safe_seam_css(
    strip: Image.Image,
    actual_css: float,
    covered_css: float,
    viewport_h: float,
    device_pixel_ratio: float,
) -> float:
    """
    Pick a strip join row inside the already-overlapped region.

    Joining through text is usually harmless when the page is perfectly stable,
    but support chats can repaint by a pixel or two between scrolls. Moving the
    join to a visually blank row makes those tiny shifts much less noticeable.
    """
    overlap_top_css = max(actual_css, covered_css - 180.0)
    overlap_bottom_css = min(covered_css, actual_css + viewport_h)
    if overlap_bottom_css - overlap_top_css < 8.0:
        return covered_css

    top_px = max(0, round((overlap_top_css - actual_css) * device_pixel_ratio))
    bottom_px = min(strip.height, round((overlap_bottom_css - actual_css) * device_pixel_ratio))
    if bottom_px - top_px < 8:
        return covered_css

    scores = _row_ink_scores(strip.crop((0, top_px, strip.width, bottom_px)))
    min_band = max(3, round(6 * device_pixel_ratio))
    threshold = 0.003
    best: tuple[int, int] | None = None
    i = 0
    while i < len(scores):
        if scores[i] > threshold:
            i += 1
            continue
        start = i
        while i < len(scores) and scores[i] <= threshold:
            i += 1
        end = i
        if end - start >= min_band:
            if best is None:
                best = (start, end)
            else:
                centre = (start + end) // 2
                best_centre = (best[0] + best[1]) // 2
                target = len(scores) - 1
                if abs(target - centre) < abs(target - best_centre):
                    best = (start, end)

    if best is None:
        return covered_css

    seam_px = top_px + (best[0] + best[1]) // 2
    seam_css = actual_css + seam_px / device_pixel_ratio
    return min(covered_css, max(actual_css, seam_css))


def stitch_element(
    driver: webdriver.Chrome,
    el,
    rect: dict,
    device_pixel_ratio: float,
    label: str,
    tmp_subdir: str = "",
) -> Optional[Image.Image]:
    """
    Scroll el from top to bottom, capturing one strip per step, then stitch
    into a single tall image.

    Seam-free stitching
    -------------------
    Every CSS pixel in the scrollable content is represented exactly once.
    For each strip we compute the exact CSS coordinate interval it covers
    [scroll_top, scroll_top + viewport_h], then paste only the non-overlapping
    portion onto the canvas.  `round()` (not `int()`) is used throughout so
    that sub-pixel DPR values (1.25×, 1.5×) don't accumulate floor errors
    that would leave 1-px white gaps between strips.

    Canvas size is derived from the element's actual scrollHeight × DPR,
    not from strip geometry, to guarantee it covers the full content.
    """
    tmp_dir = ensure_tmp(tmp_subdir)

    # Crop box in PNG pixels — computed once, same for every strip
    px_l = round(rect["left"]   * device_pixel_ratio)
    px_t = round(rect["top"]    * device_pixel_ratio)
    px_r = round(rect["right"]  * device_pixel_ratio)
    px_b = round(rect["bottom"] * device_pixel_ratio)

    viewport_h     = rect["height"]                    # CSS px
    total_scroll_h = scroll_height(driver, el)         # CSS px
    max_scroll     = max(0.0, total_scroll_h - viewport_h)
    advance        = max(80.0, viewport_h - STRIP_OVERLAP_PX)

    log.debug(
        "[%s] scrollH=%.0f  viewH=%.0f  maxScroll=%.0f  advance=%.0f  DPR=%.2f",
        label, total_scroll_h, viewport_h, max_scroll, advance, device_pixel_ratio,
    )

    # Build the planned scroll positions; always include exact bottom
    targets: list[float] = []
    pos = 0.0
    while pos < max_scroll - 0.5:
        targets.append(pos)
        pos += advance
    targets.append(max_scroll)   # guaranteed bottom capture

    # Allocate the canvas up front from scrollHeight — no need to hold strips in RAM.
    # Each strip is captured, composited onto the canvas immediately, then discarded.
    # Peak memory = one canvas + one strip at a time, regardless of conversation length.
    strip_w  = px_r - px_l
    canvas_h = max(1, round(total_scroll_h * device_pixel_ratio))
    canvas   = Image.new("RGB", (strip_w, canvas_h), color=(255, 255, 255))

    covered_css  = 0.0
    prev_actual  = None
    strip_count  = 0
    first_strip_h: Optional[int] = None

    for idx, target in enumerate(targets):
        set_scroll(driver, el, target)
        wait_for_paint(driver)
        # Sticky response-time overlays can reappear while scrolling. Hide them
        # immediately before each strip so they do not get captured mid-thread.
        if hide_overlays(driver, el):
            wait_for_paint(driver)
        actual = scroll_top(driver, el)

        # Skip if the browser didn't advance (already at bottom)
        if prev_actual is not None and abs(actual - prev_actual) < 0.5:
            continue

        is_first = (strip_count == 0)
        is_last  = (idx == len(targets) - 1)

        strip = take_strip(driver, tmp_dir / f"{label}_{idx:04d}.png", px_l, px_t, px_r, px_b)
        strip_count += 1

        if first_strip_h is None:
            first_strip_h = strip.height

        # Compute the CSS interval this strip covers. For shared edges, prefer a
        # visually blank seam inside the overlap so tiny support-chat repaints do
        # not cut through text.
        seg_start = max(actual, covered_css)
        seg_end   = min(actual + viewport_h, total_scroll_h)

        if seg_end <= seg_start:
            strip.close()
            prev_actual = actual
            continue

        if not is_first:
            try:
                seg_start = choose_safe_seam_css(
                    strip,
                    actual,
                    covered_css,
                    viewport_h,
                    device_pixel_ratio,
                )
            except Exception as exc:
                log.debug("[%s] safe seam detection failed: %s", label, exc)
                seg_start += 1.0
        if not is_last:
            seg_end   -= 1.0

        if seg_end <= seg_start:
            strip.close()
            prev_actual = actual
            continue

        crop_t  = max(0, round((seg_start - actual) * device_pixel_ratio))
        crop_b  = min(strip.height, round((seg_end  - actual) * device_pixel_ratio))
        paste_y = round(seg_start * device_pixel_ratio)

        if crop_b > crop_t:
            if covered_css > 0 and actual > covered_css + 2.0:
                log.warning("[%s] gap at CSS %.1f–%.1f px", label, covered_css, actual)

            piece = strip.crop((0, crop_t, strip_w, crop_b))
            if paste_y + piece.height > canvas.height:
                ext = Image.new("RGB", (strip_w, paste_y + piece.height), (255, 255, 255))
                ext.paste(canvas, (0, 0))
                canvas = ext
            canvas.paste(piece, (0, paste_y))
            covered_css = max(covered_css, seg_end)
            piece.close()

        # Explicitly free the strip — critical for long conversations
        strip.close()
        log.debug("[%s] strip %d  target=%.1f  actual=%.1f", label, strip_count, target, actual)
        prev_actual = actual

    log.info("[%s] %d strip(s) captured", label, strip_count)
    if strip_count == 0:
        return None

    return canvas
