from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta
from typing import Any, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo

from core.live_pilot_preflight import validate_alpaca_submission_guardrails
from core.execution_authority_policy import (
    require_live_capital_disabled,
    require_options_capital_disabled,
)

logger = logging.getLogger(__name__)


BROKER_REJECT_PDT = "BROKER_REJECT_PDT"
BROKER_REJECT_BUYING_POWER = "BROKER_REJECT_BUYING_POWER"
BROKER_REJECT_SHORT_NOT_ALLOWED = "BROKER_REJECT_SHORT_NOT_ALLOWED"
BROKER_REJECT_ASSET_NOT_TRADABLE = "BROKER_REJECT_ASSET_NOT_TRADABLE"
BROKER_REJECT_UNKNOWN = "BROKER_REJECT_UNKNOWN"
EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT = "partial_execution_broker_abort"
EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE = "post_submit_artifact_failure"
CASH_REBALANCE_INCOMPLETE = "cash_rebalance_incomplete"


class _ExactExecutionCapability:
    __slots__ = ()


class _GenericLiveV4Capability:
    __slots__ = ("_signing_key",)

    def __init__(self) -> None:
        self._signing_key = os.urandom(32)

    def sign(self, content_hash: str) -> str:
        return hmac.new(
            self._signing_key, str(content_hash).encode("ascii"), hashlib.sha256
        ).hexdigest()

    def verify(self, content_hash: str, signature: str) -> bool:
        return hmac.compare_digest(self.sign(content_hash), str(signature))


# Process-local capability held only by the exact-v3 executor. Ambient
# environment variables and legacy callers cannot authorize broker mutation.
_EXACT_EXECUTION_CAPABILITY = _ExactExecutionCapability()
_GENERIC_LIVE_V4_CAPABILITY = _GenericLiveV4Capability()


def _require_exact_execution_capability(value: object) -> None:
    if value is not _EXACT_EXECUTION_CAPABILITY:
        raise PermissionError("exact_execution_capability_required")


def _require_generic_live_v4_capability(value: object) -> None:
    if value is not _GENERIC_LIVE_V4_CAPABILITY:
        raise PermissionError("generic_live_v4_capability_required")


