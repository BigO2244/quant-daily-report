import argparse
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# On main, Sleeve 2 code lives under sleeve2/
# Sleeve 1 still runs backtest.py / quant_report.py for now.
ROUTES = {
    ("backtest", "sleeve-1"): ROOT / "backtest.py",
    ("report",   "sleeve-1"): ROOT / "quant_report.py",

    ("backtest", "sleeve-2"): ROOT / "sleeve2" / "sleeve2_backtest.py",
    ("report",   "sleeve-2"): ROOT / "quant_report.py",  # current report lives here
}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["backtest", "report"], required=True)
    p.add_argument("--sleeve", choices=["sleeve-1", "sleeve-2"], required=True)
    args = p.parse_args()

    target = ROUTES.get((args.mode, args.sleeve))
    if not target or not target.exists():
        print(f"ERROR: No route for mode={args.mode} sleeve={args.sleeve}")
        for k, v in ROUTES.items():
            print(f"  {k} -> {v}")
        sys.exit(2)

    runpy.run_path(str(target), run_name="__main__")

if __name__ == "__main__":
    main()
