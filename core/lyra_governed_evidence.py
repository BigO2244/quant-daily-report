"""Strict factual evidence for a prospective governed Lyra v2 decision.

The builders consume explicit immutable inputs only.  They do not discover
runtime files, read a broker, select a lane, or grant execution authority.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import math
import statistics
from typing import Any, Mapping, Sequence

from core.lyra_target_selection import validate_lyra_target_selection_evidence
from core.sleeve_decision import canonical_json, content_hash
from core.governed_xnys_calendar import (
    XNYS_CALENDAR_POLICY_ID,
    is_xnys_session,
    next_xnys_session,
    xnys_session_window,
)


LYRA_MARKET_DATA_SNAPSHOT_SCHEMA = "caerus.lyra_market_data_snapshot.v1"
LYRA_SESSION_SNAPSHOT_SCHEMA = "caerus.lyra_governed_session_snapshot.v1"
LYRA_FORECAST_RISK_SCHEMA = "caerus.lyra_forecast_risk_evidence.v1"
LYRA_LIQUIDITY_SCHEMA = "caerus.lyra_liquidity_evidence.v1"
LYRA_CAPACITY_SCHEMA = "caerus.lyra_capacity_evidence.v1"
LYRA_RISK_POLICY_SCHEMA = "caerus.lyra_forecast_risk_policy.v1"
LYRA_RISK_POLICY_PROPOSAL_SCHEMA = "caerus.lyra_forecast_risk_policy_proposal.v1"
LYRA_RISK_POLICY_OWNER_DECISION_SCHEMA = (
    "caerus.lyra_forecast_risk_policy_owner_decision.v1"
)

RISK_FORMULA = "STATIC_TARGET_WEIGHTED_20_SESSION_CLOSE_RETURN_VOLATILITY_ANNUALIZED_V1"
LIQUIDITY_FORMULA = "20_SESSION_MEAN_DOLLAR_VOLUME_AND_1PCT_ORDER_PARTICIPATION_V1"
CAPACITY_FORMULA = "MIN_SYMBOL_PARTICIPATION_CAPACITY_DIVIDED_BY_TARGET_WEIGHT_V1"
TURNOVER_FORMULA = "FULL_L1_TARGET_WEIGHT_CHANGE_V1"
REQUIRED_PRICE_OBSERVATIONS = 21
LIQUIDITY_LOOKBACK = 20
ANNUALIZATION_FACTOR = 252
MAX_PARTICIPATION_RATE = 0.05
MAX_ORDER_PARTICIPATION_RATE = 0.01
MINIMUM_MEAN_DOLLAR_VOLUME_USD = 20_000_000.0
MINIMUM_CAPACITY_MULTIPLE = 20.0
LIVE_V1_CAPITAL_REFERENCE_USD = 460.0


class LyraGovernedEvidenceError(ValueError):
    """Raised when factual Lyra evidence is missing, stale, or inconsistent."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _sha(value: Any, *, label: str) -> str:
    raw = str(value or "")
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise LyraGovernedEvidenceError(f"{label} must be a lowercase SHA-256 digest")
    return raw


def _timestamp(value: Any, *, label: str) -> tuple[str, dt.datetime]:
    raw = str(value or "")
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LyraGovernedEvidenceError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise LyraGovernedEvidenceError(f"{label} must include a timezone")
    return raw, parsed


def _date(value: Any, *, label: str) -> str:
    raw = str(value or "")
    try:
        dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise LyraGovernedEvidenceError(f"{label} must be an ISO date") from exc
    return raw


def _finite(value: Any, *, label: str, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise LyraGovernedEvidenceError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LyraGovernedEvidenceError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0.0):
        raise LyraGovernedEvidenceError(f"{label} is invalid")
    return result


def _expected_xnys_dates(data_as_of: str, *, count: int) -> list[str]:
    if not is_xnys_session(data_as_of):
        raise LyraGovernedEvidenceError("data_as_of must be an XNYS session")
    return xnys_session_window(data_as_of, count=count)


def _validate_weekly_session(*, signal_as_of: str, execution_session: str) -> None:
    signal = dt.date.fromisoformat(signal_as_of)
    if (
        signal.weekday() != 0
        or not is_xnys_session(signal_as_of)
        or next_xnys_session(signal_as_of) != execution_session
    ):
        raise LyraGovernedEvidenceError(
            "Lyra signal must be a Monday XNYS close followed by the immediate XNYS session"
        )


def normalized_target_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, (list, tuple)) or not rows:
        raise LyraGovernedEvidenceError("Lyra target rows are absent")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise LyraGovernedEvidenceError("Lyra target row is not an object")
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
        weight = _finite(
            raw.get("target_weight"), label=f"{symbol or 'target'}.target_weight",
            positive=True,
        )
        if not symbol or symbol in seen or weight > 1.0:
            raise LyraGovernedEvidenceError("Lyra target symbols/weights are invalid")
        seen.add(symbol)
        normalized.append({"symbol": symbol, "target_weight": round(weight, 12)})
    normalized.sort(key=lambda row: row["symbol"])
    if abs(sum(row["target_weight"] for row in normalized) - 1.0) > 1e-9:
        raise LyraGovernedEvidenceError("Lyra target weights must sum to one")
    return normalized


def target_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    return content_hash(normalized_target_rows(rows))


def _risk_policy_terms(*, effective_from: str) -> dict[str, Any]:
    return {
        "sleeve_id": "caerus_lyra",
        "metric": "annualized_volatility",
        "formula_id": RISK_FORMULA,
        "lookback_sessions": 20,
        "minimum_price_observations": REQUIRED_PRICE_OBSERVATIONS,
        "annualization_factor": ANNUALIZATION_FACTOR,
        "liquidity_formula_id": LIQUIDITY_FORMULA,
        "liquidity_lookback_sessions": LIQUIDITY_LOOKBACK,
        "minimum_mean_dollar_volume_usd": MINIMUM_MEAN_DOLLAR_VOLUME_USD,
        "maximum_order_participation_rate": MAX_ORDER_PARTICIPATION_RATE,
        "maximum_liquidation_participation_rate": MAX_PARTICIPATION_RATE,
        "capacity_formula_id": CAPACITY_FORMULA,
        "minimum_capacity_multiple": MINIMUM_CAPACITY_MULTIPLE,
        "capital_reference_usd": LIVE_V1_CAPITAL_REFERENCE_USD,
        "turnover_formula_id": TURNOVER_FORMULA,
        "calendar_policy_id": XNYS_CALENDAR_POLICY_ID,
        "effective_from": _date(effective_from, label="risk policy effective_from"),
        "execution_authority": False,
        "activation_authority": False,
    }


