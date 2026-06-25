from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from ..config import BANNER_BG, BANNER_FG, BANNER_FONT_SIZE, BANNER_HEIGHT_PX

def _font(size: int) -> ImageFont.ImageFont:
    for name in ["cour.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf",
                 "Menlo.ttc", "FreeMono.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()


def stamp_banner(
    image: Image.Image,
    conversation_id: str,
    url: str,
    utc: datetime,
    local: datetime,
) -> Image.Image:
    """Prepend a dark metadata banner to the top of the image."""
    banner = Image.new("RGB", (image.width, BANNER_HEIGHT_PX), BANNER_BG)
    draw   = ImageDraw.Draw(banner)
    font   = _font(BANNER_FONT_SIZE)
    draw.text((12,  8), f"Airbnb conversation {conversation_id}  |  {url}",
              fill=BANNER_FG, font=font)
    draw.text((12, 36), f"Captured: {utc.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                        f"  ({local.strftime('%Y-%m-%d %H:%M:%S %Z')})",
              fill=BANNER_FG, font=font)
    out = Image.new("RGB", (image.width, BANNER_HEIGHT_PX + image.height))
    out.paste(banner, (0, 0))
    out.paste(image,  (0, BANNER_HEIGHT_PX))
    return out
