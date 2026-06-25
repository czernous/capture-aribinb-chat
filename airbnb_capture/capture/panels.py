from __future__ import annotations

from typing import Optional

from PIL import Image

def compose(chat: Image.Image, details: Optional[Image.Image]) -> Image.Image:
    if details is None:
        return chat
    w = chat.width + details.width
    h = max(chat.height, details.height)
    out = Image.new("RGB", (w, h), (255, 255, 255))
    out.paste(chat,    (0, 0))
    out.paste(details, (chat.width, 0))
    return out
