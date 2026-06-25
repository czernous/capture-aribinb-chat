from __future__ import annotations

import shutil
from pathlib import Path

from .config import TMP_DIR

def ensure_tmp(subdir: str = "") -> Path:
    path = TMP_DIR / subdir if subdir else TMP_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def purge_tmp() -> None:
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR, ignore_errors=True)
