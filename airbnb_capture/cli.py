from __future__ import annotations

import argparse
import logging
import multiprocessing
from pathlib import Path

from .browser.factory import build_driver
from .config import log
from .dom.diagnostics import run_diagnose
from .orchestration.bulk import run_bulk_capture
from .tmp import purge_tmp

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airbnb_chat_capture",
        description="Capture full Airbnb conversation screenshots for evidence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("conversation_ids", nargs="*", metavar="ID")
    parser.add_argument("--ids-file", metavar="FILE")
    parser.add_argument("--out",     metavar="PATH")
    parser.add_argument("--out-dir", metavar="DIR", default="screenshots")
    parser.add_argument("--domain",  default="https://www.airbnb.co.uk")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--no-details", action="store_true")
    parser.add_argument("--delay", type=float, default=3.0, metavar="SECONDS")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--workers", type=int, default=4, metavar="N",
        help="Max parallel Chrome workers (default: 4; capped at 2 on Windows)",
    )
    return parser


def main() -> int:
    multiprocessing.freeze_support()
    parser = build_parser()
    args   = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Collect IDs
    ids: list[str] = list(args.conversation_ids)
    if args.ids_file:
        p = Path(args.ids_file)
        if not p.exists():
            log.error("IDs file not found: %s", p)
            return 1
        file_ids = [
            ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        ids.extend(file_ids)
        log.info("Loaded %d ID(s) from %s", len(file_ids), p)

    if not ids:
        parser.print_help()
        return 1

    # Diagnose mode is the only path that needs a persistent-profile browser
    # owned by main().  Bulk capture extracts cookies inside run_bulk_capture(),
    # then closes that profile browser before headless workers are launched.
    # Opening a persistent Chrome here as well causes profile/debug-port
    # contention and breaks parallel capture.
    if args.diagnose:
        driver = build_driver()
        try:
            run_diagnose(driver, ids[0], args.domain)
            return 0
        finally:
            try:
                driver.quit()
            except Exception:
                pass
            purge_tmp()

    try:
        summary = run_bulk_capture(
            conversation_ids  = ids,
            out_flag          = args.out,
            out_dir           = args.out_dir,
            domain            = args.domain,
            page_load_extra_s = args.delay,
            capture_details   = not args.no_details,
            max_workers       = args.workers,
        )
        summary.print_summary()
        return 0 if all(r.success for r in summary.results) else 1
    finally:
        purge_tmp()
