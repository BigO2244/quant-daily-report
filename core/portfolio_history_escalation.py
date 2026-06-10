"""FR-066 §4 — canonical NAV freshness escalation (fail-loud).

Every trading day must have a canonical NAV row. A gap greater than one trading
day emits reason code ``NAV_GAP`` and sends an operator email through the same
email path as the Shadow CIO report. Two consecutive failed evaluations escalate
to the subject prefix ``[CAERUS NAV BROKEN]``. Silence is never a valid state —
this is the lesson of the 2026-05-20 -> 2026-06-04 Alpaca 401 freeze.

This module integrates with the FR-059 ``live_status`` reason codes
(``alpaca_auth_failed``, ``nav_artifact_stale``) rather than duplicating them.

Governance: OPERATIONAL_TELEMETRY / NON_EXECUTIONAL. No broker submission,
order routing, allocation, strategy, or promotion behavior is touched.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from paper.trading_calendar import is_trading_day, next_trading_day, prev_trading_day
except Exception:  # pragma: no cover - defensive
    is_trading_day = None  # type: ignore[assignment]
    next_trading_day = None  # type: ignore[assignment]
    prev_trading_day = None  # type: ignore[assignment]

SCHEMA_VERSION = "caerus_portfolio_history_escalation_v1"
NAV_CSV = Path("outputs/portfolio_history/nav.csv")
STATE_FILE = Path("outputs/portfolio_history/escalation_state.json")
BROKEN_SUBJECT_PREFIX = "[CAERUS NAV BROKEN]"
GAP_TRADING_DAYS_THRESHOLD = 1  # gap GREATER than one trading day -> NAV_GAP


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _latest_nav_date(repo_root: Path) -> str | None:
    path = repo_root / NAV_CSV
    if not path.exists() or path.stat().st_size <= 0:
        return None
    latest: str | None = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date = str(row.get("date") or "").strip()[:10]
            if len(date) == 10 and (latest is None or date > latest):
                latest = date
    return latest


def _expected_latest_trading_day(trade_date: str) -> str:
    """The trading day a post-close build for ``trade_date`` should have covered."""
    if is_trading_day is None or prev_trading_day is None:
        return trade_date
    return trade_date if is_trading_day(trade_date) else prev_trading_day(trade_date)


def _trading_days_between(start: str, end: str) -> int | None:
    """Count of trading sessions strictly after ``start`` up to and incl. ``end``."""
    if next_trading_day is None or start >= end:
        return 0 if start >= end else None
    count = 0
    cursor = start
    # Bounded walk; ~250 sessions/yr, cap protects against bad input.
    for _ in range(2000):
        cursor = next_trading_day(cursor)
        count += 1
        if cursor >= end:
            return count
    return None


def evaluate_nav_escalation(
    *,
    trade_date: str,
    repo_root: Path | str = ".",
    live_status_reason_codes: list[str] | None = None,
    persist_state: bool = True,
) -> dict[str, Any]:
    """Evaluate canonical NAV freshness and decide whether to escalate."""
    root = Path(repo_root)
    live_codes = [str(c).strip().lower() for c in (live_status_reason_codes or [])]
    latest = _latest_nav_date(root)
    expected = _expected_latest_trading_day(trade_date)

    reason_codes: list[str] = []
    status = "OK"

    # FR-059 reason-code integration (do not duplicate; reference).
    if "alpaca_auth_failed" in live_codes:
        reason_codes.append("BROKER_HISTORY_UNAVAILABLE")
        status = "BROKER_UNAVAILABLE"
    if "nav_artifact_stale" in live_codes:
        reason_codes.append("NAV_ARTIFACT_STALE")

    gap_days: int | None = None
    if latest is None:
        reason_codes.append("NAV_ARTIFACT_MISSING")
        status = "MISSING"
    else:
        gap_days = _trading_days_between(latest, expected)
        if gap_days is not None and gap_days > GAP_TRADING_DAYS_THRESHOLD:
            reason_codes.append("NAV_GAP")
            if status == "OK":
                status = "NAV_GAP"

    is_failure = status != "OK"

    # Consecutive-failure tracking (fail-loud escalation).
    state_path = root / STATE_FILE
    prior_state = _read_json(state_path) if isinstance(_read_json(state_path), dict) else {}
    prior_failures = int((prior_state or {}).get("consecutive_failures") or 0)
    consecutive_failures = prior_failures + 1 if is_failure else 0
    escalated = consecutive_failures >= 2

    subject = _build_subject(status=status, trade_date=trade_date, escalated=escalated)
    body = _build_body(
        status=status,
        trade_date=trade_date,
        latest=latest,
        expected=expected,
        gap_days=gap_days,
        reason_codes=reason_codes,
        consecutive_failures=consecutive_failures,
        live_codes=live_codes,
    )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "governance_label": "OPERATIONAL_TELEMETRY",
        "execution_impact": "NON_EXECUTIONAL",
        "trade_date": trade_date,
        "status": status,
        "should_email": is_failure,
        "escalated": escalated,
        "consecutive_failures": consecutive_failures,
        "latest_nav_date": latest,
        "expected_latest_trading_day": expected,
        "gap_trading_days": gap_days,
        "reason_codes": reason_codes or ["ok"],
        "subject": subject,
        "body": body,
    }

    if persist_state:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "consecutive_failures": consecutive_failures,
                    "last_trade_date": trade_date,
                    "last_status": status,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return payload


def _build_subject(*, status: str, trade_date: str, escalated: bool) -> str:
    if status == "OK":
        return f"Caerus NAV freshness OK — {trade_date}"
    base = f"Caerus NAV freshness {status} — {trade_date}"
    return f"{BROKEN_SUBJECT_PREFIX} {base}" if escalated else base


def _build_body(
    *,
    status: str,
    trade_date: str,
    latest: str | None,
    expected: str | None,
    gap_days: int | None,
    reason_codes: list[str],
    consecutive_failures: int,
    live_codes: list[str],
) -> str:
    lines = [
        f"Canonical NAV freshness evaluation for trade date {trade_date}.",
        "",
        f"Status: {status}",
        f"Latest canonical NAV row: {latest or 'NONE'}",
        f"Expected latest trading day: {expected}",
        f"Gap (trading days): {gap_days if gap_days is not None else 'unknown'}",
        f"Consecutive failed evaluations: {consecutive_failures}",
        f"Reason codes: {', '.join(reason_codes) or 'ok'}",
    ]
    if live_codes:
        lines.append(f"FR-059 live_status codes seen: {', '.join(live_codes)}")
    if status != "OK":
        lines += [
            "",
            "Action: a trading day is missing from the canonical broker NAV series.",
            "Inspect outputs/portfolio_history/nav.csv and the daily build log, then",
            "re-run: python3 scripts/build_portfolio_history.py --trade-date " + trade_date,
        ]
    return "\n".join(lines)


def send_nav_escalation(
    payload: dict[str, Any],
    *,
    send_fn: Callable[..., None] | None = None,
    force: bool = False,
) -> bool:
    """Send the escalation email when warranted. Returns True if an email was sent."""
    if not payload.get("should_email") and not force:
        return False
    sender = send_fn
    if sender is None:  # lazy import keeps this module test-friendly / import-light
        from core.quant_report import send_email as sender  # type: ignore
    sender(subject=payload["subject"], body_text=payload["body"])
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-066 canonical NAV freshness escalation (fail-loud).")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--live-status-code", action="append", default=[],
                        help="FR-059 live_status reason code (repeatable).")
    parser.add_argument("--send", action="store_true", help="Send the escalation email if warranted.")
    parser.add_argument("--no-state", action="store_true", help="Do not persist consecutive-failure state.")
    args = parser.parse_args(argv)

    payload = evaluate_nav_escalation(
        trade_date=args.trade_date,
        repo_root=args.repo_root,
        live_status_reason_codes=args.live_status_code,
        persist_state=not args.no_state,
    )
    if args.send:
        sent = send_nav_escalation(payload)
        payload["email_sent"] = sent
    print(json.dumps(
        {k: payload[k] for k in ("trade_date", "status", "should_email", "escalated",
                                 "consecutive_failures", "latest_nav_date", "reason_codes")},
        sort_keys=True,
    ))
    return 0 if payload["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
