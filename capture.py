from __future__ import annotations

import multiprocessing
import sys

from airbnb_capture.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
