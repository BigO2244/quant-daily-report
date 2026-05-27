"""Read-only shadow learning artifact health diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.portfolio_learning_report import build_portfolio_learning_report


def _latest_dated_dir(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = [path for path in root.iterdir() if path.is_dir() and len(path.name) == 10]
    return sorted(candidates, key=lambda path: path.name)[-1] if candidates else None


def _resolve_trade_date(repo_root: Path, trade_date: str | None, latest: bool) -> str | None:
    if trade_date:
        return trade_date
    if latest:
        latest_dir = _latest_dated_dir(repo_root / "outputs" / "shadow_candidates")
        return latest_dir.name if latest_dir else None
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _status_from_report_status(status: str) -> str:
    if status == "OK":
        return "READY"
    if status in {"PARTIAL", "NO_DATA"}:
        return "INCOMPLETE"
    return "UNKNOWN"


def inspect_shadow_learning_health(
    *,
    repo_root: Path,
    trade_date: str | None = None,
    latest: bool = False,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    resolved_trade_date = _resolve_trade_date(repo_root, trade_date, latest)
    if not resolved_trade_date:
        return {
            "trade_date": None,
            "learning_health": "UNKNOWN",
            "status": "UNKNOWN",
            "blocking_reasons": ["unable to resolve trade date"],
            "recommended_next_action": "Inspect outputs/shadow_candidates for dated shadow artifacts.",
            "runtime_effect": "none",
        }

    shadow_dir = repo_root / "outputs" / "shadow_candidates"
    report = build_portfolio_learning_report(
        repo_root=repo_root,
        trade_date=resolved_trade_date,
        shadow_dir=shadow_dir,
    )
    payload = report.payload
    artifact_health = payload.get("artifact_health") if isinstance(payload.get("artifact_health"), dict) else {}
    missing_by_category = (
        artifact_health.get("missing_by_category")
        if isinstance(artifact_health.get("missing_by_category"), dict)
        else {}
    )
    learning_rows = payload.get("learning_readiness") if isinstance(payload.get("learning_readiness"), list) else []
    watch_items = payload.get("watch_items") if isinstance(payload.get("watch_items"), list) else []

    low_readiness = [
        str(row.get("name"))
        for row in learning_rows
        if isinstance(row, dict) and str(row.get("learning_readiness")) == "LOW"
    ]
    status = str(payload.get("status") or report.status)
    health = _status_from_report_status(status)
    blocking_reasons: list[str] = []
    if missing_by_category.get("required"):
        blocking_reasons.append("required learning source artifacts missing")
    if artifact_health.get("stale_reasons"):
        blocking_reasons.append("learning source artifacts stale")
    if status == "NO_DATA":
        blocking_reasons.append("learning report has no usable shadow evaluation")
    if not blocking_reasons and health != "READY":
        blocking_reasons.append("learning report is incomplete")

    next_action = "Learning artifacts are ready for operator review."
    if health != "READY":
        next_action = (
            "Refresh post-close shadow artifacts after hydration, then rerun the learning health diagnostic."
        )
    elif low_readiness:
        next_action = "Review LOW strategy learning readiness before relying on weekly learning interpretation."

    return {
        "trade_date": resolved_trade_date,
        "learning_health": health,
        "status": status,
        "shadow_dir": str(shadow_dir / resolved_trade_date),
        "required_missing": list(missing_by_category.get("required") or []),
        "optional_missing": list(missing_by_category.get("optional") or []),
        "diagnostic_missing": list(missing_by_category.get("diagnostic") or []),
        "stale_reasons": list(artifact_health.get("stale_reasons") or []),
        "raw_stale_reasons": list(artifact_health.get("raw_stale_reasons") or []),
        "low_learning_readiness_strategies": low_readiness,
        "watch_item_count": len(watch_items),
        "watch_items": [str(item) for item in watch_items],
        "blocking_reasons": blocking_reasons,
        "recommended_next_action": next_action,
        "runtime_effect": "none",
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Shadow Learning Health",
        "",
        f"- Trade date: {payload.get('trade_date') or 'UNKNOWN'}",
        f"- Learning health: {payload.get('learning_health')}",
        f"- Report status: {payload.get('status')}",
        f"- Runtime effect: {payload.get('runtime_effect')}",
        f"- Required missing: {len(payload.get('required_missing') or [])}",
        f"- Optional missing: {len(payload.get('optional_missing') or [])}",
        f"- Diagnostic missing: {len(payload.get('diagnostic_missing') or [])}",
        f"- Stale reasons: {', '.join(payload.get('stale_reasons') or []) or 'none'}",
        f"- LOW readiness strategies: {', '.join(payload.get('low_learning_readiness_strategies') or []) or 'none'}",
        "",
        "## Blocking Reasons",
    ]
    reasons = payload.get("blocking_reasons") or []
    if reasons:
        lines.extend(f"- {reason}" for reason in reasons)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Operator Next Action",
            str(payload.get("recommended_next_action") or "Inspect learning artifacts."),
            "",
            "## Watch Items",
        ]
    )
    watch_items = payload.get("watch_items") or []
    if watch_items:
        lines.extend(f"- {item}" for item in watch_items[:12])
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check read-only shadow learning artifact health.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--trade-date")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero unless learning health is READY.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = inspect_shadow_learning_health(
        repo_root=Path(args.repo_root),
        trade_date=args.trade_date,
        latest=bool(args.latest),
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.markdown:
        print(render_markdown(payload), end="")
    else:
        print(
            f"[LEARNING] Health: {payload.get('learning_health')}\n"
            f"[LEARNING] Status: {payload.get('status')}\n"
            f"[LEARNING] Trade Date: {payload.get('trade_date')}\n"
            f"[LEARNING] Next Action: {payload.get('recommended_next_action')}"
        )
    if args.strict and payload.get("learning_health") != "READY":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
