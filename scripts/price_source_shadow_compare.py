"""Shadow-compare Alpaca vs. yfinance prices without touching production paths.

Fetches the last ~30 completed sessions of adjusted daily closes and the most
recent completed session's opens from BOTH sources for the trading universe
(+SPY), then writes a divergence report to

    outputs/workflow/<REPORT_DATE>/price_source_shadow.json

Divergence is REPORTED, not fatal: the script exits 0 unless it itself errors.
Offline / missing-credential runs degrade gracefully to a status=SKIPPED
artifact (exit 0) so the cron precompute is never blocked or delayed.

Usage:
    python -m scripts.price_source_shadow_compare [--date YYYY-MM-DD]
        [--tickers AAPL,MSFT,...] [--max-symbols 120] [--output-root outputs]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

# Standard scripts/ entry-point boilerplate: when invoked by path
# (python3 scripts/price_source_shadow_compare.py), sys.path[0] is scripts/,
# not the repo root, so `import core...` fails. Match the other lane scripts.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger(__name__)

DEFAULT_MAX_SYMBOLS = 120
HISTORY_SESSIONS = 30
ARTIFACT_NAME = "price_source_shadow.json"


def _report_date(cli_value: Optional[str]) -> str:
    return cli_value or os.environ.get("REPORT_DATE") or date.today().isoformat()


def _universe_tickers(repo_root: Path, max_symbols: int) -> List[str]:
    import pandas as pd

    path = repo_root / "data" / "universe.csv"
    u = pd.read_csv(path)
    tickers = [str(t).strip().upper() for t in u["ticker"].dropna().tolist()]
    seen = set()
    ordered: List[str] = []
    for t in ["SPY"] + tickers:
        if t and t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered[:max_symbols]


def _closes_by_symbol(prices, sessions: int) -> Dict[str, Dict[str, float]]:
    """{ticker: {date_iso: close}} restricted to each ticker's last N sessions."""
    out: Dict[str, Dict[str, float]] = {}
    for ticker, grp in prices.groupby("ticker"):
        grp = grp.sort_values("date").tail(sessions)
        out[str(ticker)] = {
            str(d.date() if hasattr(d, "date") else d): float(c)
            for d, c in zip(grp["date"], grp["close"])
        }
    return out


def _fetch_both(tickers: List[str]):
    """Fetch history + opens from both sources. Raises on total failure."""
    from core.price_sources import alpaca_download_prices, alpaca_fetch_open_prices
    from core.quant_report import _download_prices_yfinance
    from paper.paper_broker import _fetch_open_prices_yfinance_impl

    # ~30 completed sessions => ~45 calendar days; ask both for the same window.
    yf_hist = _download_prices_yfinance(tickers, period="3mo", interval="1d")
    # fallback disabled so divergence/missing symbols are visible, not papered over
    alp_hist = alpaca_download_prices(tickers, period="3mo", interval="1d", fallback_to_yfinance=False)

    # Most recent completed session = last date both sources agree exists.
    yf_last = max(str(d.date()) for d in yf_hist["date"])
    alp_last = max(str(d.date()) for d in alp_hist["date"])
    open_session = min(yf_last, alp_last)

    yf_open = _fetch_open_prices_yfinance_impl(tickers, open_session)
    alp_open = alpaca_fetch_open_prices(tickers, open_session, fallback_to_yfinance=False)
    return yf_hist, alp_hist, yf_open, alp_open, open_session


