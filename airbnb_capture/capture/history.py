from __future__ import annotations

import time

from selenium import webdriver

from ..config import HISTORY_MAX_ROUNDS, HISTORY_STABLE_ROUNDS, log
from ..dom.js import _js, set_scroll
from ..network import drain_network_log, wait_network_quiet

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

        # Do not declare long threads complete too early. Airbnb can sit unchanged
        # for several rounds before another older-message batch appears.
        min_rounds_before_done = 8 if int(state.get("scrollHeight") or 0) > 10_000 else 4

        if (
            round_num >= min_rounds_before_done
            and (stable_rounds >= HISTORY_STABLE_ROUNDS or no_progress_clicks >= 4)
        ):
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
