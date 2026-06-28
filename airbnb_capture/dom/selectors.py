from __future__ import annotations

from selenium import webdriver

from ..config import KNOWN_CHAT_TESTIDS, KNOWN_DETAILS_TESTIDS, log
from ..models import Selectors
from .js import all_testids, mark_capture_region, scrollable_divs, viewport_width

def detect_selectors(driver: webdriver.Chrome) -> Selectors:
    log.info("Auto-detecting selectors...")
    tids  = all_testids(driver)
    divs  = scrollable_divs(driver)
    vw    = viewport_width(driver)
    log.info("  found %d data-testid value(s), %d scrollable div(s)", len(tids), len(divs))
    if tids:
        log.info("  data-testid values: %s", ", ".join(sorted(tids)))

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

    if not chat_sel:
        ranked = sorted(divs, key=lambda d: d["scrollH"], reverse=True)
        for idx, d in enumerate(ranked):
            cx = d["left"] + d["width"] / 2
            if cx < vw * 0.85:
                chat_sel = f"[data-airbnb-capture-scrollable='{idx}']"
                log.info(
                    "  chat    → largest scrollable div fallback %s "
                    "(scrollH=%s clientH=%s w=%s left=%s)",
                    chat_sel,
                    d["scrollH"],
                    d["clientH"],
                    d["width"],
                    d["left"],
                )
                break

    if chat_sel and chat_sel.startswith("[data-airbnb-capture-scrollable="):
        # Mark scrollable candidates after choosing the rank so Selenium can
        # locate a support-chat container even when Airbnb omits data-testid.
        driver.execute_script("""
            var ranked = [];
            var candidates = Array.from(document.querySelectorAll('div'));
            candidates.push(document.scrollingElement || document.documentElement);
            candidates.forEach(function(el) {
                if (el.scrollHeight <= el.clientHeight + 100) return;
                if (el.clientHeight < 200) return;
                var r = el.getBoundingClientRect();
                var isPage = el === document.body || el === document.documentElement;
                if (!isPage && r.width < 200) return;
                ranked.push({el: el, scrollH: el.scrollHeight, left: r.left, width: r.width});
            });
            ranked.sort(function(a, b) { return b.scrollH - a.scrollH; });
            ranked.forEach(function(item, idx) {
                item.el.setAttribute('data-airbnb-capture-scrollable', String(idx));
            });
        """)

    if not chat_sel:
        region = mark_capture_region(driver)
        if region:
            chat_sel = region["selector"]
            log.info(
                "  chat    → visible content fallback %s "
                "(tag=%s testid=%s text=%s w=%s h=%s left=%s top=%s)",
                chat_sel,
                region.get("tag", ""),
                region.get("testid", ""),
                region.get("textLen", 0),
                region.get("width", 0),
                region.get("height", 0),
                region.get("left", 0),
                region.get("top", 0),
            )

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
