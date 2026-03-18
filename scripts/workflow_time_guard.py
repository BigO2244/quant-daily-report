from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from core.workflow_status import (
    classify_live_window,
    classify_precompute_window,
    workflow_status_dir,
)


def _bool_flag(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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
    parser = argparse.ArgumentParser(description="Evaluate workflow time-window guards for Alpaca orchestration")
    parser.add_argument("workflow_kind", choices=("precompute", "live"))
    parser.add_argument("--report-date", required=True)
    parser.add_argument("--event-name", default=os.getenv("GITHUB_EVENT_NAME", "schedule"))
    parser.add_argument("--force-refresh-precompute", action="store_true")
    parser.add_argument("--force-live-outside-window", action="store_true")
    parser.add_argument("--json-output", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.workflow_kind == "precompute":
        payload = classify_precompute_window(
            force_refresh=bool(args.force_refresh_precompute),
            event_name=str(args.event_name or "schedule"),
        )
    else:
        payload = classify_live_window(
            force_outside_window=bool(args.force_live_outside_window),
            event_name=str(args.event_name or "schedule"),
        )

    payload["report_date"] = str(args.report_date)
    json_output = str(args.json_output or "").strip()
    if not json_output:
        json_output = str(workflow_status_dir(args.report_date) / f"{args.workflow_kind}_guard.json")
    _write_json(Path(json_output), payload)
    _write_outputs(payload)
    print(
        "[WORKFLOW_GUARD] "
        f"workflow_kind={payload['workflow_kind']} "
        f"report_date={payload['report_date']} "
        f"now_et={payload['now_et']} "
        f"intended_schedule_class={payload['intended_schedule_class']} "
        f"event_freshness_status={payload['event_freshness_status']} "
        f"execution_window_status={payload['execution_window_status']} "
        f"allow_run={str(payload['allow_run']).lower()} "
        f"reason={payload['reason']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
