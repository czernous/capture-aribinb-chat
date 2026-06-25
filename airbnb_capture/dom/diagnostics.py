from __future__ import annotations

import time
from pathlib import Path

from selenium import webdriver

from ..config import log
from .selectors import detect_selectors, print_diagnostics

def run_diagnose(driver: webdriver.Chrome, conversation_id: str, domain: str) -> None:
    url = f"{domain}/guest/messages/{conversation_id}"
    log.info("Opening %s for diagnostics", url)
    driver.get(url)
    time.sleep(6)
    print_diagnostics(driver)
    sel = detect_selectors(driver)
    diag = Path("airbnb_diagnose.jpg")
    driver.save_screenshot(str(diag))
    log.info("Diagnostic screenshot → %s", diag)
    log.info("Selectors: chat=%s  details=%s", sel.chat, sel.details)
    input("\nPress Enter to close browser…")
