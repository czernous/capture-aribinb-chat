from __future__ import annotations

import logging
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from ..browser.cookies import _extract_cookies
from ..browser.factory import _build_headless_driver, build_driver
from ..capture.conversation import capture_conversation
from ..config import log
from ..models import BulkSummary, CaptureResult, Selectors
from ..output.pdf import PdfOptions
from ..paths import resolve_path
from ..tmp import purge_tmp
from .sequential import run_captures


def _run_visible_fallback(
    conversation_ids: list[str],
    out_flag: Optional[str],
    out_dir: str,
    domain: str,
    page_load_extra_s: float,
    capture_details: bool,
    include_banner: bool,
    export_format: str,
    pdf_dir: str,
    pdf_options: PdfOptions | None,
    reason: str,
) -> BulkSummary:
    log.warning("%s; falling back to visible sequential capture.", reason)
    driver = build_driver()
    try:
        return run_captures(
            driver=driver,
            conversation_ids=conversation_ids,
            out_flag=out_flag,
            out_dir=out_dir,
            domain=domain,
            page_load_extra_s=page_load_extra_s,
            capture_details=capture_details,
            include_banner=include_banner,
            export_format=export_format,
            pdf_dir=pdf_dir,
            pdf_options=pdf_options,
        )
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _worker(
    conversation_id: str,
    output_path: Path,
    pdf_path: Path,
    domain: str,
    page_load_extra_s: float,
    capture_details: bool,
    include_banner: bool,
    export_format: str,
    pdf_options: PdfOptions | None,
    worker_index: int,
    cookies: list[dict],
) -> CaptureResult:
    """
    Subprocess entry point.  Must be a module-level function for pickle
    compatibility with ProcessPoolExecutor on Windows (spawn start method).
    """
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s  [worker-{worker_index}]  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # Stagger Chrome startup so renderers don't all compete for the GPU/compositor
    # resource at the exact same millisecond.  Each worker sleeps worker_index * 4s
    # before creating its browser.  Worker 0 starts immediately; worker 3 waits 12s.
    # The actual capture (30-120s) runs concurrently — the stagger cost is negligible.
    # This is applied inside the worker (not at submission time) so the ProcessPoolExecutor
    # spawns all processes immediately but they start Chrome in sequence.
    if worker_index > 0:
        time.sleep(worker_index * 4.0)

    driver = None
    try:
        driver = _build_headless_driver(worker_index, cookies, domain)
        capture_conversation(
            driver=driver,
            conversation_id=conversation_id,
            output_path=output_path,
            pdf_path=pdf_path,
            selectors=Selectors(),
            domain=domain,
            page_load_extra_s=page_load_extra_s,
            capture_details=capture_details,
            include_banner=include_banner,
            export_format=export_format,
            pdf_options=pdf_options,
        )
        primary_path = pdf_path if export_format == "pdf" else output_path
        return CaptureResult(conversation_id=conversation_id, output_path=primary_path)
    except Exception as exc:
        return CaptureResult(
            conversation_id=conversation_id,
            error=f"{type(exc).__name__}: {exc}",
            tb=traceback.format_exc(),
        )
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def run_bulk_capture(
    conversation_ids: list[str],
    out_flag: Optional[str],
    out_dir: str,
    domain: str,
    page_load_extra_s: float,
    capture_details: bool,
    max_workers: int,
    include_banner: bool = False,
    export_format: str = "jpg",
    pdf_dir: str = "pdfs",
    pdf_options: PdfOptions | None = None,
) -> BulkSummary:
    """
    Capture conversations in parallel using headless Chrome workers.

    Flow:
      1. Extract session cookies with a single visible Chrome (3 s).
      2. Submit all jobs to a process pool; each worker builds its own headless
         Chrome, injects the cookies, and runs independently.
      3. Workers are submitted with a 3 s gap between each so Chrome renderers
         don't all allocate RAM at the exact same millisecond.
    """
    summary  = BulkSummary()
    is_multi = len(conversation_ids) > 1

    jobs = [
        (
            cid,
            resolve_path(cid, out_flag, out_dir, is_multi, ".jpg"),
            resolve_path(cid, out_flag, pdf_dir, is_multi, ".pdf"),
        )
        for cid in conversation_ids
    ]

    try:
        cookies = _extract_cookies(domain, conversation_ids[0] if conversation_ids else None)
    except Exception as exc:
        log.error("Cookie extraction failed: %s", exc)
        return _run_visible_fallback(
            conversation_ids=conversation_ids,
            out_flag=out_flag,
            out_dir=out_dir,
            domain=domain,
            page_load_extra_s=page_load_extra_s,
            capture_details=capture_details,
            include_banner=include_banner,
            export_format=export_format,
            pdf_dir=pdf_dir,
            pdf_options=pdf_options,
            reason=f"Cookie extraction failed: {exc}",
        )

    if not cookies:
        return _run_visible_fallback(
            conversation_ids=conversation_ids,
            out_flag=out_flag,
            out_dir=out_dir,
            domain=domain,
            page_load_extra_s=page_load_extra_s,
            capture_details=capture_details,
            include_banner=include_banner,
            export_format=export_format,
            pdf_dir=pdf_dir,
            pdf_options=pdf_options,
            reason="No cookies were found in the saved profile",
        )

    n_workers = min(max_workers, len(jobs))
    log.info("Launching %d parallel worker(s) for %d conversation(s)...", n_workers, len(jobs))

    futures: dict = {}
    results_by_id: dict[str, CaptureResult] = {}
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        for idx, (cid, output_path, pdf_path) in enumerate(jobs):
            target = pdf_path if export_format == "pdf" else output_path
            log.info("  Queuing %s -> %s (slot %d)", cid, target, idx)
            future = executor.submit(
                _worker,
                cid, output_path, pdf_path, domain, page_load_extra_s,
                capture_details, include_banner, export_format, pdf_options,
                idx, cookies,
            )
            futures[future] = cid

        for future in as_completed(futures):
            cid = futures[future]
            try:
                result = future.result()
                if result.success:
                    log.info("OK  %s -> %s", cid, result.output_path)
                else:
                    log.error("FAIL %s -- %s", cid, result.error)
                    if result.tb:
                        log.warning("Traceback for %s:\n%s", cid, result.tb)
                results_by_id[cid] = result
            except Exception as exc:
                log.error("Worker crashed for %s: %s", cid, exc)
                results_by_id[cid] = CaptureResult(cid, error=f"{type(exc).__name__}: {exc}")

    failed_jobs = [
        (cid, output_path, pdf_path)
        for cid, output_path, pdf_path in jobs
        if cid in results_by_id and not results_by_id[cid].success
    ]
    if failed_jobs and n_workers > 1:
        log.warning(
            "Retrying %d failed capture(s) sequentially with one headless worker...",
            len(failed_jobs),
        )
        for cid, output_path, pdf_path in failed_jobs:
            result = _worker(
                cid,
                output_path,
                pdf_path,
                domain,
                page_load_extra_s,
                capture_details,
                include_banner,
                export_format,
                pdf_options,
                0,
                cookies,
            )
            if result.success:
                log.info("RETRY OK  %s -> %s", cid, result.output_path)
                results_by_id[cid] = result
            else:
                log.error("RETRY FAIL %s -- %s", cid, result.error)
                if result.tb:
                    log.warning("Retry traceback for %s:\n%s", cid, result.tb)
                results_by_id[cid] = result

    for cid, _output_path, _pdf_path in jobs:
        if cid in results_by_id:
            summary.results.append(results_by_id[cid])

    purge_tmp()
    return summary
