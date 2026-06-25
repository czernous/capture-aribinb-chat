from __future__ import annotations

import time

from ..config import KNOWN_CHAT_TESTIDS, log
from .factory import build_driver

LOGIN_WAIT_TIMEOUT_S = 300
LOGIN_WAIT_POLL_S = 2

LOGIN_URL_PARTS = (
    "/login",
    "/signup_login",
    "/authenticate",
    "/oauth",
)


def _looks_like_login_page(driver) -> bool:
    current_url = (driver.current_url or "").lower()
    if any(part in current_url for part in LOGIN_URL_PARTS):
        return True

    try:
        return bool(driver.execute_script("""
            var text = (document.body && document.body.innerText || '').toLowerCase();
            if (!text) return false;
            var hasLoginCopy = text.indexOf('log in') >= 0 || text.indexOf('login') >= 0;
            var hasAuthInput = !!document.querySelector(
                'input[type="email"], input[type="password"], input[type="tel"]'
            );
            return hasLoginCopy && hasAuthInput;
        """))
    except Exception:
        return False


def _conversation_is_accessible(driver) -> bool:
    selectors = ", ".join(f"[data-testid='{testid}']" for testid in KNOWN_CHAT_TESTIDS)
    try:
        return bool(driver.execute_script(
            "return !!document.querySelector(arguments[0]);",
            selectors,
        ))
    except Exception:
        return False


def _needs_user_auth_action(driver, conversation_id: str | None) -> bool:
    if _looks_like_login_page(driver):
        return True
    return bool(conversation_id and not _conversation_is_accessible(driver))


def _extract_cookies(domain: str, conversation_id: str | None = None) -> list[dict]:
    """
    Open a short-lived visible Chrome with the saved profile, grab session
    cookies, then quit — releasing the profile dir lock before workers start.
    """
    log.info("Extracting session cookies from saved profile...")
    driver = build_driver()
    try:
        url = f"{domain}/guest/messages"
        if conversation_id:
            url = f"{url}/{conversation_id}"
        driver.get(url)
        time.sleep(3)

        if _needs_user_auth_action(driver, conversation_id):
            log.info("Airbnb requires a login or continue action in the opened Chrome window.")
            log.info("Waiting up to %d seconds for the conversation to become accessible...", LOGIN_WAIT_TIMEOUT_S)
            deadline = time.monotonic() + LOGIN_WAIT_TIMEOUT_S
            while time.monotonic() < deadline and _needs_user_auth_action(driver, conversation_id):
                time.sleep(LOGIN_WAIT_POLL_S)
            if _needs_user_auth_action(driver, conversation_id):
                log.warning("Conversation still does not appear accessible after waiting.")
                return []
            time.sleep(2)

        cookies = driver.get_cookies()
        log.info("  Extracted %d cookie(s)", len(cookies))
        return cookies
    finally:
        driver.quit()
