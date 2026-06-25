from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import KNOWN_CHAT_TESTIDS, KNOWN_DETAILS_TESTIDS, log

@dataclass
class Selectors:
    chat:    str = f"[data-testid='{KNOWN_CHAT_TESTIDS[0]}']"
    details: str = f"[data-testid='{KNOWN_DETAILS_TESTIDS[0]}']"


@dataclass
class CaptureResult:
    conversation_id: str
    output_path: Optional[Path]  = None
    error: Optional[str]         = None
    tb: Optional[str]            = None   # traceback, for debug logging

    @property
    def success(self) -> bool:
        return self.error is None

@dataclass
class BulkSummary:
    results: list[CaptureResult] = field(default_factory=list)

    @property
    def succeeded(self) -> list[CaptureResult]:
        return [r for r in self.results if r.success]

    @property
    def failed(self) -> list[CaptureResult]:
        return [r for r in self.results if not r.success]

    def print_summary(self) -> None:
        log.info("─" * 60)
        log.info("COMPLETE  %d/%d succeeded", len(self.succeeded), len(self.results))
        for r in self.succeeded:
            log.info("  ✅  %s  →  %s", r.conversation_id, r.output_path)
        for r in self.failed:
            log.error("  ❌  %s  —  %s", r.conversation_id, r.error)
        log.info("─" * 60)
