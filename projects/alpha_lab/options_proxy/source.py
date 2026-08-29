"""Read-only yfinance adapter for current option chains and later price bars."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional


class YFinanceUnavailable(RuntimeError):
    """The optional yfinance dependency is absent or unusable."""


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _optional_int(value: Any) -> Optional[int]:
    converted = _optional_float(value)
    if converted is None or converted < 0:
        return None
    return int(converted)


def _optional_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    text = str(value).strip()
    return text or None


def _records(frame: Any) -> Iterable[Mapping[str, Any]]:
    if frame is None:
        return ()
    to_dict = getattr(frame, "to_dict", None)
    if not callable(to_dict):
        raise YFinanceUnavailable("option chain frame does not support to_dict")
    rows = to_dict(orient="records")
    if not isinstance(rows, list):
        raise YFinanceUnavailable("option chain frame returned an invalid record collection")
    return rows


class YFinanceSource:
    """Fetch current public Yahoo Finance data without any trading dependency."""

    def __init__(self, yf_module: Any = None) -> None:
        if yf_module is None:
            try:
                import yfinance as yf_module  # type: ignore
            except ImportError as exc:
                raise YFinanceUnavailable(
                    "yfinance is not installed; use the repository's pinned research environment"
                ) from exc
        self._yf = yf_module

    @property
    def source_version(self) -> str:
        return str(getattr(self._yf, "__version__", "UNKNOWN"))

    def collect_chain(
        self,
        *,
        symbol: str,
        as_of_date: date,
        minimum_dte: int,
        maximum_dte: int,
    ) -> Dict[str, Any]:
        ticker = self._yf.Ticker(symbol)
        spot = self._spot(ticker)
        contracts: List[Dict[str, Any]] = []
        expirations_considered = []
        for expiration_text in tuple(getattr(ticker, "options", ()) or ()):
            try:
                expiration = date.fromisoformat(str(expiration_text))
            except ValueError:
                continue
            dte = (expiration - as_of_date).days
            if dte < minimum_dte or dte > maximum_dte:
                continue
            chain = ticker.option_chain(expiration_text)
            expirations_considered.append(expiration.isoformat())
            for option_type, frame in (
                ("call", getattr(chain, "calls", None)),
                ("put", getattr(chain, "puts", None)),
            ):
                for raw in _records(frame):
                    contracts.append(
                        {
                            "contract_symbol": str(raw.get("contractSymbol") or "").strip(),
                            "option_type": option_type,
                            "expiration": expiration.isoformat(),
                            "strike": _optional_float(raw.get("strike")),
                            "bid": _optional_float(raw.get("bid")),
                            "ask": _optional_float(raw.get("ask")),
                            "last_price": _optional_float(raw.get("lastPrice")),
                            "volume": _optional_int(raw.get("volume")),
                            "open_interest": _optional_int(raw.get("openInterest")),
                            "implied_volatility": _optional_float(
                                raw.get("impliedVolatility")
                            ),
                            "last_trade_at": _optional_timestamp(raw.get("lastTradeDate")),
                            "in_the_money": bool(raw.get("inTheMoney", False)),
                            "contract_size": str(raw.get("contractSize") or ""),
                            "currency": str(raw.get("currency") or ""),
                        }
                    )
        return {
            "symbol": symbol,
            "spot": spot,
            "expirations_considered": expirations_considered,
            "contracts": contracts,
        }

    def daily_bars(
        self,
        *,
        symbol: str,
        start: date,
        end: date,
    ) -> List[Dict[str, Any]]:
        ticker = self._yf.Ticker(symbol)
        frame = ticker.history(
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
        reset_index = getattr(frame, "reset_index", None)
        if not callable(reset_index):
            raise YFinanceUnavailable("daily price frame does not support reset_index")
        rows = reset_index().to_dict(orient="records")
        result = []
        for raw in rows:
            timestamp = raw.get("Date", raw.get("Datetime"))
            date_text = _optional_timestamp(timestamp)
            if date_text is None:
                continue
            result.append(
                {
                    "date": date_text[:10],
                    "open": _optional_float(raw.get("Open")),
                    "high": _optional_float(raw.get("High")),
                    "low": _optional_float(raw.get("Low")),
                    "close": _optional_float(raw.get("Close")),
                    "volume": _optional_int(raw.get("Volume")),
                }
            )
        return result

    @staticmethod
    def _spot(ticker: Any) -> Optional[float]:
        fast_info = getattr(ticker, "fast_info", None)
        if fast_info is not None:
            try:
                value = fast_info["last_price"]
            except (KeyError, TypeError):
                value = getattr(fast_info, "last_price", None)
            spot = _optional_float(value)
            if spot is not None and spot > 0:
                return spot
        frame = ticker.history(period="5d", interval="1d", auto_adjust=False)
        close = getattr(frame, "Close", None)
        if close is None:
            return None
        dropna = getattr(close, "dropna", None)
        values = dropna() if callable(dropna) else close
        try:
            return _optional_float(values.iloc[-1])
        except (AttributeError, IndexError, KeyError):
            return None


SOURCE_LIMITATIONS = (
    "current_chain_snapshot_not_historical_tape",
    "no_trade_aggressor_side",
    "no_prevailing_trade_time_nbbo",
    "no_exchange_or_condition_code",
    "no_quote_sizes",
    "no_occ_deliverable_lineage",
    "personal_use_terms_require_review",
)
