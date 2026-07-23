"""Forward-only no-key analyst aggregate proxy capture via yfinance."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Callable, Dict, Iterable
from zoneinfo import ZoneInfo

from projects.alpha_lab.factory import canonical_json

from .storage import write_bundle


SymbolFetcher = Callable[[str], Dict[str, Any]]
_SECTIONS = ("earnings_estimate", "revenue_estimate", "eps_trend", "eps_revisions")


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        try:
            return _sanitize(value.item())
        except (TypeError, ValueError):
            pass
    return value


def _fetch_symbol(symbol: str) -> Dict[str, Any]:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    return {
        "symbol": symbol,
        "earnings_estimate": ticker.get_earnings_estimate(as_dict=True),
        "revenue_estimate": ticker.get_revenue_estimate(as_dict=True),
        "eps_trend": ticker.get_eps_trend(as_dict=True),
        "eps_revisions": ticker.get_eps_revisions(as_dict=True),
    }


def collect_yfinance_analyst_proxy(
    *,
    repo_root: Path,
    tickers: Iterable[str],
    max_tickers: int = 250,
    workers: int = 4,
    fetcher: SymbolFetcher = _fetch_symbol,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    """Capture current consensus levels/trends; never represent them as PIT history."""

    if max_tickers < 1 or max_tickers > 1000:
        raise ValueError("max_tickers must be between 1 and 1000")
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    symbols = tuple(sorted({str(value).strip().upper() for value in tickers if str(value).strip()}))[:max_tickers]
    if not symbols:
        raise ValueError("at least one ticker is required")

    def capture(symbol: str) -> tuple[str, Dict[str, Any] | None, str | None]:
        try:
            return symbol, _sanitize(fetcher(symbol)), None
        except Exception as exc:
            return symbol, None, type(exc).__name__

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(capture, symbols))
    timestamp = retrieved_at or datetime.now(timezone.utc)
    files: Dict[str, bytes] = {}
    inventory = []
    successful = 0
    usable = 0
    for symbol, payload, error_type in results:
        if payload is not None:
            successful += 1
            has_data = any(payload.get(section) for section in _SECTIONS)
            usable += int(has_data)
            files["symbols/{}.json".format(symbol)] = (canonical_json(payload) + "\n").encode("utf-8")
        else:
            has_data = False
        inventory.append(
            {
                "symbol": symbol,
                "status": (
                    "CAPTURED_USABLE"
                    if has_data
                    else ("CAPTURED_EMPTY" if payload is not None else "ERROR")
                ),
                "error_type": error_type,
            }
        )
    files["inventory.json"] = (canonical_json(inventory) + "\n").encode("utf-8")
    return write_bundle(
        repo_root=repo_root,
        source_id="yfinance_analyst_proxy",
        files=files,
        metadata={
            "as_of_date_et": timestamp.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
            "requested_ticker_count": len(symbols),
            "captured_ticker_count": successful,
            "usable_ticker_count": usable,
            "empty_ticker_count": successful - usable,
            "error_count": len(symbols) - successful,
            "workers": workers,
            "api_key_required": False,
            "current_aggregate_forward_proxy_only": True,
            "not_historical_point_in_time": True,
            "no_analyst_or_broker_identity": True,
            "raw_provider_response_not_preserved": True,
            "unofficial_provider_interface": True,
        },
        retrieved_at=timestamp,
    )
