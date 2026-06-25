"""
airbnb_chat_capture.py — Capture full Airbnb conversation screenshots for evidence.

Navigates to each Airbnb conversation, scrolls the full message history into the
DOM, and stitches a single JPEG per conversation containing:
  - The complete chat thread (left panel)
  - The Details panel (right panel, optional)
  - A metadata banner stamped at the top (URL, conversation ID, UTC + local time)

Architecture
------------
Single Chrome session, sequential capture.  One browser is reused across all
conversations — no parallel processes, no renderer startup contention, no
resource leaks.  On a typical machine this is faster than parallel headless
Chrome because startup/teardown overhead dominates for short conversations.

History loading
---------------
Uses Chrome CDP (Performance Log) to detect in-flight network requests after
each scroll-to-top.  Waits until the network has been quiet AND the DOM node
count has been stable for several consecutive rounds before declaring done.
The DOM query counts all descendants (not just direct children) so it works
even when Airbnb wraps messages in a single container div.

Usage
-----
Single conversation:
  py airbnb_chat_capture.py 2569717633
  py airbnb_chat_capture.py 2569717633 --out evidence/chat.jpg

Multiple conversations:
  py airbnb_chat_capture.py 2569717633 2507140193 2534821074
  py airbnb_chat_capture.py --ids-file conversations.txt --out-dir evidence/

Diagnose DOM (run when Airbnb deploys changes that break selector detection):
  py airbnb_chat_capture.py 2569717633 --diagnose

Options
-------
  --out PATH        Output path for single capture (default: screenshots/<id>.jpg)
  --out-dir DIR     Output directory for multi capture (default: screenshots/)
  --domain URL      Airbnb domain (default: https://www.airbnb.co.uk)
  --ids-file FILE   Text file with one conversation ID per line (# = comment)
  --diagnose        Print DOM info, detect selectors, save screenshot, then exit
  --no-details      Skip the Details panel
  --delay SECONDS   Extra wait after page load for slow connections (default: 3)
  --verbose         Enable debug logging
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("airbnb_capture")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CHROME_PROFILE_DIR: Path = Path("chrome_airbnb_profile").resolve()
TMP_DIR: Path = Path(".tmp_airbnb_capture").resolve()


# ---------------------------------------------------------------------------
# Selectors — tried in order, first match wins
# ---------------------------------------------------------------------------
KNOWN_CHAT_TESTIDS: list[str] = [
    "message-thread-container",
    "conversation-thread",
    "thread-view",
]
KNOWN_DETAILS_TESTIDS: list[str] = [
    "orbital-panel-details",
    "co-host-panel",
    "reservation-details",
    "details-panel",
]


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
PAGE_LOAD_WAIT_S: int    = 30     # Selenium explicit-wait ceiling
NETWORK_QUIET_S: float   = 2.5   # Seconds of network silence = page settled
HISTORY_STABLE_ROUNDS: int = 3   # Consecutive stable rounds = history done
HISTORY_MAX_ROUNDS: int  = 80    # Hard cap (~4 min for very long chats)
STRIP_OVERLAP_PX: int    = 100   # Overlap between adjacent strips (CSS px)
STRIP_PAUSE_S: float     = 0.6   # Wait after scroll before screenshotting

# URL fragments that indicate Airbnb is fetching message data
MESSAGE_FETCH_PATTERNS: list[str] = ["/api/v3/", "/messaging/", "/message_threads/"]
MESSAGE_FETCH_EXCLUDE:  list[str] = ["jitney", "logging", "tracking", "analytics"]

# Evidence banner
BANNER_HEIGHT_PX: int = 64
BANNER_FONT_SIZE: int = 18
BANNER_BG: tuple      = (30, 30, 30)
BANNER_FG: tuple      = (220, 220, 220)

JPEG_QUALITY: int = 60


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Selectors:
    chat:    str = f"[data-testid='{KNOWN_CHAT_TESTIDS[0]}']"
    details: str = f"[data-testid='{KNOWN_DETAILS_TESTIDS[0]}']"


@dataclass
class CaptureResult:
    conversation_id: str
    output_path: Optional[Path]  = None
    error: Optional[str]         = None
    tb: Optional[str]            = None   # traceback, for debug logging

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class BulkSummary:
    results: list[CaptureResult] = field(default_factory=list)

    @property
    def succeeded(self) -> list[CaptureResult]:
        return [r for r in self.results if r.success]

    @property
    def failed(self) -> list[CaptureResult]:
        return [r for r in self.results if not r.success]

    def print_summary(self) -> None:
        log.info("─" * 60)
        log.info("COMPLETE  %d/%d succeeded", len(self.succeeded), len(self.results))
        for r in self.succeeded:
            log.info("  ✅  %s  →  %s", r.conversation_id, r.output_path)
        for r in self.failed:
            log.error("  ❌  %s  —  %s", r.conversation_id, r.error)
        log.info("─" * 60)


# ---------------------------------------------------------------------------
# Browser — single visible Chrome, persistent profile (keeps login)
# ---------------------------------------------------------------------------
def build_driver() -> webdriver.Chrome:
    """
    One Chrome instance reused across all captures.
    Uses the saved profile so the user stays logged in.
    Performance logging enabled for CDP network monitoring.
    """
    options = Options()
    options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--window-size=1600,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--remote-debugging-port=9200")  # reserved for cookie extraction only
    options.add_argument("--disable-extensions")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


# ---------------------------------------------------------------------------
# Temp directory
# ---------------------------------------------------------------------------
def ensure_tmp(subdir: str = "") -> Path:
    path = TMP_DIR / subdir if subdir else TMP_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def purge_tmp() -> None:
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR, ignore_errors=True)

# ---------------------------------------------------------------------------
# Parallel infrastructure
# ---------------------------------------------------------------------------
from concurrent.futures import ProcessPoolExecutor, as_completed


def _build_headless_driver(worker_index: int, cookies: list[dict], domain: str) -> webdriver.Chrome:
    """
    Anonymous headless Chrome authenticated via injected cookies.

    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1600,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--remote-debugging-port={9300 + worker_index}")
    options.add_argument("--disable-extensions")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    service = Service(ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(30)

    # Must visit the domain before injecting cookies (same-origin restriction)
    driver.get(domain)
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        safe = {k: v for k, v in cookie.items()
                if k in ("name", "value", "domain", "path", "secure", "httpOnly", "expiry")}
        try:
            driver.add_cookie(safe)
        except Exception:
            pass

    return driver


def _extract_cookies(domain: str) -> list[dict]:
    """
    Open a short-lived visible Chrome with the saved profile, grab session
    cookies, then quit — releasing the profile dir lock before workers start.
    """
    log.info("Extracting session cookies from saved profile...")
    driver = build_driver()
    try:
        driver.get(f"{domain}/guest/messages")
        time.sleep(3)
        cookies = driver.get_cookies()
        log.info("  Extracted %d cookie(s)", len(cookies))
        return cookies
    finally:
        driver.quit()


def _worker(
    conversation_id: str,
    output_path: Path,
    domain: str,
    page_load_extra_s: float,
    capture_details: bool,
    worker_index: int,
    cookies: list[dict],
) -> CaptureResult:
    """
    Subprocess entry point.  Must be a module-level function for pickle
    compatibility with ProcessPoolExecutor on Windows (spawn start method).
    """
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s  [worker-{worker_index}]  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # Stagger Chrome startup so renderers don't all compete for the GPU/compositor
    # resource at the exact same millisecond.  Each worker sleeps worker_index * 4s
    # before creating its browser.  Worker 0 starts immediately; worker 3 waits 12s.
    # The actual capture (30-120s) runs concurrently — the stagger cost is negligible.
    # This is applied inside the worker (not at submission time) so the ProcessPoolExecutor
    # spawns all processes immediately but they start Chrome in sequence.
    if worker_index > 0:
        time.sleep(worker_index * 4.0)

    driver = None
    try:
        driver = _build_headless_driver(worker_index, cookies, domain)
        capture_conversation(
            driver=driver,
            conversation_id=conversation_id,
            output_path=output_path,
            selectors=Selectors(),
            domain=domain,
            page_load_extra_s=page_load_extra_s,
            capture_details=capture_details,
        )
        return CaptureResult(conversation_id=conversation_id, output_path=output_path)
    except Exception as exc:
        return CaptureResult(
            conversation_id=conversation_id,
            error=str(exc),
            tb=traceback.format_exc(),
        )
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def run_bulk_capture(
    conversation_ids: list[str],
    out_flag: Optional[str],
    out_dir: str,
    domain: str,
    page_load_extra_s: float,
    capture_details: bool,
    max_workers: int,
) -> BulkSummary:
    """
    Capture conversations in parallel using headless Chrome workers.

    Worker count is capped at 2 on Windows because each headless Chrome
    renderer needs ~500 MB RAM and its own GPU process.  Starting more than
    2 simultaneously causes renderer startup failures regardless of staggering.
    2 workers still gives a meaningful speedup for 3+ conversations.

    Flow:
      1. Extract session cookies with a single visible Chrome (3 s).
      2. Submit all jobs to a process pool; each worker builds its own headless
         Chrome, injects the cookies, and runs independently.
      3. Workers are submitted with a 3 s gap between each so Chrome renderers
         don't all allocate RAM at the exact same millisecond.
    """
    summary  = BulkSummary()
    is_multi = len(conversation_ids) > 1

    jobs = [
        (cid, resolve_path(cid, out_flag, out_dir, is_multi))
        for cid in conversation_ids
    ]

    try:
        cookies = _extract_cookies(domain)
    except Exception as exc:
        log.error("Cookie extraction failed: %s", exc)
        for cid, _ in jobs:
            summary.results.append(CaptureResult(cid, error=f"Cookie extraction failed: {exc}"))
        return summary

    if not cookies:
        log.error("No session cookies — are you logged in to Airbnb in the saved profile?")
        for cid, _ in jobs:
            summary.results.append(CaptureResult(cid, error="No session cookies found"))
        return summary

    n_workers = min(max_workers, len(jobs))
    log.info("Launching %d parallel worker(s) for %d conversation(s)...", n_workers, len(jobs))

    futures: dict = {}
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        for idx, (cid, output_path) in enumerate(jobs):
            log.info("  Queuing %s -> %s (slot %d)", cid, output_path, idx)
            future = executor.submit(
                _worker,
                cid, output_path, domain, page_load_extra_s,
                capture_details, idx, cookies,
            )
            futures[future] = cid

        for future in as_completed(futures):
            cid = futures[future]
            try:
                result = future.result()
                if result.success:
                    log.info("OK  %s -> %s", cid, result.output_path)
                else:
                    log.error("FAIL %s -- %s", cid, result.error)
                    if result.tb:
                        log.debug("Traceback:\n%s", result.tb)
                summary.results.append(result)
            except Exception as exc:
                log.error("Worker crashed for %s: %s", cid, exc)
                summary.results.append(CaptureResult(cid, error=str(exc)))

    purge_tmp()
    return summary



# ---------------------------------------------------------------------------
# JS helpers — thin wrappers so callers read cleanly
# ---------------------------------------------------------------------------
def _js(driver: webdriver.Chrome, script: str, *args):
    return driver.execute_script(script, *args)


def elem_rect(driver: webdriver.Chrome, el) -> dict:
    return _js(driver, """
        var r = arguments[0].getBoundingClientRect();
        return {left:r.left, top:r.top, right:r.right, bottom:r.bottom,
                width:r.width, height:r.height};
    """, el)


def scroll_height(driver: webdriver.Chrome, el) -> float:
    return float(_js(driver, "return arguments[0].scrollHeight;", el) or 0)


def scroll_top(driver: webdriver.Chrome, el) -> float:
    return float(_js(driver, "return arguments[0].scrollTop;", el) or 0)


def set_scroll(driver: webdriver.Chrome, el, value: float) -> None:
    _js(driver, "arguments[0].scrollTop = arguments[1];", el, value)


def dpr(driver: webdriver.Chrome) -> float:
    return float(_js(driver, "return window.devicePixelRatio;") or 1.0)


def viewport_width(driver: webdriver.Chrome) -> int:
    return int(_js(driver, "return window.innerWidth;") or 1600)


def all_testids(driver: webdriver.Chrome) -> set[str]:
    raw = _js(driver, """
        var s = new Set();
        document.querySelectorAll('[data-testid]').forEach(e => s.add(e.getAttribute('data-testid')));
        return Array.from(s);
    """)
    return set(raw or [])


def scrollable_divs(driver: webdriver.Chrome) -> list[dict]:
    return _js(driver, """
        var out = [];
        document.querySelectorAll('div').forEach(function(el) {
            if (el.scrollHeight <= el.clientHeight + 100) return;
            if (el.clientHeight < 200) return;
            var r = el.getBoundingClientRect();
            if (r.width < 200) return;
            out.push({
                testid: el.getAttribute('data-testid') || '',
                scrollH: el.scrollHeight, clientH: el.clientHeight,
                width: Math.round(r.width), left: Math.round(r.left)
            });
        });
        return out;
    """) or []


def descendant_count(driver: webdriver.Chrome, el) -> int:
    """
    Count all descendants inside the chat container.
    More reliable than childElementCount because Airbnb wraps everything
    in a single div; descendant count always grows as messages load.
    """
    return int(_js(driver, "return arguments[0].querySelectorAll('*').length;", el) or 0)


# ---------------------------------------------------------------------------
# CDP network monitoring
# ---------------------------------------------------------------------------
def drain_network_log(driver: webdriver.Chrome) -> list[str]:
    """Read and clear the performance log; return matched message-fetch URLs."""
    try:
        entries = driver.get_log("performance")
    except Exception:
        return []
    matched = []
    for entry in entries:
        try:
            msg    = json.loads(entry["message"])["message"]
            method = msg.get("method", "")
            if method not in ("Network.responseReceived", "Network.requestWillBeSent"):
                continue
            params = msg.get("params", {})
            url    = (params.get("response") or params.get("request") or {}).get("url", "")
            if not url:
                continue
            if (any(p in url for p in MESSAGE_FETCH_PATTERNS)
                    and not any(x in url for x in MESSAGE_FETCH_EXCLUDE)):
                matched.append(url)
        except Exception:
            pass
    return matched


def wait_network_quiet(driver: webdriver.Chrome, quiet_s: float = NETWORK_QUIET_S) -> None:
    """Block until no message-fetch requests have fired for quiet_s seconds."""
    deadline    = time.monotonic() + 45   # hard cap
    last_active = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.3)
        if drain_network_log(driver):
            last_active = time.monotonic()
        elif time.monotonic() - last_active >= quiet_s:
            return
    log.debug("wait_network_quiet: deadline reached")


