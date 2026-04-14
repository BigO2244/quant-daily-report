#!/usr/bin/env python3
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    target = ROOT / "scripts" / "research" / "research_backtest_sleeve1_alpha_variant_random_windows.py"
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
