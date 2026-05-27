"""Advisory read-only semantic validation for precompute bundles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.precompute_bundle_validation import validate_precompute_bundle


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _find_order_lists(payload: Any) -> list[list[dict[str, Any]]]:
    found: list[list[dict[str, Any]]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"orders", "planned_orders", "target_orders", "shadow_orders"} and isinstance(value, list):
                order_rows = [item for item in value if isinstance(item, dict)]
                if order_rows:
                    found.append(order_rows)
            else:
                found.extend(_find_order_lists(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_find_order_lists(item))
    return found


def _first_present(payloads: list[dict[str, Any]], *keys: str) -> Any:
    for payload in payloads:
        for key in keys:
            if key in payload and payload.get(key) not in (None, ""):
                return payload.get(key)
    return None


def _check(name: str, status: str, severity: str, message: str) -> dict[str, str]:
    return {"name": name, "status": status, "severity": severity, "message": message}


def inspect_precompute_semantics(*, bundle_dir: Path, trade_date: str) -> dict[str, Any]:
    bundle_dir = Path(bundle_dir)
    baseline = validate_precompute_bundle(bundle_dir, trade_date=trade_date)
    payloads = {
        name: _read_json(bundle_dir / name)
        for name in baseline.get("required_files", [])
        if (bundle_dir / name).is_file()
    }
    dict_payloads = [payload for payload in payloads.values() if isinstance(payload, dict)]
    checks: list[dict[str, str]] = []

    if baseline.get("status") != "OK":
        checks.append(
            _check(
                "baseline_bundle_integrity",
                "NOT_ASSESSABLE",
                "WARN",
                "Semantic validation requires the existing bundle integrity validator to pass first.",
            )
        )
    else:
        checks.append(_check("baseline_bundle_integrity", "OK", "INFO", "Existing bundle integrity validation passed."))

    run_ids = sorted({str(payload.get("run_id")) for payload in dict_payloads if payload.get("run_id")})
    checks.append(
        _check(
            "run_identity_consistency",
            "OK" if len(run_ids) <= 1 else "WARN",
            "INFO" if len(run_ids) <= 1 else "WARN",
            "Run identity is consistent." if len(run_ids) <= 1 else f"Multiple run ids found: {', '.join(run_ids)}.",
        )
    )

    workflow_kinds = sorted({str(payload.get("workflow_kind")) for payload in dict_payloads if payload.get("workflow_kind")})
    checks.append(
        _check(
            "workflow_kind_consistency",
            "OK" if len(workflow_kinds) <= 1 else "WARN",
            "INFO" if len(workflow_kinds) <= 1 else "WARN",
            "Workflow kind is consistent."
            if len(workflow_kinds) <= 1
            else f"Multiple workflow kinds found: {', '.join(workflow_kinds)}.",
        )
    )

    strategy_value = _first_present(dict_payloads, "live_strategy_id", "strategy_id", "strategy")
    strategy_text = str(strategy_value or "").lower()
    if strategy_value is None:
        checks.append(_check("strategy_surface", "NOT_ASSESSABLE", "WARN", "No strategy surface metadata found."))
    elif "orion" in strategy_text or "lyra" in strategy_text:
        checks.append(
            _check(
                "strategy_surface",
                "FAIL_ADVISORY",
                "FAIL_ADVISORY",
                f"Execution-facing strategy metadata references shadow strategy: {strategy_value}.",
            )
        )
    else:
        checks.append(_check("strategy_surface", "OK", "INFO", f"Strategy surface metadata is {strategy_value}."))

    planned_payload = payloads.get("planned_execution_payload.json") or {}
    order_lists = _find_order_lists(planned_payload)
    orders = [order for order_list in order_lists for order in order_list]
    if not orders:
        checks.append(_check("planned_order_shape", "NOT_ASSESSABLE", "WARN", "No planned order list found."))
    else:
        malformed = []
        for idx, order in enumerate(orders):
            symbol = order.get("symbol") or order.get("ticker")
            side = order.get("side") or order.get("action")
            qty = order.get("qty") or order.get("quantity") or order.get("shares")
            if symbol in (None, "") or side in (None, "") or qty in (None, ""):
                malformed.append(str(idx))
        checks.append(
            _check(
                "planned_order_shape",
                "OK" if not malformed else "WARN",
                "INFO" if not malformed else "WARN",
                f"{len(orders)} planned orders have symbol, side/action, and quantity fields."
                if not malformed
                else f"Malformed planned order indexes: {', '.join(malformed[:10])}.",
            )
        )

    severities = {check["severity"] for check in checks}
    status = "FAIL_ADVISORY" if "FAIL_ADVISORY" in severities else "WARN" if "WARN" in severities else "OK"
    return {
        "schema_version": 1,
        "validation_scope": "precompute_semantic_advisory",
        "trade_date": trade_date,
        "bundle_dir": str(bundle_dir),
        "status": status,
        "blocking": False,
        "runtime_effect": "none",
        "baseline_bundle_status": baseline.get("status"),
        "baseline_validation_failures": baseline.get("validation_failures") or [],
        "checks": checks,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Precompute Semantic Validation",
        "",
        f"- Trade date: {payload.get('trade_date')}",
        f"- Status: {payload.get('status')}",
        f"- Blocking: {payload.get('blocking')}",
        f"- Runtime effect: {payload.get('runtime_effect')}",
        f"- Baseline bundle status: {payload.get('baseline_bundle_status')}",
        "",
        "## Checks",
    ]
    for check in payload.get("checks") or []:
        lines.append(
            f"- {check.get('name')}: {check.get('status')} / {check.get('severity')} - {check.get('message')}"
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run advisory read-only semantic validation on a precompute bundle.")
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero unless advisory status is OK.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = inspect_precompute_semantics(bundle_dir=Path(args.bundle_dir), trade_date=args.trade_date)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.markdown:
        print(render_markdown(payload), end="")
    else:
        print(
            f"[PRECOMPUTE_SEMANTIC] Status: {payload.get('status')}\n"
            f"[PRECOMPUTE_SEMANTIC] Blocking: {payload.get('blocking')}\n"
            f"[PRECOMPUTE_SEMANTIC] Runtime Effect: {payload.get('runtime_effect')}"
        )
    if args.strict and payload.get("status") != "OK":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
