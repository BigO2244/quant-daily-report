"""Pure feature and hypothetical target construction for current-chain proxies."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from projects.alpha_lab.factory import ContractValidationError, canonical_hash

from .config import ProxyConfig


def _finite_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def black_scholes_delta(
    *,
    spot: float,
    strike: float,
    time_years: float,
    volatility: float,
    option_type: str,
    risk_free_rate: float,
    dividend_yield: float,
) -> float:
    if spot <= 0 or strike <= 0 or time_years <= 0 or volatility <= 0:
        raise ContractValidationError("invalid Black-Scholes delta inputs")
    if option_type not in {"call", "put"}:
        raise ContractValidationError("option_type must be call or put")
    d1 = (
        math.log(spot / strike)
        + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_years
    ) / (volatility * math.sqrt(time_years))
    discounted = math.exp(-dividend_yield * time_years)
    call_delta = discounted * _normal_cdf(d1)
    return call_delta if option_type == "call" else call_delta - discounted


def _weighted_average(pairs: Iterable[Tuple[float, float]]) -> Optional[float]:
    numerator = 0.0
    denominator = 0.0
    for value, weight in pairs:
        if math.isfinite(value) and math.isfinite(weight) and weight > 0:
            numerator += value * weight
            denominator += weight
    return numerator / denominator if denominator > 0 else None


def _one_underlying_feature(
    chain: Mapping[str, Any],
    *,
    as_of_date: date,
    previous_iv_skew: Optional[float],
    config: ProxyConfig,
) -> Dict[str, Any]:
    symbol = str(chain.get("symbol") or "")
    spot = _finite_float(chain.get("spot"))
    raw_contracts = chain.get("contracts")
    if spot is None or spot <= 0 or not isinstance(raw_contracts, list):
        return {
            "symbol": symbol,
            "status": "BLOCKED_SOURCE",
            "blockers": ["missing_positive_spot_or_contract_collection"],
            "valid_contract_count": 0,
            "nonzero_volume_contract_count": 0,
        }

    eligible = []
    exclusion_counts: Dict[str, int] = {}
    for raw in raw_contracts:
        if not isinstance(raw, Mapping):
            exclusion_counts["invalid_record"] = exclusion_counts.get("invalid_record", 0) + 1
            continue
        reason = None
        option_type = str(raw.get("option_type") or "")
        try:
            expiration = date.fromisoformat(str(raw.get("expiration") or ""))
        except ValueError:
            reason = "invalid_expiration"
            expiration = as_of_date
        dte = (expiration - as_of_date).days
        strike = _finite_float(raw.get("strike"))
        bid = _finite_float(raw.get("bid"))
        ask = _finite_float(raw.get("ask"))
        iv = _finite_float(raw.get("implied_volatility"))
        volume = _finite_float(raw.get("volume"))
        open_interest = _finite_float(raw.get("open_interest"))
        if reason is None and option_type not in {"call", "put"}:
            reason = "invalid_option_type"
        if reason is None and not (config.minimum_dte <= dte <= config.maximum_dte):
            reason = "dte_outside_window"
        if reason is None and (strike is None or strike <= 0):
            reason = "invalid_strike"
        if reason is None and (bid is None or ask is None or bid <= 0 or ask < bid):
            reason = "invalid_nbbo_proxy"
        if reason is None and (iv is None or iv <= 0):
            reason = "invalid_implied_volatility"
        if reason is None and (volume is None or volume < 0):
            reason = "missing_volume"
        if reason is None and (open_interest is None or open_interest < 0):
            reason = "missing_open_interest"
        if reason is not None:
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
            continue
        assert strike is not None and iv is not None and volume is not None
        assert open_interest is not None
        delta = black_scholes_delta(
            spot=spot,
            strike=strike,
            time_years=max(dte, 1) / 365.0,
            volatility=iv,
            option_type=option_type,
            risk_free_rate=config.risk_free_rate_assumption,
            dividend_yield=config.dividend_yield_assumption,
        )
        if not (
            config.minimum_absolute_delta
            <= abs(delta)
            <= config.maximum_absolute_delta
        ):
            exclusion_counts["delta_outside_window"] = (
                exclusion_counts.get("delta_outside_window", 0) + 1
            )
            continue
        eligible.append(
            {
                "option_type": option_type,
                "strike": strike,
                "volume": volume,
                "open_interest": open_interest,
                "iv": iv,
                "delta": delta,
                "dte": dte,
            }
        )

    nonzero = [row for row in eligible if row["volume"] > 0]
    blockers = []
    if len(eligible) < config.minimum_valid_contracts:
        blockers.append("insufficient_valid_contracts")
    if len(nonzero) < config.minimum_nonzero_volume_contracts:
        blockers.append("insufficient_nonzero_volume_contracts")

    call_delta_volume = sum(
        abs(row["delta"]) * row["volume"]
        for row in eligible
        if row["option_type"] == "call"
    )
    put_delta_volume = sum(
        abs(row["delta"]) * row["volume"]
        for row in eligible
        if row["option_type"] == "put"
    )
    volume_denominator = call_delta_volume + put_delta_volume
    volume_imbalance = (
        (call_delta_volume - put_delta_volume) / volume_denominator
        if volume_denominator > 0
        else None
    )

    call_oi = sum(row["open_interest"] for row in eligible if row["option_type"] == "call")
    put_oi = sum(row["open_interest"] for row in eligible if row["option_type"] == "put")
    oi_denominator = call_oi + put_oi
    oi_imbalance = (call_oi - put_oi) / oi_denominator if oi_denominator > 0 else None

    call_moneyness = _weighted_average(
        (
            math.log(row["strike"] / spot),
            abs(row["delta"]) * max(row["volume"], 1.0),
        )
        for row in eligible
        if row["option_type"] == "call"
    )
    put_moneyness = _weighted_average(
        (
            math.log(row["strike"] / spot),
            abs(row["delta"]) * max(row["volume"], 1.0),
        )
        for row in eligible
        if row["option_type"] == "put"
    )
    strike_displacement = (
        call_moneyness + put_moneyness
        if call_moneyness is not None and put_moneyness is not None
        else None
    )

    def surface_weight(row: Mapping[str, float]) -> float:
        delta_distance = abs(abs(row["delta"]) - 0.25)
        tenor_distance = abs(row["dte"] - 30.0) / 30.0
        return 1.0 / (0.05 + delta_distance + tenor_distance)

    call_iv = _weighted_average(
        (row["iv"], surface_weight(row))
        for row in eligible
        if row["option_type"] == "call" and 0.15 <= abs(row["delta"]) <= 0.35
    )
    put_iv = _weighted_average(
        (row["iv"], surface_weight(row))
        for row in eligible
        if row["option_type"] == "put" and 0.15 <= abs(row["delta"]) <= 0.35
    )
    iv_skew_level = call_iv - put_iv if call_iv is not None and put_iv is not None else None
    iv_skew_change = (
        iv_skew_level - previous_iv_skew
        if iv_skew_level is not None and previous_iv_skew is not None
        else None
    )
    if iv_skew_change is None:
        blockers.append("prior_iv_skew_unavailable")
    if volume_imbalance is None:
        blockers.append("volume_imbalance_unavailable")
    if strike_displacement is None:
        blockers.append("strike_displacement_unavailable")

    return {
        "symbol": symbol,
        "status": "READY_FOR_PROXY_SCORING" if not blockers else "BLOCKED_PROXY_SCORING",
        "blockers": sorted(set(blockers)),
        "spot": spot,
        "source_contract_count": len(raw_contracts),
        "valid_contract_count": len(eligible),
        "nonzero_volume_contract_count": len(nonzero),
        "exclusion_counts": exclusion_counts,
        "delta_weighted_call_put_volume_imbalance": volume_imbalance,
        "call_put_open_interest_imbalance": oi_imbalance,
        "strike_location_displacement_proxy": strike_displacement,
        "call_minus_put_iv_skew_level": iv_skew_level,
        "one_observation_iv_skew_change": iv_skew_change,
        "delta_method": "black_scholes_r0_q0_proxy",
    }


def build_feature_rows(
    snapshot: Mapping[str, Any],
    *,
    config: ProxyConfig,
    previous_features: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    as_of_date = date.fromisoformat(str(snapshot["as_of_date"]))
    previous_features = previous_features or {}
    rows = []
    for chain in snapshot.get("chains", []):
        symbol = str(chain.get("symbol") or "")
        prior = previous_features.get(symbol, {})
        prior_skew = _finite_float(prior.get("call_minus_put_iv_skew_level"))
        rows.append(
            _one_underlying_feature(
                chain,
                as_of_date=as_of_date,
                previous_iv_skew=prior_skew,
                config=config,
            )
        )
    return sorted(rows, key=lambda row: row["symbol"])


def _percentile_ranks(
    rows: Sequence[Dict[str, Any]],
    field: str,
    sector_by_symbol: Mapping[str, str],
) -> Dict[str, float]:
    result = {}
    sectors = sorted({sector_by_symbol[row["symbol"]] for row in rows})
    for sector in sectors:
        values = sorted(
            (float(row[field]), row["symbol"])
            for row in rows
            if sector_by_symbol[row["symbol"]] == sector
            and _finite_float(row.get(field)) is not None
        )
        grouped: Dict[float, List[Tuple[int, str]]] = {}
        for index, (value, symbol) in enumerate(values):
            grouped.setdefault(value, []).append((index, symbol))
        denominator = max(len(values) - 1, 1)
        for members in grouped.values():
            average_index = sum(index for index, _ in members) / len(members)
            rank = average_index / denominator if len(values) > 1 else 0.5
            for _, symbol in members:
                result[symbol] = rank
    return result


def build_signal(
    *,
    snapshot: Mapping[str, Any],
    feature_rows: Sequence[Dict[str, Any]],
    config: ProxyConfig,
) -> Dict[str, Any]:
    scoreable = [
        row
        for row in feature_rows
        if row["symbol"] != config.benchmark_symbol
        and row.get("status") == "READY_FOR_PROXY_SCORING"
    ]
    fields = (
        "delta_weighted_call_put_volume_imbalance",
        "strike_location_displacement_proxy",
        "one_observation_iv_skew_change",
    )
    ranks = {
        field: _percentile_ranks(scoreable, field, config.sector_by_symbol)
        for field in fields
    }
    scored = []
    for row in scoreable:
        symbol = row["symbol"]
        raw_values = [_finite_float(row.get(field)) for field in fields]
        if any(value is None for value in raw_values):
            continue
        volume_value, strike_value, iv_change_value = raw_values
        assert volume_value is not None and strike_value is not None
        assert iv_change_value is not None
        concordant = volume_value > 0 and strike_value > 0 and iv_change_value > 0
        score = (
            0.50 * ranks[fields[0]][symbol]
            + 0.30 * ranks[fields[1]][symbol]
            + 0.20 * ranks[fields[2]][symbol]
        )
        scored.append(
            {
                "symbol": symbol,
                "sector": config.sector_by_symbol[symbol],
                "proxy_score": score,
                "concordant_positive_components": concordant,
                "component_percentile_ranks": {
                    field: ranks[field][symbol] for field in fields
                },
            }
        )
    concordant_rows = sorted(
        (row for row in scored if row["concordant_positive_components"]),
        key=lambda row: (-row["proxy_score"], row["symbol"]),
    )
    universe_count = len(config.candidate_symbols)
    desired_count = max(1, math.ceil(universe_count * config.top_fraction))
    selected = concordant_rows[: min(desired_count, config.maximum_positions)]
    equal_weight = min(
        config.maximum_position_weight,
        1.0 / len(selected) if selected else 0.0,
    )
    targets = [
        {
            "symbol": row["symbol"],
            "research_target_weight": equal_weight,
            "proxy_score": row["proxy_score"],
        }
        for row in selected
    ]
    source_success_count = int(snapshot.get("source_success_count", 0))
    source_coverage = source_success_count / len(config.symbols)
    scoreable_coverage = len(scoreable) / len(config.candidate_symbols)
    decision_eligible = (
        snapshot.get("collection_window_status") == "DECISION_TIME_ELIGIBLE"
        and source_coverage >= config.minimum_source_coverage
        and scoreable_coverage >= config.minimum_source_coverage
        and bool(targets)
    )
    payload = {
        "schema_version": "caerus_options_proxy_signal_v1",
        "hypothesis_id": config.hypothesis_id,
        "classification": "PROXY_FORWARD_OBSERVATION_ONLY",
        "evidence_relationship": config.experiment_relationship,
        "alpha_claim_permitted": False,
        "trading_or_order_artifact": False,
        "snapshot_id": snapshot["snapshot_id"],
        "as_of_date": snapshot["as_of_date"],
        "available_at": snapshot["available_at"],
        "collection_window_status": snapshot.get("collection_window_status"),
        "benchmark_symbol": config.benchmark_symbol,
        "sector_classification": config.sector_classification,
        "source_coverage": source_coverage,
        "scoreable_symbol_count": len(scoreable),
        "scoreable_coverage": scoreable_coverage,
        "concordant_symbol_count": len(concordant_rows),
        "baseline_symbols": sorted(row["symbol"] for row in scoreable),
        "research_targets": targets,
        "cash_weight": max(0.0, 1.0 - equal_weight * len(targets)),
        "decision_eligible": decision_eligible,
        "decision_blockers": [],
        "limitations": [
            "no_trade_aggressor_side",
            "no_prevailing_trade_time_nbbo",
            "no_exchange_or_condition_codes",
            "no_quote_sizes",
            "no_occ_adjustment_lineage",
            "current_chain_forward_observation_only",
            "static_current_sector_map_not_historical_pit_classification",
        ],
        "scored_rows": scored,
        "config_hash": config.config_hash,
    }
    if snapshot.get("collection_window_status") != "DECISION_TIME_ELIGIBLE":
        payload["decision_blockers"].append("collection_before_frozen_decision_time")
    if source_coverage < config.minimum_source_coverage:
        payload["decision_blockers"].append("source_coverage_below_threshold")
    if scoreable_coverage < config.minimum_source_coverage:
        payload["decision_blockers"].append("scoreable_coverage_below_threshold")
    if not targets:
        payload["decision_blockers"].append("no_concordant_proxy_targets")
    payload["signal_hash"] = canonical_hash(payload)
    return payload
