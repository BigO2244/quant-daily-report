from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

try:
    from paper.trading_calendar import is_trading_day, next_trading_day
except Exception:  # pragma: no cover - defensive
    is_trading_day = None  # type: ignore[assignment]
    next_trading_day = None  # type: ignore[assignment]

from research_registry.research.model_quality_common import (
    collect_reason_codes,
    md_join,
    model_quality_dir,
    normalize_date,
    read_json,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_portfolio_history_freshness_v1"

PORTFOLIO_HISTORY_FILES = {
    "summary": Path("outputs/portfolio_history/summary.json"),
    "nav": Path("outputs/portfolio_history/nav.csv"),
    "positions": Path("outputs/portfolio_history/positions.csv"),
    "transactions": Path("outputs/portfolio_history/transactions.csv"),
    "attribution": Path("outputs/portfolio_history/attribution.csv"),
}


def build_portfolio_history_freshness(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    broker_auth_status: str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    inspected = _inspect_portfolio_files(repo=repo)
    summary_payload = inspected["summary"].get("payload") if isinstance(inspected.get("summary"), dict) else None

    latest_candidates = []
    if isinstance(summary_payload, dict):
        latest_candidates.extend(_date_candidates(summary_payload.get("as_of_date")))
        latest_candidates.extend(_date_candidates((summary_payload.get("latest") or {}).get("date")))
    for name in ("nav", "positions", "transactions", "attribution"):
        latest_candidates.extend(_date_candidates((inspected.get(name) or {}).get("latest_date")))
    latest_date = max(latest_candidates) if latest_candidates else None

    reason_blocks = []
    for info in inspected.values():
        if isinstance(info, dict):
            reason_blocks.append(info.get("reason_codes") or [])
    reason_codes = set(collect_reason_codes(*reason_blocks))
    if reason_codes == {"ok"}:
        reason_codes = set()

    status = _freshness_status(
        target_date=target,
        latest_date=latest_date,
        inspected=inspected,
        broker_auth_status=broker_auth_status,
        reason_codes=reason_codes,
    )
    if status == "READY":
        reason_codes.discard("PORTFOLIO_HISTORY_STALE")
    elif status == "STALE":
        reason_codes.add("PORTFOLIO_HISTORY_STALE")
    elif status == "MISSING":
        reason_codes.add("PORTFOLIO_HISTORY_MISSING")
    elif status == "AUTH_FAILED":
        reason_codes.update({"BROKER_HISTORY_UNAVAILABLE", "AUTH_FAILED"})
    elif status == "UNKNOWN":
        reason_codes.add("PORTFOLIO_HISTORY_DIAGNOSTIC_UNKNOWN")

    # FR-066 §5: verify the checksum manifest and scan for trading-day NAV gaps.
    checksum_verification = _verify_checksum_manifest(repo=repo)
    nav_gap_check = _nav_gap_check(repo=repo)
    reason_codes.update(checksum_verification.get("reason_codes") or [])
    reason_codes.update(nav_gap_check.get("reason_codes") or [])
    reason_codes.discard("ok")

    confidence = _downstream_confidence(status)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": target,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "available": status in {"READY", "STALE"},
        "freshness_status": status,
        "latest_portfolio_history_date": latest_date,
        "target_date": target,
        "row_counts": {
            name: info.get("row_count")
            for name, info in sorted(inspected.items())
            if isinstance(info, dict) and name != "summary"
        },
        "nav_date_coverage": _date_coverage(inspected.get("nav") or {}),
        "source_paths_inspected": _source_paths(repo=repo, inspected=inspected, summary_payload=summary_payload),
        "canonical_artifacts": {
            name: str(repo / rel_path)
            for name, rel_path in sorted(PORTFOLIO_HISTORY_FILES.items())
        },
        "broker_auth_status": broker_auth_status,
        "safe_refresh_command": "python3 scripts/build_portfolio_history.py --trade-date "
        f"{target}",
        "downstream_impact": confidence,
        "summary_artifact": _summary_view(summary_payload),
        "checksum_verification": checksum_verification,
        "nav_gap_check": nav_gap_check,
        "reason_codes": sorted(reason_codes) or ["ok"],
    }
    if write:
        out_dir = model_quality_dir(repo, target, output_root)
        write_json(out_dir / "portfolio_history_freshness.json", payload)
        write_text(out_dir / "portfolio_history_freshness.md", render_markdown(payload))
    return payload


def _verify_checksum_manifest(*, repo: Path) -> dict[str, Any]:
    """Recompute artifact checksums and compare to the FR-066 checksum manifest."""
    manifest_path = repo / "outputs" / "portfolio_history" / "checksum_manifest.json"
    if not manifest_path.exists():
        return {"status": "MISSING", "mismatches": [], "reason_codes": ["CHECKSUM_MANIFEST_MISSING"]}
    payload = read_json(manifest_path)
    artifacts = (payload or {}).get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, dict):
        return {"status": "MALFORMED", "mismatches": [], "reason_codes": ["CHECKSUM_MANIFEST_MALFORMED"]}
    mismatches: list[dict[str, Any]] = []
    verified = 0
    for name, entry in sorted(artifacts.items()):
        if not isinstance(entry, dict) or not entry.get("sha256"):
            continue
        rel = entry.get("path") or ""
        path = repo / rel if not Path(str(rel)).is_absolute() else Path(str(rel))
        if not path.exists():
            mismatches.append({"artifact": name, "reason": "missing_on_disk", "path": str(rel)})
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry.get("sha256"):
            mismatches.append({"artifact": name, "reason": "sha256_mismatch", "path": str(rel)})
        else:
            verified += 1
    reason_codes = ["CHECKSUM_MISMATCH"] if mismatches else ["ok"]
    return {
        "status": "MISMATCH" if mismatches else "VERIFIED",
        "verified_count": verified,
        "mismatches": mismatches,
        "reason_codes": reason_codes,
    }