# ---------------------------------------------------------------------------
# Selector detection
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# History loading
# ---------------------------------------------------------------------------
def history_state(driver: webdriver.Chrome, el) -> dict:
    """Return DOM signals that change when older messages are actually loaded."""
    return _js(driver, """
        var el = arguments[0];
        var text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        return {
            nodes: el.querySelectorAll('*').length,
            scrollHeight: Math.round(el.scrollHeight || 0),
            textLength: text.length,
            firstText: text.slice(0, 300)
        };
    """, el) or {"nodes": 0, "scrollHeight": 0, "textLength": 0, "firstText": ""}


def nudge_scroll(driver: webdriver.Chrome, el) -> None:
    """
    Simulate natural upward scrolling to trigger Airbnb's lazy-history sentinel.
    Direct scrollTop=0 alone is not always enough in headless Chrome.
    """
    _js(driver, """
        var el = arguments[0];
        el.style.scrollBehavior = 'auto';
        var h = el.clientHeight || 600;

        // Start slightly below the top so the top sentinel crosses the viewport.
        el.scrollTop = Math.max(0, Math.min(el.scrollTop || 0, h * 2));
        el.dispatchEvent(new Event('scroll', {bubbles: true}));

        for (var i = 0; i < 8; i++) {
            el.scrollTop = Math.max(0, el.scrollTop - h * 0.35);
            el.dispatchEvent(new WheelEvent('wheel', {
                deltaY: -Math.max(120, h * 0.25),
                bubbles: true,
                cancelable: true,
                view: window
            }));
            el.dispatchEvent(new Event('scroll', {bubbles: true}));
        }

        el.scrollTop = 0;
        el.dispatchEvent(new Event('scroll', {bubbles: true}));
    """, el)


