from __future__ import annotations

from selenium import webdriver

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