def _nav_gap_check(*, repo: Path) -> dict[str, Any]:
    """Scan canonical nav.csv for missing trading days (FR-066 §4 NAV_GAP)."""
    path = repo / "outputs" / "portfolio_history" / "nav.csv"
    if not path.exists() or path.stat().st_size <= 0:
        return {"status": "NO_DATA", "gaps": [], "reason_codes": ["ok"]}
    dates: list[str] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                for candidate in _date_candidates(row.get("date")):
                    dates.append(candidate)
                    break
    except Exception:
        return {"status": "READ_ERROR", "gaps": [], "reason_codes": ["NAV_GAP_CHECK_READ_ERROR"]}
    dates = sorted(set(dates))
    if len(dates) < 2 or is_trading_day is None or next_trading_day is None:
        return {"status": "OK", "gaps": [], "reason_codes": ["ok"]}
    present = set(dates)
    gaps: list[str] = []
    cursor = dates[0]
    for _ in range(5000):
        cursor = next_trading_day(cursor)
        if cursor >= dates[-1]:
            break
        if is_trading_day(cursor) and cursor not in present:
            gaps.append(cursor)
    return {
        "status": "GAP_DETECTED" if gaps else "OK",
        "gaps": gaps,
        "reason_codes": ["NAV_GAP"] if gaps else ["ok"],
    }


def _inspect_portfolio_files(*, repo: Path) -> dict[str, dict[str, Any]]:
    inspected: dict[str, dict[str, Any]] = {}
    for name, rel_path in sorted(PORTFOLIO_HISTORY_FILES.items()):
        path = repo / rel_path
        if name == "summary":
            inspected[name] = _inspect_json(path)
        else:
            inspected[name] = _inspect_csv(path)
    return inspected


def _inspect_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "payload": None, "reason_codes": ["SUMMARY_MISSING"]}
    payload = read_json(path)
    if payload is None:
        return {"path": str(path), "exists": True, "payload": None, "reason_codes": ["SUMMARY_PARSE_ERROR"]}
    return {"path": str(path), "exists": True, "payload": payload, "reason_codes": ["ok"]}


