from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.run_pointer import read_latest_run_pointer


def _resolve_run_root(run_root: str | None = None) -> tuple[Path, dict[str, Any] | None]:
    if run_root:
        return Path(run_root), None
    latest = read_latest_run_pointer()
    if not latest or not str(latest.get("run_root") or "").strip():
        raise FileNotFoundError("latest_run.json missing or has no run_root")
    return Path(str(latest.get("run_root"))), latest


def _resolve_recon_path(run_root: Path, trade_date: str | None = None) -> Path:
    broker_dir = run_root / "broker"
    if trade_date:
        candidate = broker_dir / f"recon_posttrade_{trade_date}.json"
        if candidate.exists():
            return candidate
    matches = sorted(broker_dir.glob("recon_posttrade_*.json"))
    if not matches:
        raise FileNotFoundError(f"no recon_posttrade artifact found under {broker_dir}")
    return matches[-1]


def load_paper_repair_actions(
    *,
    run_root: str | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    resolved_run_root, latest = _resolve_run_root(run_root)
    recon_path = _resolve_recon_path(resolved_run_root, trade_date or (latest or {}).get("trade_date"))
    payload = json.loads(recon_path.read_text(encoding="utf-8"))
    return {
        "run_root": str(resolved_run_root),
        "trade_date": str(payload.get("trade_date") or trade_date or (latest or {}).get("trade_date") or ""),
        "recon_path": str(recon_path),
        "drift_status": str(payload.get("drift_status") or "UNKNOWN"),
        "affected_symbols": list(payload.get("affected_symbols") or []),
        "repair_suggestions": list(payload.get("repair_suggestions") or []),
        "unexpected_short_positions": list(payload.get("unexpected_short_positions") or []),
        "operator_message": str(payload.get("operator_message") or ""),
    }


def format_paper_repair_actions(plan: dict[str, Any]) -> str:
    lines = [
        "[PAPER_REPAIR_HELPER]",
        f"trade_date={plan.get('trade_date') or ''}",
        f"drift_status={plan.get('drift_status') or 'UNKNOWN'}",
        f"recon_path={plan.get('recon_path') or ''}",
        f"affected_symbols={','.join(list(plan.get('affected_symbols') or [])) or 'none'}",
        f"operator_message={plan.get('operator_message') or ''}",
    ]
    suggestions = list(plan.get("repair_suggestions") or [])
    if suggestions:
        lines.append("recommended_actions:")
        lines.extend([f"- {item}" for item in suggestions])
    else:
        lines.append("recommended_actions: none")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print paper-only repair suggestions from recon_posttrade artifact.")
    parser.add_argument("--run-root", default=None, help="Optional outputs/runs/<RUN_ID> path. Defaults to latest_run.json.")
    parser.add_argument("--trade-date", default=None, help="Optional trade date YYYY-MM-DD.")
    args = parser.parse_args()

    plan = load_paper_repair_actions(run_root=args.run_root, trade_date=args.trade_date)
    print(format_paper_repair_actions(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
