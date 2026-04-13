#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.research.build_quant_dashboard import DashboardBuilder, main, parse_args

__all__ = ["DashboardBuilder", "parse_args", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