def _inspect_csv(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "row_count": 0,
            "latest_date": None,
            "min_date": None,
            "date_column": None,
            "reason_codes": [f"{path.stem.upper()}_MISSING"],
        }
    latest: str | None = None
    earliest: str | None = None
    row_count = 0
    date_column: str | None = None
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames or []
            date_column = _first_existing(fields, ("date", "as_of_date", "trade_date", "filled_at", "submitted_at", "timestamp"))
            if not fields:
                return {
                    "path": str(path),
                    "exists": True,
                    "row_count": 0,
                    "latest_date": None,
                    "min_date": None,
                    "date_column": None,
                    "reason_codes": [f"{path.stem.upper()}_MALFORMED"],
                }
            for row in reader:
                row_count += 1
                for candidate in _row_date_candidates(row=row, preferred=date_column):
                    if latest is None or candidate > latest:
                        latest = candidate
                    if earliest is None or candidate < earliest:
                        earliest = candidate
                    break
    except Exception:
        return {
            "path": str(path),
            "exists": True,
            "row_count": 0,
            "latest_date": None,
            "min_date": None,
            "date_column": date_column,
            "reason_codes": [f"{path.stem.upper()}_READ_ERROR"],
        }
    reasons: list[str] = []
    if row_count == 0:
        reasons.append(f"{path.stem.upper()}_EMPTY")
    if row_count > 0 and latest is None:
        reasons.append(f"{path.stem.upper()}_DATE_MISSING")
    return {
        "path": str(path),
        "exists": True,
        "row_count": row_count,
        "latest_date": latest,
        "min_date": earliest,
        "date_column": date_column,
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _first_existing(fields: Iterable[str], candidates: Iterable[str]) -> str | None:
    lower_to_original = {str(field).lower(): str(field) for field in fields}
    for candidate in candidates:
        if candidate in lower_to_original:
            return lower_to_original[candidate]
    return None


def _row_date_candidates(*, row: dict[str, Any], preferred: str | None) -> list[str]:
    columns = [preferred] if preferred else []
    columns.extend(["date", "as_of_date", "trade_date", "filled_at", "submitted_at", "timestamp"])
    out: list[str] = []
    for column in columns:
        if not column:
            continue
        value = row.get(column)
        out.extend(_date_candidates(value))
    return sorted(set(out))


def _date_candidates(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    text = str(value).strip()
    candidates = [text[:10]]
    if "T" in text:
        candidates.append(text.split("T", 1)[0])
    out = []
    for candidate in candidates:
        try:
            out.append(dt.date.fromisoformat(candidate).isoformat())
        except Exception:
            continue
    return sorted(set(out))


def _freshness_status(
    *,
    target_date: str,
    latest_date: str | None,
    inspected: dict[str, dict[str, Any]],
    broker_auth_status: str | None,
    reason_codes: set[str],
) -> str:
    auth = str(broker_auth_status or "").upper().strip()
    if auth in {"AUTH_FAILED", "UNAUTHORIZED", "UNAUTHORIZED_401"} and latest_date is None:
        return "AUTH_FAILED"
    if not any((info or {}).get("exists") for info in inspected.values()):
        return "MISSING"
    if any("MALFORMED" in code or "PARSE_ERROR" in code or "READ_ERROR" in code for code in reason_codes):
        return "UNKNOWN"
    if latest_date is None:
        return "MISSING"
    return "READY" if latest_date >= target_date else "STALE"


def _downstream_confidence(status: str) -> dict[str, str]:
    if status == "READY":
        value = "HIGH"
    elif status == "STALE":
        value = "LOW"
    elif status in {"MISSING", "AUTH_FAILED"}:
        value = "UNAVAILABLE"
    else:
        value = "UNKNOWN"
    return {
        "operational_drag_confidence": value,
        "promotion_readiness_confidence": value,
        "tournament_confidence": value,
        "attribution_confidence": value,
    }


def _source_paths(*, repo: Path, inspected: dict[str, dict[str, Any]], summary_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    paths: dict[str, str] = {}
    for name, info in inspected.items():
        paths[f"canonical_{name}"] = str(info.get("path"))
    for name, value in sorted(((summary_payload or {}).get("paths") or {}).items()):
        if isinstance(value, list):
            for idx, item in enumerate(value):
                paths[f"summary_{name}_{idx}"] = str(repo / item if not Path(str(item)).is_absolute() else item)
        elif value:
            paths[f"summary_{name}"] = str(repo / value if not Path(str(value)).is_absolute() else value)
    broker_root = repo / "outputs" / "broker_snapshot"
    if broker_root.exists():
        for path in sorted(broker_root.glob("broker_snapshot_*.json"))[-10:]:
            paths[f"broker_snapshot_{path.stem.removeprefix('broker_snapshot_')}"] = str(path)
    posttrade = repo / "outputs" / "broker" / "posttrade_positions.json"
    if posttrade.exists():
        paths["broker_posttrade_positions"] = str(posttrade)
    return [
        {"name": name, "path": path, "exists": Path(path).exists()}
        for name, path in sorted(paths.items())
    ]


def _date_coverage(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "min_date": info.get("min_date"),
        "max_date": info.get("latest_date"),
        "row_count": info.get("row_count"),
        "date_column": info.get("date_column"),
        "reason_codes": info.get("reason_codes") or ["ok"],
    }


def _summary_view(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"available": False}
    return {
        "available": True,
        "report_date": payload.get("report_date"),
        "as_of_date": payload.get("as_of_date"),
        "counts": payload.get("counts") or {},
        "warnings": payload.get("warnings") or [],
        "source_priority": payload.get("source_priority") or [],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Portfolio History Freshness - {payload.get('date')}",
        "",
        f"- Status: {payload.get('freshness_status')}",
        f"- Latest portfolio history date: {payload.get('latest_portfolio_history_date')}",
        f"- Target date: {payload.get('target_date')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        f"- Safe refresh command: `{payload.get('safe_refresh_command')}`",
        "",
        "## Downstream Impact",
        "",
    ]
    for name, value in sorted((payload.get("downstream_impact") or {}).items()):
        lines.append(f"- {name}: {value}")
    lines.extend(["", "## Row Counts", "", "| Artifact | Rows |", "|---|---:|"])
    for name, count in sorted((payload.get("row_counts") or {}).items()):
        lines.append(f"| {name} | {count} |")
    lines.extend(["", "## Source Paths", "", "| Source | Exists | Path |", "|---|:---:|---|"])
    for row in payload.get("source_paths_inspected") or []:
        lines.append(f"| {row.get('name')} | {row.get('exists')} | {row.get('path')} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit research-only portfolio history freshness.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_portfolio_history_freshness(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": payload["date"],
                "freshness_status": payload["freshness_status"],
                "latest_portfolio_history_date": payload["latest_portfolio_history_date"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
