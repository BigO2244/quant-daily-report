from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from core.precompute_contract import (
    load_precompute_contract,
    validate_precompute_contract,
)
from core.workflow_status import workflow_status_dir


def _write_outputs(payload: dict[str, object]) -> None:
    target = str(os.getenv("GITHUB_OUTPUT", "")).strip()
    if not target:
        return
    with open(target, "a", encoding="utf-8") as fh:
        for key, value in payload.items():
            if isinstance(value, bool):
                rendered = str(value).lower()
            else:
                rendered = str(value)
            fh.write(f"{key}={rendered}\n")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect Alpaca precompute bundle status")
    parser.add_argument("--report-date", required=True)
    parser.add_argument("--mode", default="ALPACA")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--require-valid", action="store_true")
    parser.add_argument("--json-output", default="")
    return parser.parse_args()


def describe_bundle_status(
    *,
    report_date: str,
    mode: str = "ALPACA",
    force_refresh: bool = False,
) -> dict[str, object]:
    contract = load_precompute_contract(report_date)
    valid, reason = validate_precompute_contract(
        contract,
        expected_trade_date=report_date,
        expected_mode=mode,
    )

    contract_path = Path("outputs/precompute") / str(report_date) / "contract.json"
    bundle_present = contract is not None
    if valid and force_refresh:
        bundle_status = "REFRESH_REQUESTED"
        bundle_should_rebuild = True
        bundle_reason = "force_refresh_precompute"
    elif valid:
        bundle_status = "VALID"
        bundle_should_rebuild = False
        bundle_reason = ""
    elif bundle_present:
        bundle_status = "INVALID"
        bundle_should_rebuild = True
        bundle_reason = str(reason or "precompute_invalid")
    else:
        bundle_status = "MISSING"
        bundle_should_rebuild = True
        bundle_reason = str(reason or "precompute_missing")

    return {
        "report_date": str(report_date),
        "bundle_present": bundle_present,
        "bundle_valid": bool(valid),
        "bundle_status": bundle_status,
        "bundle_should_rebuild": bool(bundle_should_rebuild),
        "bundle_reason": bundle_reason,
        "contract_path": str(contract_path),
    }


def main() -> int:
    args = _parse_args()
    payload = describe_bundle_status(
        report_date=args.report_date,
        mode=args.mode,
        force_refresh=bool(args.force_refresh),
    )
    json_output = str(args.json_output or "").strip()
    if not json_output:
        json_output = str(workflow_status_dir(args.report_date) / "precompute_bundle_status.json")
    _write_json(Path(json_output), payload)
    _write_outputs(payload)
    print(
        "[PRECOMPUTE_BUNDLE] "
        f"report_date={payload['report_date']} "
        f"bundle_status={payload['bundle_status']} "
        f"bundle_present={str(payload['bundle_present']).lower()} "
        f"bundle_valid={str(payload['bundle_valid']).lower()} "
        f"bundle_should_rebuild={str(payload['bundle_should_rebuild']).lower()} "
        f"bundle_reason={payload['bundle_reason'] or 'none'}"
    )
    if args.require_valid and not bool(payload["bundle_valid"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
