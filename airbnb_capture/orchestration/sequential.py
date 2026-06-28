from __future__ import annotations

import traceback
from pathlib import Path
from typing import Optional

from selenium import webdriver

from ..capture.conversation import capture_conversation
from ..config import log
from ..models import BulkSummary, CaptureResult, Selectors
from ..output.pdf import PdfOptions
from ..paths import resolve_path

def run_captures(
    driver: webdriver.Chrome,
    conversation_ids: list[str],
    out_flag: Optional[str],
    out_dir: str,
    domain: str,
    page_load_extra_s: float,
    capture_details: bool,
    include_banner: bool = False,
    export_format: str = "jpg",
    pdf_dir: str = "pdfs",
    pdf_options: PdfOptions | None = None,
) -> BulkSummary:
    """
    Capture all conversations one by one using the same Chrome session.
    Each failure is isolated — a broken conversation does not abort the rest.
    """
    is_multi  = len(conversation_ids) > 1
    selectors = Selectors()
    summary   = BulkSummary()

    for conv_id in conversation_ids:
        jpg_path = resolve_path(conv_id, out_flag, out_dir, is_multi, ".jpg")
        pdf_path = resolve_path(conv_id, out_flag, pdf_dir, is_multi, ".pdf")
        primary_path = pdf_path if export_format == "pdf" else jpg_path
        try:
            capture_conversation(
                driver          = driver,
                conversation_id = conv_id,
                output_path     = jpg_path,
                pdf_path        = pdf_path,
                selectors       = selectors,
                domain          = domain,
                page_load_extra_s = page_load_extra_s,
                capture_details = capture_details,
                include_banner  = include_banner,
                export_format   = export_format,
                pdf_options     = pdf_options,
            )
            summary.results.append(CaptureResult(conv_id, output_path=primary_path))
        except Exception as exc:
            log.error("Failed: %s — %s", conv_id, exc)
            log.debug(traceback.format_exc())
            try:
                err_path = Path(f"airbnb_error_{conv_id}.jpg")
                driver.save_screenshot(str(err_path))
                log.info("Error screenshot → %s", err_path)
            except Exception:
                pass
            summary.results.append(CaptureResult(conv_id, error=str(exc)))

    return summary