def click_older_history_control(driver: webdriver.Chrome, chat_el) -> str:
    """
    Click a visible control that specifically looks like older-history loading.

    Avoid generic "show more" / "see more" because Airbnb uses those in many
    places and they can cause pointless loops without loading message history.
    Returns the clicked control text, or an empty string if nothing was clicked.
    """
    try:
        return str(_js(driver, """
            var root = arguments[0];
            var phrases = [
                'older messages', 'previous messages', 'earlier messages',
                'load older', 'load previous', 'load earlier',
                'show older', 'show previous', 'show earlier',
                'see older', 'see previous', 'see earlier'
            ];
            var candidates = Array.from(root.querySelectorAll('button, a, [role="button"]'));
            for (var i = 0; i < candidates.length; i++) {
                var el = candidates[i];
                var text = (el.innerText || el.textContent || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                if (!text) continue;
                var r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                if (phrases.some(function(p) { return text.indexOf(p) >= 0; })) {
                    el.click();
                    return text.slice(0, 120);
                }
            }
            return '';
        """, chat_el) or "")
    except Exception:
        return ""


def load_full_history(driver: webdriver.Chrome, chat_el) -> None:
    """
    Scroll to the top repeatedly until older message history stops changing.

    Completion is based on several signals, not just direct child count:
      • descendant node count
      • scrollHeight
      • text length
      • first visible/loaded text

    This matters because Airbnb often wraps the whole thread in one direct child,
    so childElementCount can stay at 1 even while the real message DOM changes.
    """
    log.info("Loading full message history...")

    # Jump to bottom first so Airbnb marks the thread active, then clear
    # network-log noise from initial page load.
    set_scroll(driver, chat_el, 999_999)
    time.sleep(2.0)
    drain_network_log(driver)

    previous_key = None
    stable_rounds = 0
    no_progress_clicks = 0
    last_state = {"nodes": 0, "scrollHeight": 0, "textLength": 0, "firstText": ""}

    for round_num in range(1, HISTORY_MAX_ROUNDS + 1):
        clicked_text = click_older_history_control(driver, chat_el)
        if clicked_text:
            time.sleep(0.6)

        nudge_scroll(driver, chat_el)
        wait_network_quiet(driver)
        time.sleep(0.8)
        wait_network_quiet(driver)

        state = history_state(driver, chat_el)
        key = (
            state.get('nodes'),
            state.get('scrollHeight'),
            state.get('textLength'),
            state.get('firstText'),
        )

        progressed = key != previous_key
        if progressed:
            stable_rounds = 0
            no_progress_clicks = 0
            delta_nodes = max(int(state.get('nodes') or 0) - int(last_state.get('nodes') or 0), 0)
            delta_height = max(int(state.get('scrollHeight') or 0) - int(last_state.get('scrollHeight') or 0), 0)
            log.info(
                "  Round %d: %s nodes (+%d) / %spx (+%dpx) / text=%s%s",
                round_num,
                state.get('nodes'),
                delta_nodes,
                state.get('scrollHeight'),
                delta_height,
                state.get('textLength'),
                "  clicked older-control: " + clicked_text if clicked_text else "",
            )
        else:
            if clicked_text:
                no_progress_clicks += 1
            else:
                stable_rounds += 1
            log.info(
                "  Round %d: unchanged  %s nodes / %spx / text=%s  "
                "(stable %d/%d, no-progress-clicks %d/4)%s",
                round_num,
                state.get('nodes'),
                state.get('scrollHeight'),
                state.get('textLength'),
                stable_rounds,
                HISTORY_STABLE_ROUNDS,
                no_progress_clicks,
                "  clicked older-control: " + clicked_text if clicked_text else "",
            )
            if stable_rounds >= HISTORY_STABLE_ROUNDS or no_progress_clicks >= 4:
                log.info(
                    "  History fully loaded/stalled — %s nodes, %spx, text=%s, %d round(s)",
                    state.get('nodes'),
                    state.get('scrollHeight'),
                    state.get('textLength'),
                    round_num,
                )
                break

        previous_key = key
        last_state = state
    else:
        log.warning(
            "Reached max rounds (%d) — history may be incomplete (%s nodes, %spx)",
            HISTORY_MAX_ROUNDS,
            last_state.get('nodes'),
            last_state.get('scrollHeight'),
        )

    set_scroll(driver, chat_el, 0)
    time.sleep(0.8)


