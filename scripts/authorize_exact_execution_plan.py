"""Publish the one broker-state-bound exact order plan for the PAPER lane.

The precompute and target builder remain evidence/portfolio-construction workers.
This authorizer takes a fresh broker snapshot, applies deterministic risk and
capital constraints once, and seals the resulting orders into v3.  The executor
may not repeat any of this work.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import sys
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from authority.exact_plan import (
    build_exact_execution_plan,
    compute_starting_state_hash,
    exact_execution_plan_from_dict,
)
from authority.pipeline import validate_persisted_authority_chain
from brokers.alpaca_broker import AlpacaBroker
from core.broker_retry_policy import is_retryable_broker_read_error
from core.economic_reconciliation import DEFAULT_MARK_TIMING_TOLERANCE_BPS
from core.live_pilot_guardrails import resolve_dynamic_cap
from core.precompute_bundle_validation import validate_sleeve_evaluation_payload
from core.paper_drill_epoch import scoped_wal_root, validate_drill_epoch
from core.regime_state_store import (
    RegimeAuthorityEvent,
    RegimePersistenceResult,
    commit_prepared_regime_authority,
    persist_regime_authority,
    prepare_regime_authority,
)
from core.submission_wal import (
    OrderIntent,
    unresolved_foreign_intent_client_ids,
)
from execution.core import (
    apply_capital_budget_and_execution_filter,
    compute_transition_trades,
    live_pilot_execution_config,
)
from paper.trading_calendar import ET_TZ, market_session_status
from paper.run_manager import safe_write_text
from scripts.live_pilot_execute import (
    _broker_snapshot,
    _build_core_request,
    _core_rows_from_frame,
    _finite_float,
    _settled_cash_context,
)


_SESSION_FINAL_BAR_PRICE_BASIS = (
    "alpaca_iex_regular_session_final_minute_bar_close"
)
_BROKER_AUTHORITATIVE_PRICE_BASES = {
    "timestamped_alpaca_latest_trade_at_authorization",
    _SESSION_FINAL_BAR_PRICE_BASIS,
}
MAX_ADVERSE_FILL_SLIPPAGE_BPS = 100.0


def _protective_day_limit_orders(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert broker-ready rows to sealed worst-price DAY limit orders.

    A market order can spend beyond an immutable capital cap before any
    post-fill reconciliation can react.  The collar makes that risk
    preventive: BUY prices round down and SELL prices round up so tick
    normalization never widens the governed one-percent boundary.
    """

    result: list[dict[str, Any]] = []
    collar_fraction = Decimal(str(MAX_ADVERSE_FILL_SLIPPAGE_BPS)) / Decimal(
        "10000"
    )
    for raw in rows:
        row = dict(raw)
        side = str(row.get("side") or "").strip().upper()
        quantity = _finite_float(
            row.get("quantity", row.get("shares", row.get("qty")))
        )
        reference = _finite_float(
            row.get("expected_price", row.get("price"))
        )
        if side not in {"BUY", "SELL"} or quantity is None or quantity <= 0:
            raise RuntimeError("exact order cannot be price-collared")
        if reference is None or reference <= 0:
            raise RuntimeError("exact order lacks a positive collar reference price")
        reference_decimal = Decimal(str(reference))
        raw_collar = reference_decimal * (
            Decimal("1") + collar_fraction
            if side == "BUY"
            else Decimal("1") - collar_fraction
        )
        tick = Decimal("0.01") if raw_collar >= Decimal("1") else Decimal("0.0001")
        collar = raw_collar.quantize(
            tick,
            rounding=ROUND_DOWN if side == "BUY" else ROUND_UP,
        )
        if collar <= 0:
            raise RuntimeError("exact order protective limit is non-positive")
        collar_float = float(collar)
        row.update(
            {
                "order_type": "limit",
                # DAY limit is supported by Alpaca for both whole and
                # fractional equity quantities. IOC availability is account-
                # dependent and therefore cannot be assumed by authorization.
                "time_in_force": "day",
                "extended_hours": False,
                "expected_price": float(reference),
                "limit_price": collar_float,
                "cap_enforcement_price": collar_float,
                "notional": float(quantity) * collar_float,
                "max_adverse_fill_slippage_bps": (
                    MAX_ADVERSE_FILL_SLIPPAGE_BPS
                ),
            }
        )
        result.append(row)
    return result


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _regime_state_root(
    *,
    plan_path: Path | None,
    env: Mapping[str, str],
) -> Path:
    configured = str(env.get("CAERUS_REGIME_AUTHORITY_STATE_ROOT") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else REPO_ROOT / path
    if plan_path is not None:
        plan_parent = plan_path.resolve().parent
        if plan_parent.name == "plans":
            return plan_parent.parent / "state" / "regime_authority"
        return plan_parent / "regime_authority_state"
    return REPO_ROOT / "outputs" / "paper_lane" / "state" / "regime_authority"


def _bool_value(value: Any, *, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off", ""}:
        return False
    raise RuntimeError(f"{label} must be boolean")


def _first_present(mappings: list[Mapping[str, Any]], keys: tuple[str, ...]) -> Any:
    for mapping in mappings:
        for key in keys:
            if key in mapping and mapping.get(key) not in (None, ""):
                return mapping.get(key)
    return None


def _governed_regime_inputs(
    *,
    plan: Mapping[str, Any],
    risk_controls: Mapping[str, Any],
    risk_package_id: str,
) -> tuple[str, float, bool, str]:
    """Resolve regime inputs only from the hash-validated Risk authority.

    Risk may carry a nested ``regime_authority``/``market_state`` object or the
    established flat fields.  Mutable outer-plan aliases are accepted only as
    exact redundant copies; outer counters and previous state are forbidden
    because the append-only store owns them.
    """

    regime_authority = (
        dict(risk_controls.get("regime_authority"))
        if isinstance(risk_controls.get("regime_authority"), Mapping)
        else {}
    )
    market_state = (
        dict(risk_controls.get("market_state"))
        if isinstance(risk_controls.get("market_state"), Mapping)
        else {}
    )
    sources = [regime_authority, market_state, risk_controls]
    governed_observation = _first_present(
        sources,
        ("observed_state", "observed_regime", "composite_regime", "regime"),
    )
    observed_state = str(governed_observation or "UNKNOWN").strip().upper()
    governed_confidence = _first_present(
        sources,
        ("confidence", "regime_confidence"),
    )
    confidence = float(
        governed_confidence
        if governed_confidence is not None
        else (1.0 if governed_observation is not None else 0.0)
    )
    metrics = (
        dict(risk_controls.get("metrics"))
        if isinstance(risk_controls.get("metrics"), Mapping)
        else {}
    )
    nested_acute = _first_present(
        [regime_authority, market_state],
        ("acute_risk", "emergency_risk_response", "risk_veto_buys"),
    )
    acute_risk = (
        _bool_value(risk_controls.get("blocked"), label="Risk blocked")
        or _bool_value(
            metrics.get("circuit_breaker_triggered"),
            label="Risk circuit breaker",
        )
        or (
            _bool_value(nested_acute, label="governed acute risk")
            if nested_acute is not None
            else False
        )
    )
    governed_market_state_id = _first_present(
        [regime_authority, market_state, risk_controls],
        ("market_state_id", "state_id"),
    )
    if governed_market_state_id is None:
        raise RuntimeError(
            "persisted Risk authority requires a stable market_state_id source bar"
        )
    market_state_id = str(governed_market_state_id).strip()

    outer_observation = _first_present(
        [plan],
        ("observed_regime", "regime"),
    )
    if outer_observation is not None:
        if governed_observation is None or str(outer_observation).strip().upper() != observed_state:
            raise RuntimeError("mutable outer regime observation diverges from persisted Risk authority")
    if plan.get("regime_confidence") is not None:
        if governed_confidence is None or float(plan["regime_confidence"]) != confidence:
            raise RuntimeError("mutable outer regime confidence diverges from persisted Risk authority")
    if any(
        plan.get(key) is not None
        for key in (
            "previous_regime",
            "regime_bars_in_state",
            "regime_consecutive_observations",
        )
    ):
        raise RuntimeError("mutable outer regime persistence state is forbidden")
    if plan.get("emergency_risk_response") is not None and _bool_value(
        plan.get("emergency_risk_response"),
        label="outer emergency risk response",
    ) is not acute_risk:
        raise RuntimeError("mutable outer acute-risk claim diverges from persisted Risk authority")
    return observed_state, confidence, acute_risk, market_state_id


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _fresh_market_prices(
    *,
    broker: Any,
    symbols: list[str],
    as_of: str,
    env: Mapping[str, str],
    session_open_et: dt.datetime,
    session_close_et: dt.datetime,
) -> tuple[dict[str, float], dict[str, Any]]:
    getter = getattr(broker, "get_latest_trades", None)
    if not callable(getter):
        raise RuntimeError("broker lacks timestamped latest-trade reads for final Decision")
    unique = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    rows = getter(unique)
    if not isinstance(rows, Mapping):
        raise RuntimeError("latest-trade response is malformed")
    try:
        decision_time = dt.datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if decision_time.tzinfo is None:
            raise ValueError("timestamp lacks timezone")
        max_age_seconds = float(env.get("CAERUS_AUTHORIZATION_QUOTE_MAX_AGE_SECONDS") or 120)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid final-Decision quote freshness policy") from exc
    if max_age_seconds <= 0:
        raise RuntimeError("final-Decision quote freshness policy must be positive")
    prices: dict[str, float] = {}
    evidence_rows: list[dict[str, Any]] = []
    for symbol in unique:
        raw = rows.get(symbol)
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"latest trade missing for {symbol}")
        provider_symbol = str(raw.get("symbol") or "").strip().upper()
        if provider_symbol != symbol:
            raise RuntimeError(f"latest trade symbol mismatch for {symbol}")
        price = _finite_float(raw.get("price"))
        timestamp_raw = str(raw.get("timestamp") or "").strip()
        try:
            timestamp = dt.datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("timestamp lacks timezone")
        except ValueError as exc:
            raise RuntimeError(f"latest trade timestamp invalid for {symbol}") from exc
        age = (decision_time.astimezone(dt.timezone.utc) - timestamp.astimezone(dt.timezone.utc)).total_seconds()
        timestamp_et = timestamp.astimezone(ET_TZ)
        if (
            price is None
            or price <= 0
            or age < -5.0
            or age > max_age_seconds
            or timestamp_et < session_open_et
            or timestamp_et >= session_close_et
        ):
            raise RuntimeError(f"latest trade is missing, invalid, or stale for {symbol}")
        prices[symbol] = float(price)
        evidence_rows.append(
            {
                "symbol": symbol,
                "price": float(price),
                "timestamp": timestamp.isoformat(),
                "age_seconds": age,
                "feed": str(raw.get("feed") or "UNKNOWN"),
            }
        )
    evidence = {
        "schema_version": "caerus.authorization_market_state.v1",
        "captured_at": str(as_of),
        "price_as_of": str(as_of),
        "pricing_basis": "timestamped_alpaca_latest_trade_at_authorization",
        "market_closed_at_authorization": False,
        "new_order_submission_allowed_at_authorization": True,
        "freshness_reference": "authorization_wall_clock",
        "max_age_seconds": max_age_seconds,
        "session_open_et": session_open_et.isoformat(),
        "session_close_et": session_close_et.isoformat(),
        "quotes": evidence_rows,
    }
    evidence["content_hash"] = _canonical_hash(evidence)
    return prices, evidence


def _revalidate_open_market_prices_at_seal(
    *,
    market_state_evidence: Mapping[str, Any],
    required_symbols: list[str],
    seal_time: dt.datetime,
    session_open_et: dt.datetime,
    session_close_et: dt.datetime,
) -> dict[str, Any]:
    """Recheck quote freshness at the instant authority becomes immutable.

    Quote freshness at capture is necessary but not sufficient: validation,
    planning, or persistence work can stall while the market remains open.  A
    plan may be sealed only if every governed quote is still within the same
    strict age/skew/session bounds at the final authorization timestamp.
    """

    if (
        str(market_state_evidence.get("pricing_basis") or "")
        != "timestamped_alpaca_latest_trade_at_authorization"
    ):
        raise RuntimeError("open-session pricing evidence basis is invalid")
    max_age_seconds = _finite_float(market_state_evidence.get("max_age_seconds"))
    if max_age_seconds is None or max_age_seconds <= 0:
        raise RuntimeError("open-session quote freshness evidence is invalid")
    raw_quotes = market_state_evidence.get("quotes")
    if not isinstance(raw_quotes, list):
        raise RuntimeError("open-session quote evidence is malformed")
    expected_symbols = {
        str(symbol).strip().upper()
        for symbol in required_symbols
        if str(symbol).strip()
    }
    observed_symbols: set[str] = set()
    sealed_quotes: list[dict[str, Any]] = []
    for raw in raw_quotes:
        if not isinstance(raw, Mapping):
            raise RuntimeError("open-session quote evidence row is malformed")
        symbol = str(raw.get("symbol") or "").strip().upper()
        if not symbol or symbol in observed_symbols:
            raise RuntimeError("open-session quote evidence symbols are invalid")
        try:
            timestamp = dt.datetime.fromisoformat(
                str(raw.get("timestamp") or "").replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise RuntimeError(
                f"open-session quote timestamp is invalid for {symbol}"
            ) from exc
        if timestamp.tzinfo is None:
            raise RuntimeError(
                f"open-session quote timestamp lacks timezone for {symbol}"
            )
        timestamp_et = timestamp.astimezone(ET_TZ)
        age_at_seal = (
            seal_time.astimezone(dt.timezone.utc)
            - timestamp.astimezone(dt.timezone.utc)
        ).total_seconds()
        if (
            age_at_seal < -5.0
            or age_at_seal > max_age_seconds
            or timestamp_et < session_open_et
            or timestamp_et >= session_close_et
        ):
            raise RuntimeError(
                f"latest trade became invalid or stale before authorization seal for {symbol}"
            )
        observed_symbols.add(symbol)
        sealed_quotes.append(
            {**dict(raw), "age_at_authorization_seal_seconds": age_at_seal}
        )
    if observed_symbols != expected_symbols:
        raise RuntimeError(
            "open-session quote evidence does not cover every Decision symbol"
        )
    result = dict(market_state_evidence)
    result.pop("content_hash", None)
    result["quotes"] = sealed_quotes
    result["freshness_revalidated_at_seal"] = True
    return result


def _verified_market_session(
    *,
    broker: Any,
    trade_date: str,
    authorized_at: str,
) -> tuple[Any, dt.datetime, dict[str, Any]]:
    try:
        decision_time = dt.datetime.fromisoformat(
            str(authorized_at).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RuntimeError("authorization timestamp is invalid") from exc
    if decision_time.tzinfo is None:
        raise RuntimeError("authorization timestamp lacks timezone")
    decision_et = decision_time.astimezone(ET_TZ)
    status = market_session_status(trade_date, decision_et, "16:00")
    if status.reason not in {"MARKET_OPEN", "AFTER_MARKET_CUTOFF"}:
        raise RuntimeError(
            f"exact authorization market session is unavailable: {status.reason}"
        )
    if status.session_open_et is None or status.session_close_et is None:
        raise RuntimeError("exact authorization market session bounds are missing")

    # Alpaca's date-specific session is required for every Decision, not just
    # after close. This prevents an unrecognized early close from being treated
    # as open by a stale local calendar. Any disagreement fails closed.
    getter = getattr(broker, "get_market_session_calendar", None)
    if not callable(getter):
        raise RuntimeError(
            "broker lacks authoritative market-calendar reads for exact Decision"
        )
    broker_session = getter(trade_date)
    if not isinstance(broker_session, Mapping):
        raise RuntimeError("broker market-calendar response is malformed")
    try:
        broker_open = dt.datetime.fromisoformat(
            str(broker_session.get("session_open_et") or "").replace(
                "Z", "+00:00"
            )
        )
        broker_close = dt.datetime.fromisoformat(
            str(broker_session.get("session_close_et") or "").replace(
                "Z", "+00:00"
            )
        )
    except ValueError as exc:
        raise RuntimeError("broker market-calendar bounds are invalid") from exc
    if broker_open.tzinfo is None or broker_close.tzinfo is None:
        raise RuntimeError("broker market-calendar bounds lack timezone")
    if (
        str(broker_session.get("trade_date") or "") != trade_date
        or broker_open.astimezone(ET_TZ) != status.session_open_et
        or broker_close.astimezone(ET_TZ) != status.session_close_et
    ):
        raise RuntimeError(
            "broker market calendar disagrees with the governed XNYS session"
        )
    broker_session_evidence = {
        "source": str(broker_session.get("calendar") or "Alpaca"),
        "trade_date": trade_date,
        "session_open_et": broker_open.astimezone(ET_TZ).isoformat(),
        "session_close_et": broker_close.astimezone(ET_TZ).isoformat(),
        "cross_check": "MATCH",
    }
    return status, decision_et, broker_session_evidence


def _session_final_bar_prices(
    *,
    broker: Any,
    symbols: list[str],
    authorized_at: str,
    session_open_et: dt.datetime,
    session_close_et: dt.datetime,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Validate final regular-session minute-bar marks against the official close.

    The ordinary 120-second wall-clock freshness rule remains unchanged while
    the market is open. After the same day's close, only the provider-formed
    close of the exact final regular-session minute is accepted. Extended-hours,
    previous-session, missing, and earlier-minute bars remain invalid.
    """

    getter = getattr(broker, "get_session_final_bars", None)
    if not callable(getter):
        raise RuntimeError(
            "broker lacks regular-session-final minute-bar reads for closed-session Decision"
        )
    unique = sorted(
        {
            str(symbol).strip().upper()
            for symbol in symbols
            if str(symbol).strip()
        }
    )
    rows = getter(
        unique,
        session_open_et=session_open_et,
        session_close_et=session_close_et,
    )
    if not isinstance(rows, Mapping):
        raise RuntimeError("session-final-bar response is malformed")
    returned_keys = {
        str(symbol).strip().upper()
        for symbol in rows
        if str(symbol).strip()
    }
    if returned_keys != set(unique):
        raise RuntimeError(
            "session-final bar symbol set does not exactly match Decision symbols"
        )
    decision_time = dt.datetime.fromisoformat(
        str(authorized_at).replace("Z", "+00:00")
    )
    if decision_time.tzinfo is None:
        raise RuntimeError("closed-session Decision timestamp lacks timezone")
    expected_bar_start = session_close_et - dt.timedelta(minutes=1)
    if decision_time.astimezone(ET_TZ) < session_close_et:
        raise RuntimeError("closed-session Decision precedes the official close")

    prices: dict[str, float] = {}
    evidence_rows: list[dict[str, Any]] = []
    for symbol in unique:
        raw = rows.get(symbol)
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"session-final bar missing for {symbol}")
        provider_symbol = str(raw.get("symbol") or "").strip().upper()
        if provider_symbol != symbol:
            raise RuntimeError(f"session-final bar symbol mismatch for {symbol}")
        price = _finite_float(raw.get("close", raw.get("price")))
        timestamp_raw = str(raw.get("bar_start") or "").strip()
        try:
            timestamp = dt.datetime.fromisoformat(
                timestamp_raw.replace("Z", "+00:00")
            )
            if timestamp.tzinfo is None:
                raise ValueError("timestamp lacks timezone")
        except ValueError as exc:
            raise RuntimeError(
                f"session-final bar timestamp invalid for {symbol}"
            ) from exc
        timestamp_et = timestamp.astimezone(ET_TZ)
        wall_age = (
            decision_time.astimezone(dt.timezone.utc)
            - session_close_et.astimezone(dt.timezone.utc)
        ).total_seconds()
        open_price = _finite_float(raw.get("open"))
        high_price = _finite_float(raw.get("high"))
        low_price = _finite_float(raw.get("low"))
        volume = _finite_float(raw.get("volume"))
        trade_count = _finite_float(raw.get("trade_count"))
        vwap = _finite_float(raw.get("vwap"))
        if (
            price is None
            or price <= 0.0
            or open_price is None
            or open_price <= 0.0
            or high_price is None
            or high_price <= 0.0
            or low_price is None
            or low_price <= 0.0
            or high_price < max(open_price, price, low_price)
            or low_price > min(open_price, price, high_price)
            or volume is None
            or volume <= 0.0
            or trade_count is None
            or trade_count <= 0.0
            or vwap is None
            or vwap <= 0.0
            or timestamp_et != expected_bar_start
            or str(raw.get("bar_end_exclusive") or "")
            != session_close_et.isoformat()
            or str(raw.get("timeframe") or "") != "1Min"
            or str(raw.get("feed") or "").upper() != "IEX"
            or str(raw.get("adjustment") or "").lower() != "raw"
            or str(raw.get("currency") or "").upper() != "USD"
            or wall_age < -5.0
        ):
            raise RuntimeError(
                f"session-final bar is missing, invalid, or outside final minute for {symbol}"
            )
        prices[symbol] = float(price)
        evidence_rows.append(
            {
                "symbol": symbol,
                "price": float(price),
                "close": float(price),
                "bar_start": timestamp.isoformat(),
                "bar_end_exclusive": session_close_et.isoformat(),
                "mark_interval_completed_at": session_close_et.isoformat(),
                "wall_age_since_completed_interval_seconds": wall_age,
                "timeframe": "1Min",
                "feed": "IEX",
                "adjustment": "raw",
                "currency": "USD",
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "volume": volume,
                "trade_count": trade_count,
                "vwap": vwap,
            }
        )
    evidence = {
        "schema_version": "caerus.authorization_market_state.v1",
        "captured_at": str(authorized_at),
        "price_as_of": session_close_et.isoformat(),
        "pricing_basis": _SESSION_FINAL_BAR_PRICE_BASIS,
        "market_closed_at_authorization": True,
        "new_order_submission_allowed_at_authorization": False,
        "freshness_reference": "completed_official_regular_session_final_minute",
        "calendar": "XNYS",
        "session_date": session_close_et.date().isoformat(),
        "session_open_et": session_open_et.isoformat(),
        "session_close_et": session_close_et.isoformat(),
        "query_start_et": expected_bar_start.isoformat(),
        "query_end_exclusive_et": session_close_et.isoformat(),
        "requested_symbols": unique,
        "returned_symbols": sorted(prices),
        "quotes": evidence_rows,
    }
    evidence["content_hash"] = _canonical_hash(evidence)
    return prices, evidence


def _rebase_request_to_authoritative_prices(
    *,
    request: Any,
    prices: Mapping[str, float],
    broker_cash: float,
    broker_reported_nav: float,
    planning_cap: float | None,
    price_basis: str,
    required_symbols: list[str],
) -> tuple[Any, dict[str, Any]]:
    """Put pricing, NAV, holdings, and target sizing on one Decision window."""

    if price_basis not in _BROKER_AUTHORITATIVE_PRICE_BASES:
        raise RuntimeError("unsupported broker-authoritative price basis")
    authoritative_prices = request.prices.iloc[0:0].copy()
    normalized_required = sorted(
        {
            str(symbol).strip().upper()
            for symbol in required_symbols
            if str(symbol).strip()
        }
    )
    for normalized in normalized_required:
        price = _finite_float(prices.get(normalized))
        if price is None or price <= 0.0:
            raise RuntimeError(
                f"broker-authoritative Decision price missing for {normalized or 'UNKNOWN'}"
            )
        authoritative_prices.loc[normalized] = float(price)

    position_value = 0.0
    if request.holdings is not None and not request.holdings.empty:
        for _, row in request.holdings.iterrows():
            symbol = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
            quantity = _finite_float(row.get("shares", row.get("qty")))
            price = _finite_float(authoritative_prices.get(symbol))
            if (
                not symbol
                or quantity is None
                or quantity < 0.0
                or price is None
                or price <= 0.0
            ):
                raise RuntimeError(
                    f"same-window NAV reconstruction failed for {symbol or 'UNKNOWN'}"
                )
            position_value += float(quantity) * float(price)

    reconstructed_nav = float(broker_cash) + position_value
    if not math.isfinite(reconstructed_nav) or reconstructed_nav <= 0.0:
        raise RuntimeError("broker-authoritative reconstructed NAV is invalid")
    planning_equity = reconstructed_nav
    if planning_cap is not None and planning_cap > 0.0:
        planning_equity = min(planning_equity, float(planning_cap))
    planning_cash = min(float(broker_cash), planning_equity)
    planning_account = dict(request.planning_account)
    planning_account.update(
        {
            "cash": str(planning_cash),
            "equity": str(planning_equity),
            "portfolio_value": str(planning_equity),
        }
    )
    rebased = dataclasses.replace(
        request,
        prices=authoritative_prices,
        total_equity=float(planning_equity),
        starting_cash=float(planning_cash),
        planning_account=planning_account,
        price_basis=price_basis,
    )
    return rebased, {
        "broker_reported_nav": float(broker_reported_nav),
        "authoritative_position_value": position_value,
        "authoritative_account_nav": reconstructed_nav,
        "broker_reported_to_authoritative_nav_delta": (
            reconstructed_nav - float(broker_reported_nav)
        ),
        "planning_equity": float(planning_equity),
        "planning_equity_cap": (
            float(planning_cap)
            if planning_cap is not None and planning_cap > 0.0
            else None
        ),
        "planning_cash": float(planning_cash),
        "price_basis": price_basis,
        "priced_symbols": normalized_required,
        "current_post_snapshot_quantities_and_cash_marked_at_price_as_of": True,
    }


def _recover_existing_authority_for_wal(
    *, latest_pointer: Path, trade_date: str, wal_intents: Path
) -> Path | None:
    """Resolve the immutable authority artifact bound to durable WAL intents.

    Once any broker intent exists, fresh authorization is forbidden even when
    the mutable latest pointer was lost. Recovery is by the immutable plan ID
    and hash carried in the WAL itself.
    """
    intent_paths = sorted(wal_intents.glob("*.json")) if wal_intents.exists() else []
    if not intent_paths:
        return None
    identities: set[tuple[str, str]] = set()
    for path in intent_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        intent = OrderIntent.from_dict(payload)
        if intent.trade_date != trade_date:
            raise RuntimeError("submission WAL trade date does not match authorization date")
        identities.add((intent.plan_id, intent.plan_hash))
    if len(identities) != 1:
        raise RuntimeError("submission WAL contains multiple or mismatched exact plans")
    expected_plan_id, expected_plan_hash = next(iter(identities))

    candidates: set[Path] = set()
    if latest_pointer.exists():
        try:
            pointer = json.loads(latest_pointer.read_text(encoding="utf-8"))
            raw_path = Path(str(pointer.get("json_path") or ""))
            if raw_path and not raw_path.is_absolute():
                raw_path = latest_pointer.parent / raw_path
            if raw_path.is_file():
                candidates.add(raw_path.resolve())
        except (OSError, ValueError, TypeError):
            pass
    authority_dir = latest_pointer.parent / "authority" / trade_date
    if authority_dir.exists():
        candidates.update(path.resolve() for path in authority_dir.glob("*.json"))

    matches: list[Path] = []
    for path in sorted(candidates):
        try:
            handoff = json.loads(path.read_text(encoding="utf-8"))
            exact_payload = handoff.get("exact_execution_plan")
            if not isinstance(exact_payload, Mapping):
                continue
            exact = exact_execution_plan_from_dict(
                exact_payload,
                expected_plan_id=expected_plan_id,
                expected_account_scope="PAPER",
            )
            if exact.content_hash == expected_plan_hash:
                matches.append(path)
        except Exception:
            continue
    if len(matches) != 1:
        raise RuntimeError(
            "stable_wal_original_plan_recovery_unresolved: immutable authority artifact "
            f"matches={len(matches)}"
        )
    return matches[0]


def _quantity_positions(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in snapshot.get("positions") or []:
        if not isinstance(raw, Mapping):
            continue
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
        quantity = _finite_float(raw.get("qty", raw.get("quantity", raw.get("shares"))))
        if symbol and quantity is not None and quantity > 1e-12:
            rows.append({"symbol": symbol, "quantity": float(quantity)})
    return sorted(rows, key=lambda row: row["symbol"])


def _expected_state(
    *,
    positions: list[dict[str, Any]],
    cash: float,
    orders: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float]:
    quantity_by_symbol = {str(row["symbol"]): float(row["quantity"]) for row in positions}
    expected_cash = float(cash)
    for order in orders:
        symbol = str(order["symbol"])
        quantity = float(order.get("quantity", order.get("shares", order.get("qty"))) or 0.0)
        price = float(order.get("expected_price", order.get("price", order.get("limit_price"))) or 0.0)
        notional = float(order.get("notional") or quantity * price)
        if str(order["side"]).upper() == "SELL":
            quantity_by_symbol[symbol] = max(0.0, quantity_by_symbol.get(symbol, 0.0) - quantity)
            expected_cash += notional
        else:
            quantity_by_symbol[symbol] = quantity_by_symbol.get(symbol, 0.0) + quantity
            expected_cash -= notional
    expected_positions = [
        {"symbol": symbol, "quantity": quantity}
        for symbol, quantity in sorted(quantity_by_symbol.items())
        if quantity > 1e-12
    ]
    return expected_positions, max(0.0, expected_cash)


def _seal_regime_committed_handoff(
    prepared_handoff: Mapping[str, Any],
    committed_regime: RegimePersistenceResult,
    *,
    verify_event: bool,
) -> dict[str, Any]:
    """Re-seal a handoff with a committed view of its prepared event."""

    if not committed_regime.committed or (
        verify_event and not committed_regime.event_path.is_file()
    ):
        raise RuntimeError("regime authority event is not durably committed")
    metadata = prepared_handoff.get("regime_authority_event")
    exact_payload = prepared_handoff.get("exact_execution_plan")
    if not isinstance(metadata, Mapping) or not isinstance(exact_payload, Mapping):
        raise RuntimeError("prepared exact handoff lacks regime commit metadata")
    if (
        str(metadata.get("content_hash") or "")
        != committed_regime.event.content_hash
        or str(metadata.get("observation_id") or "")
        != committed_regime.event.observation_id
    ):
        raise RuntimeError("committed regime event differs from prepared handoff")

    source_hashes = dict(exact_payload.get("source_artifact_hashes") or {})
    finalized = build_exact_execution_plan(
        run_id=str(exact_payload.get("run_id") or ""),
        as_of=str(exact_payload.get("as_of") or ""),
        created_at=str(exact_payload.get("created_at") or ""),
        orchestrator_version=str(exact_payload.get("orchestrator_version") or ""),
        source_precompute_ids=exact_payload.get("source_precompute_ids") or (),
        source_artifact_hashes=source_hashes,
        market_state_id=str(exact_payload.get("market_state_id") or ""),
        market_state=exact_payload.get("market_state") or {},
        regime_state=committed_regime.regime_state(),
        sleeve_allocations=exact_payload.get("sleeve_allocations") or (),
        portfolio_nav=exact_payload.get("portfolio_nav"),
        starting_positions=exact_payload.get("starting_positions") or (),
        starting_cash=exact_payload.get("starting_cash"),
        account_id_hash=str(exact_payload.get("account_id_hash") or ""),
        risk_state=exact_payload.get("risk_state") or {},
        sell_orders=exact_payload.get("sell_orders") or (),
        buy_orders=exact_payload.get("buy_orders") or (),
        expected_posttrade_positions=(
            exact_payload.get("expected_posttrade_positions") or ()
        ),
        expected_posttrade_cash=exact_payload.get("expected_posttrade_cash"),
        constraints=exact_payload.get("constraints") or {},
        authorization_state=exact_payload.get("authorization_state") or {},
        strategy_id=str(exact_payload.get("strategy_id") or ""),
        account_scope=str(exact_payload.get("account_scope") or ""),
    )
    result = dict(prepared_handoff)
    result.update(
        {
            "exact_execution_plan": finalized.to_dict(),
            "exact_execution_plan_id": finalized.plan_id,
            "exact_execution_plan_hash": finalized.content_hash,
            "exact_execution_authority_run_id": finalized.run_id,
            "regime_authority_event": {
                "path": str(committed_regime.event_path),
                "content_hash": committed_regime.event.content_hash,
                "observation_id": committed_regime.event.observation_id,
                "sequence": committed_regime.event.sequence,
                "created": committed_regime.created,
                "committed_at_evaluation": True,
                "commit_required_before_pointer": False,
                "event": committed_regime.event.to_dict(),
            },
        }
    )
    if verify_event:
        exact_execution_plan_from_dict(result["exact_execution_plan"])
    return result


def finalize_regime_committed_handoff(
    prepared_handoff: Mapping[str, Any],
    committed_regime: RegimePersistenceResult,
) -> dict[str, Any]:
    """Re-seal an exact handoff only after its regime event is durable."""

    return _seal_regime_committed_handoff(
        prepared_handoff,
        committed_regime,
        verify_event=True,
    )


def authorize_exact_execution_plan(
    *,
    plan: Mapping[str, Any],
    broker: Any,
    env: Mapping[str, str],
    run_id: str,
    plan_path: Path | None = None,
    created_at: str | None = None,
    authorization_completed_at: str | None = None,
    regime_state_root: Path | None = None,
    drill_epoch: str | None = None,
    broker_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    trade_date = str(plan.get("trade_date") or "")
    if str(plan.get("execution_lane") or "").strip().lower() != "paper":
        raise RuntimeError("exact execution authorization is PAPER-lane only")
    from core.sleeve_control_plane import load_sleeve_control_registry

    control_registry = load_sleeve_control_registry()
    capital_ids = sorted(
        (control_registry.paper_allocation_policy.get("sleeve_risk_budgets") or {}).keys()
    )
    expected_approved_sleeve = (
        capital_ids[0] if len(capital_ids) == 1 else "caerus_paper_portfolio"
    )
    if str(plan.get("approved_sleeve") or "").strip().lower() != expected_approved_sleeve:
        raise RuntimeError(
            "plan sleeve identity differs from the governed PAPER allocator"
        )
    if not bool(getattr(broker, "paper", False)):
        raise RuntimeError("exact execution authorization requires a paper broker")
    sleeve_path_raw = str(plan.get("source_sleeve_evaluations") or "").strip()
    sleeve_hash = str(plan.get("source_sleeve_evaluations_sha256") or "").strip().lower()
    if not sleeve_path_raw or not sleeve_hash:
        raise RuntimeError("validated sleeve-evaluation authority lineage is required")
    sleeve_path = Path(sleeve_path_raw)
    if not sleeve_path.is_absolute():
        sleeve_path = REPO_ROOT / sleeve_path
    if not sleeve_path.is_file() or _hash_file(sleeve_path) != sleeve_hash:
        raise RuntimeError("sleeve-evaluation authority hash is missing or invalid")
    sleeve_payload = json.loads(sleeve_path.read_text(encoding="utf-8"))
    sleeve_failures = validate_sleeve_evaluation_payload(
        sleeve_payload, trade_date=str(plan.get("trade_date") or "")
    )
    if sleeve_failures:
        raise RuntimeError(
            "sleeve-evaluation authority semantic validation failed: "
            + ",".join(sleeve_failures[:5])
        )
    envelope_by_id = {
        str(row.get("sleeve_id") or ""): row
        for row in sleeve_payload.get("envelopes") or []
        if isinstance(row, Mapping)
    }
    for sleeve_id in capital_ids:
        envelope = envelope_by_id.get(sleeve_id) or {}
        if (
            (envelope.get("evaluation") or {}).get("status") != "OK"
            or (envelope.get("eligibility") or {}).get(
                "evaluation_usable_for_capital"
            )
            is not True
            or (envelope.get("opportunity") or {}).get("decision_eligible")
            is not True
        ):
            raise RuntimeError(
                f"capital sleeve is not Decision-eligible: {sleeve_id}"
            )
    embedded_execution = plan.get("approved_execution_package")
    authority_paths = plan.get("authority_package_paths")
    if not isinstance(embedded_execution, Mapping) or not isinstance(authority_paths, Mapping):
        raise RuntimeError("complete governed authority package chain is required")
    _evidence, _decision, _risk, governed_execution = validate_persisted_authority_chain(
        paths={str(key): str(value) for key, value in authority_paths.items()},
        embedded_execution=embedded_execution,
        trade_date=str(plan.get("trade_date") or ""),
        required_source_hash=sleeve_hash,
    )
    sealed_builder_plan = (
        str(plan.get("schema_version") or "")
        == "live_pilot_plan_from_precompute.v2"
    )
    declared_target_hash = str(plan.get("approved_target_hash") or "")
    if sealed_builder_plan and declared_target_hash != _decision.content_hash:
        raise RuntimeError("exact plan Decision diverges from sealed precompute target hash")
    if declared_target_hash and declared_target_hash != _decision.content_hash:
        raise RuntimeError("declared target hash diverges from persisted Decision")
    target_package_raw = str(plan.get("source_paper_target_package") or "").strip()
    target_package_hash = str(
        plan.get("source_paper_target_package_sha256") or ""
    ).strip().lower()
    if sealed_builder_plan and (not target_package_raw or not target_package_hash):
        raise RuntimeError("sealed precompute target package lineage is required")
    target_package_path: Path | None = None
    target_package_payload: dict[str, Any] | None = None
    portfolio_allocation_payload: dict[str, Any] | None = None
    operating_lineage_paths: dict[str, tuple[Path, str]] = {}
    if target_package_raw or target_package_hash:
        if not target_package_raw or not target_package_hash:
            raise RuntimeError("sealed precompute target package lineage is incomplete")
        target_package_path = Path(target_package_raw)
        if not target_package_path.is_absolute():
            target_package_path = REPO_ROOT / target_package_path
        if (
            not target_package_path.is_file()
            or _hash_file(target_package_path) != target_package_hash
        ):
            raise RuntimeError("sealed precompute target package hash is invalid")
        target_package_payload = json.loads(
            target_package_path.read_text(encoding="utf-8")
        )
        if target_package_payload.get("schema_version") == "caerus.paper_target_package.v2":
            for label in (
                "session_manifest",
                "sleeve_decisions",
                "portfolio_allocation",
            ):
                raw_path = str(plan.get(f"source_{label}") or "").strip()
                raw_hash = str(
                    plan.get(f"source_{label}_sha256") or ""
                ).strip().lower()
                if not raw_path or not raw_hash:
                    raise RuntimeError(
                        f"sealed {label} authority lineage is required"
                    )
                resolved_path = Path(raw_path)
                if not resolved_path.is_absolute():
                    # A sealed target can be replayed from an isolated repository
                    # root during certification.  Resolve its declared repo-relative
                    # lineage from the target package itself, not the authorizer's
                    # installed source tree.
                    target_repo_root = target_package_path.resolve().parents[3]
                    resolved_path = target_repo_root / resolved_path
                if (
                    not resolved_path.is_file()
                    or _hash_file(resolved_path) != raw_hash
                ):
                    raise RuntimeError(f"sealed {label} authority hash is invalid")
                operating_lineage_paths[label] = (resolved_path, raw_hash)
            portfolio_allocation_payload = json.loads(
                operating_lineage_paths["portfolio_allocation"][0].read_text(
                    encoding="utf-8"
                )
            )
            if (
                str(target_package_payload.get("allocation_id") or "")
                != str(portfolio_allocation_payload.get("allocation_id") or "")
                or str(target_package_payload.get("allocation_content_hash") or "")
                != str(portfolio_allocation_payload.get("content_hash") or "")
            ):
                raise RuntimeError(
                    "sealed target package diverges from portfolio allocation"
                )
            if (
                target_package_payload.get("target_rows")
                != portfolio_allocation_payload.get("targets")
            ):
                raise RuntimeError(
                    "sealed Decision target diverges from portfolio allocation"
                )
    governed_risk_controls = _plain(_risk.constraints)
    governed_outer_controls = dict(governed_risk_controls)
    governed_outer_controls.pop("target_attainment_policy", None)
    outer_risk_controls = (
        dict(plan.get("risk_controls"))
        if isinstance(plan.get("risk_controls"), Mapping)
        else {}
    )
    # market_state_id is consumed from the persisted, hash-validated Risk
    # package.  Legacy outer handoffs need not redundantly copy it, but when
    # they do carry it the value must still match exactly.
    comparable_governed = _plain(governed_outer_controls)
    for nested_key in ("regime_authority", "market_state"):
        governed_nested = comparable_governed.get(nested_key)
        outer_nested = outer_risk_controls.get(nested_key)
        if isinstance(governed_nested, dict) and not isinstance(outer_nested, Mapping):
            governed_nested.pop("market_state_id", None)
            governed_nested.pop("state_id", None)
            if not governed_nested:
                comparable_governed.pop(nested_key, None)
        elif isinstance(governed_nested, dict) and isinstance(outer_nested, Mapping):
            for identity_key in ("market_state_id", "state_id"):
                if identity_key not in outer_nested:
                    governed_nested.pop(identity_key, None)
    for identity_key in ("market_state_id", "state_id"):
        if identity_key not in outer_risk_controls:
            comparable_governed.pop(identity_key, None)
    if outer_risk_controls != comparable_governed:
        raise RuntimeError("outer risk_controls diverge from persisted Risk authority")
    snapshot = (
        dict(broker_snapshot)
        if isinstance(broker_snapshot, Mapping)
        else _broker_snapshot(broker, fail_on_open_order_lookup=True)
    )
    account = snapshot.get("account") if isinstance(snapshot.get("account"), Mapping) else {}
    open_orders = snapshot.get("open_orders")
    if not isinstance(open_orders, list):
        raise RuntimeError("fresh broker open-order snapshot is malformed")
    if open_orders:
        raise RuntimeError(
            "fresh broker snapshot contains unresolved open orders; exact authorization prohibited"
        )
    cash = _finite_float((account or {}).get("cash"))
    nav = _finite_float((account or {}).get("portfolio_value") or (account or {}).get("equity"))
    if cash is None or cash < 0 or nav is None or nav <= 0:
        raise RuntimeError("fresh broker cash/NAV is unavailable at Decision")

    planning_cap = _finite_float(env.get("CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP"))
    request, malformed = _build_core_request(
        pre_snapshot=snapshot,
        plan=plan,
        run_id=run_id,
        # Apply this only after every holding and target is repriced onto the
        # broker-authoritative Decision window below.
        planning_equity_cap=None,
    )
    if request is None or malformed:
        raise RuntimeError(f"fresh broker state cannot support exact planning: {malformed}")
    # Bind the Decision clock immediately before the final calendar/market-data
    # reads. Earlier authority-chain validation must not consume the quote-age
    # budget or make a newly captured trade look artificially future-dated.
    effective_authorized_at = created_at or _now()
    # Explicit timestamps are a hermetic replay/test clock. Snapshot reads are
    # real-time even in those fixtures, so project only their timing envelope
    # onto that injected Decision instant while preserving measured duration.
    # Production passes no ``created_at`` and retains the actual wall clock.
    if created_at is not None:
        raw_snapshot_started = dt.datetime.fromisoformat(
            str(snapshot.get("capture_started_at") or snapshot.get("captured_at") or "").replace(
                "Z", "+00:00"
            )
        )
        raw_snapshot_completed = dt.datetime.fromisoformat(
            str(snapshot.get("capture_completed_at") or snapshot.get("captured_at") or "").replace(
                "Z", "+00:00"
            )
        )
        injected_decision = dt.datetime.fromisoformat(
            str(effective_authorized_at).replace("Z", "+00:00")
        )
        if (
            raw_snapshot_started.tzinfo is None
            or raw_snapshot_completed.tzinfo is None
            or injected_decision.tzinfo is None
            or raw_snapshot_completed < raw_snapshot_started
        ):
            raise RuntimeError("fresh broker snapshot timing evidence is inconsistent")
        measured_duration = raw_snapshot_completed - raw_snapshot_started
        snapshot["capture_completed_at"] = injected_decision.isoformat()
        snapshot["captured_at"] = injected_decision.isoformat()
        snapshot["capture_started_at"] = (
            injected_decision - measured_duration
        ).isoformat()
    try:
        snapshot_started_at = dt.datetime.fromisoformat(
            str(snapshot.get("capture_started_at") or snapshot.get("captured_at") or "").replace(
                "Z", "+00:00"
            )
        )
        snapshot_completed_at = dt.datetime.fromisoformat(
            str(snapshot.get("capture_completed_at") or snapshot.get("captured_at") or "").replace(
                "Z", "+00:00"
            )
        )
        decision_capture_time = dt.datetime.fromisoformat(
            str(effective_authorized_at).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RuntimeError("fresh broker snapshot timing evidence is invalid") from exc
    if (
        snapshot_started_at.tzinfo is None
        or snapshot_completed_at.tzinfo is None
        or decision_capture_time.tzinfo is None
        or snapshot_completed_at < snapshot_started_at
        or snapshot_completed_at > decision_capture_time
    ):
        raise RuntimeError("fresh broker snapshot timing evidence is inconsistent")
    try:
        snapshot_max_age_seconds = float(
            env.get("CAERUS_AUTHORIZATION_SNAPSHOT_MAX_AGE_SECONDS") or 120
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid broker snapshot freshness policy") from exc
    snapshot_age_seconds = (
        decision_capture_time.astimezone(dt.timezone.utc)
        - snapshot_completed_at.astimezone(dt.timezone.utc)
    ).total_seconds()
    snapshot_capture_duration_seconds = (
        snapshot_completed_at.astimezone(dt.timezone.utc)
        - snapshot_started_at.astimezone(dt.timezone.utc)
    ).total_seconds()
    if (
        snapshot_max_age_seconds <= 0
        or snapshot_age_seconds < -5.0
        or snapshot_age_seconds > snapshot_max_age_seconds
        or snapshot_capture_duration_seconds > snapshot_max_age_seconds
    ):
        raise RuntimeError("fresh broker snapshot became stale before Decision")
    held_symbols = {
        str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        for row in snapshot.get("positions") or []
        if isinstance(row, Mapping)
        and str(row.get("symbol") or row.get("ticker") or "").strip()
    }
    governed_target_symbols = {
        str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        for row in governed_execution.approved_target_rows
        if str(row.get("symbol") or row.get("ticker") or "").strip()
    }
    request_target_symbols = {
        str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        for _, row in request.targets.iterrows()
        if str(row.get("ticker") or row.get("symbol") or "").strip()
    }
    if request_target_symbols != governed_target_symbols:
        raise RuntimeError(
            "governed target symbols could not be represented in exact planning"
        )
    decision_symbols = sorted(held_symbols | governed_target_symbols)
    session, decision_et, broker_session_evidence = _verified_market_session(
        broker=broker,
        trade_date=str(plan.get("trade_date") or ""),
        authorized_at=effective_authorized_at,
    )
    if session.reason == "AFTER_MARKET_CUTOFF":
        try:
            snapshot_captured_at = dt.datetime.fromisoformat(
                str(snapshot.get("captured_at") or "").replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise RuntimeError(
                "closed-session broker snapshot timestamp is invalid"
            ) from exc
        if (
            snapshot_captured_at.tzinfo is None
            or snapshot_captured_at.astimezone(ET_TZ) < session.session_close_et
        ):
            raise RuntimeError(
                "closed-session broker snapshot predates the official close"
            )
    if session.reason == "MARKET_OPEN":
        decision_prices, market_state_evidence = _fresh_market_prices(
            broker=broker,
            symbols=decision_symbols,
            as_of=effective_authorized_at,
            env=env,
            session_open_et=session.session_open_et,
            session_close_et=session.session_close_et,
        )
    else:
        decision_prices, market_state_evidence = _session_final_bar_prices(
            broker=broker,
            symbols=decision_symbols,
            authorized_at=effective_authorized_at,
            session_open_et=session.session_open_et,
            session_close_et=session.session_close_et,
        )
    price_basis = str(market_state_evidence.get("pricing_basis") or "")
    request, nav_evidence = _rebase_request_to_authoritative_prices(
        request=request,
        prices=decision_prices,
        broker_cash=float(cash),
        broker_reported_nav=float(nav),
        planning_cap=planning_cap,
        price_basis=price_basis,
        required_symbols=decision_symbols,
    )
    market_state_evidence = dict(market_state_evidence)
    market_state_evidence.pop("content_hash", None)
    market_state_evidence.update(
        {
            "session_reason": session.reason,
            "session_open_et": session.session_open_et.isoformat(),
            "session_close_et": session.session_close_et.isoformat(),
            "decision_time_et": decision_et.isoformat(),
            "broker_snapshot_captured_at": snapshot.get("captured_at"),
            "broker_snapshot_capture_started_at": snapshot.get(
                "capture_started_at"
            ),
            "broker_snapshot_capture_completed_at": snapshot.get(
                "capture_completed_at"
            ),
            "broker_snapshot_age_at_decision_seconds": snapshot_age_seconds,
            "broker_snapshot_capture_duration_seconds": (
                snapshot_capture_duration_seconds
            ),
            "broker_snapshot_max_age_seconds": snapshot_max_age_seconds,
            "broker_session_authority": broker_session_evidence or None,
            "nav_reconstruction": nav_evidence,
        }
    )
    market_state_evidence["content_hash"] = _canonical_hash(
        market_state_evidence
    )
    decision_nav = float(nav_evidence["authoritative_account_nav"])
    settled, _history, availability = _settled_cash_context(
        broker,
        broker_cash=float(cash),
        as_of_date=str(plan.get("trade_date") or ""),
        env=env,
    )
    if settled.fail_closed:
        raise RuntimeError(f"settled cash unavailable at Decision: {availability}")
    request.planning_account["settled_cash"] = float(settled.settled_cash)
    request.planning_account["settled_cash_fail_closed"] = False
    cap, cap_source = resolve_dynamic_cap(decision_nav, env)
    if cap is None or cap <= 0:
        raise RuntimeError("dynamic capital cap is unresolved at Decision")
    max_orders = int(float(env.get("CAERUS_LIVE_PILOT_MAX_ORDERS") or 50))
    min_trade = float(env.get("CAERUS_LIVE_PILOT_MIN_TRADE_USD") or 10)
    config = live_pilot_execution_config(
        approved_cap_usd=float(cap),
        allow_fractional=bool(plan.get("allow_fractional", False)),
        allow_fractional_sells=(
            str(env.get("CAERUS_PAPER_FRACTIONAL_EXIT_ENABLED") or "")
            .strip()
            .lower()
            in {"1", "true", "yes", "y", "on"}
        ),
        fractional_sell_min_trade_usd=float(
            env.get("CAERUS_PAPER_FRACTIONAL_EXIT_MIN_NOTIONAL_USD") or 1.0
        ),
        max_orders=max_orders,
        min_trade_usd=min_trade,
        buy_buffer_pct=float(env.get("CAERUS_LIVE_PILOT_BUY_BUFFER_PCT") or 0.98),
        ledger_enabled=False,
    )
    # Whole-share target-attainment is governed PAPER policy. The shared factory
    # defaults to the live-pilot label, so preserve the constraints but carry the
    # actual lane identity before planning (the executor already does this).
    config = dataclasses.replace(config, mode="paper")
    raw, trade_meta = compute_transition_trades(request=request, config=config)
    _capital, capital_budget, executable, filter_stats = apply_capital_budget_and_execution_filter(
        trades=raw,
        planning_account=request.planning_account,
        config=config,
    )
    whole_share_proof = trade_meta.get("whole_share_feasibility")
    governed_whole_share_target = isinstance(whole_share_proof, Mapping) and bool(
        whole_share_proof
    )
    # The governed integer optimizer has already selected the exact quantity
    # vector subject to the policy cash floor, order count, and minimum notional.
    # A generic live-pilot cash buffer must not silently resize that vector after
    # its proof is sealed. PAPER affordability is checked explicitly below using
    # settled cash plus only this plan's sell proceeds and protective limit marks.
    exact_trade_frame = raw if governed_whole_share_target else executable
    if governed_whole_share_target:
        capital_budget = {
            **dict(capital_budget),
            "governed_whole_share_quantity_vector_preserved": True,
            "generic_capital_clipping_applied": False,
        }
        filter_stats = {
            **dict(filter_stats),
            "governed_whole_share_quantity_vector_preserved": True,
        }
    exact_rows = _protective_day_limit_orders(
        _core_rows_from_frame(exact_trade_frame, plan=plan)
    )
    for row in exact_rows:
        if plan.get("session_id"):
            row["session_id"] = str(plan["session_id"])
        if plan.get("allocation_id"):
            row["allocation_id"] = str(plan["allocation_id"])
    if len(exact_rows) > max_orders:
        raise RuntimeError("exact order count exceeds authorized maximum")
    sells = [dict(row) for row in exact_rows if str(row.get("side")).upper() == "SELL"]
    buys = [dict(row) for row in exact_rows if str(row.get("side")).upper() == "BUY"]
    if len(sells) + len(buys) != len(exact_rows):
        raise RuntimeError("exact planning produced unsupported order sides")
    if governed_whole_share_target:
        policy = whole_share_proof.get("policy")
        if not isinstance(policy, Mapping):
            raise RuntimeError("whole-share proof omits governed cash policy")
        minimum_cash_weight = _finite_float(policy.get("minimum_cash_weight"))
        if minimum_cash_weight is None or not 0.0 <= minimum_cash_weight <= 1.0:
            raise RuntimeError("whole-share proof minimum cash weight is invalid")
        sell_proceeds = sum(float(row.get("notional") or 0.0) for row in sells)
        buy_notional = sum(float(row.get("notional") or 0.0) for row in buys)
        projected_cash_at_limits = float(cash) + sell_proceeds - buy_notional
        minimum_cash_dollars = decision_nav * minimum_cash_weight
        if projected_cash_at_limits + 0.01 < minimum_cash_dollars:
            raise RuntimeError(
                "protective limits violate governed whole-share cash floor"
            )
        paper_execution_spendable_cash = float(settled.settled_cash) + sell_proceeds
        if buy_notional > paper_execution_spendable_cash + 0.01:
            raise RuntimeError(
                "governed whole-share buys exceed settled cash plus current-plan sells"
            )
        if buy_notional > float(cap) + 0.01:
            raise RuntimeError("governed whole-share buys exceed dynamic capital cap")
        capital_budget = {
            **dict(capital_budget),
            "governed_minimum_cash_weight": minimum_cash_weight,
            "governed_minimum_cash_dollars": minimum_cash_dollars,
            "projected_cash_at_protective_limits": projected_cash_at_limits,
            "paper_execution_spendable_cash": paper_execution_spendable_cash,
            "requested_buy_notional": buy_notional,
            "allowed_buy_notional": buy_notional,
            "capital_constraint_triggered": False,
        }

    risk_controls = governed_outer_controls
    observed_regime, regime_confidence, acute_risk, market_state_id = (
        _governed_regime_inputs(
            plan=plan,
            risk_controls=risk_controls,
            risk_package_id=_risk.package_id,
        )
    )
    # The broker snapshot intentionally exposes only the deterministic account
    # hash; that stable identity is sufficient to isolate persistent authority
    # without writing the raw broker account identifier to disk.
    broker_account_id_hash = str(
        (account or {}).get("account_id_hash") or ""
    ).strip().lower()
    if not broker_account_id_hash:
        raise RuntimeError("fresh PAPER broker account identity is unavailable")
    resolved_regime_state_root = regime_state_root or _regime_state_root(
        plan_path=plan_path,
        env=env,
    )
    regime_inputs = {
        "account_scope": "PAPER",
        "account_id": broker_account_id_hash,
        "sleeve_id": expected_approved_sleeve,
        "authorization_run_id": run_id,
        "trade_date": str(plan.get("trade_date") or ""),
        "recorded_at": effective_authorized_at,
        "observed_state": observed_regime,
        "confidence": regime_confidence,
        "acute_risk": acute_risk,
        "risk_package_id": _risk.package_id,
        "risk_package_hash": _risk.content_hash,
        "market_state_id": market_state_id,
    }
    prepared_regime = prepare_regime_authority(
        resolved_regime_state_root,
        **regime_inputs,
    )
    # Acute risk is durable immediately, before any possible buy authority.
    # Normal observations remain prepared until main() publishes the immutable
    # exact handoff and commits them before exposing its workflow pointer.
    immediate_risk_authority = (
        acute_risk or prepared_regime.event.to_decision().risk_veto_buys
    )
    regime_record = (
        persist_regime_authority(resolved_regime_state_root, **regime_inputs)
        if immediate_risk_authority
        else prepared_regime
    )
    regime_decision = regime_record.event.to_decision()
    if regime_decision.risk_veto_buys and buys:
        raise RuntimeError("emergency regime risk response vetoes new buy exposure")

    starting_positions = _quantity_positions(snapshot)
    expected_positions, expected_cash = _expected_state(
        positions=starting_positions,
        cash=float(cash),
        orders=[*sells, *buys],
    )
    seal_checked_at = authorization_completed_at
    if seal_checked_at is None:
        seal_checked_at = (
            effective_authorized_at
            if created_at is not None
            else _now()
        )
    try:
        seal_time = dt.datetime.fromisoformat(
            str(seal_checked_at).replace("Z", "+00:00")
        )
        authorization_start = dt.datetime.fromisoformat(
            str(effective_authorized_at).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RuntimeError("authorization seal timestamp is invalid") from exc
    if seal_time.tzinfo is None or authorization_start.tzinfo is None:
        raise RuntimeError("authorization seal timestamp lacks timezone")
    if seal_time < authorization_start:
        raise RuntimeError("authorization seal timestamp precedes Decision capture")
    seal_session = market_session_status(
        trade_date,
        seal_time.astimezone(ET_TZ),
        "16:00",
    )
    if (
        seal_session.reason != session.reason
        or seal_session.session_open_et != session.session_open_et
        or seal_session.session_close_et != session.session_close_et
    ):
        raise RuntimeError(
            "market session changed before exact authorization was sealed"
        )
    if session.reason == "MARKET_OPEN":
        market_state_evidence = _revalidate_open_market_prices_at_seal(
            market_state_evidence=market_state_evidence,
            required_symbols=decision_symbols,
            seal_time=seal_time,
            session_open_et=session.session_open_et,
            session_close_et=session.session_close_et,
        )
    # The snapshot is one logical Decision input with the price marks. It must
    # remain fresh through the immutable seal, not merely through quote capture.
    snapshot_age_at_seal_seconds = (
        seal_time.astimezone(dt.timezone.utc)
        - snapshot_completed_at.astimezone(dt.timezone.utc)
    ).total_seconds()
    if (
        snapshot_age_at_seal_seconds < -5.0
        or snapshot_age_at_seal_seconds > snapshot_max_age_seconds
    ):
        raise RuntimeError("broker snapshot became stale before authorization seal")
    # Re-read open-order truth at the final seal. An unrelated order can alter
    # cash/buying power even when it does not overlap a planned symbol.
    seal_open_orders = getattr(broker, "list_orders", None)
    if not callable(seal_open_orders):
        raise RuntimeError("broker lacks open-order lookup at authorization seal")
    seal_open_rows = seal_open_orders(status="open", limit=100)
    if not isinstance(seal_open_rows, list) or any(
        not isinstance(row, Mapping) for row in seal_open_rows
    ):
        raise RuntimeError("broker open-order seal response is malformed")
    if seal_open_rows:
        raise RuntimeError(
            "broker open order appeared before exact authorization seal"
        )
    market_state_evidence = dict(market_state_evidence)
    market_state_evidence.pop("content_hash", None)
    market_state_evidence.update(
        {
            "authorization_seal_checked_at": seal_time.isoformat(),
            "authorization_seal_session_reason": seal_session.reason,
            "broker_snapshot_age_at_authorization_seal_seconds": (
                snapshot_age_at_seal_seconds
            ),
            "open_order_revalidated_at_authorization_seal": True,
        }
    )
    market_state_evidence["content_hash"] = _canonical_hash(
        market_state_evidence
    )
    source_hashes: dict[str, str] = {}
    if plan_path is not None and plan_path.exists():
        source_hashes[str(plan_path)] = _hash_file(plan_path)
    decision_source = plan.get("decision_source_artifact")
    if isinstance(decision_source, Mapping):
        source_path = Path(str(decision_source.get("path") or ""))
        if source_path and not source_path.is_absolute():
            source_path = REPO_ROOT / source_path
        if source_path.exists():
            source_hashes[str(source_path)] = _hash_file(source_path)
    source_hashes["approved_target_package"] = governed_execution.content_hash
    if target_package_path is not None:
        source_hashes[str(target_package_path)] = target_package_hash
        source_hashes["sealed_precompute_decision_target"] = _decision.content_hash
    source_hashes[str(sleeve_path)] = sleeve_hash
    for lineage_path, lineage_hash in operating_lineage_paths.values():
        source_hashes[str(lineage_path)] = lineage_hash
    source_hashes["authorization_market_state"] = str(
        market_state_evidence["content_hash"]
    )
    source_hashes["regime_authority_event_content"] = (
        regime_record.event.content_hash
    )
    market_closed_at_authorization = bool(
        market_state_evidence.get("market_closed_at_authorization")
    )
    new_order_submission_allowed = (
        market_state_evidence.get(
            "new_order_submission_allowed_at_authorization"
        )
        is True
    )
    authorization_reason = (
        "MARKET_CLOSED_AUTHORIZED_NO_TRADE"
        if market_closed_at_authorization and not exact_rows
        else (
            "MARKET_CLOSED_EXACT_PLAN_SEALED_NO_NEW_ORDER_AUTHORITY"
            if market_closed_at_authorization
            else (
                "AUTHORIZED_NO_TRADE"
                if not exact_rows
                else "PAPER_ALLOCATOR_EXACT_ORDERS_AUTHORIZED"
            )
        )
    )
    exact = build_exact_execution_plan(
        run_id=run_id,
        as_of=str(market_state_evidence["price_as_of"]),
        created_at=effective_authorized_at,
        orchestrator_version=str(env.get("CAERUS_ORCHESTRATOR_VERSION") or "choice2.v1"),
        source_precompute_ids=[
            value
            for value in (
                str(plan.get("source_precompute_payload") or ""),
                str(plan.get("source_signals") or ""),
                str(plan.get("source_sleeve_evaluations") or ""),
                str(plan.get("source_session_manifest") or ""),
                str(plan.get("source_sleeve_decisions") or ""),
                str(plan.get("source_portfolio_allocation") or ""),
            )
            if value
        ],
        source_artifact_hashes=source_hashes,
        market_state_id=market_state_id,
        market_state={
            "captured_at": snapshot.get("captured_at"),
            "price_as_of": market_state_evidence["price_as_of"],
            "pricing_basis": price_basis,
            "quote_evidence": market_state_evidence,
            "risk_package_id": _risk.package_id,
            "risk_package_hash": _risk.content_hash,
        },
        regime_state=regime_record.regime_state(),
        sleeve_allocations=(
            [
                {
                    "sleeve_id": str(row.get("sleeve_id") or ""),
                    "capital_eligible": True,
                    "account_scope": "PAPER",
                    "allocation_weight": float(row.get("risk_budget") or 0.0),
                    "decision_id": row.get("decision_id"),
                    "decision_hash": row.get("decision_hash"),
                    "allocation_id": portfolio_allocation_payload.get("allocation_id"),
                }
                for row in portfolio_allocation_payload.get("sleeve_allocations") or []
            ]
            if isinstance(portfolio_allocation_payload, Mapping)
            else [
                {
                    "sleeve_id": "caerus_orion",
                    "capital_eligible": True,
                    "account_scope": "PAPER",
                    "allocation_weight": 1.0,
                }
            ]
        ),
        portfolio_nav=decision_nav,
        starting_positions=starting_positions,
        starting_cash=float(cash),
        account_id_hash=broker_account_id_hash,
        risk_state={
            "target_risk": risk_controls,
            "trade_meta": trade_meta,
            "capital_budget": capital_budget,
            "execution_filter": filter_stats,
            "settled_cash": settled.to_report(),
            "decision_nav_reconstruction": nav_evidence,
        },
        sell_orders=sells,
        buy_orders=buys,
        expected_posttrade_positions=expected_positions,
        expected_posttrade_cash=expected_cash,
        constraints={
            "max_orders": max_orders,
            "capital_cap_usd": float(cap),
            "capital_cap_source": cap_source,
            "allow_fractional": bool(plan.get("allow_fractional", False)),
            "sell_first": True,
            "post_sell_rebudgeting": "FORBIDDEN",
            "sleeve_attribution_interval": "execution_pre_to_post_broker_nav",
            "sleeve_attribution_mark_timing_tolerance_bps": (
                DEFAULT_MARK_TIMING_TOLERANCE_BPS
            ),
            "cash_reconciliation_tolerance_usd": max(
                1.0,
                sum(float(row.get("notional") or 0.0) for row in exact_rows) * 0.01,
            ),
            "max_adverse_fill_slippage_bps": (
                MAX_ADVERSE_FILL_SLIPPAGE_BPS
            ),
            "new_order_execution_style": "protective_day_limit",
            "authorization_session_reason": session.reason,
            "authorization_price_basis": price_basis,
            "market_closed_at_authorization": market_closed_at_authorization,
            "new_order_submission_allowed_at_authorization": (
                new_order_submission_allowed
            ),
            **(
                {
                    "paper_drill_epoch": drill_epoch,
                    "paper_drill_live_eligible": False,
                }
                if drill_epoch
                else {}
            ),
        },
        authorization_state={
            "status": "AUTHORIZED",
            "authority": "CAERUS_ORCHESTRATOR",
            "authorized_at": seal_time.isoformat(),
            "authorization_reason": authorization_reason,
        },
        strategy_id=(
            "caerus_paper_portfolio"
            if isinstance(portfolio_allocation_payload, Mapping)
            and len(portfolio_allocation_payload.get("sleeve_allocations") or []) > 1
            else str(plan.get("approved_sleeve") or "caerus_orion")
        ),
    )
    result = dict(plan)
    result.update(
        {
            "schema_version": "caerus.authorized_execution_handoff.v1",
            "status": "AUTHORIZED_NO_TRADE" if not exact.orders else "AUTHORIZED_EXACT_PLAN",
            "reason_code": (
                "market_closed_authorized_no_trade"
                if market_closed_at_authorization and not exact.orders
                else (
                    "market_closed_exact_plan_sealed_no_submission_authority"
                    if market_closed_at_authorization
                    else (
                        "authorized_no_trade"
                        if not exact.orders
                        else "fresh_broker_state_exact_plan_authorized"
                    )
                )
            ),
            "exact_execution_plan": exact.to_dict(),
            "exact_execution_plan_id": exact.plan_id,
            "exact_execution_plan_hash": exact.content_hash,
            "exact_execution_authority_run_id": exact.run_id,
            "execution_authority": "exact_execution_plan_only",
            "precompute_execution_authority": False,
            "broker_state_at_decision": snapshot,
            "regime_authority_event": {
                "path": str(regime_record.event_path),
                "content_hash": regime_record.event.content_hash,
                "observation_id": regime_record.event.observation_id,
                "sequence": regime_record.event.sequence,
                "created": regime_record.created,
                "committed_at_evaluation": regime_record.committed,
                "commit_required_before_pointer": not regime_record.committed,
                "event": regime_record.event.to_dict(),
            },
        }
    )
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--drill-epoch")
    parser.add_argument("--drill-policy-config", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    trade_date = str(plan.get("trade_date") or "")
    latest_pointer = args.output or args.plan.with_name(
        f"exact_execution_plan_{trade_date}.latest.json"
    )
    try:
        paper_mode = str(os.environ.get("MODE") or "").strip().lower() == "paper"
        trading_mode = (
            str(os.environ.get("TRADING_MODE") or "").strip().lower() == "paper"
        )
        paper_flag = str(os.environ.get("ALPACA_PAPER") or "").strip() == "1"
        if args.drill_epoch and not (paper_mode and trading_mode and paper_flag):
            raise RuntimeError("paper drill epoch requires all PAPER mode pins")
        drill_epoch = validate_drill_epoch(
            args.drill_epoch,
            trade_date=trade_date,
            policy_path=args.drill_policy_config,
            broker_paper=True,
            base_url=str(os.environ.get("ALPACA_BASE_URL") or ""),
        )
    except Exception as exc:
        print(json.dumps({
            "status": "BLOCKED",
            "reason_code": "paper_drill_epoch_policy_failed",
            "error": str(exc)[:1000],
            "orders_submitted": 0,
        }, sort_keys=True))
        return 1
    base_wal_root = latest_pointer.parent.parent / "submission_wal"
    epoch_wal_root = scoped_wal_root(base_wal_root, drill_epoch)
    wal_intents = epoch_wal_root / trade_date / "intents"
    try:
        prior_path = _recover_existing_authority_for_wal(
            latest_pointer=latest_pointer,
            trade_date=trade_date,
            wal_intents=wal_intents,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason_code": "stable_wal_original_plan_recovery_unresolved",
                    "error": str(exc)[:1000],
                    "orders_submitted": 0,
                },
                sort_keys=True,
            )
        )
        return 1
    if prior_path is not None:
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "status": "RECOVER_EXISTING_EXACT_PLAN",
                    "json_path": str(prior_path),
                    "plan_id": prior.get("exact_execution_plan_id"),
                    "reason_code": "stable_wal_requires_original_plan_recovery",
                },
                sort_keys=True,
            )
        )
        return 0
    broker = AlpacaBroker.from_env()
    try:
        preauthorization_snapshot = _broker_snapshot(
            broker, fail_on_open_order_lookup=True
        )
        preauthorization_account = preauthorization_snapshot.get("account")
        if not isinstance(preauthorization_account, Mapping):
            raise RuntimeError("PAPER broker account snapshot is malformed")
        preauthorization_cash = _finite_float(
            preauthorization_account.get("cash")
        )
        if preauthorization_cash is None or preauthorization_cash < 0:
            raise RuntimeError("PAPER broker cash is unavailable")
        preauthorization_state_hash = compute_starting_state_hash(
            _quantity_positions(preauthorization_snapshot),
            preauthorization_cash,
        )
        unresolved = unresolved_foreign_intent_client_ids(
            base_wal_root,
            current_wal_root=epoch_wal_root,
            trade_date=trade_date,
            lookup_by_client_order_id=getattr(
                broker, "find_order_by_client_id", None
            ),
            current_state_hash=preauthorization_state_hash,
        )
        if unresolved:
            raise RuntimeError(
                "unresolved prior PAPER submission WAL intents: "
                + ",".join(sorted(unresolved))
            )
    except Exception as exc:
        print(json.dumps({
            "status": "BLOCKED",
            "reason_code": "paper_drill_prior_submission_unresolved",
            "error": str(exc)[:1000],
            "orders_submitted": 0,
        }, sort_keys=True))
        return 1
    resolved_regime_state_root = (
        latest_pointer.parent.parent / "state" / "regime_authority"
    )
    try:
        result = authorize_exact_execution_plan(
            plan=plan,
            broker=broker,
            env=os.environ,
            run_id=args.run_id,
            plan_path=args.plan,
            regime_state_root=resolved_regime_state_root,
            drill_epoch=drill_epoch,
            broker_snapshot=preauthorization_snapshot,
        )
    except Exception as exc:
        transient = is_retryable_broker_read_error(exc)
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason_code": (
                        "paper_exact_plan_authorization_transient_failed"
                        if transient
                        else "paper_exact_plan_authorization_nonretryable_failed"
                    ),
                    "error": str(exc)[:1000],
                    "orders_submitted": 0,
                },
                sort_keys=True,
            )
        )
        return 2 if transient else 1
    authority_dir = latest_pointer.parent / "authority" / trade_date
    authority_dir.mkdir(parents=True, exist_ok=True)
    regime_metadata = result.get("regime_authority_event")
    if not isinstance(regime_metadata, Mapping):
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason_code": "regime_authority_commit_metadata_missing",
                    "orders_submitted": 0,
                },
                sort_keys=True,
            )
        )
        return 1
    if bool(regime_metadata.get("commit_required_before_pointer")):
        try:
            event_payload = regime_metadata.get("event")
            if not isinstance(event_payload, Mapping):
                raise RuntimeError("prepared regime event payload is missing")
            prepared_event = RegimeAuthorityEvent.from_dict(event_payload)
            prepared_regime = RegimePersistenceResult(
                event=prepared_event,
                event_path=Path(str(regime_metadata.get("path") or "")),
                created=False,
                committed=False,
            )
            committed_view = RegimePersistenceResult(
                event=prepared_event,
                event_path=prepared_regime.event_path,
                created=False,
                committed=True,
            )
            final_result = _seal_regime_committed_handoff(
                result,
                committed_view,
                verify_event=False,
            )
            safe_plan_id = str(final_result["exact_execution_plan_id"]).replace(
                ":", "_"
            )
            output = authority_dir / f"{safe_plan_id}.json"
            staging = authority_dir / f".{safe_plan_id}.staged"
            safe_write_text(
                staging,
                json.dumps(final_result, indent=2, sort_keys=True, default=str) + "\n",
                allow_overwrite=False,
            )
            committed_regime = commit_prepared_regime_authority(
                resolved_regime_state_root,
                prepared_regime,
            )
            if committed_regime.event.content_hash != str(
                regime_metadata.get("content_hash") or ""
            ):
                raise RuntimeError("committed regime event differs from exact handoff")
            exact_execution_plan_from_dict(final_result["exact_execution_plan"])
            if output.exists():
                existing = json.loads(output.read_text(encoding="utf-8"))
                exact_execution_plan_from_dict(existing["exact_execution_plan"])
                result = existing
                staging.unlink(missing_ok=True)
            else:
                os.replace(staging, output)
                directory_fd = os.open(str(authority_dir), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                result = final_result
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "status": "BLOCKED",
                        "reason_code": "regime_authority_commit_failed",
                        "error": str(exc)[:1000],
                        "orders_submitted": 0,
                    },
                    sort_keys=True,
                )
            )
            return 1
    else:
        exact_execution_plan_from_dict(result["exact_execution_plan"])
        safe_plan_id = str(result["exact_execution_plan_id"]).replace(":", "_")
        output = authority_dir / f"{safe_plan_id}.json"
        if output.exists():
            existing = json.loads(output.read_text(encoding="utf-8"))
            exact_execution_plan_from_dict(existing["exact_execution_plan"])
            result = existing
        else:
            safe_write_text(
                output,
                json.dumps(result, indent=2, sort_keys=True, default=str) + "\n",
                allow_overwrite=False,
            )
    pointer_payload = {
        "schema_version": "caerus.exact_execution_plan_pointer.v1",
        "trade_date": trade_date,
        "plan_id": result["exact_execution_plan_id"],
        "plan_hash": result["exact_execution_plan_hash"],
        "json_path": str(output),
    }
    safe_write_text(
        latest_pointer,
        json.dumps(pointer_payload, indent=2, sort_keys=True) + "\n",
        allow_overwrite=True,
    )
    print(json.dumps({"status": result["status"], "json_path": str(output), "plan_id": result["exact_execution_plan_id"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
