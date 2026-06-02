from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper.trading_calendar import is_trading_day
from scripts.research.execution_timing_replay import load_plan, parse_execution_date
from scripts.research.intraday_research_cache import (
    CACHE_KEY_VERSION,
    DEFAULT_CACHE_ROOT,
    DEFAULT_PLAN_ROOT,
    DEFAULT_STATUS_ROOT,
    collect_intraday_cache,
    resolve_plan_path,
)


SCHEMA_VERSION = "caerus_execution_timing_cache_request_v1"


@dataclass(frozen=True)
class ExecutionTimingCacheRequest:
    plan_date: str
    execution_date: str
    plan_path: Path
    plan_exists: bool
    planned_for_raw: str | None
    reason_codes: tuple[str, ...]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_execution_timing_cache_request(
    *,
    plan_date: str,
    plan_root: Path = DEFAULT_PLAN_ROOT,
) -> ExecutionTimingCacheRequest:
    plan_path = resolve_plan_path(plan_date, plan_root)
    if not plan_path.exists():
        return ExecutionTimingCacheRequest(
            plan_date=plan_date,
            execution_date=plan_date,
            plan_path=plan_path,
            plan_exists=False,
            planned_for_raw=None,
            reason_codes=("plan_payload_missing",),
        )
    try:
        plan = load_plan(plan_path, plan_date)
    except Exception:
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        return ExecutionTimingCacheRequest(
            plan_date=plan_date,
            execution_date=parse_execution_date(payload if isinstance(payload, dict) else {}, plan_date),
            plan_path=plan_path,
            plan_exists=True,
            planned_for_raw=payload.get("planned_for") if isinstance(payload, dict) else None,
            reason_codes=("plan_payload_parse_error",),
        )
    reasons: list[str] = []
    if plan.execution_date != plan_date:
        reasons.append("execution_date_derived_from_planned_for")
    if not plan.trades:
        reasons.append("empty_planned_payload")
    if not is_trading_day(plan.execution_date):
        reasons.append("execution_date_not_trading_day")
    return ExecutionTimingCacheRequest(
        plan_date=plan_date,
        execution_date=plan.execution_date,
        plan_path=plan_path,
        plan_exists=True,
        planned_for_raw=plan.planned_for_raw,
        reason_codes=tuple(sorted(set(reasons)) or ("ok",)),
    )


def build_execution_timing_cache(
    *,
    plan_date: str,
    repo_root: Path | str = Path("."),
    cache_root: Path | str | None = None,
    status_root: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    repo = Path(repo_root)
    resolved_cache_root = Path(cache_root) if cache_root is not None else repo / DEFAULT_CACHE_ROOT
    resolved_status_root = Path(status_root) if status_root is not None else repo / DEFAULT_STATUS_ROOT
    request = resolve_execution_timing_cache_request(plan_date=plan_date, plan_root=repo / DEFAULT_PLAN_ROOT)
    out_path = repo / "outputs" / "research" / "execution_timing" / plan_date / "cache_status.json"
    base_payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_date": request.plan_date,
        "execution_date": request.execution_date,
        "planned_for_raw": request.planned_for_raw,
        "plan_path": str(request.plan_path),
        "plan_exists": request.plan_exists,
        "cache_root": str(resolved_cache_root),
        "status_root": str(resolved_status_root),
        "cache_key_version": CACHE_KEY_VERSION,
        "dry_run": dry_run,
        "reason_codes": list(request.reason_codes),
        "notes": "Research-only execution timing minute-bar cache; no orders submitted and no execution artifacts mutated.",
    }
    if dry_run:
        payload = {**base_payload, "overall_status": "DRY_RUN"}
        _write_json(out_path, payload)
        return payload
    if not request.plan_exists:
        payload = {**base_payload, "overall_status": "SKIPPED"}
        _write_json(out_path, payload)
        return payload
    if "empty_planned_payload" in request.reason_codes or "execution_date_not_trading_day" in request.reason_codes:
        payload = {**base_payload, "overall_status": "SKIPPED"}
        _write_json(out_path, payload)
        return payload
    try:
        result = collect_intraday_cache(
            trade_date=request.execution_date,
            plan_path=request.plan_path,
            cache_root=resolved_cache_root,
            status_root=resolved_status_root,
            require_trading_day=True,
        )
    except Exception as exc:
        payload = {
            **base_payload,
            "overall_status": "FAILED",
            "reason_codes": sorted(set(base_payload["reason_codes"] + ["intraday_cache_runtime_error"])),
            "error": str(exc),
        }
        _write_json(out_path, payload)
        return payload
    payload = {
        **base_payload,
        "overall_status": result.overall_status,
        "counts": result.counts,
        "collector_status_path": str(result.status_path),
        "reason_codes": list(request.reason_codes),
    }
    if result.overall_status != "OK":
        payload["reason_codes"] = sorted(set(payload["reason_codes"] + ["intraday_cache_incomplete"]))
    _write_json(out_path, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hydrate read-only minute bars for execution timing counterfactuals.")
    parser.add_argument("--date", required=True, help="Precompute plan date in YYYY-MM-DD format.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--status-root", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = build_execution_timing_cache(
        plan_date=args.date,
        repo_root=Path(args.repo_root),
        cache_root=Path(args.cache_root) if args.cache_root else None,
        status_root=Path(args.status_root) if args.status_root else None,
        dry_run=args.dry_run,
    )
    print(json.dumps({
        "plan_date": payload["plan_date"],
        "execution_date": payload["execution_date"],
        "overall_status": payload["overall_status"],
        "reason_codes": payload["reason_codes"],
    }, sort_keys=True))
    return 1 if payload["overall_status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
