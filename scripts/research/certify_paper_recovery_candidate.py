"""Certify the drawdown recovery candidate for paper observation only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.paper_recovery_acceptance import (  # noqa: E402
    evaluate_paper_recovery_acceptance,
)


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _latest_jsonl(path: Path) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return rows[-1] if rows and isinstance(rows[-1], dict) else {}


def _open_orders(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"live order ledger missing: {path}")
    latest_by_id: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        order_id = str(
            row.get("id")
            or row.get("order_id")
            or row.get("client_order_id")
            or ""
        ).strip()
        if not order_id:
            raise ValueError("live order ledger row missing stable order id")
        latest_by_id[order_id] = row
    terminal = {
        "filled",
        "canceled",
        "cancelled",
        "expired",
        "rejected",
        "done_for_day",
        "replaced",
        "stopped",
        "suspended",
    }
    return [
        row
        for row in latest_by_id.values()
        if str(row.get("status") or "").strip().lower() not in terminal
    ]


def _live_control(
    *,
    kill_state_path: Path,
    live_ledger_root: Path,
) -> dict[str, Any]:
    kill = _json(kill_state_path)
    account = _latest_jsonl(live_ledger_root / "account_snapshots.jsonl")
    position_paths = sorted((live_ledger_root / "positions").glob("positions_*.json"))
    positions_payload = _json(position_paths[-1]) if position_paths else {}
    positions = (
        positions_payload.get("positions")
        if isinstance(positions_payload.get("positions"), list)
        else positions_payload
        if isinstance(positions_payload, list)
        else []
    )
    orders_path = live_ledger_root / "orders.jsonl"
    open_orders = _open_orders(orders_path)
    return {
        "kill_switch_engaged": kill.get("engaged") is True,
        "kill_switch_source": str(kill_state_path),
        "positions_count": len(positions),
        "open_orders_count": len(open_orders),
        "open_order_ids": [
            str(row.get("id") or row.get("order_id") or row.get("client_order_id"))
            for row in open_orders
        ],
        "long_market_value": account.get("long_market_value"),
        "short_market_value": account.get("short_market_value"),
        "cash": account.get("cash"),
        "equity": account.get("equity"),
        "account_snapshot_pulled_at_utc": account.get("pulled_at_utc"),
        "positions_source": str(position_paths[-1]) if position_paths else None,
        "orders_source": str(orders_path),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--replay",
        type=Path,
        default=Path(
            "outputs/research/drawdown_recovery/2026-07-28/drawdown_recovery_replay.json"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/paper_recovery_policy.json"),
    )
    parser.add_argument("--kill-state", type=Path, required=True)
    parser.add_argument(
        "--live-ledger-root",
        type=Path,
        default=Path("outputs/ledger/live"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "outputs/research/drawdown_recovery/2026-07-28/paper_recovery_acceptance.json"
        ),
    )
    return parser.parse_args(argv)


def _under(repo: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo / path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    replay = _json(_under(repo, args.replay))
    config = _json(_under(repo, args.config))
    live_control = _live_control(
        kill_state_path=_under(repo, args.kill_state),
        live_ledger_root=_under(repo, args.live_ledger_root),
    )
    result = evaluate_paper_recovery_acceptance(
        replay=replay,
        config=config,
        live_control=live_control,
    )
    result["source_artifacts"] = {
        "replay": str(_under(repo, args.replay)),
        "config": str(_under(repo, args.config)),
        "kill_state": str(_under(repo, args.kill_state)),
        "live_ledger_root": str(_under(repo, args.live_ledger_root)),
    }
    output = _under(repo, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": result["status"], "output": str(output)}))
    return 0 if result["paper_enablement_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