def build_report(tickers: List[str], report_date: str) -> dict:
    yf_hist, alp_hist, yf_open, alp_open, open_session = _fetch_both(tickers)

    yf_closes = _closes_by_symbol(yf_hist, HISTORY_SESSIONS)
    alp_closes = _closes_by_symbol(alp_hist, HISTORY_SESSIONS)
    yf_opens = {str(r["ticker"]): float(r["open"]) for _, r in yf_open.iterrows()}
    alp_opens = {str(r["ticker"]): float(r["open"]) for _, r in alp_open.iterrows()}

    per_symbol: Dict[str, dict] = {}
    close_devs: List[float] = []
    open_devs: List[float] = []
    missing_from_alpaca = sorted(t for t in tickers if t not in alp_closes)
    missing_from_yfinance = sorted(t for t in tickers if t not in yf_closes)

    for t in tickers:
        y = yf_closes.get(t)
        a = alp_closes.get(t)
        entry: dict = {}
        if y and a:
            common = sorted(set(y) & set(a))[-HISTORY_SESSIONS:]
            devs = [
                abs(a[d] - y[d]) / abs(y[d]) * 100.0
                for d in common
                if y[d]
            ]
            entry["close_sessions_compared"] = len(devs)
            entry["close_max_abs_dev_pct"] = round(max(devs), 6) if devs else None
            if devs:
                close_devs.append(max(devs))
        yo, ao = yf_opens.get(t), alp_opens.get(t)
        if yo is not None and ao is not None and yo:
            open_dev = abs(ao - yo) / abs(yo) * 100.0
            entry["open_dev_pct"] = round(open_dev, 6)
            open_devs.append(open_dev)
        if entry:
            per_symbol[t] = entry

    def _summary(devs: List[float]) -> dict:
        if not devs:
            return {"n": 0, "max_pct": None, "mean_pct": None}
        return {
            "n": len(devs),
            "max_pct": round(max(devs), 6),
            "mean_pct": round(sum(devs) / len(devs), 6),
        }

    worst_close = sorted(
        (
            (t, e["close_max_abs_dev_pct"])
            for t, e in per_symbol.items()
            if e.get("close_max_abs_dev_pct") is not None
        ),
        key=lambda kv: kv[1],
        reverse=True,
    )[:10]

    return {
        "status": "OK",
        "report_date": report_date,
        "open_session": open_session,
        "n_symbols_requested": len(tickers),
        "history_sessions": HISTORY_SESSIONS,
        "per_symbol": per_symbol,
        "missing_from_alpaca": missing_from_alpaca,
        "missing_from_yfinance": missing_from_yfinance,
        "missing_open_from_alpaca": sorted(
            t for t in tickers if t in yf_opens and t not in alp_opens
        ),
        "missing_open_from_yfinance": sorted(
            t for t in tickers if t in alp_opens and t not in yf_opens
        ),
        "summary": {
            "close": _summary(close_devs),
            "open": _summary(open_devs),
            "worst_close_symbols": [{"ticker": t, "max_abs_dev_pct": v} for t, v in worst_close],
        },
    }


def _write_artifact(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _summary_line(payload: dict) -> str:
    if payload.get("status") != "OK":
        return (
            f"price_source_shadow status=SKIPPED reason={payload.get('reason', 'unknown')}"
        )
    s = payload["summary"]
    return (
        "price_source_shadow status=OK "
        f"symbols={payload['n_symbols_requested']} "
        f"close_max_dev_pct={s['close']['max_pct']} close_mean_dev_pct={s['close']['mean_pct']} "
        f"open_max_dev_pct={s['open']['max_pct']} "
        f"missing_alpaca={len(payload['missing_from_alpaca'])} "
        f"missing_yf={len(payload['missing_from_yfinance'])}"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="REPORT_DATE (default: env or today)")
    parser.add_argument("--tickers", default=None, help="comma-separated ticker override")
    parser.add_argument("--max-symbols", type=int, default=DEFAULT_MAX_SYMBOLS)
    parser.add_argument("--output-root", default="outputs", help="artifact root directory")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    repo_root = Path(__file__).resolve().parents[1]
    report_date = _report_date(args.date)
    artifact_path = Path(args.output_root) / "workflow" / report_date / ARTIFACT_NAME

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        tickers = tickers[: args.max_symbols]
    else:
        tickers = _universe_tickers(repo_root, args.max_symbols)

    has_creds = bool(
        os.environ.get("ALPACA_API_KEY_ID", "").strip()
        and os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    )
    if not has_creds:
        payload = {
            "status": "SKIPPED",
            "report_date": report_date,
            "reason": "missing ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY",
            "n_symbols_requested": len(tickers),
        }
    else:
        try:
            payload = build_report(tickers, report_date)
        except Exception as exc:  # noqa: BLE001 - shadow must degrade, never block
            payload = {
                "status": "SKIPPED",
                "report_date": report_date,
                "reason": f"{type(exc).__name__}: {exc}",
                "n_symbols_requested": len(tickers),
            }

    _write_artifact(artifact_path, payload)
    print(_summary_line(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