def _is_truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_first(*names: str) -> Optional[str]:
    """Return the first non-empty environment variable value for the given names."""
    for n in names:
        v = os.getenv(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _canonical_alpaca_base(base_url: Optional[str], paper: bool) -> str:
    default_base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    base = str(base_url or default_base).strip().rstrip("/")
    if base.endswith("/v2"):
        base = base[:-3]
    if paper and "paper-api.alpaca.markets" not in base.lower():
        raise RuntimeError(
            f"ALPACA_PAPER=1 but ALPACA_BASE_URL resolves to non-paper host: {base!r}. "
            "Set ALPACA_BASE_URL=https://paper-api.alpaca.markets or unset it."
        )
    return base


def _as_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    for attr in ("model_dump", "dict", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                data = fn()
                if isinstance(data, dict):
                    return dict(data)
            except Exception:
                pass
    data = getattr(obj, "__dict__", None)
    if isinstance(data, dict):
        return dict(data)
    return {}


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def json_safe_primitive(value: Any) -> Any:
    """Normalize Alpaca SDK payloads into deterministic JSON-safe primitives."""
    import decimal
    import uuid
    from datetime import date, datetime
    from enum import Enum
    from pathlib import Path

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return json_safe_primitive(value.value if hasattr(value, "value") else str(value))
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            _safe_str(key): json_safe_primitive(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [json_safe_primitive(item) for item in value]
    if isinstance(value, set):
        normalized = [json_safe_primitive(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))

    obj_dict = _as_dict(value)
    if obj_dict:
        return json_safe_primitive(obj_dict)
    return _safe_str(value)


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def classify_alpaca_broker_reject(exc: Any) -> Dict[str, Any]:
    raw_message = _safe_str(exc).strip()
    parsed = _extract_json_object(raw_message)
    code: int | None = None
    if parsed.get("code") is not None:
        try:
            code = int(parsed.get("code"))
        except Exception:
            code = None
    if code is None:
        match = re.search(r'"code"\s*:\s*([0-9]+)', raw_message)
        if match:
            try:
                code = int(match.group(1))
            except Exception:
                code = None

    broker_message = _safe_str(parsed.get("message") or parsed.get("msg") or "").strip()
    if not broker_message:
        match = re.search(r'"message"\s*:\s*"([^"]+)"', raw_message)
        if match:
            broker_message = match.group(1).strip()
    if not broker_message:
        broker_message = raw_message or "unknown broker submission failure"

    normalized = broker_message.lower()
    if code == 40310000 or "pattern day trading" in normalized:
        classification = BROKER_REJECT_PDT
    elif "buying power" in normalized:
        classification = BROKER_REJECT_BUYING_POWER
    elif (
        code == 40410000
        or "asset not found" in normalized
        or "not tradable" in normalized
        or "asset is not tradable" in normalized
    ):
        classification = BROKER_REJECT_ASSET_NOT_TRADABLE
    elif "short" in normalized and (
        "not allowed" in normalized
        or "unable to open" in normalized
        or "trade denied" in normalized
        or "cannot" in normalized
    ):
        classification = BROKER_REJECT_SHORT_NOT_ALLOWED
    else:
        classification = BROKER_REJECT_UNKNOWN

    return {
        "classification": classification,
        "code": code,
        "message": broker_message,
        "raw_message": raw_message or broker_message,
    }


def broker_reject_policy_outcome(
    classification: str,
    *,
    successful_submissions: int = 0,
) -> Dict[str, Any]:
    classification_norm = str(classification or BROKER_REJECT_UNKNOWN).strip() or BROKER_REJECT_UNKNOWN
    reason_code = classification_norm.lower()
    partial_execution = int(successful_submissions or 0) > 0

    halt_reason_parts = [
        EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT if partial_execution else "broker_reject_abort",
        reason_code,
    ]
    if partial_execution:
        halt_reason_parts.append(CASH_REBALANCE_INCOMPLETE)

    return {
        "execution_outcome": (
            EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT if partial_execution else "broker_reject_abort"
        ),
        "execution_reason": reason_code,
        "cash_rebalance_status": CASH_REBALANCE_INCOMPLETE if partial_execution else None,
        "halt_reason": ":".join(halt_reason_parts),
        "halt_remaining_orders": True,
        "halt_remaining_buys": True,
        "partial_execution": partial_execution,
    }


class AlpacaSubmissionRejectError(RuntimeError):
    def __init__(
        self,
        *,
        classification: str,
        broker_message: str,
        raw_message: str,
        broker_code: int | None = None,
        order_id: str | None = None,
        symbol: str | None = None,
        side: str | None = None,
        quantity: float | None = None,
        attempted_submissions: int | None = None,
        successful_submissions: int | None = None,
        failed_submissions: int | None = None,
        submitted_orders: list[dict[str, Any]] | None = None,
    ) -> None:
        self.classification = str(classification or BROKER_REJECT_UNKNOWN)
        self.broker_message = str(broker_message or raw_message or "unknown broker submission failure")
        self.raw_message = str(raw_message or self.broker_message)
        self.broker_code = broker_code
        self.order_id = str(order_id or "")
        self.symbol = str(symbol or "")
        self.side = str(side or "")
        self.quantity = float(quantity) if quantity is not None else None
        self.attempted_submissions = int(attempted_submissions or 0)
        self.successful_submissions = int(successful_submissions or 0)
        self.failed_submissions = int(failed_submissions or 0)
        self.submitted_orders = [dict(item) for item in (submitted_orders or [])]
        super().__init__(f"{self.classification}: {self.broker_message}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "classification": self.classification,
            "message": self.broker_message,
            "raw_message": self.raw_message,
            "code": self.broker_code,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "attempted_submissions": self.attempted_submissions,
            "successful_submissions": self.successful_submissions,
            "failed_submissions": self.failed_submissions,
            "submitted_orders": [dict(item) for item in self.submitted_orders],
        }


def alpaca_client_order_id(order_id: str) -> str:
    raw = _safe_str(order_id).strip()
    if raw and len(raw) <= 48 and all(32 <= ord(ch) <= 126 for ch in raw):
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    # <= 48 chars total, ASCII safe.
    return f"qd:{digest[:45]}"


def _normalize_order_obj(order: Any) -> Dict[str, Any]:
    d = _as_dict(order)
    return {
        "id": _safe_str(d.get("id") or getattr(order, "id", "")),
        "client_order_id": _safe_str(
            d.get("client_order_id") or getattr(order, "client_order_id", "")
        ),
        "symbol": _safe_str(d.get("symbol") or getattr(order, "symbol", "")),
        "side": _safe_str(d.get("side") or getattr(order, "side", "")),
        "status": _safe_str(d.get("status") or getattr(order, "status", "")),
        "submitted_at": _safe_str(
            d.get("submitted_at") or getattr(order, "submitted_at", "")
        ),
        "filled_qty": _safe_str(
            d.get("filled_qty")
            or d.get("filled_quantity")
            or getattr(order, "filled_qty", "")
            or getattr(order, "filled_quantity", "")
        ),
        "filled_at": _safe_str(d.get("filled_at") or getattr(order, "filled_at", "")),
        "filled_avg_price": _safe_str(
            d.get("filled_avg_price")
            or d.get("avg_fill_price")
            or getattr(order, "filled_avg_price", "")
        ),
        "qty": _safe_str(d.get("qty") or getattr(order, "qty", "")),
        "raw": d,
    }


def _normalize_asset_obj(asset: Any) -> Dict[str, Any]:
    d = _as_dict(asset)
    return {
        "id": _safe_str(d.get("id") or getattr(asset, "id", "")),
        "symbol": _safe_str(d.get("symbol") or getattr(asset, "symbol", "")).upper(),
        "name": _safe_str(d.get("name") or getattr(asset, "name", "")),
        "status": _safe_str(d.get("status") or getattr(asset, "status", "")),
        "tradable": _safe_bool(d.get("tradable") if "tradable" in d else getattr(asset, "tradable", False)),
        "asset_class": _safe_str(d.get("asset_class") or getattr(asset, "asset_class", "")),
        "exchange": _safe_str(d.get("exchange") or getattr(asset, "exchange", "")),
        "raw": d,
    }


def _normalize_position_obj(position: Any) -> Dict[str, Any]:
    d = _as_dict(position)
    return {
        "symbol": _safe_str(d.get("symbol") or getattr(position, "symbol", "")),
        "qty": _safe_str(d.get("qty") or getattr(position, "qty", "")),
        "market_value": _safe_str(
            d.get("market_value") or getattr(position, "market_value", "")
        ),
        "current_price": _safe_str(
            d.get("current_price") or getattr(position, "current_price", "")
        ),
        "side": _safe_str(d.get("side") or getattr(position, "side", "")),
        "raw": d,
    }


@dataclass(frozen=True)
class AlpacaEnv:
    key_id: str
    secret_key: str
    base_url: str
    paper: bool


def load_alpaca_env() -> AlpacaEnv:
    """
    Read Alpaca credentials/config from env.

    Preferred:
      - ALPACA_API_KEY_ID
      - ALPACA_API_SECRET_KEY
      - ALPACA_PAPER (true/false)
      - (optional) ALPACA_BASE_URL

    Legacy supported:
      - ALPACA_KEY_ID
      - ALPACA_SECRET_KEY
      - (optional) ALPACA_BASE_URL
    """
    key_id = _env_first("ALPACA_API_KEY_ID", "ALPACA_KEY_ID")
    secret_key = _env_first("ALPACA_API_SECRET_KEY", "ALPACA_SECRET_KEY")
    paper = _env_bool("ALPACA_PAPER", default=True)

    base_url = _canonical_alpaca_base(_env_first("ALPACA_BASE_URL"), paper=paper)

    if not key_id or not secret_key:
        # Log which credentials are missing (without revealing values)
        key_status = "SET" if key_id else "MISSING"
        secret_status = "SET" if secret_key else "MISSING"
        logger.error(
            "[ALPACA_LOAD_ENV] Credential status: ALPACA_API_KEY_ID=%s, ALPACA_API_SECRET_KEY=%s",
            key_status,
            secret_status,
        )
        raise RuntimeError(
            "Missing Alpaca credentials. "
            "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY as GitHub Secrets and pass via workflow env. "
            f"Current status: key_id={key_status}, secret_key={secret_status}"
        )

    return AlpacaEnv(
        key_id=key_id,
        secret_key=secret_key,
        base_url=base_url,
        paper=paper,
    )


@dataclass
class AlpacaBroker:
    trading_client: Any
    paper: bool = True
    base_url: str = ""

    @classmethod
    def from_env(cls) -> "AlpacaBroker":
        cfg = load_alpaca_env()
        logger.info(
            "[ALPACA] base=%s key_set=%s secret_set=%s paper=%s",
            cfg.base_url,
            bool(cfg.key_id),
            bool(cfg.secret_key),
            bool(cfg.paper),
        )
        try:
            from alpaca.trading.client import TradingClient
        except Exception as exc:
            raise RuntimeError(
                "alpaca-py is required for paper mode broker access. Install with `pip install alpaca-py`."
            ) from exc
        try:
            client = TradingClient(
                cfg.key_id,
                cfg.secret_key,
                paper=cfg.paper,
                url_override=cfg.base_url,
            )
        except TypeError:
            client = TradingClient(cfg.key_id, cfg.secret_key, paper=cfg.paper)
        return cls(trading_client=client, paper=cfg.paper, base_url=cfg.base_url)

    def get_account(self) -> Dict[str, Any]:
        account_endpoint = f"{str(self.base_url).rstrip('/')}/v2/account"
        try:
            account = self.trading_client.get_account()
        except Exception as exc:
            msg = _safe_str(exc)
            if "404" in msg.lower() or "not found" in msg.lower():
                raise RuntimeError(
                    f"Alpaca get_account 404 endpoint={account_endpoint}: {msg}"
                ) from exc
            raise RuntimeError(
                f"Alpaca get_account failed endpoint={account_endpoint}: {msg}"
            ) from exc
        d = _as_dict(account)
        out = {
            "id": _safe_str(d.get("id") or getattr(account, "id", "")),
            "status": _safe_str(d.get("status") or getattr(account, "status", "")),
            "cash": _safe_str(d.get("cash") or getattr(account, "cash", "")),
            "equity": _safe_str(d.get("equity") or getattr(account, "equity", "")),
            "buying_power": _safe_str(
                d.get("buying_power") or getattr(account, "buying_power", "")
            ),
            "pending_transfer_in": _safe_str(
                d.get("pending_transfer_in") or getattr(account, "pending_transfer_in", "0")
            ),
            "pending_transfer_out": _safe_str(
                d.get("pending_transfer_out") or getattr(account, "pending_transfer_out", "0")
            ),
            "long_market_value": _safe_str(
                d.get("long_market_value") or getattr(account, "long_market_value", "0")
            ),
            "short_market_value": _safe_str(
                d.get("short_market_value") or getattr(account, "short_market_value", "0")
            ),
            "portfolio_value": _safe_str(
                d.get("portfolio_value") or getattr(account, "portfolio_value", "")
            ),
            "trading_blocked": _safe_bool(
                d.get("trading_blocked") or getattr(account, "trading_blocked", False)
            ),
            "account_blocked": _safe_bool(
                d.get("account_blocked") or getattr(account, "account_blocked", False)
            ),
            "raw": d,
        }
        out["id_hash"] = hashlib.sha256(out["id"].encode("utf-8")).hexdigest()
        logger.info(
            "[ALPACA] account id_hash_prefix=%s status=%s equity=%s cash=%s",
            str(out.get("id_hash", ""))[:12],
            out.get("status", ""),
            out.get("equity", ""),
            out.get("cash", ""),
        )
        return out

    def get_market_session_calendar(self, trade_date: str) -> Dict[str, Any]:
        """Return Alpaca's authoritative open/close for one market date."""

        try:
            requested_date = datetime.fromisoformat(str(trade_date)).date()
        except ValueError as exc:
            raise RuntimeError("Alpaca calendar trade date is invalid") from exc
        try:
            from alpaca.trading.requests import GetCalendarRequest

            rows = self.trading_client.get_calendar(
                GetCalendarRequest(start=requested_date, end=requested_date)
            )
        except Exception as exc:
            raise RuntimeError(
                f"Alpaca market-calendar read failed: {_safe_str(exc)}"
            ) from exc
        if not isinstance(rows, (list, tuple)) or len(rows) != 1:
            raise RuntimeError(
                "Alpaca market calendar did not return exactly one session"
            )
        row = rows[0]
        raw = _as_dict(row)
        returned_date = raw.get("date") or getattr(row, "date", None)
        if str(returned_date) != requested_date.isoformat():
            raise RuntimeError("Alpaca market-calendar date does not match request")
        open_at = raw.get("open") or getattr(row, "open", None)
        close_at = raw.get("close") or getattr(row, "close", None)
        et = ZoneInfo("America/New_York")

        def normalize_bound(value: Any) -> datetime:
            # alpaca-py 0.43.2 currently returns naive datetimes, but accepting
            # its documented date+time representation as well keeps the
            # adapter robust across SDK serialization variants.
            if isinstance(value, datetime):
                combined = value
            elif isinstance(value, datetime_time):
                combined = datetime.combine(requested_date, value)
            else:
                raise RuntimeError(
                    "Alpaca market-calendar session bounds are malformed"
                )
            return (
                combined.replace(tzinfo=et)
                if combined.tzinfo is None
                else combined.astimezone(et)
            )

        open_et = normalize_bound(open_at)
        close_et = normalize_bound(close_at)
        if (
            open_et.date() != requested_date
            or close_et.date() != requested_date
            or close_et <= open_et
        ):
            raise RuntimeError("Alpaca market-calendar session bounds are invalid")
        return {
            "calendar": "Alpaca",
            "trade_date": requested_date.isoformat(),
            "session_open_et": open_et.isoformat(),
            "session_close_et": close_et.isoformat(),
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        positions = self.trading_client.get_all_positions()
        return [_normalize_position_obj(p) for p in positions]

    def get_latest_trades(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Return timestamped IEX trades for final PAPER authorization sizing."""

        normalized = sorted(
            {str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()}
        )
        if not normalized:
            return {}
        cfg = load_alpaca_env()
        try:
            from alpaca.data.enums import DataFeed
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestTradeRequest

            client = StockHistoricalDataClient(
                api_key=cfg.key_id,
                secret_key=cfg.secret_key,
            )
            response = client.get_stock_latest_trade(
                StockLatestTradeRequest(
                    symbol_or_symbols=normalized,
                    feed=DataFeed.IEX,
                )
            )
        except Exception as exc:
            raise RuntimeError(f"Alpaca latest-trade read failed: {_safe_str(exc)}") from exc
        payload = getattr(response, "data", response)
        if not isinstance(payload, dict):
            try:
                payload = dict(payload)
            except Exception as exc:
                raise RuntimeError("Alpaca latest-trade response is malformed") from exc
        result: Dict[str, Dict[str, Any]] = {}
        for symbol in normalized:
            trade = payload.get(symbol)
            raw = _as_dict(trade) if trade is not None else {}
            provider_symbol = _safe_str(
                raw.get("symbol")
                or raw.get("S")
                or getattr(trade, "symbol", None)
            ).strip().upper()
            price = raw.get("price") or raw.get("p") or getattr(trade, "price", None)
            timestamp = (
                raw.get("timestamp")
                or raw.get("t")
                or getattr(trade, "timestamp", None)
            )
            result[symbol] = {
                "symbol": provider_symbol,
                "price": _safe_str(price),
                "timestamp": _safe_str(timestamp),
                "feed": "IEX",
            }
        return result

    def get_session_final_bars(
        self,
        symbols: List[str],
        *,
        session_open_et: datetime,
        session_close_et: datetime,
    ) -> Dict[str, Dict[str, Any]]:
        """Return each symbol's final regular-session one-minute IEX bar.

        Alpaca applies its bar-eligibility rules when forming the close, so this
        avoids treating a raw odd-lot or contingent print as the Decision mark.
        The exact final minute is queried in one batch with no global row limit;
        the authorizer independently validates every returned interval.
        """

        normalized = sorted(
            {
                str(symbol or "").strip().upper()
                for symbol in symbols
                if str(symbol or "").strip()
            }
        )
        if not normalized:
            return {}
        if session_open_et.tzinfo is None or session_close_et.tzinfo is None:
            raise RuntimeError("session-final bar bounds must be timezone-aware")
        if session_close_et <= session_open_et:
            raise RuntimeError("session-final bar bounds are invalid")
        cfg = load_alpaca_env()
        try:
            from alpaca.common.enums import Sort, SupportedCurrencies
            from alpaca.data.enums import Adjustment, DataFeed
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame

            client = StockHistoricalDataClient(
                api_key=cfg.key_id,
                secret_key=cfg.secret_key,
            )
            query_start = session_close_et - timedelta(minutes=1)
            query_end = session_close_et - timedelta(microseconds=1)
            if query_start < session_open_et:
                raise RuntimeError("session-final bar interval precedes session open")
            response = client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=normalized,
                    timeframe=TimeFrame.Minute,
                    start=query_start,
                    end=query_end,
                    sort=Sort.DESC,
                    feed=DataFeed.IEX,
                    adjustment=Adjustment.RAW,
                    asof=session_close_et.date().isoformat(),
                    currency=SupportedCurrencies.USD,
                )
            )
            payload = getattr(response, "data", response)
            if not isinstance(payload, dict):
                try:
                    payload = dict(payload)
                except Exception as exc:
                    raise RuntimeError(
                        "Alpaca session-final-bar response is malformed"
                    ) from exc
            result: Dict[str, Dict[str, Any]] = {}
            for symbol in normalized:
                rows: Any = payload.get(symbol, [])
                if rows is None:
                    rows = []
                if not isinstance(rows, (list, tuple)):
                    try:
                        rows = list(rows)
                    except Exception:
                        rows = [rows]
                matching: list[tuple[datetime, Any, dict[str, Any]]] = []
                for trade in rows:
                    raw = _as_dict(trade)
                    provider_symbol = _safe_str(
                        raw.get("symbol")
                        or raw.get("S")
                        or getattr(trade, "symbol", None)
                    ).strip().upper()
                    if provider_symbol != symbol:
                        continue
                    close = raw.get("close")
                    if close is None:
                        close = raw.get("c")
                    if close is None:
                        close = getattr(trade, "close", None)
                    timestamp = (
                        raw.get("timestamp")
                        or raw.get("t")
                        or getattr(trade, "timestamp", None)
                    )
                    try:
                        parsed = datetime.fromisoformat(
                            _safe_str(timestamp).replace("Z", "+00:00")
                        )
                    except ValueError:
                        continue
                    if parsed.tzinfo is None:
                        continue
                    parsed_et = parsed.astimezone(session_close_et.tzinfo)
                    if parsed_et != query_start:
                        continue
                    matching.append((parsed_et, close, raw))
                if len(matching) != 1:
                    continue
                bar_start, close, raw = matching[0]
                result[symbol] = {
                    "symbol": symbol,
                    "price": _safe_str(close),
                    "close": _safe_str(close),
                    "bar_start": bar_start.isoformat(),
                    "bar_end_exclusive": session_close_et.isoformat(),
                    "open": _safe_str(raw.get("open") or raw.get("o")),
                    "high": _safe_str(raw.get("high") or raw.get("h")),
                    "low": _safe_str(raw.get("low") or raw.get("l")),
                    "volume": _safe_str(raw.get("volume") or raw.get("v")),
                    "trade_count": _safe_str(
                        raw.get("trade_count") or raw.get("n")
                    ),
                    "vwap": _safe_str(raw.get("vwap") or raw.get("vw")),
                    "timeframe": "1Min",
                    "feed": "IEX",
                    "adjustment": "raw",
                    "currency": "USD",
                }
        except Exception as exc:
            raise RuntimeError(
                f"Alpaca session-final-bar read failed: {_safe_str(exc)}"
            ) from exc
        return result

    def get_asset(self, symbol: str) -> Optional[Dict[str, Any]]:
        symbol_norm = str(symbol or "").upper().strip()
        if not symbol_norm:
            return None
        getter = getattr(self.trading_client, "get_asset", None)
        if not callable(getter):
            raise AttributeError("Alpaca trading client does not support asset lookup")
        try:
            asset = getter(symbol_norm)
        except Exception as exc:
            msg = _safe_str(exc).lower()
            if "not found" in msg or "404" in msg:
                return None
            raise
        if asset is None:
            return None
        return _normalize_asset_obj(asset)

    def list_assets(
        self,
        *,
        status: str | None = "active",
        asset_class: str | None = "us_equity",
    ) -> List[Dict[str, Any]]:
        getter = getattr(self.trading_client, "get_all_assets", None)
        if not callable(getter):
            raise AttributeError("Alpaca trading client does not support asset listing")
        try:
            from alpaca.trading.enums import AssetClass, AssetStatus
            from alpaca.trading.requests import GetAssetsRequest

            status_norm = str(status or "").strip().lower()
            class_norm = str(asset_class or "").strip().lower()
            req_kwargs: Dict[str, Any] = {}
            if status_norm:
                req_kwargs["status"] = (
                    AssetStatus.ACTIVE if status_norm == "active" else status_norm
                )
            if class_norm:
                req_kwargs["asset_class"] = (
                    AssetClass.US_EQUITY if class_norm in {"us_equity", "equity"} else class_norm
                )
            assets = getter(GetAssetsRequest(**req_kwargs))
        except TypeError:
            assets = getter()
        except Exception:
            raise
        records = [_normalize_asset_obj(asset) for asset in assets or []]
        if status:
            status_norm = str(status).strip().lower().replace("assetstatus.", "")
            records = [
                record
                for record in records
                if str(record.get("status") or "").strip().lower().replace("assetstatus.", "") == status_norm
            ]
        if asset_class:
            class_norm = str(asset_class).strip().lower().replace("assetclass.", "")
            records = [
                record
                for record in records
                if str(record.get("asset_class") or "").strip().lower().replace("assetclass.", "")
                in {class_norm, class_norm.replace("_", "")}
            ]
        return sorted(records, key=lambda row: str(row.get("symbol") or ""))

    def find_order_by_client_id(self, client_id: str) -> Optional[Dict[str, Any]]:
        try:
            order = self.trading_client.get_order_by_client_id(client_id)
        except Exception as exc:
            msg = _safe_str(exc).lower()
            if "not found" in msg or "404" in msg:
                return None
            raise
        if order is None:
            return None
        return _normalize_order_obj(order)

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        order_id_norm = str(order_id).strip()
        if not order_id_norm:
            return None
        getter = getattr(self.trading_client, "get_order_by_id", None)
        if not callable(getter):
            getter = getattr(self.trading_client, "get_order", None)
        if not callable(getter):
            raise AttributeError("Alpaca trading client does not support single-order lookup")
        try:
            order = getter(order_id_norm)
        except TypeError:
            import uuid

            try:
                order = getter(uuid.UUID(order_id_norm))
            except Exception as exc:
                msg = _safe_str(exc).lower()
                if "not found" in msg or "404" in msg:
                    return None
                raise
        except Exception as exc:
            msg = _safe_str(exc).lower()
            if "not found" in msg or "404" in msg:
                return None
            raise
        if order is None:
            return None
        return _normalize_order_obj(order)

    def submit_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        client_order_id: str,
        tif: str = "day",
        estimated_notional: float | None = None,
        _execution_capability: object | None = None,
    ) -> Dict[str, Any]:
        qty_float = float(qty)
        if not math.isfinite(qty_float) or qty_float <= 0.0:
            raise RuntimeError("Refusing market order with non-finite or non-positive quantity.")
        if estimated_notional is not None:
            estimated_notional_float = float(estimated_notional)
            if not math.isfinite(estimated_notional_float) or estimated_notional_float <= 0.0:
                raise RuntimeError("Refusing market order with non-finite or non-positive estimated notional.")
        if not bool(self.paper):
            require_live_capital_disabled(
                mutation_path="brokers.alpaca_broker.submit_market_order"
            )
        _require_exact_execution_capability(_execution_capability)
        validate_alpaca_submission_guardrails(
            broker_paper=bool(self.paper),
            base_url=self.base_url,
            order_notional=estimated_notional,
        )
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        base_url_norm = str(self.base_url or "").strip().lower().rstrip("/")
        if bool(self.paper) and "paper-api.alpaca.markets" not in base_url_norm:
            raise RuntimeError(
                "Refusing equity market order outside Alpaca paper environment. "
                f"base_url={self.base_url!r}"
            )
        side_norm = str(side).upper()
        client_id = str(client_order_id)
        symbol_norm = str(symbol).upper()
        logger.info(
            "[ALPACA_SUBMIT] attempt order_type=market symbol=%s side=%s qty=%.6f client_order_id=%s",
            symbol_norm,
            side_norm,
            qty_float,
            client_id,
        )
        req = MarketOrderRequest(
            symbol=symbol_norm,
            qty=qty_float,
            side=OrderSide.BUY if side_norm == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce(str(tif).lower()),
            client_order_id=client_id,
        )
        try:
            out = _normalize_order_obj(self.trading_client.submit_order(order_data=req))
        except Exception as exc:
            logger.exception(
                "[ALPACA_SUBMIT] ERROR order_type=market symbol=%s side=%s qty=%.6f client_order_id=%s error=%s",
                symbol_norm,
                side_norm,
                qty_float,
                client_id,
                _safe_str(exc),
            )
            raise
        logger.info(
            "[ALPACA_SUBMIT] OK order_type=market symbol=%s side=%s qty=%.6f client_order_id=%s alpaca_order_id=%s status=%s",
            symbol_norm,
            side_norm,
            qty_float,
            client_id,
            _safe_str(out.get("id")),
            _safe_str(out.get("status")),
        )
        return out

    def submit_generic_live_v4_limit_order(
        self,
        *,
        symbol: str,
        qty: float,
        side: str,
        client_order_id: str,
        limit_price: float,
        max_fee_usd: float,
        mutation_context: Mapping[str, Any],
        tif: str = "day",
        _generic_live_v4_capability: object | None = None,
    ) -> Dict[str, Any]:
        """Sole generic-v4 Live mutation boundary for the approved $460 lane.

        The legacy Live executor cannot reach this method because it does not
        possess the process-local capability.  The older submission methods
        retain their unconditional Live-capital stop.
        """

        _require_generic_live_v4_capability(_generic_live_v4_capability)
        if bool(self.paper):
            raise RuntimeError("generic Live v4 submission requires the Live broker")
        base_url_norm = str(self.base_url or "").strip().lower().rstrip("/")
        if base_url_norm != "https://api.alpaca.markets":
            raise RuntimeError("generic Live v4 submission requires the canonical Alpaca Live endpoint")
        qty_float = float(qty)
        limit_price_float = float(limit_price)
        fee = float(max_fee_usd)
        notional = qty_float * limit_price_float + fee
        if not math.isfinite(qty_float) or qty_float <= 0.0 or abs(qty_float - round(qty_float)) > 1e-9:
            raise RuntimeError("generic Live v4 requires a positive whole-share quantity")
        if not math.isfinite(limit_price_float) or limit_price_float <= 0.0 or not math.isfinite(fee) or fee < 0.0:
            raise RuntimeError("generic Live v4 limit price/fee is invalid")
        if not math.isfinite(notional) or notional < 100.0 or notional > 437.0:
            raise RuntimeError("generic Live v4 notional must remain within $100-$437")
        side_norm = str(side or "").strip().upper()
        if side_norm not in {"BUY", "SELL"}:
            raise RuntimeError("generic Live v4 side must be BUY or SELL")
        client_id = str(client_order_id or "").strip()
        if not re.fullmatch(r"cx4-[0-9a-f]{39}", client_id):
            raise RuntimeError("generic Live v4 client order id is invalid")
        if str(tif).strip().lower() != "day":
            raise RuntimeError("generic Live v4 requires DAY time in force")
        context_fields = {
            "schema_version", "action", "effective_session", "owner_decision_hash",
            "preflight_hash", "plan_hash", "execution_policy_hash",
            "account_id_hash", "deployed_sha", "order_id", "client_order_id",
            "symbol", "side", "quantity", "order_type", "time_in_force",
            "extended_hours", "allow_fractional_shares", "quantity_precision",
            "limit_price", "max_fee_usd", "maximum_gross_usd",
            "capital_proof_hash", "fresh_equity_usd", "fresh_cash_usd",
            "effective_capital_usd", "dynamic_gross_cap_usd",
            "required_cash_reserve_usd", "worst_case_posttrade_gross_usd",
            "worst_case_posttrade_cash_usd", "capital_gross_limit_pass",
            "capital_cash_reserve_pass", "starting_symbol_quantity",
            "starting_other_gross_usd", "gross_valuation_price",
            "expected_posttrade_symbol_quantity",
            "content_hash", "capability_signature",
        }
        if not isinstance(mutation_context, Mapping) or set(mutation_context) != context_fields:
            raise RuntimeError("generic Live v4 mutation context fields are invalid")
        context_body = dict(mutation_context)
        declared_context_hash = context_body.pop("content_hash", None)
        signature = context_body.pop("capability_signature", None)
        expected_context_hash = hashlib.sha256(
            json.dumps(context_body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        if declared_context_hash != expected_context_hash:
            raise RuntimeError("generic Live v4 mutation context hash mismatch")
        if not isinstance(signature, str) or not _GENERIC_LIVE_V4_CAPABILITY.verify(
            declared_context_hash, signature
        ):
            raise RuntimeError("generic Live v4 mutation context signature mismatch")
        if mutation_context.get("schema_version") != "caerus.generic_live_v1_mutation_context.v1":
            raise RuntimeError("generic Live v4 mutation context schema differs")
        if mutation_context.get("action") != "SUBMIT":
            raise RuntimeError("generic Live v4 mutation context action differs")
        for field in ("owner_decision_hash", "preflight_hash", "plan_hash", "execution_policy_hash", "account_id_hash", "capital_proof_hash"):
            value = mutation_context.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise RuntimeError(f"generic Live v4 mutation context {field} is invalid")
        expected_order_context = {
            "client_order_id": client_id,
            "symbol": str(symbol or "").strip().upper(),
            "side": side_norm,
            "quantity": qty_float,
            "order_type": "limit",
            "time_in_force": "day",
            "extended_hours": False,
            "allow_fractional_shares": False,
            "quantity_precision": 0,
            "limit_price": limit_price_float,
            "max_fee_usd": fee,
        }
        if any(mutation_context.get(field) != value for field, value in expected_order_context.items()):
            raise RuntimeError("generic Live v4 mutation context does not match order boundary")
        if not re.fullmatch(r"[0-9a-f]{40}", str(mutation_context.get("deployed_sha") or "")):
            raise RuntimeError("generic Live v4 mutation context deployed_sha is invalid")
        capital_fields = (
            "fresh_equity_usd", "fresh_cash_usd", "effective_capital_usd",
            "dynamic_gross_cap_usd", "required_cash_reserve_usd",
            "worst_case_posttrade_gross_usd", "worst_case_posttrade_cash_usd",
            "starting_symbol_quantity", "starting_other_gross_usd",
            "gross_valuation_price", "expected_posttrade_symbol_quantity",
            "maximum_gross_usd",
        )
        try:
            capital = {field: float(mutation_context[field]) for field in capital_fields}
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("generic Live v4 capital context is invalid") from exc
        if not all(math.isfinite(value) for value in capital.values()):
            raise RuntimeError("generic Live v4 capital context is non-finite")
        effective = min(460.0, capital["fresh_equity_usd"])
        dynamic_cap = effective * 0.95
        reserve = effective * 0.05
        expected_quantity = capital["starting_symbol_quantity"] + (
            qty_float if side_norm == "BUY" else -qty_float
        )
        expected_gross = (
            capital["starting_other_gross_usd"]
            + expected_quantity * capital["gross_valuation_price"]
        )
        expected_cash = capital["fresh_cash_usd"] + (
            -qty_float * limit_price_float - fee
            if side_norm == "BUY"
            else qty_float * limit_price_float - fee
        )
        if any(
            abs(observed - expected) > 1e-8
            for observed, expected in (
                (capital["effective_capital_usd"], effective),
                (capital["dynamic_gross_cap_usd"], dynamic_cap),
                (capital["maximum_gross_usd"], dynamic_cap),
                (capital["required_cash_reserve_usd"], reserve),
                (capital["expected_posttrade_symbol_quantity"], expected_quantity),
                (capital["worst_case_posttrade_gross_usd"], expected_gross),
                (capital["worst_case_posttrade_cash_usd"], expected_cash),
            )
        ):
            raise RuntimeError("generic Live v4 capital context arithmetic differs")
        if (
            mutation_context.get("capital_gross_limit_pass") is not True
            or mutation_context.get("capital_cash_reserve_pass") is not True
            or expected_quantity < -1e-9
            or expected_gross > dynamic_cap + 1e-9
            or expected_cash + 1e-9 < reserve
        ):
            raise RuntimeError("generic Live v4 dynamic gross/cash boundary is not green")
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        request = LimitOrderRequest(
            symbol=str(symbol or "").strip().upper(),
            qty=qty_float,
            side=OrderSide.BUY if side_norm == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price_float,
            extended_hours=False,
            client_order_id=client_id,
        )
        return _normalize_order_obj(self.trading_client.submit_order(order_data=request))

    def cancel_generic_live_v4_order(
        self, *, broker_order_id: str, mutation_context: Mapping[str, Any],
        _generic_live_v4_capability: object | None = None,
    ) -> None:
        _require_generic_live_v4_capability(_generic_live_v4_capability)
        if bool(self.paper) or str(self.base_url or "").strip().lower().rstrip("/") != "https://api.alpaca.markets":
            raise RuntimeError("generic Live v4 cancellation requires canonical Live broker")
        cancellation_fields = {
            "schema_version", "action", "effective_session", "owner_decision_hash",
            "preflight_hash", "plan_hash", "execution_policy_hash",
            "account_id_hash", "deployed_sha", "order_id", "client_order_id",
            "symbol", "side", "quantity", "order_type", "time_in_force",
            "extended_hours", "allow_fractional_shares", "quantity_precision",
            "limit_price", "max_fee_usd", "maximum_gross_usd",
            "capital_proof_hash", "fresh_equity_usd", "fresh_cash_usd",
            "effective_capital_usd", "dynamic_gross_cap_usd",
            "required_cash_reserve_usd", "worst_case_posttrade_gross_usd",
            "worst_case_posttrade_cash_usd", "capital_gross_limit_pass",
            "capital_cash_reserve_pass", "starting_symbol_quantity",
            "starting_other_gross_usd", "gross_valuation_price",
            "expected_posttrade_symbol_quantity",
            "content_hash", "capability_signature", "broker_order_id",
        }
        if (
            not isinstance(mutation_context, Mapping)
            or set(mutation_context) != cancellation_fields
            or mutation_context.get("schema_version") != "caerus.generic_live_v1_cancellation_context.v1"
            or mutation_context.get("action") != "CANCEL"
            or mutation_context.get("broker_order_id") != str(broker_order_id)
        ):
            raise RuntimeError("generic Live v4 cancellation context is invalid")
        body = dict(mutation_context)
        signature = body.pop("capability_signature", None)
        declared = body.pop("content_hash", None)
        expected = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if declared != expected or not isinstance(signature, str) or not _GENERIC_LIVE_V4_CAPABILITY.verify(declared, signature):
            raise RuntimeError("generic Live v4 cancellation context hash/signature mismatch")
        cancel = getattr(self.trading_client, "cancel_order_by_id", None)
        if not callable(cancel):
            raise RuntimeError("Alpaca client cannot cancel exact generic order")
        cancel(str(broker_order_id))

    def submit_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: float,
        client_order_id: str,
        tif: str = "day",
        extended_hours: bool = False,
        _execution_capability: object | None = None,
    ) -> Dict[str, Any]:
        qty_float = float(qty)
        limit_price_float = float(limit_price)
        if not math.isfinite(qty_float) or qty_float <= 0.0:
            raise RuntimeError("Refusing limit order with non-finite or non-positive quantity.")
        if not math.isfinite(limit_price_float) or limit_price_float <= 0.0:
            raise RuntimeError("Refusing limit order with non-finite or non-positive limit price.")
        if not bool(self.paper):
            require_live_capital_disabled(
                mutation_path="brokers.alpaca_broker.submit_limit_order"
            )
        _require_exact_execution_capability(_execution_capability)
        validate_alpaca_submission_guardrails(
            broker_paper=bool(self.paper),
            base_url=self.base_url,
            order_notional=float(qty) * float(limit_price),
        )
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        base_url_norm = str(self.base_url or "").strip().lower().rstrip("/")
        if bool(self.paper) and "paper-api.alpaca.markets" not in base_url_norm:
            raise RuntimeError(
                "Refusing equity limit order outside Alpaca paper environment. "
                f"base_url={self.base_url!r}"
            )
        side_norm = str(side).upper()
        symbol_norm = str(symbol).upper()
        client_id = str(client_order_id)
        logger.info(
            "[ALPACA_SUBMIT] attempt order_type=limit symbol=%s side=%s qty=%.6f limit_price=%.6f notional=%.2f client_order_id=%s",
            symbol_norm,
            side_norm,
            qty_float,
            limit_price_float,
            qty_float * limit_price_float,
            client_id,
        )
        req = LimitOrderRequest(
            symbol=symbol_norm,
            qty=qty_float,
            side=OrderSide.BUY if side_norm == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce(str(tif).lower()),
            client_order_id=client_id,
            limit_price=limit_price_float,
            extended_hours=bool(extended_hours),
        )
        try:
            out = _normalize_order_obj(self.trading_client.submit_order(order_data=req))
        except Exception as exc:
            logger.exception(
                "[ALPACA_SUBMIT] ERROR order_type=limit symbol=%s side=%s qty=%.6f limit_price=%.6f client_order_id=%s error=%s",
                symbol_norm,
                side_norm,
                qty_float,
                limit_price_float,
                client_id,
                _safe_str(exc),
            )
            raise
        logger.info(
            "[ALPACA_SUBMIT] OK order_type=limit symbol=%s side=%s qty=%.6f limit_price=%.6f client_order_id=%s alpaca_order_id=%s status=%s",
            symbol_norm,
            side_norm,
            qty_float,
            limit_price_float,
            client_id,
            _safe_str(out.get("id")),
            _safe_str(out.get("status")),
        )
        return out

    def submit_option_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        client_order_id: str,
        tif: str = "day",
    ) -> Dict[str, Any]:
        require_options_capital_disabled(
            mutation_path="brokers.alpaca_broker.submit_option_market_order"
        )

    def submit_option_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: float,
        client_order_id: str,
        tif: str = "day",
    ) -> Dict[str, Any]:
        require_options_capital_disabled(
            mutation_path="brokers.alpaca_broker.submit_option_limit_order"
        )

    def list_orders(
        self, status: str = "open", limit: int = 100, after: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        st = str(status).strip().lower()
        if st == "open":
            query_status = QueryOrderStatus.OPEN
        elif st == "closed":
            query_status = QueryOrderStatus.CLOSED
        else:
            query_status = QueryOrderStatus.ALL
        req_kwargs: Dict[str, Any] = {"status": query_status, "limit": int(limit)}
        # Optional date bound (ISO date or datetime string). Used by the settled-cash
        # guard to query only the unsettled window instead of a count-bounded page.
        if after:
            after_text = str(after).strip()
            after_dt: Optional[datetime] = None
            try:
                after_dt = datetime.fromisoformat(after_text.replace("Z", "+00:00"))
            except ValueError:
                try:
                    after_date = datetime.strptime(after_text[:10], "%Y-%m-%d")
                    after_dt = after_date
                except ValueError:
                    after_dt = None
            if after_dt is not None:
                if after_dt.tzinfo is None:
                    # Treat a bare date as start-of-day ET (exchange calendar time).
                    after_dt = after_dt.replace(tzinfo=ZoneInfo("America/New_York"))
                req_kwargs["after"] = after_dt
        req = GetOrdersRequest(**req_kwargs)
        orders = self.trading_client.get_orders(filter=req)
        return [_normalize_order_obj(o) for o in orders]

    def list_fills_since(self, date_iso: str) -> List[Dict[str, Any]]:
        # Best-effort wrapper; Alpaca response model can vary by SDK version.
        try:
            fills = self.trading_client.get_activities(
                activity_types=["FILL"],
                date=str(date_iso),
            )
        except TypeError:
            fills = self.trading_client.get_activities()
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for f in fills or []:
            d = _as_dict(f)
            out.append(
                {
                    "id": _safe_str(d.get("id") or getattr(f, "id", "")),
                    "activity_type": _safe_str(
                        d.get("activity_type") or getattr(f, "activity_type", "")
                    ),
                    "symbol": _safe_str(d.get("symbol") or getattr(f, "symbol", "")),
                    "qty": _safe_str(d.get("qty") or getattr(f, "qty", "")),
                    "price": _safe_str(d.get("price") or getattr(f, "price", "")),
                    "transaction_time": _safe_str(
                        d.get("transaction_time")
                        or getattr(f, "transaction_time", "")
                    ),
                    "raw": d,
                }
            )
        return out

    def list_generic_live_v1_fill_activities(
        self, date_iso: str,
    ) -> List[Dict[str, Any]]:
        """Read exact Alpaca FILL activities with order and fee lineage."""

        payload = self.trading_client.get(
            "/account/activities/FILL",
            data={
                "date": str(date_iso), "direction": "asc", "page_size": 100,
            },
        )
        if not isinstance(payload, list):
            raise RuntimeError("Alpaca FILL activities response is not an array")
        rows: List[Dict[str, Any]] = []
        for activity in payload:
            raw = _as_dict(activity)
            if not raw:
                raise RuntimeError("Alpaca FILL activity is not an object")
            fee = raw.get("fee_amount")
            if fee is None:
                fee = raw.get("fee")
            side = _safe_str(raw.get("side"))
            if fee is None and side.strip().lower().split(".")[-1] == "sell":
                raise RuntimeError(
                    "Alpaca SELL fill lacks explicit fee evidence"
                )
            rows.append(
                {
                    "id": _safe_str(raw.get("id")),
                    "activity_type": _safe_str(raw.get("activity_type")),
                    "transaction_time": _safe_str(raw.get("transaction_time")),
                    "order_id": _safe_str(raw.get("order_id")),
                    "symbol": _safe_str(raw.get("symbol")).upper(),
                    "side": side,
                    "qty": _safe_str(raw.get("qty")),
                    "price": _safe_str(raw.get("price")),
                    # A missing BUY commission is normalized to zero. SELL
                    # activities fail closed above because regulatory fees can
                    # exist and must be explicit for factual accounting.
                    "fee_amount": _safe_str("0" if fee is None else fee),
                }
            )
        return rows
