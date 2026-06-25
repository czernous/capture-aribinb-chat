from __future__ import annotations

from selenium import webdriver

from ..config import KNOWN_CHAT_TESTIDS, KNOWN_DETAILS_TESTIDS, log
from ..models import Selectors
from .js import all_testids, scrollable_divs, viewport_width

def detect_selectors(driver: webdriver.Chrome) -> Selectors:
    log.info("Auto-detecting selectors...")
    tids  = all_testids(driver)
    divs  = scrollable_divs(driver)
    vw    = viewport_width(driver)

    chat_sel    = None
    details_sel = None

    for tid in KNOWN_CHAT_TESTIDS:
        if tid in tids:
            chat_sel = f"[data-testid='{tid}']"
            log.info("  chat    → %s", chat_sel)
            break

    for tid in KNOWN_DETAILS_TESTIDS:
        if tid in tids:
            details_sel = f"[data-testid='{tid}']"
            log.info("  details → %s", details_sel)
            break

    if not chat_sel or not details_sel:
        ranked = sorted(divs, key=lambda d: d["scrollH"], reverse=True)
        for d in ranked:
            cx = d["left"] + d["width"] / 2
            if not chat_sel and cx < vw * 0.75 and d["testid"]:
                chat_sel = f"[data-testid='{d['testid']}']"
                log.info("  chat    → %s (heuristic)", chat_sel)
            if chat_sel and not details_sel and d["left"] > vw * 0.6 and d["testid"]:
                details_sel = f"[data-testid='{d['testid']}']"
                log.info("  details → %s (heuristic)", details_sel)
            if chat_sel and details_sel:
                break

    sel = Selectors()
    if chat_sel:
        sel.chat = chat_sel
    if details_sel:
        sel.details = details_sel
    return sel


def print_diagnostics(driver: webdriver.Chrome) -> None:
    divs = scrollable_divs(driver)
    tids = all_testids(driver)
    print("\n══ SCROLLABLE DIVS ══")
    for d in divs:
        print(f"  testid={d['testid']!r:40s}  scrollH={d['scrollH']:5d}  "
              f"clientH={d['clientH']:5d}  w={d['width']:4d}  left={d['left']:4d}")
    print("\n══ ALL data-testid VALUES ══")
    for tid in sorted(tids):
        print(f"  {tid}")
