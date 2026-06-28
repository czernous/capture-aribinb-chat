from __future__ import annotations

from selenium import webdriver

from .js import _js

def hide_overlays(driver: webdriver.Chrome, chat_el=None) -> int:
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
                'responds within', 'respond within', 'response time is',
                'average response time', 'response time', 'usually replies',
                'typically replies', 'replies within', 'reply within',
                'time for host', 'host to respond', 'host response',
                'host usually responds', 'host typically responds',
                'host responds', 'response time for host',
                'for your host', 'your host to respond',
                'your host usually responds', 'your host typically responds',
                'this conversation is closed', 'conversation is closed',
                'thread is closed', 'case is closed', 'chat is closed',
                'you can no longer', 'you won\\'t be able to reply',
                'cannot reply', 'can\\'t reply'
            ];
            var hostTimePhrases = [
                'for your host', 'your host to respond', 'time for host',
                'host to respond', 'host response', 'host usually responds',
                'host typically responds', 'your host usually responds',
                'your host typically responds', 'response time for host'
            ];
            var rootRect = root
                ? root.getBoundingClientRect()
                : {top: 0, bottom: window.innerHeight, left: 0, right: window.innerWidth, width: window.innerWidth, height: window.innerHeight};
            var hidden = 0;
            var styleId = 'airbnb-capture-hide-overlays-style';
            if (!document.getElementById(styleId)) {
                var style = document.createElement('style');
                style.id = styleId;
                style.textContent = '[data-airbnb-hidden="1"]{display:none!important;visibility:hidden!important;opacity:0!important;pointer-events:none!important;}';
                document.head.appendChild(style);
            }

            function normText(el) {
                return (el.innerText || el.textContent || '').toLowerCase().replace(/\\s+/g, ' ').trim();
            }

            function overlapsChat(r) {
                return r.bottom >= rootRect.top && r.top <= rootRect.bottom &&
                       r.right >= rootRect.left && r.left <= rootRect.right;
            }

            function hideTargetFor(el, text) {
                var target = el;
                var cur = el;
                while (cur && cur.parentElement && cur.parentElement !== document.body && (!root || cur.parentElement !== root)) {
                    var parent = cur.parentElement;
                    var parentText = normText(parent);
                    if (!parentText || parentText.length > 320) break;
                    if (parentText.indexOf(text) < 0 && text.indexOf(parentText) < 0) break;

                    var pr = parent.getBoundingClientRect();
                    if (pr.width <= 0 || pr.height <= 0) break;
                    if (pr.height > 260 || pr.width > rootRect.width * 1.45) break;
                    if (!overlapsChat(pr)) break;
                    if (!edgeBandContains(pr)) break;

                    target = parent;
                    cur = parent;
                }
                return target;
            }

            function edgeBandContains(r) {
                var edgeSize = Math.max(220, rootRect.height * 0.33);
                var topLimit = rootRect.top + edgeSize;
                var bottomLimit = rootRect.bottom - edgeSize;
                return r.top <= topLimit || r.bottom >= bottomLimit;
            }

            function overlayKind(el) {
                var cur = el;
                while (cur && cur !== document.body && (!root || cur !== root)) {
                    var style = window.getComputedStyle(cur);
                    var pos = style.position;
                    if (pos === 'fixed' || pos === 'sticky') return pos;
                    if (pos === 'absolute') {
                        var cr = cur.getBoundingClientRect();
                        if (edgeBandContains(cr) && overlapsChat(cr)) return pos;
                    }
                    var z = Number(style.zIndex);
                    if (!Number.isNaN(z) && z >= 10) return 'elevated';
                    cur = cur.parentElement;
                }
                return '';
            }

            function isOverlayLike(el) {
                var r = el.getBoundingClientRect();
                return edgeBandContains(r) && !!overlayKind(el);
            }

            function hideElement(el) {
                if (el.dataset && el.dataset.airbnbHidden === '1') return false;
                if (el.dataset) el.dataset.airbnbHidden = '1';
                el.style.setProperty('display', 'none', 'important');
                el.style.setProperty('visibility', 'hidden', 'important');
                el.style.setProperty('opacity', '0', 'important');
                el.style.setProperty('pointer-events', 'none', 'important');
                hidden++;
                return true;
            }

            function hasPhrase(text, phraseList) {
                return text && phraseList.some(function(p) { return text.indexOf(p) >= 0; });
            }

            function hideEdgeOverlayCandidates() {
                var candidates = Array.from(document.querySelectorAll(
                    'div,section,aside,header,footer,[role="status"],[role="note"],[role="alert"],[aria-live]'
                ));

                for (var i = 0; i < candidates.length; i++) {
                    var el = candidates[i];
                    if (root && el === root) continue;
                    if (el.dataset && el.dataset.airbnbHidden === '1') continue;

                    var r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    if (r.height < 18 || r.height > 260) continue;
                    if (r.width < Math.min(rootRect.width * 0.35, 220)) continue;
                    if (!edgeBandContains(r)) continue;
                    if (!overlapsChat(r)) continue;

                    var text = normText(el);
                    if (text.length > 520) continue;

                    var kind = overlayKind(el);
                    var overlayPosition = !!kind;
                    var liveRegion = el.hasAttribute('aria-live') ||
                        ['status', 'note', 'alert'].indexOf((el.getAttribute('role') || '').toLowerCase()) >= 0;
                    var phraseMatch = hasPhrase(text, phrases);
                    var hostTimeMatch = hasPhrase(text, hostTimePhrases);

                    if (!phraseMatch) continue;
                    if (!overlayPosition && !liveRegion && !hostTimeMatch) continue;
                    hideElement(el);
                }
            }

            var candidates = Array.from(document.querySelectorAll(
                'div,section,aside,span,p,[role="status"],[role="note"],[aria-live]'
            ));

            for (var i = 0; i < candidates.length; i++) {
                var el = candidates[i];
                if (el.dataset && el.dataset.airbnbHidden === '1') continue;

                var text = normText(el);
                if (!text || text.length > 320) continue;
                if (!hasPhrase(text, phrases)) continue;

                var r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                if (r.height > 220) continue;

                if (!overlapsChat(r)) continue;

                var target = hideTargetFor(el, text);
                var hostTimeMatch = hasPhrase(text, hostTimePhrases);
                if (!isOverlayLike(target) && !(hostTimeMatch && edgeBandContains(r))) continue;

                hideElement(target);
            }
            hideEdgeOverlayCandidates();
            return hidden;
        """, chat_el) or 0)
    except Exception:
        return 0
