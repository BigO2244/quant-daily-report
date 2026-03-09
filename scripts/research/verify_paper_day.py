from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper.paths import LEDGER_TRADES_PATH, LEDGER_TRADES_LEGACY_PATH


def _count_dupes(df: pd.DataFrame) -> int:
    if df.empty or "trade_date" not in df.columns or "order_id" not in df.columns:
        return 0
    return int(df.duplicated(subset=["trade_date", "order_id"]).sum())


def main() -> None:
    print(f"ledger_canonical={LEDGER_TRADES_PATH}")
    print(f"ledger_legacy={LEDGER_TRADES_LEGACY_PATH} exists={LEDGER_TRADES_LEGACY_PATH.exists()}")

    if LEDGER_TRADES_PATH.exists() and LEDGER_TRADES_PATH.stat().st_size > 0:
        ledger = pd.read_csv(LEDGER_TRADES_PATH)
        print(f"ledger_rows={len(ledger)} ledger_dup_rows={_count_dupes(ledger)}")
    else:
        print("ledger_rows=0 ledger_dup_rows=0")

    nav_path = Path("outputs/perf/nav_timeseries.csv")
    if nav_path.exists() and nav_path.stat().st_size > 0:
        nav = pd.read_csv(nav_path)
        print("nav_latest=", nav.iloc[-1].to_dict())
    else:
        print("nav_latest=None")

    health_dir = Path("outputs/daily")
    latest_health = None
    if health_dir.exists():
        health_files = sorted(health_dir.glob("health_*.json"))
        if health_files:
            latest_health = health_files[-1]
    if latest_health is None:
        print("health_latest=None")
        return
    payload = json.loads(latest_health.read_text(encoding="utf-8"))
    summary = {
        "path": str(latest_health),
        "status": payload.get("status"),
        "error": payload.get("error"),
        "exec_basis_equity": payload.get("execution_basis_equity"),
        "broker_equity": payload.get("broker_equity"),
        "turnover_dollars": payload.get("turnover_dollars"),
        "turnover_pct": payload.get("turnover_pct"),
    }
    print("health_latest=", summary)


if __name__ == "__main__":
    main()
