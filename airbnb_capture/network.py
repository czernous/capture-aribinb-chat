from __future__ import annotations

import json
import time

from selenium import webdriver

from .config import MESSAGE_FETCH_EXCLUDE, MESSAGE_FETCH_PATTERNS, NETWORK_QUIET_S, log

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
