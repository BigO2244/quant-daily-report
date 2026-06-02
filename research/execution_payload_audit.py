"""Execution payload audit — Workstream 3.

Determines why governance reports ``planned_execution_payload_missing``
and ``no_planned_orders`` by classifying the situation into one of five
hypotheses:

  A. PAYLOAD_TRULY_MISSING       — no payload artifact exists anywhere
                                    relevant on disk.
  B. WRONG_PATH_DISCOVERY        — payload exists but at a path the
                                    consumer does not look in (or a
                                    pointer is stale).
  C. DATE_MISMATCH               — payload exists for a different date
                                    than the research packet is reading.
  D. EMPTY_BUT_VALID             — payload exists, is well-formed, but
                                    legitimately contains zero orders
                                    (e.g. no-trade day).
  E. PACKET_CONSUMING_WRONG_DATE — packet target date does not match
                                    the most recent shadow / precompute
                                    activity; consumer should target a
                                    different date.

Research-only diagnostic. Does NOT alter execution behavior. Path
discovery is reported as part of the diagnostic; the consumer can
choose to patch the discovery layer if and only if the audit
recommends it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "caerus_execution_payload_audit_v1"

VERDICT_TRULY_MISSING = "PAYLOAD_TRULY_MISSING"
VERDICT_WRONG_PATH = "WRONG_PATH_DISCOVERY"
VERDICT_DATE_MISMATCH = "DATE_MISMATCH"
VERDICT_EMPTY_BUT_VALID = "EMPTY_BUT_VALID"
VERDICT_PACKET_WRONG_DATE = "PACKET_CONSUMING_WRONG_DATE"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _scan_precompute_payloads(repo: Path) -> list[dict[str, Any]]:
    """Walk outputs/precompute/ and return one record per date with a
    planned_execution_payload.json. Each record contains the date, order
    count, trade count, and full path."""
    root = repo / "outputs" / "precompute"
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        payload_path = child / "planned_execution_payload.json"
        if not payload_path.exists():
            continue
        payload = _read_json(payload_path) or {}
        order_count = len(payload.get("orders") or []) if isinstance(payload.get("orders"), list) else 0
        trade_count = len(payload.get("trades") or []) if isinstance(payload.get("trades"), list) else 0
        out.append(
            {
                "date": child.name,
                "path": str(payload_path),
                "order_count": int(order_count),
                "trade_count": int(trade_count),
                "execution_status": str(payload.get("execution_status") or ""),
            }
        )
    return out


def _scan_run_payloads(repo: Path, trade_date: str) -> list[dict[str, Any]]:
    """Look for run directories under outputs/runs/ that match trade_date
    by prefix and report whether they contain payload artifacts. This
    is the alternative path layout used by some pipelines."""
    root = repo / "outputs" / "runs"
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not child.name.startswith(trade_date):
            continue
        candidates = list(child.glob("planned_execution_payload*.json"))
        out.append(
            {
                "run_dir": str(child),
                "matches": [str(p) for p in candidates],
                "match_count": len(candidates),
            }
        )
    return out


def _verdict_and_remediation(
    *,
    target_payload_present: bool,
    target_payload_order_count: int,
    precompute_records: list[dict[str, Any]],
    run_records: list[dict[str, Any]],
    trade_date: str,
) -> tuple[str, str, str]:
    """Return ``(verdict, root_cause, remediation)``."""
    most_recent_date = precompute_records[-1]["date"] if precompute_records else None
    if target_payload_present and target_payload_order_count == 0:
        return (
            VERDICT_EMPTY_BUT_VALID,
            "payload_exists_with_zero_orders",
            "Confirm this was a no-trade day. If so, governance should treat 'no_planned_orders' as a benign condition rather than a blocker.",
        )
    if target_payload_present and target_payload_order_count > 0:
        return (
            "PAYLOAD_PRESENT_AND_NONEMPTY",
            "payload_is_fine_blocker_should_clear",
            "Rebuild downstream artifacts (universe_governance, execution_timing) so the blocker clears.",
        )
    if not precompute_records and not run_records:
        return (
            VERDICT_TRULY_MISSING,
            "no_planned_execution_payload_artifacts_anywhere",
            "Bootstrap the precompute pipeline so planned_execution_payload.json is produced.",
        )
    if run_records and any(r.get("matches") for r in run_records):
        return (
            VERDICT_WRONG_PATH,
            "payload_present_in_outputs_runs_but_not_outputs_precompute",
            "Discovery layer should also check outputs/runs/<run-id>/ for payload artifacts, or copy them into outputs/precompute/<date>/.",
        )
    if most_recent_date and most_recent_date != trade_date:
        return (
            VERDICT_PACKET_WRONG_DATE,
            f"most_recent_payload_date_{most_recent_date}_differs_from_target_date_{trade_date}",
            f"Either run the precompute pipeline for {trade_date}, or build research artifacts against {most_recent_date} instead.",
        )
    if precompute_records:
        return (
            VERDICT_DATE_MISMATCH,
            "no_payload_for_target_date_but_history_present",
            "Run scripts/run_precomputed_alpaca_execution.py (or equivalent) for the target date to produce planned_execution_payload.json.",
        )
    return (
        VERDICT_TRULY_MISSING,
        "fallthrough_payload_missing",
        "Bootstrap the precompute pipeline.",
    )


def build_execution_payload_audit(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    target_payload_path = repo / "outputs" / "precompute" / trade_date / "planned_execution_payload.json"
    target_payload = _read_json(target_payload_path)
    target_payload_present = target_payload is not None
    target_orders = []
    target_trades = []
    if isinstance(target_payload, dict):
        if isinstance(target_payload.get("orders"), list):
            target_orders = target_payload["orders"]
        if isinstance(target_payload.get("trades"), list):
            target_trades = target_payload["trades"]

    precompute_records = _scan_precompute_payloads(repo)
    run_records = _scan_run_payloads(repo, trade_date)

    target_order_count = max(len(target_orders), len(target_trades))

    verdict, root_cause, remediation = _verdict_and_remediation(
        target_payload_present=target_payload_present,
        target_payload_order_count=target_order_count,
        precompute_records=precompute_records,
        run_records=run_records,
        trade_date=trade_date,
    )

    most_recent_payload = precompute_records[-1] if precompute_records else None
    reason_codes: list[str] = []
    if verdict != "PAYLOAD_PRESENT_AND_NONEMPTY":
        reason_codes.append(verdict.lower())
    if not precompute_records:
        reason_codes.append("no_precompute_history")
    if not reason_codes:
        reason_codes.append("ok")

    coverage = {
        "target_payload_present": target_payload_present,
        "target_order_count": target_order_count,
        "target_trade_count": len(target_trades),
        "precompute_dates_present": len(precompute_records),
        "run_dirs_matching_target_date": len(run_records),
    }

    source_artifacts: list[str] = []
    if target_payload_present:
        source_artifacts.append(str(target_payload_path))
    for rec in precompute_records:
        source_artifacts.append(rec["path"])

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": True,
        "confidence": "HIGH",
        "verdict": verdict,
        "root_cause": root_cause,
        "remediation": remediation,
        "target_payload_path": str(target_payload_path),
        "target_payload_present": target_payload_present,
        "most_recent_payload": most_recent_payload,
        "precompute_dates": [r["date"] for r in precompute_records],
        "precompute_records": precompute_records,
        "run_records": run_records,
        "coverage": coverage,
        "reason_codes": sorted(set(reason_codes)),
        "source_artifacts": source_artifacts,
    }

    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "execution_payload_audit") / trade_date
    _write_json(out_dir / "execution_payload_audit.json", payload)
    _write_text(out_dir / "execution_payload_audit.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    cov = payload.get("coverage") or {}
    lines = [
        f"# Execution Payload Audit - {payload.get('date')}",
        "",
        f"- Verdict: {payload.get('verdict')}",
        f"- Root cause: {payload.get('root_cause')}",
        f"- Remediation: {payload.get('remediation')}",
        f"- Target payload path: `{payload.get('target_payload_path')}`",
        f"- Target payload present: {payload.get('target_payload_present')}",
        f"- Target order count: {cov.get('target_order_count')}",
        f"- Target trade count: {cov.get('target_trade_count')}",
        f"- Most recent payload: {payload.get('most_recent_payload')}",
        f"- Precompute dates present: {cov.get('precompute_dates_present')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "## Precompute Records",
        "",
        "| Date | Orders | Trades | Execution Status |",
        "|---|---:|---:|---|",
    ]
    for row in payload.get("precompute_records") or []:
        lines.append(
            f"| {row.get('date')} | {row.get('order_count')} | {row.get('trade_count')} | {row.get('execution_status')} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose why planned_execution_payload is reported as missing.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_execution_payload_audit(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": args.date,
                "verdict": payload["verdict"],
                "root_cause": payload["root_cause"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
