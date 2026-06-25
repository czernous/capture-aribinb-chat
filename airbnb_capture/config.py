from __future__ import annotations

import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("airbnb_capture")

for noisy_logger in ("selenium", "urllib3", "WDM", "webdriver_manager"):
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

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
PAGE_LOAD_WAIT_S: int = 30
NETWORK_QUIET_S: float = 2.5
HISTORY_STABLE_ROUNDS: int = 3
HISTORY_MAX_ROUNDS: int = 80
STRIP_OVERLAP_PX: int = 100
STRIP_PAUSE_S: float = 0.6

# URL fragments that indicate Airbnb is fetching message data
MESSAGE_FETCH_PATTERNS: list[str] = ["/api/v3/", "/messaging/", "/message_threads/"]
MESSAGE_FETCH_EXCLUDE: list[str] = ["jitney", "logging", "tracking", "analytics"]

# Evidence banner
BANNER_HEIGHT_PX: int = 64
BANNER_FONT_SIZE: int = 18
BANNER_BG: tuple = (30, 30, 30)
BANNER_FG: tuple = (220, 220, 220)

JPEG_QUALITY: int = 60