# ---------------------------------------------------------------------------
# Strip capture & stitching
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Panel composition
# ---------------------------------------------------------------------------
def compose(chat: Image.Image, details: Optional[Image.Image]) -> Image.Image:
    if details is None:
        return chat
    w = chat.width + details.width
    h = max(chat.height, details.height)
    out = Image.new("RGB", (w, h), (255, 255, 255))
    out.paste(chat,    (0, 0))
    out.paste(details, (chat.width, 0))
    return out


# ---------------------------------------------------------------------------
# Evidence banner
# ---------------------------------------------------------------------------
def _font(size: int) -> ImageFont.ImageFont:
    for name in ["cour.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf",
                 "Menlo.ttc", "FreeMono.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()


def stamp_banner(
    image: Image.Image,
    conversation_id: str,
    url: str,
    utc: datetime,
    local: datetime,
) -> Image.Image:
    """Prepend a dark metadata banner to the top of the image."""
    banner = Image.new("RGB", (image.width, BANNER_HEIGHT_PX), BANNER_BG)
    draw   = ImageDraw.Draw(banner)
    font   = _font(BANNER_FONT_SIZE)
    draw.text((12,  8), f"Airbnb conversation {conversation_id}  |  {url}",
              fill=BANNER_FG, font=font)
    draw.text((12, 36), f"Captured: {utc.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                        f"  ({local.strftime('%Y-%m-%d %H:%M:%S %Z')})",
              fill=BANNER_FG, font=font)
    out = Image.new("RGB", (image.width, BANNER_HEIGHT_PX + image.height))
    out.paste(banner, (0, 0))
    out.paste(image,  (0, BANNER_HEIGHT_PX))
    return out


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
def save_jpeg(image: Image.Image, path: Path, conversation_id: str, url: str) -> None:
    utc   = datetime.now(timezone.utc)
    local = datetime.now().astimezone()
    out   = stamp_banner(image, conversation_id, url, utc, local)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    log.info("Saved → %s  (%dx%dpx  %.1f MB)",
             path, out.width, out.height, path.stat().st_size / 1_048_576)


# ---------------------------------------------------------------------------
# Hide transient overlays (e.g. "typical response time" banners)
# ---------------------------------------------------------------------------
def hide_overlays(driver: webdriver.Chrome, chat_el) -> int:
    """
    Hide transient Airbnb UI overlays such as response-time banners.

    The banner is not always a descendant of the chat scroll container, so this
    searches the document and only hides small visible elements whose rectangle
    overlaps the chat panel and whose text matches response-time wording.
    """
    try:
        return int(_js(driver, """
            var root = arguments[0];
            var phrases = [
                'typical response time', 'typically responds', 'usually responds',
                'responds within', 'response time is', 'average response time'
            ];
            var rootRect = root.getBoundingClientRect();
            var hidden = 0;
            var candidates = Array.from(document.querySelectorAll('div,section,aside,span,[role="status"],[role="note"]'));

            for (var i = 0; i < candidates.length; i++) {
                var el = candidates[i];
                if (el.dataset && el.dataset.airbnbHidden === '1') continue;

                var text = (el.innerText || el.textContent || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                if (!text || text.length > 260) continue;
                if (!phrases.some(function(p) { return text.indexOf(p) >= 0; })) continue;

                var r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                if (r.height > 180) continue;

                var overlapsVertically = r.bottom >= rootRect.top && r.top <= rootRect.bottom;
                var overlapsHorizontally = r.right >= rootRect.left && r.left <= rootRect.right;
                if (!overlapsVertically || !overlapsHorizontally) continue;

                if (el.dataset) el.dataset.airbnbHidden = '1';
                el.style.setProperty('display', 'none', 'important');
                el.style.setProperty('visibility', 'hidden', 'important');
                hidden++;
            }
            return hidden;
        """, chat_el) or 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Single conversation capture
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Diagnose mode
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Output path resolution
# ---------------------------------------------------------------------------
def resolve_path(
    conversation_id: str,
    out_flag: Optional[str],
    out_dir: str,
    is_multi: bool,
) -> Path:
    if out_flag and not is_multi:
        return Path(out_flag)
    return Path(out_dir) / f"{conversation_id}.jpg"


# ---------------------------------------------------------------------------
# Orchestration — sequential, single driver
# ---------------------------------------------------------------------------
def run_captures(
    driver: webdriver.Chrome,
    conversation_ids: list[str],
    out_flag: Optional[str],
    out_dir: str,
    domain: str,
    page_load_extra_s: float,
    capture_details: bool,
) -> BulkSummary:
    """
    Capture all conversations one by one using the same Chrome session.
    Each failure is isolated — a broken conversation does not abort the rest.
    """
    is_multi  = len(conversation_ids) > 1
    selectors = Selectors()
    summary   = BulkSummary()

    for conv_id in conversation_ids:
        output_path = resolve_path(conv_id, out_flag, out_dir, is_multi)
        try:
            capture_conversation(
                driver          = driver,
                conversation_id = conv_id,
                output_path     = output_path,
                selectors       = selectors,
                domain          = domain,
                page_load_extra_s = page_load_extra_s,
                capture_details = capture_details,
            )
            summary.results.append(CaptureResult(conv_id, output_path=output_path))
        except Exception as exc:
            log.error("Failed: %s — %s", conv_id, exc)
            log.debug(traceback.format_exc())
            try:
                err_path = Path(f"airbnb_error_{conv_id}.jpg")
                driver.save_screenshot(str(err_path))
                log.info("Error screenshot → %s", err_path)
            except Exception:
                pass
            summary.results.append(CaptureResult(conv_id, error=str(exc)))

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airbnb_chat_capture",
        description="Capture full Airbnb conversation screenshots for evidence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("conversation_ids", nargs="*", metavar="ID")
    parser.add_argument("--ids-file", metavar="FILE")
    parser.add_argument("--out",     metavar="PATH")
    parser.add_argument("--out-dir", metavar="DIR", default="screenshots")
    parser.add_argument("--domain",  default="https://www.airbnb.co.uk")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--no-details", action="store_true")
    parser.add_argument("--delay", type=float, default=3.0, metavar="SECONDS")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--workers", type=int, default=4, metavar="N",
        help="Max parallel Chrome workers (default: 4; capped at 2 on Windows)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args   = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Collect IDs
    ids: list[str] = list(args.conversation_ids)
    if args.ids_file:
        p = Path(args.ids_file)
        if not p.exists():
            log.error("IDs file not found: %s", p)
            return 1
        file_ids = [
            ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        ids.extend(file_ids)
        log.info("Loaded %d ID(s) from %s", len(file_ids), p)

    if not ids:
        parser.print_help()
        return 1

    # Diagnose mode is the only path that needs a persistent-profile browser
    # owned by main().  Bulk capture extracts cookies inside run_bulk_capture(),
    # then closes that profile browser before headless workers are launched.
    # Opening a persistent Chrome here as well causes profile/debug-port
    # contention and breaks parallel capture.
    if args.diagnose:
        driver = build_driver()
        try:
            run_diagnose(driver, ids[0], args.domain)
            return 0
        finally:
            try:
                driver.quit()
            except Exception:
                pass
            purge_tmp()

    try:
        summary = run_bulk_capture(
            conversation_ids  = ids,
            out_flag          = args.out,
            out_dir           = args.out_dir,
            domain            = args.domain,
            page_load_extra_s = args.delay,
            capture_details   = not args.no_details,
            max_workers       = args.workers,
        )
        summary.print_summary()
        return 0 if all(r.success for r in summary.results) else 1
    finally:
        purge_tmp()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