def validate_lyra_forecast_risk_policy_proposal(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    fields = {
        "schema_version", "proposal_id", "proposed_at", "proposed_by",
        "policy_terms", "execution_authority", "activation_authority",
        "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise LyraGovernedEvidenceError("forecast risk policy proposal fields differ")
    if (
        payload.get("schema_version") != LYRA_RISK_POLICY_PROPOSAL_SCHEMA
        or not isinstance(payload.get("proposal_id"), str)
        or not payload.get("proposal_id")
        or payload.get("proposed_by") != "CAERUS_OPERATING_MODEL_MIGRATION"
        or payload.get("execution_authority") is not False
        or payload.get("activation_authority") is not False
    ):
        raise LyraGovernedEvidenceError("forecast risk policy proposal semantics differ")
    _timestamp(payload.get("proposed_at"), label="risk policy proposed_at")
    terms = payload.get("policy_terms")
    if not isinstance(terms, Mapping) or dict(terms) != _risk_policy_terms(
        effective_from=str(terms.get("effective_from") if isinstance(terms, Mapping) else "")
    ):
        raise LyraGovernedEvidenceError("forecast risk policy proposal terms differ")
    if payload.get("content_hash") != _hash(payload):
        raise LyraGovernedEvidenceError("forecast risk policy proposal content_hash mismatch")
    return copy.deepcopy(dict(payload))


def validate_lyra_forecast_risk_policy_owner_decision(
    payload: Mapping[str, Any], *, proposal: Mapping[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    fields = {
        "schema_version", "owner_decision_id", "proposal_id", "proposal_hash",
        "decision", "owner", "decided_at", "expires_at",
        "execution_authority", "activation_authority", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise LyraGovernedEvidenceError("forecast risk policy owner decision fields differ")
    if (
        payload.get("schema_version") != LYRA_RISK_POLICY_OWNER_DECISION_SCHEMA
        or not isinstance(payload.get("owner_decision_id"), str)
        or not payload.get("owner_decision_id")
        or payload.get("decision") != "APPROVE"
        or payload.get("owner") != "Brett Olson"
        or payload.get("execution_authority") is not False
        or payload.get("activation_authority") is not False
    ):
        raise LyraGovernedEvidenceError("forecast risk policy owner decision differs")
    _, decided = _timestamp(payload.get("decided_at"), label="risk policy decided_at")
    _, expires = _timestamp(payload.get("expires_at"), label="risk policy expires_at")
    if expires <= decided:
        raise LyraGovernedEvidenceError("forecast risk policy owner decision is expired")
    _sha(payload.get("proposal_hash"), label="risk policy proposal_hash")
    if proposal is not None:
        checked = validate_lyra_forecast_risk_policy_proposal(proposal)
        if (
            payload.get("proposal_id") != checked["proposal_id"]
            or payload.get("proposal_hash") != checked["content_hash"]
            or decided < dt.datetime.fromisoformat(
                checked["proposed_at"].replace("Z", "+00:00")
            )
        ):
            raise LyraGovernedEvidenceError(
                "forecast risk policy owner decision/proposal binding differs"
            )
    if as_of is not None:
        _, observed = _timestamp(as_of, label="risk policy owner decision as_of")
        if observed < decided or observed > expires:
            raise LyraGovernedEvidenceError(
                "forecast risk policy owner decision is not current"
            )
    if payload.get("content_hash") != _hash(payload):
        raise LyraGovernedEvidenceError(
            "forecast risk policy owner decision content_hash mismatch"
        )
    return copy.deepcopy(dict(payload))


def validate_lyra_forecast_risk_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "policy_id", "status", "sleeve_id", "metric",
        "formula_id", "lookback_sessions", "minimum_price_observations",
        "annualization_factor", "liquidity_formula_id",
        "liquidity_lookback_sessions", "minimum_mean_dollar_volume_usd",
        "maximum_order_participation_rate",
        "maximum_liquidation_participation_rate", "capacity_formula_id",
        "minimum_capacity_multiple", "capital_reference_usd",
        "turnover_formula_id", "calendar_policy_id", "approved_by",
        "approved_at", "effective_from",
        "owner_decision_hash", "live_owner_decision_hash",
        "execution_authority", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise LyraGovernedEvidenceError("forecast risk policy fields differ")
    if (
        payload.get("schema_version") != LYRA_RISK_POLICY_SCHEMA
        or payload.get("status") != "APPROVED"
        or payload.get("sleeve_id") != "caerus_lyra"
        or payload.get("metric") != "annualized_volatility"
        or payload.get("formula_id") != RISK_FORMULA
        or payload.get("lookback_sessions") != 20
        or payload.get("minimum_price_observations") != REQUIRED_PRICE_OBSERVATIONS
        or payload.get("annualization_factor") != ANNUALIZATION_FACTOR
        or payload.get("liquidity_formula_id") != LIQUIDITY_FORMULA
        or payload.get("liquidity_lookback_sessions") != LIQUIDITY_LOOKBACK
        or payload.get("minimum_mean_dollar_volume_usd")
        != MINIMUM_MEAN_DOLLAR_VOLUME_USD
        or payload.get("maximum_order_participation_rate")
        != MAX_ORDER_PARTICIPATION_RATE
        or payload.get("maximum_liquidation_participation_rate")
        != MAX_PARTICIPATION_RATE
        or payload.get("capacity_formula_id") != CAPACITY_FORMULA
        or payload.get("minimum_capacity_multiple") != MINIMUM_CAPACITY_MULTIPLE
        or payload.get("capital_reference_usd") != LIVE_V1_CAPITAL_REFERENCE_USD
        or payload.get("turnover_formula_id") != TURNOVER_FORMULA
        or payload.get("calendar_policy_id") != XNYS_CALENDAR_POLICY_ID
        or payload.get("approved_by") != "OWNER"
        or payload.get("execution_authority") is not False
    ):
        raise LyraGovernedEvidenceError("forecast risk policy semantics differ")
    _timestamp(payload.get("approved_at"), label="risk policy approved_at")
    _date(payload.get("effective_from"), label="risk policy effective_from")
    _sha(payload.get("owner_decision_hash"), label="owner_decision_hash")
    _sha(
        payload.get("live_owner_decision_hash"),
        label="live_owner_decision_hash",
    )
    if payload.get("content_hash") != _hash(payload):
        raise LyraGovernedEvidenceError("forecast risk policy content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_lyra_market_data_snapshot(
    *, trade_date: str, data_as_of: str, captured_at: str,
    source_path: str, source_sha256: str,
    required_symbols: Sequence[str], price_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    trade_date = _date(trade_date, label="trade_date")
    data_as_of = _date(data_as_of, label="data_as_of")
    captured_at, captured = _timestamp(captured_at, label="captured_at")
    if data_as_of >= trade_date or captured.date().isoformat() != trade_date:
        raise LyraGovernedEvidenceError(
            "market data must end before, and be captured on, the decision trade date"
        )
    _validate_weekly_session(signal_as_of=data_as_of, execution_session=trade_date)
    if not source_path or source_path.startswith("/") or ".." in source_path.split("/"):
        raise LyraGovernedEvidenceError("market data source_path must be a safe logical path")
    source_sha256 = _sha(source_sha256, label="source_sha256")
    symbols = sorted({str(value or "").strip().upper() for value in required_symbols})
    if not symbols or any(not value for value in symbols):
        raise LyraGovernedEvidenceError("required_symbols are invalid")
    by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    seen: set[tuple[str, str]] = set()
    for raw in price_rows:
        if not isinstance(raw, Mapping):
            raise LyraGovernedEvidenceError("market data row is not an object")
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
        if symbol not in by_symbol:
            continue
        date = _date(raw.get("date"), label=f"{symbol}.date")
        if date > data_as_of:
            continue
        key = (symbol, date)
        if key in seen:
            raise LyraGovernedEvidenceError("market data contains duplicate symbol/date")
        seen.add(key)
        by_symbol[symbol].append({
            "date": date, "symbol": symbol,
            "close": round(_finite(raw.get("close"), label=f"{symbol}.close", positive=True), 10),
            "volume": round(_finite(raw.get("volume"), label=f"{symbol}.volume", positive=True), 6),
        })
    expected_dates = _expected_xnys_dates(
        data_as_of, count=REQUIRED_PRICE_OBSERVATIONS
    )
    for symbol in symbols:
        by_symbol[symbol] = sorted(by_symbol[symbol], key=lambda row: row["date"])[
            -REQUIRED_PRICE_OBSERVATIONS:
        ]
        if len(by_symbol[symbol]) != REQUIRED_PRICE_OBSERVATIONS:
            raise LyraGovernedEvidenceError(
                f"{symbol} lacks {REQUIRED_PRICE_OBSERVATIONS} completed price observations"
            )
        dates = [row["date"] for row in by_symbol[symbol]]
        if dates != expected_dates:
            raise LyraGovernedEvidenceError(
                f"{symbol} does not cover the governed XNYS price window"
            )
    rows = [row for symbol in symbols for row in by_symbol[symbol]]
    body = {
        "schema_version": LYRA_MARKET_DATA_SNAPSHOT_SCHEMA,
        "snapshot_id": "pending", "trade_date": trade_date,
        "data_as_of": data_as_of, "captured_at": captured_at,
        "source_path": source_path, "source_sha256": source_sha256,
        "required_symbols": symbols, "observation_dates": expected_dates,
        "rows": sorted(rows, key=lambda row: (row["date"], row["symbol"])),
        "execution_authority": False, "broker_write_performed": False,
    }
    seed = _hash(body)
    body["snapshot_id"] = f"lyra-market-data:{trade_date}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_lyra_market_data_snapshot(body)


def validate_lyra_market_data_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "snapshot_id", "trade_date", "data_as_of",
        "captured_at", "source_path", "source_sha256", "required_symbols",
        "observation_dates", "rows", "execution_authority",
        "broker_write_performed", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise LyraGovernedEvidenceError("market data snapshot fields are invalid")
    if payload.get("schema_version") != LYRA_MARKET_DATA_SNAPSHOT_SCHEMA:
        raise LyraGovernedEvidenceError("market data snapshot schema differs")
    if payload.get("execution_authority") is not False or payload.get("broker_write_performed") is not False:
        raise LyraGovernedEvidenceError("market data snapshot authority flags differ")
    trade_date = _date(payload.get("trade_date"), label="trade_date")
    data_as_of = _date(payload.get("data_as_of"), label="data_as_of")
    _, captured = _timestamp(payload.get("captured_at"), label="captured_at")
    if data_as_of >= trade_date or captured.date().isoformat() != trade_date:
        raise LyraGovernedEvidenceError("market data snapshot timing differs")
    _validate_weekly_session(signal_as_of=data_as_of, execution_session=trade_date)
    _sha(payload.get("source_sha256"), label="source_sha256")
    symbols = payload.get("required_symbols")
    dates = payload.get("observation_dates")
    rows = payload.get("rows")
    if (
        not isinstance(symbols, list) or symbols != sorted(set(symbols)) or not symbols
        or not isinstance(dates, list) or dates != sorted(set(dates))
        or dates != _expected_xnys_dates(
            data_as_of, count=REQUIRED_PRICE_OBSERVATIONS
        )
        or not isinstance(rows, list)
        or len(rows) != len(symbols) * REQUIRED_PRICE_OBSERVATIONS
    ):
        raise LyraGovernedEvidenceError("market data snapshot coverage differs")
    expected = {(date, symbol) for date in dates for symbol in symbols}
    actual: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"date", "symbol", "close", "volume"}:
            raise LyraGovernedEvidenceError("market data snapshot row differs")
        actual.add((str(row["date"]), str(row["symbol"])))
        _finite(row["close"], label="close", positive=True)
        _finite(row["volume"], label="volume", positive=True)
    if actual != expected:
        raise LyraGovernedEvidenceError("market data snapshot rows do not exactly cover the window")
    if payload.get("content_hash") != _hash(payload):
        raise LyraGovernedEvidenceError("market data snapshot content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_lyra_governed_session_snapshot(
    *, trade_date: str, execution_session: str, signal_as_of: str,
    effective_target_date: str, as_of: str, captured_at: str,
    source_session_id: str, source_session_hash: str,
    evaluation_file_hash: str, legacy_decision_file_hash: str,
    legacy_lyra_decision_hash: str,
    lyra_source_hash: str, prior_lyra_source_hash: str,
    universe_freeze_hash: str, universe_source_hash: str,
    market_data_snapshot_hash: str, target_selection_evidence_hash: str,
    forecast_risk_policy_hash: str,
    forecast_risk_policy_proposal_hash: str,
    forecast_risk_policy_owner_decision_hash: str,
) -> dict[str, Any]:
    trade_date = _date(trade_date, label="trade_date")
    execution_session = _date(execution_session, label="execution_session")
    signal_as_of = _date(signal_as_of, label="signal_as_of")
    effective_target_date = _date(
        effective_target_date, label="effective_target_date"
    )
    as_of, observed = _timestamp(as_of, label="as_of")
    captured_at, captured = _timestamp(captured_at, label="captured_at")
    if (
        execution_session != trade_date
        or signal_as_of != effective_target_date
        or signal_as_of >= execution_session
        or observed.date().isoformat() != execution_session
        or captured < observed
    ):
        raise LyraGovernedEvidenceError("governed session timing differs")
    _validate_weekly_session(
        signal_as_of=signal_as_of, execution_session=execution_session
    )
    hashes = {
        name: _sha(value, label=name)
        for name, value in {
            "source_session_hash": source_session_hash,
            "evaluation_file_hash": evaluation_file_hash,
            "legacy_decision_file_hash": legacy_decision_file_hash,
            "legacy_lyra_decision_hash": legacy_lyra_decision_hash,
            "lyra_source_hash": lyra_source_hash,
            "prior_lyra_source_hash": prior_lyra_source_hash,
            "universe_freeze_hash": universe_freeze_hash,
            "universe_source_hash": universe_source_hash,
            "market_data_snapshot_hash": market_data_snapshot_hash,
            "target_selection_evidence_hash": target_selection_evidence_hash,
            "forecast_risk_policy_hash": forecast_risk_policy_hash,
            "forecast_risk_policy_proposal_hash": forecast_risk_policy_proposal_hash,
            "forecast_risk_policy_owner_decision_hash": (
                forecast_risk_policy_owner_decision_hash
            ),
        }.items()
    }
    if not source_session_id:
        raise LyraGovernedEvidenceError("source_session_id is required")
    body = {
        "schema_version": LYRA_SESSION_SNAPSHOT_SCHEMA,
        "session_id": "pending", "trade_date": trade_date,
        "execution_session": execution_session,
        "signal_as_of": signal_as_of,
        "effective_target_date": effective_target_date,
        "as_of": as_of, "captured_at": captured_at,
        "source_session_id": source_session_id, **hashes,
        "prospective_governed_transition": True,
        "legacy_evaluation_relabelled": False,
        "execution_authority": False, "activation_authority": False,
    }
    seed = _hash(body)
    body["session_id"] = f"lyra-governed-session:{trade_date}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_lyra_governed_session_snapshot(body)


def validate_lyra_governed_session_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "session_id", "trade_date", "execution_session",
        "signal_as_of", "effective_target_date", "as_of", "captured_at",
        "source_session_id", "source_session_hash", "evaluation_file_hash",
        "legacy_decision_file_hash", "legacy_lyra_decision_hash",
        "lyra_source_hash", "prior_lyra_source_hash",
        "universe_freeze_hash", "universe_source_hash",
        "market_data_snapshot_hash", "target_selection_evidence_hash",
        "forecast_risk_policy_hash", "forecast_risk_policy_proposal_hash",
        "forecast_risk_policy_owner_decision_hash",
        "prospective_governed_transition",
        "legacy_evaluation_relabelled", "execution_authority",
        "activation_authority", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise LyraGovernedEvidenceError("governed session snapshot fields are invalid")
    if payload.get("schema_version") != LYRA_SESSION_SNAPSHOT_SCHEMA:
        raise LyraGovernedEvidenceError("governed session snapshot schema differs")
    if (
        payload.get("prospective_governed_transition") is not True
        or payload.get("legacy_evaluation_relabelled") is not False
        or payload.get("execution_authority") is not False
        or payload.get("activation_authority") is not False
    ):
        raise LyraGovernedEvidenceError("governed session flags differ")
    trade_date = _date(payload.get("trade_date"), label="trade_date")
    execution_session = _date(
        payload.get("execution_session"), label="execution_session"
    )
    signal_as_of = _date(payload.get("signal_as_of"), label="signal_as_of")
    effective_target_date = _date(
        payload.get("effective_target_date"), label="effective_target_date"
    )
    _, observed = _timestamp(payload.get("as_of"), label="as_of")
    _, captured = _timestamp(payload.get("captured_at"), label="captured_at")
    if (
        execution_session != trade_date
        or signal_as_of != effective_target_date
        or signal_as_of >= execution_session
        or observed.date().isoformat() != execution_session
        or captured < observed
    ):
        raise LyraGovernedEvidenceError("governed session timing differs")
    _validate_weekly_session(
        signal_as_of=signal_as_of, execution_session=execution_session
    )
    for field in fields:
        if field.endswith("_hash"):
            _sha(payload.get(field), label=field)
    if payload.get("content_hash") != _hash(payload):
        raise LyraGovernedEvidenceError("governed session content_hash mismatch")
    return copy.deepcopy(dict(payload))


def _matrix(snapshot: Mapping[str, Any]) -> tuple[list[str], dict[str, dict[str, Mapping[str, Any]]]]:
    checked = validate_lyra_market_data_snapshot(snapshot)
    dates = list(checked["observation_dates"])
    matrix: dict[str, dict[str, Mapping[str, Any]]] = {
        symbol: {} for symbol in checked["required_symbols"]
    }
    for row in checked["rows"]:
        matrix[row["symbol"]][row["date"]] = row
    return dates, matrix


def build_lyra_forecast_risk_evidence(
    *, session_snapshot: Mapping[str, Any], market_data_snapshot: Mapping[str, Any],
    target_rows: Sequence[Mapping[str, Any]], risk_policy: Mapping[str, Any],
    risk_policy_proposal: Mapping[str, Any],
    risk_policy_owner_decision: Mapping[str, Any],
    target_selection_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    session = validate_lyra_governed_session_snapshot(session_snapshot)
    market = validate_lyra_market_data_snapshot(market_data_snapshot)
    policy = validate_lyra_forecast_risk_policy(risk_policy)
    proposal = validate_lyra_forecast_risk_policy_proposal(risk_policy_proposal)
    owner_decision = validate_lyra_forecast_risk_policy_owner_decision(
        risk_policy_owner_decision, proposal=proposal, as_of=session["captured_at"]
    )
    selection = validate_lyra_target_selection_evidence(target_selection_evidence)
    if session["market_data_snapshot_hash"] != market["content_hash"]:
        raise LyraGovernedEvidenceError("risk market snapshot binding differs")
    if session["forecast_risk_policy_hash"] != policy["content_hash"]:
        raise LyraGovernedEvidenceError("risk policy/session binding differs")
    if (
        session["forecast_risk_policy_proposal_hash"] != proposal["content_hash"]
        or session["forecast_risk_policy_owner_decision_hash"]
        != owner_decision["content_hash"]
        or policy["owner_decision_hash"] != owner_decision["content_hash"]
        or policy["approved_at"] != owner_decision["decided_at"]
        or _risk_policy_terms(effective_from=policy["effective_from"])
        != proposal["policy_terms"]
    ):
        raise LyraGovernedEvidenceError(
            "risk policy owner approval binding differs"
        )
    if session["target_selection_evidence_hash"] != selection["content_hash"]:
        raise LyraGovernedEvidenceError("target selection/session binding differs")
    if policy["effective_from"] > session["signal_as_of"]:
        raise LyraGovernedEvidenceError("risk policy is not effective for the signal")
    targets = normalized_target_rows(target_rows)
    if targets != selection["target_rows"]:
        raise LyraGovernedEvidenceError("risk targets differ from recomputed selection")
    if (
        selection["execution_session"] != session["execution_session"]
        or selection["signal_as_of"] != market["data_as_of"]
        or selection["source_sha256"] != market["source_sha256"]
    ):
        raise LyraGovernedEvidenceError("target selection/risk chronology differs")
    weights = {row["symbol"]: row["target_weight"] for row in targets}
    if sorted(weights) != market["required_symbols"]:
        raise LyraGovernedEvidenceError("risk target symbols differ from market snapshot")
    dates, matrix = _matrix(market)
    returns: list[float] = []
    observations: list[dict[str, Any]] = []
    for index in range(1, len(dates)):
        value = sum(
            weights[symbol]
            * (float(matrix[symbol][dates[index]]["close"]) / float(matrix[symbol][dates[index - 1]]["close"]) - 1.0)
            for symbol in sorted(weights)
        )
        # Seal the exact values used by the aggregate formula.  This avoids an
        # unrecorded full-precision intermediate that a validator cannot
        # reproduce from the artifact itself.
        value = round(value, 12)
        returns.append(value)
        observations.append({"date": dates[index], "portfolio_return": value})
    if len(returns) != 20:
        raise LyraGovernedEvidenceError("risk evidence requires exactly 20 returns")
    volatility = statistics.stdev(returns) * math.sqrt(ANNUALIZATION_FACTOR)
    body = {
        "schema_version": LYRA_FORECAST_RISK_SCHEMA,
        "evidence_id": "pending", "status": "PASS",
        "formula_id": RISK_FORMULA, "trade_date": session["trade_date"],
        "data_as_of": market["data_as_of"], "session_hash": session["content_hash"],
        "target_hash": target_hash(targets),
        "market_data_snapshot_hash": market["content_hash"],
        "lookback_sessions": 20, "observation_count": 20,
        "annualization_factor": ANNUALIZATION_FACTOR,
        "risk_policy": policy, "risk_policy_proposal": proposal,
        "risk_policy_owner_decision": owner_decision,
        "target_selection_evidence": selection,
        "portfolio_return_observations": observations,
        "annualized_volatility": round(volatility, 12),
        "execution_authority": False,
    }
    seed = _hash(body)
    body["evidence_id"] = f"lyra-risk:{session['trade_date']}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_lyra_forecast_risk_evidence(body)


def validate_lyra_forecast_risk_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "evidence_id", "status", "formula_id", "trade_date",
        "data_as_of", "session_hash", "target_hash", "market_data_snapshot_hash",
        "lookback_sessions", "observation_count", "annualization_factor",
        "risk_policy", "risk_policy_proposal", "risk_policy_owner_decision",
        "target_selection_evidence",
        "portfolio_return_observations", "annualized_volatility",
        "execution_authority", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise LyraGovernedEvidenceError("forecast risk evidence fields are invalid")
    policy = validate_lyra_forecast_risk_policy(payload.get("risk_policy"))
    proposal = validate_lyra_forecast_risk_policy_proposal(
        payload.get("risk_policy_proposal")
    )
    owner_decision = validate_lyra_forecast_risk_policy_owner_decision(
        payload.get("risk_policy_owner_decision"), proposal=proposal
    )
    selection = validate_lyra_target_selection_evidence(
        payload.get("target_selection_evidence")
    )
    if (
        payload.get("schema_version") != LYRA_FORECAST_RISK_SCHEMA
        or payload.get("status") != "PASS" or payload.get("formula_id") != RISK_FORMULA
        or payload.get("lookback_sessions") != 20 or payload.get("observation_count") != 20
        or payload.get("annualization_factor") != ANNUALIZATION_FACTOR
        or policy.get("formula_id") != payload.get("formula_id")
        or policy.get("owner_decision_hash") != owner_decision["content_hash"]
        or policy.get("approved_at") != owner_decision["decided_at"]
        or _risk_policy_terms(effective_from=policy["effective_from"])
        != proposal["policy_terms"]
        or selection.get("execution_session") != payload.get("trade_date")
        or selection.get("signal_as_of") != payload.get("data_as_of")
        or target_hash(selection.get("target_rows")) != payload.get("target_hash")
        or payload.get("execution_authority") is not False
    ):
        raise LyraGovernedEvidenceError("forecast risk evidence semantics differ")
    _date(payload.get("trade_date"), label="trade_date")
    _date(payload.get("data_as_of"), label="data_as_of")
    for field in ("session_hash", "target_hash", "market_data_snapshot_hash"):
        _sha(payload.get(field), label=field)
    observations = payload.get("portfolio_return_observations")
    if not isinstance(observations, list) or len(observations) != 20:
        raise LyraGovernedEvidenceError("forecast risk return observations differ")
    values: list[float] = []
    dates: list[str] = []
    for row in observations:
        if not isinstance(row, Mapping) or set(row) != {"date", "portfolio_return"}:
            raise LyraGovernedEvidenceError("forecast risk return row differs")
        dates.append(_date(row["date"], label="portfolio return date"))
        values.append(_finite(row["portfolio_return"], label="portfolio_return"))
    if dates != sorted(set(dates)):
        raise LyraGovernedEvidenceError("forecast risk return dates differ")
    expected_volatility = round(
        statistics.stdev(values) * math.sqrt(ANNUALIZATION_FACTOR), 12
    )
    if abs(float(payload.get("annualized_volatility", -1)) - expected_volatility) > 1e-12:
        raise LyraGovernedEvidenceError("forecast risk volatility is not recomputed")
    if payload.get("content_hash") != _hash(payload):
        raise LyraGovernedEvidenceError("forecast risk content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_lyra_liquidity_evidence(
    *, session_snapshot: Mapping[str, Any], market_data_snapshot: Mapping[str, Any],
    target_rows: Sequence[Mapping[str, Any]],
    governed_policy: Mapping[str, Any],
    governed_policy_proposal: Mapping[str, Any],
    governed_policy_owner_decision: Mapping[str, Any],
    capital_reference_usd: float = LIVE_V1_CAPITAL_REFERENCE_USD,
) -> dict[str, Any]:
    session = validate_lyra_governed_session_snapshot(session_snapshot)
    market = validate_lyra_market_data_snapshot(market_data_snapshot)
    policy = validate_lyra_forecast_risk_policy(governed_policy)
    proposal = validate_lyra_forecast_risk_policy_proposal(
        governed_policy_proposal
    )
    owner_decision = validate_lyra_forecast_risk_policy_owner_decision(
        governed_policy_owner_decision,
        proposal=proposal,
        as_of=session["captured_at"],
    )
    if session["market_data_snapshot_hash"] != market["content_hash"]:
        raise LyraGovernedEvidenceError("liquidity market snapshot binding differs")
    if (
        session["forecast_risk_policy_hash"] != policy["content_hash"]
        or session["forecast_risk_policy_proposal_hash"] != proposal["content_hash"]
        or session["forecast_risk_policy_owner_decision_hash"]
        != owner_decision["content_hash"]
        or policy["owner_decision_hash"] != owner_decision["content_hash"]
        or _risk_policy_terms(effective_from=policy["effective_from"])
        != proposal["policy_terms"]
    ):
        raise LyraGovernedEvidenceError(
            "liquidity governed policy binding differs"
        )
    capital = _finite(capital_reference_usd, label="capital_reference_usd", positive=True)
    if capital != LIVE_V1_CAPITAL_REFERENCE_USD:
        raise LyraGovernedEvidenceError("Lyra Live v1 liquidity capital reference must be $460")
    targets = normalized_target_rows(target_rows)
    weights = {row["symbol"]: row["target_weight"] for row in targets}
    if sorted(weights) != market["required_symbols"]:
        raise LyraGovernedEvidenceError("liquidity target symbols differ from market snapshot")
    dates, matrix = _matrix(market)
    liquidity_dates = dates[-LIQUIDITY_LOOKBACK:]
    rows: list[dict[str, Any]] = []
    for symbol in sorted(weights):
        dollar_values = [
            round(
                float(matrix[symbol][date]["close"])
                * float(matrix[symbol][date]["volume"]),
                6,
            )
            for date in liquidity_dates
        ]
        adv = sum(dollar_values) / LIQUIDITY_LOOKBACK
        max_notional = adv * MAX_PARTICIPATION_RATE
        max_order_notional = adv * MAX_ORDER_PARTICIPATION_RATE
        target_notional = capital * weights[symbol]
        rows.append({
            "symbol": symbol, "target_weight": weights[symbol],
            "dollar_volume_observations": [
                {"date": date, "dollar_volume": value}
                for date, value in zip(liquidity_dates, dollar_values)
            ],
            "mean_dollar_volume_20": round(adv, 6),
            "maximum_participation_notional_usd": round(max_notional, 6),
            "maximum_order_notional_usd": round(max_order_notional, 6),
            "target_notional_at_reference_capital_usd": round(target_notional, 6),
            "minimum_dollar_volume_pass": adv >= MINIMUM_MEAN_DOLLAR_VOLUME_USD,
            "order_participation_pass": target_notional <= max_order_notional,
            "liquidation_participation_pass": target_notional <= max_notional,
            "pass": (
                adv >= MINIMUM_MEAN_DOLLAR_VOLUME_USD
                and target_notional <= max_order_notional
                and target_notional <= max_notional
            ),
        })
    status = "PASS" if all(row["pass"] for row in rows) else "FAIL"
    body = {
        "schema_version": LYRA_LIQUIDITY_SCHEMA,
        "evidence_id": "pending", "status": status,
        "formula_id": LIQUIDITY_FORMULA, "trade_date": session["trade_date"],
        "data_as_of": market["data_as_of"], "session_hash": session["content_hash"],
        "target_hash": target_hash(targets),
        "market_data_snapshot_hash": market["content_hash"],
        "lookback_sessions": LIQUIDITY_LOOKBACK,
        "maximum_participation_rate": MAX_PARTICIPATION_RATE,
        "maximum_order_participation_rate": MAX_ORDER_PARTICIPATION_RATE,
        "minimum_mean_dollar_volume_usd": MINIMUM_MEAN_DOLLAR_VOLUME_USD,
        "capital_reference_usd": capital,
        "governed_policy": policy,
        "governed_policy_proposal": proposal,
        "governed_policy_owner_decision": owner_decision,
        "symbol_results": rows,
        "execution_authority": False,
    }
    seed = _hash(body)
    body["evidence_id"] = f"lyra-liquidity:{session['trade_date']}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_lyra_liquidity_evidence(body)


def validate_lyra_liquidity_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "evidence_id", "status", "formula_id", "trade_date",
        "data_as_of", "session_hash", "target_hash", "market_data_snapshot_hash",
        "lookback_sessions", "maximum_participation_rate",
        "maximum_order_participation_rate", "minimum_mean_dollar_volume_usd",
        "capital_reference_usd", "governed_policy",
        "governed_policy_proposal", "governed_policy_owner_decision",
        "symbol_results", "execution_authority",
        "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise LyraGovernedEvidenceError("liquidity evidence fields are invalid")
    policy = validate_lyra_forecast_risk_policy(payload.get("governed_policy"))
    proposal = validate_lyra_forecast_risk_policy_proposal(
        payload.get("governed_policy_proposal")
    )
    owner_decision = validate_lyra_forecast_risk_policy_owner_decision(
        payload.get("governed_policy_owner_decision"), proposal=proposal
    )
    rows = payload.get("symbol_results")
    if (
        payload.get("schema_version") != LYRA_LIQUIDITY_SCHEMA
        or payload.get("status") not in {"PASS", "FAIL"}
        or payload.get("formula_id") != LIQUIDITY_FORMULA
        or payload.get("lookback_sessions") != LIQUIDITY_LOOKBACK
        or payload.get("maximum_participation_rate") != MAX_PARTICIPATION_RATE
        or payload.get("maximum_order_participation_rate") != MAX_ORDER_PARTICIPATION_RATE
        or payload.get("minimum_mean_dollar_volume_usd") != MINIMUM_MEAN_DOLLAR_VOLUME_USD
        or payload.get("capital_reference_usd") != LIVE_V1_CAPITAL_REFERENCE_USD
        or policy["owner_decision_hash"] != owner_decision["content_hash"]
        or _risk_policy_terms(effective_from=policy["effective_from"])
        != proposal["policy_terms"]
        or payload.get("execution_authority") is not False
        or not isinstance(rows, list) or not rows
    ):
        raise LyraGovernedEvidenceError("liquidity evidence semantics differ")
    expected_status = "PASS"
    seen: set[str] = set()
    for row in rows:
        required = {
            "symbol", "target_weight", "mean_dollar_volume_20",
            "dollar_volume_observations",
            "maximum_participation_notional_usd",
            "maximum_order_notional_usd",
            "target_notional_at_reference_capital_usd",
            "minimum_dollar_volume_pass", "order_participation_pass",
            "liquidation_participation_pass", "pass",
        }
        if not isinstance(row, Mapping) or set(row) != required:
            raise LyraGovernedEvidenceError("liquidity symbol result differs")
        symbol = str(row["symbol"])
        if (
            not symbol or symbol in seen
            or any(type(row[field]) is not bool for field in (
                "minimum_dollar_volume_pass", "order_participation_pass",
                "liquidation_participation_pass", "pass",
            ))
        ):
            raise LyraGovernedEvidenceError("liquidity symbol identity/status differs")
        seen.add(symbol)
        target_notional = _finite(
            row["target_notional_at_reference_capital_usd"], label="target_notional"
        )
        observations = row["dollar_volume_observations"]
        if not isinstance(observations, list) or len(observations) != LIQUIDITY_LOOKBACK:
            raise LyraGovernedEvidenceError("liquidity dollar-volume observations differ")
        values: list[float] = []
        dates: list[str] = []
        for observation in observations:
            if not isinstance(observation, Mapping) or set(observation) != {"date", "dollar_volume"}:
                raise LyraGovernedEvidenceError("liquidity dollar-volume row differs")
            dates.append(_date(observation["date"], label="dollar-volume date"))
            values.append(_finite(observation["dollar_volume"], label="dollar_volume", positive=True))
        if dates != sorted(set(dates)):
            raise LyraGovernedEvidenceError("liquidity dollar-volume dates differ")
        dollar_volume = round(sum(values) / LIQUIDITY_LOOKBACK, 6)
        weight = _finite(row["target_weight"], label="target_weight", positive=True)
        expected_target_notional = round(LIVE_V1_CAPITAL_REFERENCE_USD * weight, 6)
        expected_max_order = round(dollar_volume * MAX_ORDER_PARTICIPATION_RATE, 6)
        expected_max_liquidation = round(dollar_volume * MAX_PARTICIPATION_RATE, 6)
        if (
            abs(float(row["mean_dollar_volume_20"]) - dollar_volume) > 1e-9
            or abs(target_notional - expected_target_notional) > 1e-9
            or abs(float(row["maximum_order_notional_usd"]) - expected_max_order) > 1e-9
            or abs(float(row["maximum_participation_notional_usd"]) - expected_max_liquidation) > 1e-9
        ):
            raise LyraGovernedEvidenceError("liquidity economics are not recomputed")
        expected_checks = {
            "minimum_dollar_volume_pass": dollar_volume >= MINIMUM_MEAN_DOLLAR_VOLUME_USD,
            "order_participation_pass": target_notional <= _finite(
                row["maximum_order_notional_usd"], label="max_order_notional"
            ),
            "liquidation_participation_pass": target_notional <= _finite(
                row["maximum_participation_notional_usd"], label="max_notional"
            ),
        }
        expected_pass = all(expected_checks.values())
        if any(row[field] != value for field, value in expected_checks.items()) or row["pass"] != expected_pass:
            raise LyraGovernedEvidenceError("liquidity symbol pass is not recomputed")
        if not expected_pass:
            expected_status = "FAIL"
    if payload["status"] != expected_status:
        raise LyraGovernedEvidenceError("liquidity aggregate status is not recomputed")
    for field in ("session_hash", "target_hash", "market_data_snapshot_hash"):
        _sha(payload.get(field), label=field)
    if payload.get("content_hash") != _hash(payload):
        raise LyraGovernedEvidenceError("liquidity content_hash mismatch")
    return copy.deepcopy(dict(payload))


def build_lyra_capacity_evidence(
    *, liquidity_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    liquidity = validate_lyra_liquidity_evidence(liquidity_evidence)
    capacities = [
        float(row["maximum_participation_notional_usd"]) / float(row["target_weight"])
        for row in liquidity["symbol_results"]
    ]
    capacity = min(capacities)
    status = (
        "PASS"
        if liquidity["status"] == "PASS"
        and capacity >= LIVE_V1_CAPITAL_REFERENCE_USD * MINIMUM_CAPACITY_MULTIPLE
        else "FAIL"
    )
    body = {
        "schema_version": LYRA_CAPACITY_SCHEMA,
        "evidence_id": "pending", "status": status,
        "formula_id": CAPACITY_FORMULA, "trade_date": liquidity["trade_date"],
        "data_as_of": liquidity["data_as_of"],
        "session_hash": liquidity["session_hash"],
        "target_hash": liquidity["target_hash"],
        "market_data_snapshot_hash": liquidity["market_data_snapshot_hash"],
        "maximum_deployable_capital_usd": round(capacity, 6),
        "required_capital_reference_usd": liquidity["capital_reference_usd"],
        "minimum_required_capacity_usd": round(
            liquidity["capital_reference_usd"] * MINIMUM_CAPACITY_MULTIPLE, 6
        ),
        "minimum_capacity_multiple": MINIMUM_CAPACITY_MULTIPLE,
        "liquidity_evidence": liquidity,
        "execution_authority": False,
    }
    seed = _hash(body)
    body["evidence_id"] = f"lyra-capacity:{liquidity['trade_date']}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_lyra_capacity_evidence(body)


def validate_lyra_capacity_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "evidence_id", "status", "formula_id", "trade_date",
        "data_as_of", "session_hash", "target_hash", "market_data_snapshot_hash",
        "maximum_deployable_capital_usd", "required_capital_reference_usd",
        "minimum_required_capacity_usd", "minimum_capacity_multiple",
        "liquidity_evidence", "execution_authority", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise LyraGovernedEvidenceError("capacity evidence fields are invalid")
    liquidity = validate_lyra_liquidity_evidence(payload.get("liquidity_evidence"))
    expected = min(
        float(row["maximum_participation_notional_usd"]) / float(row["target_weight"])
        for row in liquidity["symbol_results"]
    )
    expected_status = (
        "PASS"
        if liquidity["status"] == "PASS"
        and expected >= LIVE_V1_CAPITAL_REFERENCE_USD * MINIMUM_CAPACITY_MULTIPLE
        else "FAIL"
    )
    if (
        payload.get("schema_version") != LYRA_CAPACITY_SCHEMA
        or payload.get("formula_id") != CAPACITY_FORMULA
        or payload.get("status") != expected_status
        or payload.get("execution_authority") is not False
        or payload.get("required_capital_reference_usd") != LIVE_V1_CAPITAL_REFERENCE_USD
        or payload.get("minimum_capacity_multiple") != MINIMUM_CAPACITY_MULTIPLE
        or payload.get("minimum_required_capacity_usd")
        != LIVE_V1_CAPITAL_REFERENCE_USD * MINIMUM_CAPACITY_MULTIPLE
        or abs(float(payload.get("maximum_deployable_capital_usd", -1)) - round(expected, 6)) > 1e-9
    ):
        raise LyraGovernedEvidenceError("capacity evidence semantics differ")
    for field in ("trade_date", "data_as_of", "session_hash", "target_hash", "market_data_snapshot_hash"):
        if payload.get(field) != liquidity.get(field):
            raise LyraGovernedEvidenceError("capacity/liquidity lineage differs")
    if payload.get("content_hash") != _hash(payload):
        raise LyraGovernedEvidenceError("capacity content_hash mismatch")
    return copy.deepcopy(dict(payload))


def governed_evidence_source_artifacts(
    *, session_snapshot: Mapping[str, Any], market_data_snapshot: Mapping[str, Any],
    forecast_risk: Mapping[str, Any], capacity: Mapping[str, Any],
) -> list[dict[str, Any]]:
    session = validate_lyra_governed_session_snapshot(session_snapshot)
    market = validate_lyra_market_data_snapshot(market_data_snapshot)
    risk = validate_lyra_forecast_risk_evidence(forecast_risk)
    cap = validate_lyra_capacity_evidence(capacity)
    liquidity = cap["liquidity_evidence"]
    return sorted([
        {"artifact_type": "lyra_governed_session_snapshot", "schema_version": session["schema_version"], "content_hash": session["content_hash"], "sleeve_id": "caerus_lyra"},
        {"artifact_type": "lyra_market_data_snapshot", "schema_version": market["schema_version"], "content_hash": market["content_hash"], "sleeve_id": "caerus_lyra"},
        {"artifact_type": "lyra_forecast_risk_evidence", "schema_version": risk["schema_version"], "content_hash": risk["content_hash"], "sleeve_id": "caerus_lyra"},
        {"artifact_type": "lyra_forecast_risk_policy", "schema_version": risk["risk_policy"]["schema_version"], "content_hash": risk["risk_policy"]["content_hash"], "sleeve_id": "caerus_lyra"},
        {"artifact_type": "lyra_forecast_risk_policy_proposal", "schema_version": risk["risk_policy_proposal"]["schema_version"], "content_hash": risk["risk_policy_proposal"]["content_hash"], "sleeve_id": "caerus_lyra"},
        {"artifact_type": "lyra_forecast_risk_policy_owner_decision", "schema_version": risk["risk_policy_owner_decision"]["schema_version"], "content_hash": risk["risk_policy_owner_decision"]["content_hash"], "sleeve_id": "caerus_lyra"},
        {"artifact_type": "lyra_target_selection_evidence", "schema_version": risk["target_selection_evidence"]["schema_version"], "content_hash": risk["target_selection_evidence"]["content_hash"], "sleeve_id": "caerus_lyra"},
        {"artifact_type": "lyra_capacity_evidence", "schema_version": cap["schema_version"], "content_hash": cap["content_hash"], "sleeve_id": "caerus_lyra"},
        {"artifact_type": "lyra_liquidity_evidence", "schema_version": liquidity["schema_version"], "content_hash": liquidity["content_hash"], "sleeve_id": "caerus_lyra"},
    ], key=canonical_json)


__all__ = [
    "ANNUALIZATION_FACTOR", "CAPACITY_FORMULA", "LIQUIDITY_FORMULA",
    "LIVE_V1_CAPITAL_REFERENCE_USD", "LYRA_CAPACITY_SCHEMA",
    "LYRA_FORECAST_RISK_SCHEMA", "LYRA_LIQUIDITY_SCHEMA",
    "LYRA_RISK_POLICY_SCHEMA", "LYRA_RISK_POLICY_PROPOSAL_SCHEMA",
    "LYRA_RISK_POLICY_OWNER_DECISION_SCHEMA",
    "LYRA_MARKET_DATA_SNAPSHOT_SCHEMA", "LYRA_SESSION_SNAPSHOT_SCHEMA",
    "LyraGovernedEvidenceError", "MAX_PARTICIPATION_RATE",
    "MAX_ORDER_PARTICIPATION_RATE", "MINIMUM_CAPACITY_MULTIPLE",
    "MINIMUM_MEAN_DOLLAR_VOLUME_USD", "RISK_FORMULA",
    "TURNOVER_FORMULA", "build_lyra_capacity_evidence",
    "build_lyra_forecast_risk_evidence", "build_lyra_governed_session_snapshot",
    "build_lyra_liquidity_evidence", "build_lyra_market_data_snapshot",
    "governed_evidence_source_artifacts", "normalized_target_rows", "target_hash",
    "validate_lyra_capacity_evidence", "validate_lyra_forecast_risk_evidence",
    "validate_lyra_governed_session_snapshot", "validate_lyra_liquidity_evidence",
    "validate_lyra_market_data_snapshot", "validate_lyra_forecast_risk_policy",
    "validate_lyra_forecast_risk_policy_proposal",
    "validate_lyra_forecast_risk_policy_owner_decision",
]
