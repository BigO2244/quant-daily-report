#!/usr/bin/env python3
"""Compatibility wrapper for relocated sleeve1 alpha-variant backtest script."""

from __future__ import annotations

import runpy
from pathlib import Path


def main() -> None:
    target = Path(__file__).resolve().parent / "research" / "research_backtest_sleeve1_alpha_variant.py"
    if not target.exists():
        raise FileNotFoundError(f"Missing canonical script: {target}")
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
