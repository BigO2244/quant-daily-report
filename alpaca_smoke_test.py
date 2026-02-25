#!/usr/bin/env python3
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> int:
    try:
        from scripts.alpaca_smoke_test import main as smoke_main

        rc = smoke_main()
        return int(rc or 0)
    except Exception:
        script = Path(__file__).resolve().parent / "scripts" / "alpaca_smoke_test.py"
        if not script.exists():
            print(f"ERROR: missing {script}", file=sys.stderr)
            return 2
        try:
            runpy.run_path(str(script), run_name="__main__")
            return 0
        except SystemExit as exc:
            code = exc.code
            if code is None:
                return 0
            try:
                return int(code)
            except Exception:
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
