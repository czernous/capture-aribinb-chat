from __future__ import annotations

import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..config import PAGE_LOAD_WAIT_S, log
from ..dom.js import dpr, elem_rect
from ..dom.overlays import hide_overlays
from ..dom.selectors import detect_selectors
from ..models import Selectors
from ..output.writer import save_jpeg
from .history import load_full_history
from .panels import compose
from .stitcher import stitch_element

def capture_conversation(
    driver: webdriver.Chrome,
    conversation_id: str,
    output_path: Path,
    selectors: Selectors,
    domain: str,
    page_load_extra_s: float,
    capture_details: bool,
) -> None:
    """
    Navigate to one conversation and write a stitched JPEG to output_path.
    Raises on unrecoverable errors — caller records the failure and continues.
    """
    url = f"{domain}/guest/messages/{conversation_id}"
    log.info("─" * 60)
    log.info("Capturing conversation %s", conversation_id)
    driver.get(url)

    wait = WebDriverWait(driver, PAGE_LOAD_WAIT_S)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selectors.chat)))
    except TimeoutException:
        log.warning("Chat selector timed out — running auto-detect")
        selectors = detect_selectors(driver)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selectors.chat)))

    time.sleep(page_load_extra_s)
    device_pixel_ratio = dpr(driver)

    # Locate chat element
    try:
        chat_el = driver.find_element(By.CSS_SELECTOR, selectors.chat)
    except NoSuchElementException:
        log.warning("Chat element missing — re-detecting")
        selectors = detect_selectors(driver)
        chat_el   = driver.find_element(By.CSS_SELECTOR, selectors.chat)

    chat_rect = elem_rect(driver, chat_el)
    log.info("Chat   left=%.0f top=%.0f w=%.0f h=%.0f",
             chat_rect["left"], chat_rect["top"], chat_rect["width"], chat_rect["height"])

    # Locate details panel (optional)
    details_el   = None
    details_rect = None
    if capture_details:
        try:
            details_el   = driver.find_element(By.CSS_SELECTOR, selectors.details)
            details_rect = elem_rect(driver, details_el)
            log.info("Details left=%.0f top=%.0f w=%.0f h=%.0f",
                     details_rect["left"], details_rect["top"],
                     details_rect["width"], details_rect["height"])
        except NoSuchElementException:
            log.warning("Details panel not found — chat only")

    # Load full history
    load_full_history(driver, chat_el)

    # Hide transient overlays before capturing
    n_hidden = hide_overlays(driver, chat_el)
    if n_hidden:
        log.info("Hid %d overlay(s)", n_hidden)
        time.sleep(0.4)

    # Stitch chat
    chat_canvas = stitch_element(
        driver, chat_el, chat_rect, device_pixel_ratio, "chat",
    )
    if chat_canvas is None:
        raise RuntimeError("Chat produced no strips")

    # Stitch details
    details_canvas = None
    if details_el is not None and details_rect is not None:
        details_canvas = stitch_element(
            driver, details_el, details_rect, device_pixel_ratio, "details",
        )
        if details_canvas is None:
            log.warning("Details produced no strips — omitting")

    final = compose(chat_canvas, details_canvas)
    save_jpeg(final, output_path, conversation_id, url)
