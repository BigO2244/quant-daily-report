from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


BROKER_REJECT_PDT = "BROKER_REJECT_PDT"
BROKER_REJECT_BUYING_POWER = "BROKER_REJECT_BUYING_POWER"
BROKER_REJECT_SHORT_NOT_ALLOWED = "BROKER_REJECT_SHORT_NOT_ALLOWED"
BROKER_REJECT_ASSET_NOT_TRADABLE = "BROKER_REJECT_ASSET_NOT_TRADABLE"
BROKER_REJECT_UNKNOWN = "BROKER_REJECT_UNKNOWN"
EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT = "partial_execution_broker_abort"
EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE = "post_submit_artifact_failure"
CASH_REBALANCE_INCOMPLETE = "cash_rebalance_incomplete"


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
    elif "not tradable" in normalized or "asset is not tradable" in normalized:
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
            "portfolio_value": _safe_str(
                d.get("portfolio_value") or getattr(account, "portfolio_value", "")
            ),
            "raw": d,
        }
        logger.info(
            "[ALPACA] account id=%s status=%s equity=%s cash=%s buying_power=%s",
            out.get("id", ""),
            out.get("status", ""),
            out.get("equity", ""),
            out.get("cash", ""),
            out.get("buying_power", ""),
        )
        return out

    def get_positions(self) -> List[Dict[str, Any]]:
        positions = self.trading_client.get_all_positions()
        return [_normalize_position_obj(p) for p in positions]

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
    ) -> Dict[str, Any]:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        side_norm = str(side).upper()
        qty_float = float(qty)
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

    def submit_limit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        limit_price: float,
        client_order_id: str,
        tif: str = "day",
    ) -> Dict[str, Any]:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        side_norm = str(side).upper()
        qty_float = float(qty)
        symbol_norm = str(symbol).upper()
        client_id = str(client_order_id)
        limit_price_float = float(limit_price)
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
        base_url_norm = str(self.base_url or "").strip().lower().rstrip("/")
        if not bool(self.paper) or "paper-api.alpaca.markets" not in base_url_norm:
            raise RuntimeError(
                "Refusing option market order outside Alpaca paper environment"
            )
        logger.info(
            "[ALPACA_SUBMIT][OPTION] submitting market order symbol=%s qty=%.6f side=%s",
            str(symbol).upper(),
            float(qty),
            str(side).upper(),
        )
        return self.submit_market_order(
            symbol=symbol,
            qty=qty,
            side=side,
            client_order_id=client_order_id,
            tif=tif,
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
        base_url_norm = str(self.base_url or "").strip().lower().rstrip("/")
        if not bool(self.paper) or "paper-api.alpaca.markets" not in base_url_norm:
            raise RuntimeError(
                "Refusing option limit order outside Alpaca paper environment"
            )
        logger.info(
            "[ALPACA_SUBMIT][OPTION] submitting limit order symbol=%s qty=%.6f side=%s limit_price=%.6f",
            str(symbol).upper(),
            float(qty),
            str(side).upper(),
            float(limit_price),
        )
        return self.submit_limit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            limit_price=limit_price,
            client_order_id=client_order_id,
            tif=tif,
        )

    def list_orders(self, status: str = "open", limit: int = 100) -> List[Dict[str, Any]]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        st = str(status).strip().lower()
        if st == "open":
            query_status = QueryOrderStatus.OPEN
        elif st == "closed":
            query_status = QueryOrderStatus.CLOSED
        else:
            query_status = QueryOrderStatus.ALL
        req = GetOrdersRequest(status=query_status, limit=int(limit))
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
