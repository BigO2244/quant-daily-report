#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_data.hydration import STATUS_VALUES, read_json


def validate_swarm_artifact(path: Path) -> list[str]:
    payload = read_json(path)
    errors: list[str] = []
    summary = payload.get("summary") or {}
    if payload.get("attempts") is None:
        errors.append("hydration swarm artifact missing attempts")
    if payload.get("datasets") is None:
        errors.append("hydration swarm artifact missing datasets")
    if summary.get("broker_submission_invoked") is not False:
        errors.append("broker_submission_invoked must be false")
    for attempt in payload.get("attempts") or []:
        dataset_id = attempt.get("dataset_id", "<missing>")
        if attempt.get("status") not in STATUS_VALUES:
            errors.append(f"{dataset_id}: invalid status {attempt.get('status')}")
        for field in (
            "source_attempted",
            "failure_reason",
            "recommended_user_action",
            "records_written",
            "started_at",
            "completed_at",
            "ingestion_timestamp",
            "as_of_date",
            "PIT_safe_status",
            "validation_status",
        ):
            if field not in attempt:
                errors.append(f"{dataset_id}: missing attempt field {field}")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate latest hydration swarm artifact.")
    parser.add_argument("--path", type=Path, default=REPO_ROOT / "data" / "hydration_logs" / "latest_hydration_swarm.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_swarm_artifact(args.path)
    print(json.dumps({"status": "FAIL" if errors else "OK", "errors": errors}, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
