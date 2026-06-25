from __future__ import annotations

from pathlib import Path
from typing import Optional

def resolve_path(
    conversation_id: str,
    out_flag: Optional[str],
    out_dir: str,
    is_multi: bool,
) -> Path:
    if out_flag and not is_multi:
        return Path(out_flag)
    return Path(out_dir) / f"{conversation_id}.jpg"
