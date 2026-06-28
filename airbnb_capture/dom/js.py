from __future__ import annotations

from selenium import webdriver

def _js(driver: webdriver.Chrome, script: str, *args):
    return driver.execute_script(script, *args)


def elem_rect(driver: webdriver.Chrome, el) -> dict:
    return _js(driver, """
        if (arguments[0] === document.body || arguments[0] === document.documentElement) {
            return {left:0, top:0, right:window.innerWidth, bottom:window.innerHeight,
                    width:window.innerWidth, height:window.innerHeight};
        }
        var r = arguments[0].getBoundingClientRect();
        return {left:r.left, top:r.top, right:r.right, bottom:r.bottom,
                width:r.width, height:r.height};
    """, el)


def scroll_height(driver: webdriver.Chrome, el) -> float:
    return float(_js(driver, """
        if (arguments[0] === document.body || arguments[0] === document.documentElement) {
            var s = document.scrollingElement || document.documentElement;
            return Math.max(s.scrollHeight, document.body.scrollHeight, document.documentElement.scrollHeight);
        }
        return arguments[0].scrollHeight;
    """, el) or 0)


def scroll_top(driver: webdriver.Chrome, el) -> float:
    return float(_js(driver, """
        if (arguments[0] === document.body || arguments[0] === document.documentElement) {
            var s = document.scrollingElement || document.documentElement;
            return s.scrollTop || window.scrollY || 0;
        }
        return arguments[0].scrollTop;
    """, el) or 0)


def set_scroll(driver: webdriver.Chrome, el, value: float) -> None:
    _js(driver, """
        if (arguments[0] === document.body || arguments[0] === document.documentElement) {
            var s = document.scrollingElement || document.documentElement;
            s.scrollTop = arguments[1];
            window.scrollTo(0, arguments[1]);
            return;
        }
        arguments[0].scrollTop = arguments[1];
    """, el, value)


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
        var candidates = Array.from(document.querySelectorAll('div'));
        candidates.push(document.scrollingElement || document.documentElement);
        candidates.forEach(function(el) {
            if (el.scrollHeight <= el.clientHeight + 100) return;
            if (el.clientHeight < 200) return;
            var r = el.getBoundingClientRect();
            var isPage = el === document.body || el === document.documentElement;
            if (!isPage && r.width < 200) return;
            out.push({
                testid: el.getAttribute('data-testid') || '',
                tag: el.tagName.toLowerCase(),
                scrollH: el.scrollHeight, clientH: el.clientHeight,
                width: Math.round(r.width), left: Math.round(r.left)
            });
        });
        return out;
    """) or []


def mark_capture_region(driver: webdriver.Chrome) -> dict | None:
    return _js(driver, """
        function visibleRect(el) {
            var r = el.getBoundingClientRect();
            return {
                left: r.left, top: r.top, right: r.right, bottom: r.bottom,
                width: r.width, height: r.height
            };
        }

        var viewportW = window.innerWidth;
        var viewportH = window.innerHeight;
        var ranked = [];
        document.querySelectorAll('main,[role="main"],section,article,div').forEach(function(el) {
            var r = visibleRect(el);
            if (r.width < 300 || r.height < 240) return;
            if (r.right < 0 || r.left > viewportW || r.bottom < 0 || r.top > viewportH) return;
            var text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            if (text.length < 120) return;
            if (r.width > viewportW * 0.98 && r.height > viewportH * 0.98 && text.length < 1000) return;
            ranked.push({
                el: el,
                textLen: text.length,
                area: Math.max(1, r.width * r.height),
                left: r.left,
                top: r.top,
                width: r.width,
                height: r.height,
                tag: el.tagName.toLowerCase(),
                testid: el.getAttribute('data-testid') || ''
            });
        });

        ranked.sort(function(a, b) {
            var scoreA = a.textLen + Math.min(a.area / 1000, 1500) - Math.max(0, a.left - viewportW * 0.6);
            var scoreB = b.textLen + Math.min(b.area / 1000, 1500) - Math.max(0, b.left - viewportW * 0.6);
            return scoreB - scoreA;
        });

        var chosen = ranked[0];
        if (!chosen) {
            var page = document.scrollingElement || document.documentElement;
            page.setAttribute('data-airbnb-capture-region', '1');
            return {
                selector: '[data-airbnb-capture-region="1"]',
                tag: page.tagName.toLowerCase(),
                textLen: (page.innerText || page.textContent || '').length,
                width: viewportW,
                height: viewportH,
                left: 0,
                top: 0
            };
        }

        chosen.el.setAttribute('data-airbnb-capture-region', '1');
        return {
            selector: '[data-airbnb-capture-region="1"]',
            tag: chosen.tag,
            testid: chosen.testid,
            textLen: chosen.textLen,
            width: Math.round(chosen.width),
            height: Math.round(chosen.height),
            left: Math.round(chosen.left),
            top: Math.round(chosen.top)
        };
    """)


def descendant_count(driver: webdriver.Chrome, el) -> int:
    """
    Count all descendants inside the chat container.
    More reliable than childElementCount because Airbnb wraps everything
    in a single div; descendant count always grows as messages load.
    """
    return int(_js(driver, "return arguments[0].querySelectorAll('*').length;", el) or 0)
