#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research.run_audit_2022_and_worst import main, parse_args

__all__ = ["parse_args", "main"]


if __name__ == "__main__":
    main()
