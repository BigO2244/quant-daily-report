#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from paper.trading_calendar import is_trading_day, prev_trading_day  # noqa: E402

from core.portfolio_learning_report import (  # noqa: E402
    build_portfolio_learning_report,
    write_portfolio_learning_artifacts,
)

ET = ZoneInfo("America/New_York")
MARKET_EOD_READY_TIME = dt.time(hour=16, minute=15)


def _load_dotenv(repo_root: Path) -> None:
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the weekly portfolio learning brief.")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--output-dir", default="outputs/portfolio_learning")
    parser.add_argument("--shadow-dir", default="outputs/shadow_candidates")
    parser.add_argument("--dry-run", action="store_true", help="Print report and skip email.")
    parser.add_argument("--send-email", action="store_true", help="Send email after artifacts are written.")
    return parser.parse_args(argv)


def current_et(now: dt.datetime | None = None) -> dt.datetime:
    if now is None:
        return dt.datetime.now(ET)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ET)


def resolve_as_of_trade_date(*, explicit_trade_date: str | None = None, now: dt.datetime | None = None) -> str:
    if explicit_trade_date:
        return str(explicit_trade_date)
    now_et = current_et(now)
    today = now_et.date().isoformat()
    if is_trading_day(today) and now_et.time() >= MARKET_EOD_READY_TIME:
        return today
    return prev_trading_day(today)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = _REPO_ROOT
    shadow_dir = (repo_root / args.shadow_dir).resolve() if not Path(args.shadow_dir).is_absolute() else Path(args.shadow_dir)
    output_dir = (repo_root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    generated_at_et = current_et()
    as_of_trade_date = resolve_as_of_trade_date(explicit_trade_date=args.trade_date, now=generated_at_et)
    report = build_portfolio_learning_report(
        repo_root=repo_root,
        trade_date=as_of_trade_date,
        shadow_dir=shadow_dir,
        report_generated_date=generated_at_et.date().isoformat(),
        generated_at_et=generated_at_et,
    )
    json_path, md_path = write_portfolio_learning_artifacts(report=report, output_dir=output_dir)
    print(f"[PORTFOLIO_LEARNING][OK] wrote {json_path}")
    print(f"[PORTFOLIO_LEARNING][OK] wrote {md_path}")

    if args.dry_run or not args.send_email:
        print(f"Subject: {report.subject}\n")
        print(report.body_text)
        print("[PORTFOLIO_LEARNING][DRY_RUN] email not sent")
        return 0

    _load_dotenv(repo_root)
    try:
        from core.quant_report import send_email

        send_email(subject=report.subject, body_text=report.body_text, body_html=report.body_html)
    except Exception as exc:
        print(f"[PORTFOLIO_LEARNING][WARN] email failed after artifacts were written: {exc}", file=sys.stderr)
        return 0
    print(f"[PORTFOLIO_LEARNING][OK] email sent: {report.subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
