from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.construction_provenance import write_construction_provenance


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _default_run_root(trade_date: str) -> Path:
    return REPO_ROOT / "outputs" / "reporting" / "construction_provenance" / trade_date


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build reporting-only portfolio construction provenance from existing artifacts."
    )
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--signals", type=Path)
    parser.add_argument("--planned-payload", type=Path)
    parser.add_argument("--candidate-lifecycle", type=Path)
    parser.add_argument("--current-positions", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    trade_date = str(args.trade_date)
    run_root = args.run_root or _default_run_root(trade_date)

    signals_path = args.signals or _first_existing(
        REPO_ROOT / "outputs" / "precompute" / trade_date / "signals.json",
        REPO_ROOT / "signals" / f"{trade_date}.json",
    )
    planned_path = args.planned_payload or _first_existing(
        REPO_ROOT / "outputs" / "precompute" / trade_date / "planned_execution_payload.json",
        run_root / "execution_payload.json",
    )
    lifecycle_path = args.candidate_lifecycle or _first_existing(
        run_root / "audit" / f"candidate_trade_lifecycle_{trade_date}.json",
    )
    current_positions_path = args.current_positions or _first_existing(
        run_root / "broker" / "pretrade_positions.json",
    )

    out_path, payload = write_construction_provenance(
        run_root=run_root,
        trade_date=trade_date,
        run_id=args.run_id or run_root.name,
        source_artifact_paths={
            "signals": str(signals_path) if signals_path else None,
            "planned_payload": str(planned_path) if planned_path else None,
            "candidate_trade_lifecycle": str(lifecycle_path) if lifecycle_path else None,
            "current_positions": str(current_positions_path) if current_positions_path else None,
        },
        repo_root=REPO_ROOT,
    )
    print(f"Wrote {out_path}")
    print(f"Rows: {payload.get('summary', {}).get('row_count')}")
    print(f"Status: {payload.get('summary', {}).get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
