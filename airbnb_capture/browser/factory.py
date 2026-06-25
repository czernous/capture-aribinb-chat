from __future__ import annotations

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from ..config import CHROME_PROFILE_DIR

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
