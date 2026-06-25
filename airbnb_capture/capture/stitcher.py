from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PIL import Image
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
        crop = raw.crop((px_left, px_top, px_right, px_bottom)).copy()
    tmp_path.unlink(missing_ok=True)
    return crop


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
        hide_overlays(driver, el)
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

        # Compute the CSS interval this strip covers, then trim guard pixels
        # from shared edges to eliminate repaint artefacts at seam boundaries.
        seg_start = max(actual, covered_css)
        seg_end   = min(actual + viewport_h, total_scroll_h)

        if seg_end <= seg_start:
            strip.close()
            prev_actual = actual
            continue

        if not is_first:
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
