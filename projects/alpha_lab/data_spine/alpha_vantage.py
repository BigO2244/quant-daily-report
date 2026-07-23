"""Bounded Alpha Vantage forward proxies for otherwise paid data contracts."""

from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable
from urllib.request import Request, urlopen

from .storage import write_bundle


Fetcher = Callable[[str], bytes]


def _fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Caerus Alpha Lab research-data-client"})
    with urlopen(request, timeout=120) as response:
        return response.read()


def _query_url(api_key: str, **params: str) -> str:
    values = dict(params)
    values["apikey"] = api_key
    return "https://www.alphavantage.co/query?" + urllib.parse.urlencode(values)


def _validate_json(payload: bytes, symbol: str) -> Dict[str, object]:
    parsed = json.loads(payload)
    for field in ("Error Message", "Information", "Note"):
        if parsed.get(field):
            raise RuntimeError("Alpha Vantage rejected {}: {}".format(symbol, field))
    return parsed


def collect_alpha_vantage_free_proxies(
    *,
    repo_root: Path,
    tickers: Iterable[str] = (),
    max_tickers: int = 20,
    listing_date: str | None = None,
    include_listing_status: bool = True,
    fetcher: Fetcher = _fetch,
    retrieved_at: datetime | None = None,
) -> Dict[str, object]:
    """Capture a daily-call-budget-safe current aggregate estimates proxy."""

    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY is required; registration is free")
    if max_tickers < 0 or max_tickers > 23:
        raise ValueError("max_tickers must be between 0 and 23")
    symbols = tuple(sorted({str(value).strip().upper() for value in tickers if str(value).strip()}))
    selected = symbols[:max_tickers]
    files: Dict[str, bytes] = {}
    if include_listing_status:
        for state in ("active", "delisted"):
            params = {"function": "LISTING_STATUS", "state": state}
            if listing_date:
                params["date"] = listing_date
            payload = fetcher(_query_url(api_key, **params))
            if not payload.strip():
                raise RuntimeError("Alpha Vantage returned empty listing status")
            if payload.lstrip().startswith(b"{"):
                _validate_json(payload, "listing_status_{}".format(state))
                raise RuntimeError("Alpha Vantage listing status returned no CSV data")
            files["listing_status_{}.csv".format(state)] = payload
    for symbol in selected:
        payload = fetcher(
            _query_url(api_key, function="EARNINGS_ESTIMATES", symbol=symbol)
        )
        parsed = _validate_json(payload, symbol)
        if not parsed.get("symbol") or not parsed.get("estimates"):
            raise RuntimeError("Alpha Vantage returned no estimates for {}".format(symbol))
        files["earnings_estimates/{}.json".format(symbol)] = payload
    if not files:
        raise ValueError("at least one Alpha Vantage proxy request is required")
    return write_bundle(
        repo_root=repo_root,
        source_id="alpha_vantage_free_proxy",
        files=files,
        metadata={
            "api_key_required": True,
            "demo_key_used": api_key == "demo",
            "credential_value_persisted": False,
            "daily_request_count": len(files),
            "daily_free_limit_assumed": 25,
            "requested_ticker_count": len(symbols),
            "captured_ticker_count": len(selected),
            "tickers": list(selected),
            "listing_date": listing_date,
            "current_aggregate_forward_proxy_only": True,
            "not_historical_point_in_time": True,
            "no_analyst_or_broker_identity": True,
            "license_terms_must_be_reviewed_before_non_personal_use": True,
        },
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
    )
