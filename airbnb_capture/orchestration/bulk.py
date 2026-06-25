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
        )
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _worker(
    conversation_id: str,
    output_path: Path,
    domain: str,
    page_load_extra_s: float,
    capture_details: bool,
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
            selectors=Selectors(),
            domain=domain,
            page_load_extra_s=page_load_extra_s,
            capture_details=capture_details,
        )
        return CaptureResult(conversation_id=conversation_id, output_path=output_path)
    except Exception as exc:
        return CaptureResult(
            conversation_id=conversation_id,
            error=str(exc),
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
) -> BulkSummary:
    """
    Capture conversations in parallel using headless Chrome workers.

    Worker count is capped at 2 on Windows because each headless Chrome
    renderer needs ~500 MB RAM and its own GPU process.  Starting more than
    2 simultaneously causes renderer startup failures regardless of staggering.
    2 workers still gives a meaningful speedup for 3+ conversations.

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
        (cid, resolve_path(cid, out_flag, out_dir, is_multi))
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
            reason="No cookies were found in the saved profile",
        )

    n_workers = min(max_workers, len(jobs))
    log.info("Launching %d parallel worker(s) for %d conversation(s)...", n_workers, len(jobs))

    futures: dict = {}
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        for idx, (cid, output_path) in enumerate(jobs):
            log.info("  Queuing %s -> %s (slot %d)", cid, output_path, idx)
            future = executor.submit(
                _worker,
                cid, output_path, domain, page_load_extra_s,
                capture_details, idx, cookies,
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
                        log.debug("Traceback:\n%s", result.tb)
                summary.results.append(result)
            except Exception as exc:
                log.error("Worker crashed for %s: %s", cid, exc)
                summary.results.append(CaptureResult(cid, error=str(exc)))

    purge_tmp()
    return summary
