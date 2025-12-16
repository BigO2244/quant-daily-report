import argparse
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEGACY = ROOT / "legacy"

ROUTES = {
    ("backtest", "sleeve-2"): LEGACY / "backtest_legacy.py",
    ("report",   "sleeve-2"): LEGACY / "quant_report_legacy.py",
    # sleeve-1 will be added later (deliberately)
}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["backtest", "report"], required=True)
    p.add_argument("--sleeve", choices=["sleeve-1", "sleeve-2"], required=True)
    args = p.parse_args()

    key = (args.mode, args.sleeve)
    target = ROUTES.get(key)

    if not target or not target.exists():
        print(f"ERROR: No route configured for mode={args.mode}, sleeve={args.sleeve}")
        print("Configured routes:")
        for k, v in ROUTES.items():
            print(f"  {k} -> {v}")
        sys.exit(2)

    # Execute the target script as if it were __main__
    runpy.run_path(str(target), run_name="__main__")

if __name__ == "__main__":
    main()
