from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def _is_truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


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


@dataclass
class AlpacaBroker:
    trading_client: Any
    paper: bool = True

    @classmethod
    def from_env(cls) -> "AlpacaBroker":
        api_key = os.getenv("ALPACA_API_KEY_ID")
        api_secret = os.getenv("ALPACA_API_SECRET_KEY")
        if not api_key or not api_secret:
            raise RuntimeError(
                "Missing Alpaca credentials: ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY are required."
            )
        paper = _is_truthy(os.getenv("ALPACA_PAPER"), default=True)
        try:
            from alpaca.trading.client import TradingClient
        except Exception as exc:
            raise RuntimeError(
                "alpaca-py is required for TRADING_MODE=alpaca. Install with `pip install alpaca-py`."
            ) from exc
        return cls(trading_client=TradingClient(api_key, api_secret, paper=paper), paper=paper)

    def get_account(self) -> Dict[str, Any]:
        account = self.trading_client.get_account()
        d = _as_dict(account)
        return {
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

        req = MarketOrderRequest(
            symbol=str(symbol).upper(),
            qty=float(qty),
            side=OrderSide.BUY if str(side).upper() == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce(str(tif).lower()),
            client_order_id=str(client_order_id),
        )
        return _normalize_order_obj(self.trading_client.submit_order(order_data=req))

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

        req = LimitOrderRequest(
            symbol=str(symbol).upper(),
            qty=float(qty),
            side=OrderSide.BUY if str(side).upper() == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce(str(tif).lower()),
            client_order_id=str(client_order_id),
            limit_price=float(limit_price),
        )
        return _normalize_order_obj(self.trading_client.submit_order(order_data=req))

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
