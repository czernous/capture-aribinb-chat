from __future__ import annotations

from selenium import webdriver

from .js import _js

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
