"""Immutable exact-order contract between Caerus authorization and execution.

Version 3 deliberately carries broker-ready orders.  An executor consuming this
contract may validate and submit those rows, but must never rebuild quantities
from portfolio targets or substitute another source artifact.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .contracts import AuthorityContractError


EXACT_EXECUTION_SCHEMA_VERSION = "caerus.execution_plan.v3"
_SAFE_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
_SAFE_SLEEVE = re.compile(r"^[a-z][a-z0-9_\-]{1,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ORDER_TYPES = {"market", "limit"}
_TIME_IN_FORCE = {"day", "gtc", "opg", "cls", "ioc", "fok"}
_FRACTIONAL_QUANTITY_DECIMALS = 6
_FRACTIONAL_QUANTITY_RESIDUAL_TOLERANCE = 1e-12
_BROKER_OWNED_ORDER_FIELDS = {
    "id", "status", "filled_qty", "filled_quantity", "filled_avg_price",
    "fill_price", "average_price", "submitted_at", "filled_at", "canceled_at",
    "rejected_at", "expired_at", "raw", "order", "broker_order_id",
}
_EXACT_PLAN_TOP_LEVEL_FIELDS = {
    "schema_version",
    "plan_id",
    "run_id",
    "as_of",
    "created_at",
    "orchestrator_version",
    "strategy_id",
    "account_scope",
    "account_id_hash",
    "source_precompute_ids",
    "source_artifact_hashes",
    "market_state_id",
    "market_state",
    "regime_state",
    "sleeve_allocations",
    "portfolio_nav",
    "starting_positions",
    "starting_cash",
    "starting_state_hash",
    "risk_state",
    "sell_orders",
    "buy_orders",
    "expected_posttrade_positions",
    "expected_posttrade_cash",
    "constraints",
    "authorization_state",
    "content_hash",
}


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuthorityContractError(f"exact plan is not canonical JSON: {exc}") from exc


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _finite(value: Any, label: str, *, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AuthorityContractError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise AuthorityContractError(f"{label} is outside the allowed range")
    return result


def _canonical_fractional_quantity(value: Any, label: str) -> float:
    quantity = _finite(value, label, minimum=0.0)
    canonical = round(quantity, _FRACTIONAL_QUANTITY_DECIMALS)
    if abs(quantity - canonical) > _FRACTIONAL_QUANTITY_RESIDUAL_TOLERANCE:
        raise AuthorityContractError(
            f"{label} exceeds governed six-decimal fractional precision"
        )
    return canonical


def _as_plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _as_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _iso(value: str, label: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthorityContractError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise AuthorityContractError(f"{label} must include a timezone")
    return raw


def _position_rows(rows: Sequence[Mapping[str, Any]], label: str) -> tuple[Mapping[str, Any], ...]:
    normalized: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise AuthorityContractError(f"{label} rows must be objects")
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
        if not _SAFE_SYMBOL.fullmatch(symbol) or symbol in seen:
            raise AuthorityContractError(f"{label} contains invalid or duplicate symbol {symbol!r}")
        seen.add(symbol)
        quantity = _finite(raw.get("quantity", raw.get("qty", raw.get("shares", 0))), f"{label}.{symbol}.quantity", minimum=0.0)
        row = {str(key): _as_plain(value) for key, value in raw.items()}
        row["symbol"] = symbol
        row["quantity"] = quantity
        normalized.append(_freeze(row))
    return tuple(sorted(normalized, key=lambda row: str(row["symbol"])))


def compute_starting_state_hash(
    starting_positions: Sequence[Mapping[str, Any]], starting_cash: float
) -> str:
    """Hash only exposure-defining state, independent of marks and display fields."""
    positions = _position_rows(starting_positions, "starting_positions")
    payload = {
        "positions": [
            {"symbol": str(row["symbol"]), "quantity": float(row["quantity"])}
            for row in positions
        ],
        "cash": _finite(starting_cash, "starting_cash", minimum=0.0),
    }
    return _hash(payload)


def _normalize_sleeve_contributions(
    raw: Any,
    *,
    eligible_sleeves: Sequence[str],
) -> list[dict[str, Any]]:
    """Canonical causal ownership carried by every exact broker order."""

    allowed = tuple(sorted({str(value).strip().lower() for value in eligible_sleeves}))
    if not allowed or any(not _SAFE_SLEEVE.fullmatch(value) for value in allowed):
        raise AuthorityContractError("exact plan eligible sleeve set is invalid")
    supplied = raw if isinstance(raw, (list, tuple)) else []
    if not supplied:
        if len(allowed) != 1:
            raise AuthorityContractError(
                "multi-sleeve exact orders require causal sleeve contributions"
            )
        supplied = [{"sleeve_id": allowed[0], "allocation_fraction": 1.0}]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    bases: list[float] = []
    for item in supplied:
        if not isinstance(item, Mapping):
            raise AuthorityContractError("sleeve contribution rows must be objects")
        sleeve_id = str(item.get("sleeve_id") or item.get("sleeve") or "").strip().lower()
        if sleeve_id not in allowed or sleeve_id in seen:
            raise AuthorityContractError(
                "exact order sleeve contributions must uniquely match eligible sleeves"
            )
        seen.add(sleeve_id)
        basis = _finite(
            item.get("allocation_fraction", item.get("target_weight")),
            f"sleeve_contribution.{sleeve_id}",
            minimum=0.0,
        )
        if basis <= 0.0:
            raise AuthorityContractError("sleeve contribution must be positive")
        row = {str(key): _as_plain(value) for key, value in item.items()}
        row.pop("sleeve", None)
        row["sleeve_id"] = sleeve_id
        rows.append(row)
        bases.append(basis)
    total = sum(bases)
    if total <= 0.0:
        raise AuthorityContractError("sleeve contribution total must be positive")
    for row, basis in zip(rows, bases, strict=True):
        row["allocation_fraction"] = basis / total
    rows.sort(key=lambda row: str(row["sleeve_id"]))
    return rows


def _normalize_orders(
    rows: Sequence[Mapping[str, Any]],
    *,
    required_side: str,
    plan_seed: str,
    eligible_sleeves: Sequence[str],
) -> tuple[Mapping[str, Any], ...]:
    normalized: list[Mapping[str, Any]] = []
    seen_symbols: set[str] = set()
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, Mapping):
            raise AuthorityContractError("exact order rows must be objects")
        forbidden = sorted(_BROKER_OWNED_ORDER_FIELDS.intersection(raw))
        if forbidden:
            raise AuthorityContractError(
                "exact order contains broker-owned fields: " + ", ".join(forbidden)
            )
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
        side = str(raw.get("side") or required_side).strip().upper()
        if not _SAFE_SYMBOL.fullmatch(symbol):
            raise AuthorityContractError(f"invalid exact-order symbol: {symbol!r}")
        if side != required_side:
            raise AuthorityContractError(f"{required_side} order list contains {side} row")
        if symbol in seen_symbols:
            raise AuthorityContractError(f"duplicate {side} order for {symbol}")
        seen_symbols.add(symbol)
        quantity = _finite(
            raw.get("quantity", raw.get("qty", raw.get("shares"))),
            f"{side}.{symbol}.quantity",
            minimum=0.0,
        )
        if quantity <= 0:
            raise AuthorityContractError(f"{side}.{symbol}.quantity must be positive")
        order_type = str(raw.get("order_type") or "market").strip().lower()
        if order_type not in _ORDER_TYPES:
            raise AuthorityContractError(f"unsupported exact order_type {order_type!r}")
        tif = str(raw.get("time_in_force") or raw.get("tif") or "day").strip().lower()
        if tif not in _TIME_IN_FORCE:
            raise AuthorityContractError(f"unsupported exact time_in_force {tif!r}")
        extended_hours = raw.get("extended_hours", False)
        if not isinstance(extended_hours, bool):
            raise AuthorityContractError("extended_hours must be a boolean")
        if extended_hours and (order_type != "limit" or tif != "day"):
            raise AuthorityContractError(
                "extended-hours exact orders must be DAY limit orders"
            )
        row = {str(key): _as_plain(value) for key, value in raw.items()}
        for alias in ("ticker", "qty", "shares", "tif", "order_id", "client_order_id"):
            row.pop(alias, None)
        row.update(
            {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
                "time_in_force": tif,
                "extended_hours": extended_hours,
            }
        )
        for price_key in (
            "price",
            "expected_price",
            "limit_price",
            "cap_enforcement_price",
            "stop_price",
            "notional",
        ):
            if row.get(price_key) is not None:
                row[price_key] = _finite(row[price_key], f"{side}.{symbol}.{price_key}", minimum=0.0)
        if row.get("stop_price") is not None:
            raise AuthorityContractError("stop_price is invalid for market/limit exact orders")
        if order_type == "market" and row.get("limit_price") is not None:
            raise AuthorityContractError("market exact order cannot carry limit_price")
        if order_type == "limit" and not row.get("limit_price"):
            raise AuthorityContractError("limit exact order requires positive limit_price")
        row["sleeve_contributions"] = _normalize_sleeve_contributions(
            row.get("sleeve_contributions"),
            eligible_sleeves=eligible_sleeves,
        )
        row["sleeve"] = (
            str(eligible_sleeves[0])
            if len(eligible_sleeves) == 1
            else "caerus_paper_portfolio"
        )
        limit_price = row.get("limit_price")
        cap_enforcement_price = row.get("cap_enforcement_price")
        if order_type == "limit":
            price = float(limit_price or 0.0)
            if cap_enforcement_price is not None and abs(
                float(cap_enforcement_price) - price
            ) > 1e-9:
                raise AuthorityContractError(
                    f"{side}.{symbol} cap_enforcement_price must equal limit_price"
                )
        else:
            price = float(
                cap_enforcement_price
                or row.get("expected_price")
                or row.get("price")
                or 0.0
            )
        computed_notional = quantity * price
        declared_notional = float(row.get("notional") or computed_notional)
        if (
            price <= 0
            or computed_notional <= 0
            or not math.isfinite(computed_notional)
            or abs(declared_notional - computed_notional) > 0.01
        ):
            raise AuthorityContractError(
                f"{side}.{symbol} notional must equal quantity times enforcement price"
            )
        row["notional"] = computed_notional
        identity_hash = _hash(
            {"plan_seed": plan_seed, "side": side, "index": index, "order": row}
        )
        row["order_id"] = f"order:{side.lower()}:{index}:{identity_hash[:16]}"
        row["client_order_id"] = f"cx-{identity_hash[:40]}"
        normalized.append(_freeze(row))
    return tuple(normalized)


def _order_seed_rows(
    rows: Sequence[Mapping[str, Any]], *, required_side: str,
    eligible_sleeves: Sequence[str],
) -> list[dict[str, Any]]:
    """Canonicalize aliases and remove generated IDs for the decision seed."""
    result: list[dict[str, Any]] = []
    for raw in rows:
        forbidden = sorted(_BROKER_OWNED_ORDER_FIELDS.intersection(raw))
        if forbidden:
            raise AuthorityContractError(
                "exact order contains broker-owned fields: " + ", ".join(forbidden)
            )
        row = {str(key): _as_plain(value) for key, value in raw.items()}
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        quantity = row.get("quantity", row.get("qty", row.get("shares")))
        tif = str(row.get("time_in_force") or row.get("tif") or "day").strip().lower()
        extended_hours = row.get("extended_hours", False)
        if not isinstance(extended_hours, bool):
            raise AuthorityContractError("extended_hours must be a boolean")
        for alias in ("ticker", "qty", "shares", "tif", "order_id", "client_order_id"):
            row.pop(alias, None)
        row.update(
            {
                "symbol": symbol,
                "side": str(row.get("side") or required_side).strip().upper(),
                "quantity": _finite(quantity, f"{required_side}.{symbol}.quantity", minimum=0.0),
                "order_type": str(row.get("order_type") or "market").strip().lower(),
                "time_in_force": tif,
                "extended_hours": extended_hours,
            }
        )
        if extended_hours and (row["order_type"] != "limit" or tif != "day"):
            raise AuthorityContractError(
                "extended-hours exact orders must be DAY limit orders"
            )
        for price_key in (
            "price",
            "expected_price",
            "limit_price",
            "cap_enforcement_price",
            "stop_price",
            "notional",
        ):
            if row.get(price_key) is not None:
                row[price_key] = _finite(
                    row[price_key], f"{required_side}.{symbol}.{price_key}", minimum=0.0
                )
        row["sleeve_contributions"] = _normalize_sleeve_contributions(
            row.get("sleeve_contributions"),
            eligible_sleeves=eligible_sleeves,
        )
        row["sleeve"] = (
            str(eligible_sleeves[0])
            if len(eligible_sleeves) == 1
            else "caerus_paper_portfolio"
        )
        limit_price = row.get("limit_price")
        cap_enforcement_price = row.get("cap_enforcement_price")
        if row["order_type"] == "limit":
            price = float(limit_price or 0.0)
            if cap_enforcement_price is not None and abs(
                float(cap_enforcement_price) - price
            ) > 1e-9:
                raise AuthorityContractError(
                    f"{required_side}.{symbol} cap_enforcement_price must equal limit_price"
                )
        else:
            price = float(
                cap_enforcement_price
                or row.get("expected_price")
                or row.get("price")
                or 0.0
            )
        computed_notional = float(row["quantity"]) * price
        declared_notional = float(row.get("notional") or computed_notional)
        if price <= 0 or abs(declared_notional - computed_notional) > 0.01:
            raise AuthorityContractError(
                f"{required_side}.{symbol} notional must equal quantity times enforcement price"
            )
        row["notional"] = computed_notional
        result.append(row)
    return result


@dataclass(frozen=True)
class ExactExecutionPlan:
    run_id: str
    as_of: str
    created_at: str
    orchestrator_version: str
    source_precompute_ids: tuple[str, ...]
    source_artifact_hashes: Mapping[str, str]
    market_state_id: str
    market_state: Mapping[str, Any]
    regime_state: Mapping[str, Any]
    sleeve_allocations: tuple[Mapping[str, Any], ...]
    portfolio_nav: float
    starting_positions: tuple[Mapping[str, Any], ...]
    starting_cash: float
    account_id_hash: str
    risk_state: Mapping[str, Any]
    sell_orders: tuple[Mapping[str, Any], ...]
    buy_orders: tuple[Mapping[str, Any], ...]
    expected_posttrade_positions: tuple[Mapping[str, Any], ...]
    expected_posttrade_cash: float
    constraints: Mapping[str, Any]
    authorization_state: Mapping[str, Any]
    plan_id: str
    starting_state_hash: str
    content_hash: str
    strategy_id: str = "caerus_orion"
    account_scope: str = "PAPER"
    schema_version: str = EXACT_EXECUTION_SCHEMA_VERSION

    @property
    def trade_date(self) -> str:
        return self.as_of[:10]

    @property
    def orders(self) -> tuple[Mapping[str, Any], ...]:
        # The stored sell/buy rows remain recursively frozen. Consumers receive
        # detached plain-JSON copies so nested causal lineage is serializable
        # and cannot mutate the immutable plan.
        return tuple(
            _as_plain(row) for row in (*self.sell_orders, *self.buy_orders)
        )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "as_of": self.as_of,
            "created_at": self.created_at,
            "orchestrator_version": self.orchestrator_version,
            "strategy_id": self.strategy_id,
            "account_scope": self.account_scope,
            "account_id_hash": self.account_id_hash,
            "source_precompute_ids": list(self.source_precompute_ids),
            "source_artifact_hashes": _as_plain(self.source_artifact_hashes),
            "market_state_id": self.market_state_id,
            "market_state": _as_plain(self.market_state),
            "regime_state": _as_plain(self.regime_state),
            "sleeve_allocations": _as_plain(self.sleeve_allocations),
            "portfolio_nav": self.portfolio_nav,
            "starting_positions": _as_plain(self.starting_positions),
            "starting_cash": self.starting_cash,
            "starting_state_hash": self.starting_state_hash,
            "risk_state": _as_plain(self.risk_state),
            "sell_orders": _as_plain(self.sell_orders),
            "buy_orders": _as_plain(self.buy_orders),
            "expected_posttrade_positions": _as_plain(self.expected_posttrade_positions),
            "expected_posttrade_cash": self.expected_posttrade_cash,
            "constraints": _as_plain(self.constraints),
            "authorization_state": _as_plain(self.authorization_state),
        }
        if include_hash:
            payload["content_hash"] = self.content_hash
        return payload


def build_exact_execution_plan(
    *,
    run_id: str,
    as_of: str,
    created_at: str,
    orchestrator_version: str,
    source_precompute_ids: Sequence[str],
    source_artifact_hashes: Mapping[str, str],
    market_state_id: str,
    market_state: Mapping[str, Any],
    regime_state: Mapping[str, Any],
    sleeve_allocations: Sequence[Mapping[str, Any]],
    portfolio_nav: float,
    starting_positions: Sequence[Mapping[str, Any]],
    starting_cash: float,
    account_id_hash: str,
    risk_state: Mapping[str, Any],
    sell_orders: Sequence[Mapping[str, Any]],
    buy_orders: Sequence[Mapping[str, Any]],
    expected_posttrade_positions: Sequence[Mapping[str, Any]],
    expected_posttrade_cash: float,
    constraints: Mapping[str, Any],
    authorization_state: Mapping[str, Any] | str,
    strategy_id: str = "caerus_orion",
    account_scope: str = "PAPER",
    allow_legacy_missing_fill_risk_authority: bool = False,
    validate_current_allocator: bool = True,
) -> ExactExecutionPlan:
    as_of = _iso(as_of, "as_of")
    created_at = _iso(created_at, "created_at")
    if str(account_scope).strip().upper() != "PAPER":
        raise AuthorityContractError("exact execution plan is PAPER-only; live capital remains blocked")
    normalized_account_id_hash = str(account_id_hash or "").strip().lower()
    if not _SHA256.fullmatch(normalized_account_id_hash):
        raise AuthorityContractError(
            "exact PAPER plan requires a SHA-256 account_id_hash binding"
        )
    if not str(run_id or "").strip() or not str(orchestrator_version or "").strip():
        raise AuthorityContractError("run_id and orchestrator_version are required")
    if not str(market_state_id or "").strip():
        raise AuthorityContractError("market_state_id is required")
    from core.regime_state_store import REGIME_EVENT_SCHEMA_VERSION

    normalized_regime = _as_plain(regime_state)
    if normalized_regime.get("state_schema_version") != REGIME_EVENT_SCHEMA_VERSION:
        raise AuthorityContractError(
            "exact execution requires the governed regime authority schema"
        )
    precompute_ids = tuple(str(value).strip() for value in source_precompute_ids)
    if not precompute_ids or any(not value for value in precompute_ids):
        raise AuthorityContractError("nonempty source_precompute_ids are required")
    source_hashes = {
        str(key).strip(): str(value).strip().lower()
        for key, value in source_artifact_hashes.items()
    }
    if not source_hashes or any(
        not key or not _SHA256.fullmatch(value)
        for key, value in source_hashes.items()
    ):
        raise AuthorityContractError("source_artifact_hashes must contain SHA-256 lineage")
    allocations = [dict(row) for row in sleeve_allocations]
    eligible: list[str] = []
    for row in allocations:
        sleeve = str(row.get("sleeve_id") or row.get("sleeve") or "").strip().lower()
        if bool(row.get("capital_eligible")):
            eligible.append(sleeve)
    plan_eligible = tuple(sorted(eligible))
    if not plan_eligible or len(eligible) != len(set(eligible)):
        raise AuthorityContractError(
            "exact PAPER plan requires a unique capital-eligible sleeve set"
        )
    expected_eligible = plan_eligible
    if validate_current_allocator:
        from core.sleeve_control_plane import load_sleeve_control_registry

        allocation_policy = load_sleeve_control_registry().paper_allocation_policy
        governed_eligible = tuple(
            sorted(
                str(value).strip().lower()
                for value in (allocation_policy.get("sleeve_risk_budgets") or {})
            )
        )
        if plan_eligible != governed_eligible:
            raise AuthorityContractError(
                "exact PAPER capital-eligible sleeve set differs from the governed allocator"
            )
        expected_eligible = governed_eligible
    expected_strategy_id = (
        expected_eligible[0]
        if len(expected_eligible) == 1
        else "caerus_paper_portfolio"
    )
    normalized_strategy_id = str(strategy_id).strip().lower()
    if normalized_strategy_id != expected_strategy_id:
        raise AuthorityContractError(
            "exact PAPER strategy identity differs from the governed allocator"
        )
    starting = _position_rows(starting_positions, "starting_positions")
    expected = _position_rows(expected_posttrade_positions, "expected_posttrade_positions")
    cash = _finite(starting_cash, "starting_cash", minimum=0.0)
    expected_cash = _finite(expected_posttrade_cash, "expected_posttrade_cash", minimum=0.0)
    nav = _finite(portfolio_nav, "portfolio_nav", minimum=0.0)
    seed_payload = {
        # Operational retries get a new run_id, but the same immutable economic
        # decision must retain its plan/order/client identities for recovery.
        # run_id remains in the signed content payload below as audit lineage; it
        # is deliberately excluded from the economic identity seed.
        "as_of": as_of,
        "orchestrator_version": str(orchestrator_version),
        "source_precompute_ids": list(precompute_ids),
        "source_artifact_hashes": source_hashes,
        "market_state_id": str(market_state_id),
        "market_state": _as_plain(market_state),
        "regime_state": normalized_regime,
        "sleeve_allocations": _as_plain(allocations),
        "portfolio_nav": nav,
        "starting_positions": _as_plain(starting),
        "starting_cash": cash,
        "account_id_hash": normalized_account_id_hash,
        "risk_state": _as_plain(risk_state),
        "raw_sell_orders": _order_seed_rows(
            sell_orders, required_side="SELL", eligible_sleeves=expected_eligible
        ),
        "raw_buy_orders": _order_seed_rows(
            buy_orders, required_side="BUY", eligible_sleeves=expected_eligible
        ),
        "expected_posttrade_positions": _as_plain(expected),
        "expected_posttrade_cash": expected_cash,
        "constraints": _as_plain(constraints),
        "strategy_id": normalized_strategy_id,
        "account_scope": "PAPER",
    }
    plan_seed = _hash(seed_payload)
    plan_id = f"plan:{as_of[:10]}:{plan_seed[:24]}"
    auth = (
        {"status": str(authorization_state)}
        if isinstance(authorization_state, str)
        else {str(key): _as_plain(value) for key, value in authorization_state.items()}
    )
    auth["status"] = str(auth.get("status") or "").strip().upper()
    auth.setdefault("authorization_id", f"authorization:{plan_seed[:24]}")
    if auth["status"] != "AUTHORIZED":
        raise AuthorityContractError("exact execution plan must be explicitly AUTHORIZED")
    if str(auth.get("authority") or "").strip().upper() != "CAERUS_ORCHESTRATOR":
        raise AuthorityContractError(
            "exact execution authorization authority must be CAERUS_ORCHESTRATOR"
        )
    auth["authorized_at"] = _iso(auth.get("authorized_at"), "authorization_state.authorized_at")
    if not str(auth.get("authorization_reason") or "").strip():
        raise AuthorityContractError("exact execution authorization_reason is required")
    sells = _normalize_orders(
        sell_orders,
        required_side="SELL",
        plan_seed=plan_seed,
        eligible_sleeves=expected_eligible,
    )
    buys = _normalize_orders(
        buy_orders,
        required_side="BUY",
        plan_seed=plan_seed,
        eligible_sleeves=expected_eligible,
    )
    if bool(normalized_regime.get("risk_veto_buys")) and buys:
        raise AuthorityContractError(
            "emergency regime risk response vetoes all new buy exposure"
        )
    constraint_values = {str(key): _as_plain(value) for key, value in constraints.items()}
    if "sleeve_attribution_mark_timing_tolerance_bps" in constraint_values:
        attribution_bps = _finite(
            constraint_values["sleeve_attribution_mark_timing_tolerance_bps"],
            "constraints.sleeve_attribution_mark_timing_tolerance_bps",
            minimum=0.0,
        )
        if attribution_bps > 50.0:
            raise AuthorityContractError(
                "sleeve attribution mark timing tolerance exceeds 50 basis points"
            )
        constraint_values["sleeve_attribution_mark_timing_tolerance_bps"] = (
            attribution_bps
        )
    if "sleeve_attribution_interval" in constraint_values and str(
        constraint_values["sleeve_attribution_interval"]
    ) != "execution_pre_to_post_broker_nav":
        raise AuthorityContractError("unsupported sleeve attribution interval")
    try:
        max_orders = int(constraint_values["max_orders"])
        capital_cap = _finite(
            constraint_values["capital_cap_usd"], "constraints.capital_cap_usd", minimum=0.0
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthorityContractError("exact constraints require max_orders and capital_cap_usd") from exc
    if max_orders < 0 or len(sells) + len(buys) > max_orders:
        raise AuthorityContractError("exact order count exceeds constraints.max_orders")
    if sells or buys:
        try:
            max_adverse_fill_slippage_bps = _finite(
                constraint_values["max_adverse_fill_slippage_bps"],
                "constraints.max_adverse_fill_slippage_bps",
                minimum=0.0,
            )
        except (KeyError, TypeError, ValueError) as exc:
            if not allow_legacy_missing_fill_risk_authority:
                raise AuthorityContractError(
                    "exact orders require constraints.max_adverse_fill_slippage_bps"
                ) from exc
            max_adverse_fill_slippage_bps = None
        if max_adverse_fill_slippage_bps is not None:
            if max_adverse_fill_slippage_bps > 100.0:
                raise AuthorityContractError(
                    "exact adverse fill slippage tolerance exceeds 100 basis points"
                )
            constraint_values["max_adverse_fill_slippage_bps"] = (
                max_adverse_fill_slippage_bps
            )
    else:
        max_adverse_fill_slippage_bps = None
    execution_style = str(
        constraint_values.get("new_order_execution_style") or ""
    ).strip().lower()
    if execution_style == "protective_day_limit" and (sells or buys):
        if (
            max_adverse_fill_slippage_bps is None
            and not allow_legacy_missing_fill_risk_authority
        ):
            raise AuthorityContractError(
                "protective DAY-limit orders require adverse-fill authority"
            )
        collar_fraction = (
            None
            if max_adverse_fill_slippage_bps is None
            else max_adverse_fill_slippage_bps / 10000.0
        )
        for row in (*sells, *buys):
            side = str(row["side"])
            symbol = str(row["symbol"])
            expected_price = float(
                row.get("expected_price") or row.get("price") or 0.0
            )
            limit_price = float(row.get("limit_price") or 0.0)
            cap_enforcement_price = float(
                row.get("cap_enforcement_price") or 0.0
            )
            if (
                row.get("order_type") != "limit"
                or row.get("time_in_force") != "day"
                or row.get("extended_hours") is not False
                or expected_price <= 0
                or limit_price <= 0
                or abs(cap_enforcement_price - limit_price) > 1e-9
            ):
                raise AuthorityContractError(
                    f"{side}.{symbol} violates protective DAY-limit execution style"
                )
            if collar_fraction is None:
                collar_valid = True
            elif side == "BUY":
                collar_valid = limit_price <= (
                    expected_price * (1.0 + collar_fraction) + 1e-9
                )
            else:
                collar_valid = limit_price + 1e-9 >= (
                    expected_price * (1.0 - collar_fraction)
                )
            if not collar_valid:
                raise AuthorityContractError(
                    f"{side}.{symbol} protective limit exceeds adverse-fill collar"
                )
    aggregate_buy_notional = sum(float(row["notional"]) for row in buys)
    if capital_cap <= 0 or aggregate_buy_notional > capital_cap + 1e-9:
        raise AuthorityContractError("exact buy notional exceeds constraints.capital_cap_usd")
    mechanically_expected = {
        str(row["symbol"]): float(row["quantity"]) for row in starting
    }
    fractional_quantities = constraint_values.get("allow_fractional") is True
    mechanical_cash = cash
    for row in sells:
        symbol = str(row["symbol"])
        quantity = float(row["quantity"])
        if mechanically_expected.get(symbol, 0.0) + 1e-9 < quantity:
            raise AuthorityContractError(f"exact sell exceeds starting position: {symbol}")
        mechanically_expected[symbol] = mechanically_expected.get(symbol, 0.0) - quantity
        mechanical_cash += float(row["notional"])
    for row in buys:
        symbol = str(row["symbol"])
        mechanically_expected[symbol] = mechanically_expected.get(symbol, 0.0) + float(row["quantity"])
        mechanical_cash -= float(row["notional"])
    if fractional_quantities:
        mechanically_expected = {
            symbol: _canonical_fractional_quantity(
                quantity, f"mechanically_expected.{symbol}.quantity"
            )
            for symbol, quantity in mechanically_expected.items()
        }
    mechanically_expected = {
        symbol: quantity
        for symbol, quantity in mechanically_expected.items()
        if quantity > 1e-12
    }
    supplied_expected = {
        str(row["symbol"]): float(row["quantity"]) for row in expected
    }
    if fractional_quantities:
        supplied_expected = {
            symbol: _canonical_fractional_quantity(
                quantity, f"expected_posttrade_positions.{symbol}.quantity"
            )
            for symbol, quantity in supplied_expected.items()
        }
    if mechanically_expected != supplied_expected:
        raise AuthorityContractError("expected_posttrade_positions are not derived from exact orders")
    if abs(mechanical_cash - expected_cash) > 1e-6:
        raise AuthorityContractError("expected_posttrade_cash is not derived from exact orders")
    plan = ExactExecutionPlan(
        run_id=str(run_id),
        as_of=as_of,
        created_at=created_at,
        orchestrator_version=str(orchestrator_version),
        source_precompute_ids=precompute_ids,
        source_artifact_hashes=_freeze(source_hashes),
        market_state_id=str(market_state_id),
        market_state=_freeze(_as_plain(market_state)),
        regime_state=_freeze(normalized_regime),
        sleeve_allocations=tuple(_freeze(row) for row in allocations),
        portfolio_nav=nav,
        starting_positions=starting,
        starting_cash=cash,
        account_id_hash=normalized_account_id_hash,
        risk_state=_freeze(_as_plain(risk_state)),
        sell_orders=sells,
        buy_orders=buys,
        expected_posttrade_positions=expected,
        expected_posttrade_cash=expected_cash,
        constraints=_freeze(constraint_values),
        authorization_state=_freeze(auth),
        plan_id=plan_id,
        starting_state_hash=compute_starting_state_hash(starting, cash),
        content_hash="",
        strategy_id=normalized_strategy_id,
    )
    return ExactExecutionPlan(**{**plan.__dict__, "content_hash": _hash(plan.to_dict(include_hash=False))})


def exact_execution_plan_from_dict(
    payload: Mapping[str, Any],
    *,
    expected_plan_id: str | None = None,
    expected_run_id: str | None = None,
    expected_account_scope: str = "PAPER",
    require_authorized: bool = True,
) -> ExactExecutionPlan:
    unknown_fields = sorted(set(payload) - _EXACT_PLAN_TOP_LEVEL_FIELDS)
    if unknown_fields:
        raise AuthorityContractError(
            "exact execution plan contains unknown top-level fields: "
            + ", ".join(unknown_fields)
        )
    if str(payload.get("schema_version") or "") != EXACT_EXECUTION_SCHEMA_VERSION:
        raise AuthorityContractError("unsupported exact execution plan schema")
    supplied_order_ids: set[str] = set()
    supplied_client_ids: set[str] = set()
    for list_name, required_side in (("sell_orders", "SELL"), ("buy_orders", "BUY")):
        raw_orders = payload.get(list_name)
        if not isinstance(raw_orders, list):
            raise AuthorityContractError(f"exact execution {list_name} must be a list")
        for raw in raw_orders:
            if not isinstance(raw, Mapping):
                raise AuthorityContractError("exact execution order rows must be objects")
            order_id = str(raw.get("order_id") or "").strip()
            client_id = str(raw.get("client_order_id") or "").strip()
            side = str(raw.get("side") or "").strip().upper()
            if not order_id or not client_id:
                raise AuthorityContractError("serialized exact order IDs are required")
            if side != required_side:
                raise AuthorityContractError(f"{list_name} contains non-{required_side} order")
            if order_id in supplied_order_ids or client_id in supplied_client_ids:
                raise AuthorityContractError("serialized exact order IDs must be unique")
            supplied_order_ids.add(order_id)
            supplied_client_ids.add(client_id)
    rebuilt = build_exact_execution_plan(
        run_id=str(payload.get("run_id") or ""),
        as_of=str(payload.get("as_of") or ""),
        created_at=str(payload.get("created_at") or ""),
        orchestrator_version=str(payload.get("orchestrator_version") or ""),
        source_precompute_ids=payload.get("source_precompute_ids") or (),
        source_artifact_hashes=payload.get("source_artifact_hashes") or {},
        market_state_id=str(payload.get("market_state_id") or ""),
        market_state=payload.get("market_state") or {},
        regime_state=payload.get("regime_state") or {},
        sleeve_allocations=payload.get("sleeve_allocations") or (),
        portfolio_nav=payload.get("portfolio_nav"),
        starting_positions=payload.get("starting_positions") or (),
        starting_cash=payload.get("starting_cash"),
        account_id_hash=str(payload.get("account_id_hash") or ""),
        risk_state=payload.get("risk_state") or {},
        sell_orders=payload.get("sell_orders") or (),
        buy_orders=payload.get("buy_orders") or (),
        expected_posttrade_positions=payload.get("expected_posttrade_positions") or (),
        expected_posttrade_cash=payload.get("expected_posttrade_cash"),
        constraints=payload.get("constraints") or {},
        authorization_state=payload.get("authorization_state") or {},
        strategy_id=str(payload.get("strategy_id") or ""),
        account_scope=str(payload.get("account_scope") or ""),
        allow_legacy_missing_fill_risk_authority=(
            bool(supplied_order_ids)
            and "max_adverse_fill_slippage_bps"
            not in (payload.get("constraints") or {})
        ),
        # Serialized plans are immutable historical evidence. Their internal
        # strategy/allocation consistency and content hash are revalidated, but
        # a later governed allocation-policy change must not invalidate history.
        validate_current_allocator=False,
    )
    if str(payload.get("plan_id") or "") != rebuilt.plan_id:
        raise AuthorityContractError("exact execution plan_id mismatch")
    if str(payload.get("starting_state_hash") or "") != rebuilt.starting_state_hash:
        raise AuthorityContractError("exact execution starting_state_hash mismatch")
    if str(payload.get("content_hash") or "") != rebuilt.content_hash:
        raise AuthorityContractError("exact execution content_hash mismatch")
    supplied_orders = [
        *list(payload.get("sell_orders") or []),
        *list(payload.get("buy_orders") or []),
    ]
    for supplied, regenerated in zip(supplied_orders, rebuilt.orders, strict=True):
        if str(supplied.get("order_id")) != str(regenerated.get("order_id")):
            raise AuthorityContractError("serialized exact order_id mismatch")
        if str(supplied.get("client_order_id")) != str(regenerated.get("client_order_id")):
            raise AuthorityContractError("serialized exact client_order_id mismatch")
    if expected_plan_id is not None and rebuilt.plan_id != expected_plan_id:
        raise AuthorityContractError("executor received the wrong exact plan_id")
    if expected_run_id is not None and rebuilt.run_id != expected_run_id:
        raise AuthorityContractError("executor received the wrong exact run_id")
    if rebuilt.account_scope != str(expected_account_scope).strip().upper():
        raise AuthorityContractError("exact execution account_scope mismatch")
    if require_authorized and str(rebuilt.authorization_state.get("status")) != "AUTHORIZED":
        raise AuthorityContractError("exact execution authorization is missing")
    if require_authorized and str(
        rebuilt.authorization_state.get("authority") or ""
    ).strip().upper() != "CAERUS_ORCHESTRATOR":
        raise AuthorityContractError("exact execution authorization authority is invalid")
    regime_state = _as_plain(rebuilt.regime_state)
    if require_authorized:
        from core.regime_state_store import REGIME_EVENT_SCHEMA_VERSION

        if regime_state.get("state_schema_version") != REGIME_EVENT_SCHEMA_VERSION:
            raise AuthorityContractError(
                "exact execution requires committed regime authority schema"
            )
        if (
            regime_state.get("state_committed_at_evaluation") is not True
            or regime_state.get("state_commit_required_before_pointer") is not False
        ):
            raise AuthorityContractError(
                "exact execution regime authority is not durably committed"
            )
        event_path = Path(str(regime_state.get("state_event_path") or ""))
        if not event_path.is_file():
            raise AuthorityContractError(
                "exact execution committed regime event is missing"
            )
        try:
            from core.regime_state_store import (
                RegimeAuthorityEvent,
                RegimePersistenceResult,
            )

            event_payload = json.loads(event_path.read_text(encoding="utf-8"))
            event = RegimeAuthorityEvent.from_dict(event_payload)
        except Exception as exc:
            raise AuthorityContractError(
                f"exact execution committed regime event is invalid: {exc}"
            ) from exc
        expected_regime_state = RegimePersistenceResult(
            event=event,
            event_path=event_path,
            created=False,
            committed=True,
        ).regime_state()
        if regime_state != expected_regime_state:
            raise AuthorityContractError(
                "exact execution regime authority differs from committed event"
            )
        if (
            event.account_scope != rebuilt.account_scope
            or event.account_id != rebuilt.account_id_hash
            or event.sleeve_id != rebuilt.strategy_id
            or event.trade_date != rebuilt.trade_date
        ):
            raise AuthorityContractError(
                "exact execution regime authority identity scope mismatch"
            )
    return rebuilt


def validate_exact_execution_plan(payload: Mapping[str, Any], **kwargs: Any) -> ExactExecutionPlan:
    return exact_execution_plan_from_dict(payload, **kwargs)
