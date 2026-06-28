from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from ..config import JPEG_QUALITY, log
from .banner import stamp_banner
from .pdf import PdfOptions, save_pdf

JPEG_MAX_DIMENSION = 65_500


def _save_output(image: Image.Image, path: Path) -> Path:
    if image.width > JPEG_MAX_DIMENSION or image.height > JPEG_MAX_DIMENSION:
        png_path = path.with_suffix(".png")
        image.save(png_path, format="PNG", compress_level=6)
        log.warning(
            "Saved PNG instead of JPEG because image is too large for JPEG: %dx%dpx",
            image.width,
            image.height,
        )
        return png_path

    try:
        image.save(path, format="JPEG", quality=JPEG_QUALITY)
        return path
    except OSError as exc:
        png_path = path.with_suffix(".png")
        image.save(png_path, format="PNG", compress_level=6)
        log.warning("JPEG save failed (%s); saved PNG fallback instead", exc)
        return png_path


def save_jpeg(
    image: Image.Image,
    path: Path,
    conversation_id: str,
    url: str,
    include_banner: bool = False,
) -> Path:
    utc   = datetime.now(timezone.utc)
    local = datetime.now().astimezone()
    out   = stamp_banner(image, conversation_id, url, utc, local) if include_banner else image
    path.parent.mkdir(parents=True, exist_ok=True)
    saved_path = _save_output(out, path)
    log.info("Saved → %s  (%dx%dpx  %.1f MB)",
             saved_path, out.width, out.height, saved_path.stat().st_size / 1_048_576)
    return saved_path


def save_capture(
    image: Image.Image,
    image_path: Path,
    conversation_id: str,
    url: str,
    export_format: str = "jpg",
    include_banner: bool = False,
    pdf_path: Path | None = None,
    pdf_options: PdfOptions | None = None,
) -> list[Path]:
    utc = datetime.now(timezone.utc)
    local = datetime.now().astimezone()
    out = stamp_banner(image, conversation_id, url, utc, local) if include_banner else image
    saved: list[Path] = []

    if export_format in ("jpg", "both"):
        image_path.parent.mkdir(parents=True, exist_ok=True)
        saved_path = _save_output(out, image_path)
        log.info(
            "Saved → %s  (%dx%dpx  %.1f MB)",
            saved_path,
            out.width,
            out.height,
            saved_path.stat().st_size / 1_048_576,
        )
        saved.append(saved_path)

    if export_format in ("pdf", "both"):
        target_pdf = pdf_path or image_path.with_suffix(".pdf")
        saved_pdf = save_pdf(out, target_pdf, pdf_options)
        log.info(
            "Saved PDF → %s  (%.1f MB, cuts sidecar=%s)",
            saved_pdf,
            saved_pdf.stat().st_size / 1_048_576,
            "yes" if pdf_options and pdf_options.debug else "no",
        )
        saved.append(saved_pdf)

    return saved
