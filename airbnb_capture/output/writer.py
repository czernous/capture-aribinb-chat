from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from ..config import JPEG_QUALITY, log
from .banner import stamp_banner

def save_jpeg(image: Image.Image, path: Path, conversation_id: str, url: str) -> None:
    utc   = datetime.now(timezone.utc)
    local = datetime.now().astimezone()
    out   = stamp_banner(image, conversation_id, url, utc, local)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    log.info("Saved → %s  (%dx%dpx  %.1f MB)",
             path, out.width, out.height, path.stat().st_size / 1_048_576)
