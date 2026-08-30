"""Frozen PRIMARY_V1 evaluator for HYP-2026-015.

The module is deliberately research-only.  It consumes a no-return structural
event inventory and a validation-only market slice supplied by the experiment
runner.  It cannot open the challenge period or alter any trading surface.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from projects.alpha_lab.factory.canonical import canonical_hash
from projects.alpha_lab.factory.errors import ContractValidationError


DISCOVERY_START = "2012-01-01"
DISCOVERY_END = "2018-12-31"
VALIDATION_START = "2019-01-01"
VALIDATION_END = "2024-12-31"
CHALLENGE_START = "2025-01-01"

PRIMARY_VARIANT_ID = "PRIMARY_V1"
REPORTER_RETURN_THRESHOLD = 0.05
ABNORMAL_VOLUME_THRESHOLD = 2.0
MINIMUM_NEW_PEERS = 3
BASE_ONE_WAY_COST = 0.0015
STRESS_ONE_WAY_COST = 0.0030
CAPACITY_FRACTION_OF_ADV = 0.05
CAPITAL_LEVELS = (100_000.0, 1_000_000.0, 10_000_000.0)
PRIMARY_CAPITAL = 1_000_000.0
MINIMUM_VALIDATION_UNITS = 150
MINIMUM_VALIDATION_PEERS = 100
MINIMUM_VALIDATION_SICS = 20
MAXIMUM_YEAR_SHARE = 0.30
MAXIMUM_SIC_SHARE = 0.25
MAXIMUM_REPORTER_ACTIVE_SHARE = 0.15
ECONOMIC_HURDLE = 0.005
INFERENCE_ALPHA = 0.10

REQUIRED_EVENT_FIELDS = frozenset(
    {
        "reaction_session",
        "entry_session",
        "exit_session",
        "sic4",
        "sic2",
        "reporter_security_id",
        "reporter_cik",
        "accession",
        "peer_security_ids",
        "industry_control_security_ids",
    }
)
REQUIRED_PRICE_FIELDS = frozenset(
    {
        "security_id",
        "date",
        "open",
        "close",
        "closeadj",
        "volume",
        "dollar_ADV_20",
    }
)
FACTOR_NAMES = ("MKT_RF", "SMB", "HML", "RMW", "CMA", "UMD")


@dataclass(frozen=True)
class ReporterSignal:
    security_id: str
    cik: str
    accessions: Tuple[str, ...]
    reaction_return: float
    abnormal_volume_ratio: float


def _iso_date(value: Any, field_name: str) -> str:
    text = str(value).split(" ", 1)[0]
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ContractValidationError("{} must be an ISO date".format(field_name)) from exc
    return text


def _quarter(value: str) -> str:
    parsed = date.fromisoformat(value)
    return "{}Q{}".format(parsed.year, ((parsed.month - 1) // 3) + 1)


def _finite_number(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("{} must be numeric".format(field_name)) from exc
    if not math.isfinite(number):
        raise ContractValidationError("{} must be finite".format(field_name))
    return number


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta function."""

    maximum_iterations = 300
    epsilon = 3.0e-14
    tiny = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for iteration in range(1, maximum_iterations + 1):
        twice = 2 * iteration
        numerator = iteration * (b - iteration) * x / (
            (qam + twice) * (a + twice)
        )
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        numerator = -(a + iteration) * (qab + iteration) * x / (
            (a + twice) * (qap + twice)
        )
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) <= epsilon:
            return result
    raise ArithmeticError("incomplete beta continued fraction did not converge")


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(value: float, degrees_of_freedom: int) -> float:
    if degrees_of_freedom < 1:
        raise ValueError("degrees_of_freedom must be positive")
    if value == 0.0:
        return 0.5
    x = degrees_of_freedom / (degrees_of_freedom + value * value)
    tail = 0.5 * _regularized_incomplete_beta(
        degrees_of_freedom / 2.0, 0.5, x
    )
    return 1.0 - tail if value > 0.0 else tail


def student_t_ppf(probability: float, degrees_of_freedom: int) -> float:
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be between zero and one")
    if probability == 0.5:
        return 0.0
    if probability < 0.5:
        return -student_t_ppf(1.0 - probability, degrees_of_freedom)
    low = 0.0
    high = 1.0
    while student_t_cdf(high, degrees_of_freedom) < probability:
        high *= 2.0
        if high > 1.0e8:
            raise ArithmeticError("Student-t quantile search did not bracket")
    for _ in range(100):
        middle = (low + high) / 2.0
        if student_t_cdf(middle, degrees_of_freedom) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def deterministic_cluster_inference(values: Sequence[float]) -> Dict[str, Any]:
    observations = [_finite_number(value, "inference observation") for value in values]
    count = len(observations)
    if not observations:
        return {
            "status": "NOT_EVALUABLE",
            "effective_sample_size": 0,
            "minimum_effective_sample": MINIMUM_VALIDATION_UNITS,
        }
    mean = statistics.fmean(observations)
    if count < 2:
        return {
            "status": "NOT_INFERENCE_ELIGIBLE",
            "effective_sample_size": count,
            "minimum_effective_sample": MINIMUM_VALIDATION_UNITS,
            "mean": mean,
            "sample_sd": None,
            "standard_error": None,
            "t_statistic": None,
            "raw_one_sided_p_value": None,
            "holm_adjusted_p_value": None,
            "by_adjusted_p_value": None,
            "one_sided_lcb_90": None,
        }
    sample_sd = statistics.stdev(observations)
    if sample_sd == 0.0:
        if mean > 0.0:
            p_value, lower_bound = 0.0, mean
        elif mean == 0.0:
            p_value, lower_bound = 0.5, 0.0
        else:
            p_value, lower_bound = 1.0, mean
        standard_error = 0.0
        t_statistic = None
    else:
        standard_error = sample_sd / math.sqrt(count)
        t_statistic = mean / standard_error
        p_value = 1.0 - student_t_cdf(t_statistic, count - 1)
        lower_bound = mean - student_t_ppf(0.90, count - 1) * standard_error
    return {
        "status": "INFERENCE_ELIGIBLE",
        "effective_sample_size": count,
        "minimum_effective_sample": MINIMUM_VALIDATION_UNITS,
        "mean": mean,
        "sample_sd": sample_sd,
        "standard_error": standard_error,
        "t_statistic": t_statistic,
        "raw_one_sided_p_value": p_value,
        "holm_adjusted_p_value": p_value,
        "by_adjusted_p_value": p_value,
        "one_sided_lcb_90": lower_bound,
        "holm_reject_at_0_10": p_value < INFERENCE_ALPHA,
        "by_reject_at_0_10": p_value < INFERENCE_ALPHA,
        "economic_hurdle_pass": mean >= ECONOMIC_HURDLE,
        "positive_lcb_pass": lower_bound > 0.0,
        "effective_sample_floor_pass": count >= MINIMUM_VALIDATION_UNITS,
    }


def _validate_events(events: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for ordinal, raw in enumerate(events, start=1):
        missing = REQUIRED_EVENT_FIELDS - set(raw)
        if missing:
            raise ContractValidationError(
                "event row {} is missing {}".format(ordinal, ",".join(sorted(missing)))
            )
        record = dict(raw)
        for field in ("reaction_session", "entry_session", "exit_session"):
            record[field] = _iso_date(record[field], field)
        if not (
            record["reaction_session"] < record["entry_session"] <= record["exit_session"]
        ):
            raise ContractValidationError("event session ordering is invalid")
        if record["reaction_session"] >= CHALLENGE_START:
            raise ContractValidationError("challenge-period event access is forbidden")
        sic4 = str(record["sic4"])
        sic2 = str(record["sic2"])
        if len(sic4) != 4 or not sic4.isdigit() or sic2 != sic4[:2]:
            raise ContractValidationError("event SIC fields are invalid")
        record["sic4"] = sic4
        record["sic2"] = sic2
        for field in ("peer_security_ids", "industry_control_security_ids"):
            if not isinstance(record[field], list):
                raise ContractValidationError("{} must be an ordered list".format(field))
            normalized = sorted({str(item) for item in record[field] if str(item)})
            if len(normalized) != len(record[field]):
                raise ContractValidationError("{} must contain unique non-empty IDs".format(field))
            record[field] = normalized
        contamination = record.get("peer_report_during_hold_security_ids", [])
        if not isinstance(contamination, list):
            raise ContractValidationError(
                "peer_report_during_hold_security_ids must be a list"
            )
        record["peer_report_during_hold_security_ids"] = sorted(
            {str(item) for item in contamination}
        )
        records.append(record)
    return records


def _index_prices(
    prices: Iterable[Mapping[str, Any]],
) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], Dict[str, Tuple[str, ...]]]:
    by_security: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for ordinal, raw in enumerate(prices, start=1):
        missing = REQUIRED_PRICE_FIELDS - set(raw)
        if missing:
            raise ContractValidationError(
                "price row {} is missing {}".format(ordinal, ",".join(sorted(missing)))
            )
        security_id = str(raw["security_id"])
        observed_date = _iso_date(raw["date"], "price date")
        if observed_date >= CHALLENGE_START:
            raise ContractValidationError("challenge-period price access is forbidden")
        if observed_date in by_security[security_id]:
            raise ContractValidationError("duplicate security/date price row")
        record = dict(raw)
        for field in ("open", "close", "closeadj", "volume", "dollar_ADV_20"):
            record[field] = _finite_number(record[field], field)
        if record["open"] <= 0.0 or record["close"] <= 0.0 or record["closeadj"] <= 0.0:
            raise ContractValidationError("non-positive market price")
        if record["volume"] < 0.0 or record["dollar_ADV_20"] < 0.0:
            raise ContractValidationError("negative liquidity field")
        by_security[security_id][observed_date] = record
    ordered_dates = {
        security_id: tuple(sorted(rows)) for security_id, rows in by_security.items()
    }
    return dict(by_security), ordered_dates


def _history_window(
    security_id: str,
    through_date: str,
    count: int,
    *,
    price_dates: Mapping[str, Tuple[str, ...]],
) -> Tuple[str, ...]:
    dates = price_dates.get(security_id, ())
    before = [item for item in dates if item < through_date]
    if len(before) < count:
        raise ContractValidationError(
            "{} lacks {} completed sessions before {}".format(
                security_id, count, through_date
            )
        )
    return tuple(before[-count:])


def _reaction_signal(
    record: Mapping[str, Any],
    *,
    prices: Mapping[str, Mapping[str, Mapping[str, Any]]],
    price_dates: Mapping[str, Tuple[str, ...]],
) -> ReporterSignal:
    security_id = str(record["reporter_security_id"])
    reaction_session = str(record["reaction_session"])
    history = _history_window(
        security_id, reaction_session, 20, price_dates=price_dates
    )
    current = prices.get(security_id, {}).get(reaction_session)
    if current is None:
        raise ContractValidationError("reporter reaction row is missing")
    previous = prices[security_id][history[-1]]
    reaction_return = current["closeadj"] / previous["closeadj"] - 1.0
    prior_dollar_volumes = [
        prices[security_id][item]["close"] * prices[security_id][item]["volume"]
        for item in history
    ]
    median_volume = statistics.median(prior_dollar_volumes)
    if median_volume <= 0.0:
        raise ContractValidationError("reporter prior dollar-volume median is non-positive")
    abnormal_volume = current["close"] * current["volume"] / median_volume
    return ReporterSignal(
        security_id=security_id,
        cik=str(record["reporter_cik"]),
        accessions=(str(record["accession"]),),
        reaction_return=reaction_return,
        abnormal_volume_ratio=abnormal_volume,
    )


def _security_return(
    security_id: str,
    event: Mapping[str, Any],
    *,
    prices: Mapping[str, Mapping[str, Mapping[str, Any]]],
    price_dates: Mapping[str, Tuple[str, ...]],
) -> Dict[str, float]:
    rows = prices.get(security_id, {})
    reaction = rows.get(str(event["reaction_session"]))
    entry = rows.get(str(event["entry_session"]))
    exit_row = rows.get(str(event["exit_session"]))
    if reaction is None or entry is None or exit_row is None:
        raise ContractValidationError(
            "incomplete reaction/holding path for {}".format(security_id)
        )
    adjustment_ratio = entry["closeadj"] / entry["close"]
    adjusted_open = entry["open"] * adjustment_ratio
    if adjusted_open <= 0.0:
        raise ContractValidationError("adjusted entry open is non-positive")
    history = _history_window(
        security_id,
        str(event["reaction_session"]),
        20,
        price_dates=price_dates,
    )
    prior_start = prices[security_id][history[0]]["closeadj"]
    prior_return = reaction["closeadj"] / prior_start - 1.0
    holding_dates = [
        item
        for item in price_dates.get(security_id, ())
        if str(event["entry_session"]) <= item <= str(event["exit_session"])
    ]
    if len(holding_dates) != 5 or holding_dates[-1] != str(event["exit_session"]):
        raise ContractValidationError(
            "{} does not have the frozen five-session holding path".format(security_id)
        )
    return {
        "one_session_total_return": rows[holding_dates[0]]["closeadj"] / adjusted_open - 1.0,
        "three_session_total_return": rows[holding_dates[2]]["closeadj"] / adjusted_open - 1.0,
        "five_session_total_return": exit_row["closeadj"] / adjusted_open - 1.0,
        "prior_20_session_return": prior_return,
        "trailing_median_dollar_adv": reaction["dollar_ADV_20"],
    }


def _capacity_weights(
    security_ids: Sequence[str],
    *,
    target_denominator: int,
    capital: float,
    event: Mapping[str, Any],
    prices: Mapping[str, Mapping[str, Mapping[str, Any]]],
    price_dates: Mapping[str, Tuple[str, ...]],
) -> Tuple[Dict[str, float], bool, Dict[str, Dict[str, float]]]:
    if target_denominator < 1:
        return {}, False, {}
    target_weight = 1.0 / target_denominator
    weights: Dict[str, float] = {}
    diagnostics: Dict[str, Dict[str, float]] = {}
    cap_bound = False
    for security_id in security_ids:
        path = _security_return(
            security_id, event, prices=prices, price_dates=price_dates
        )
        maximum_weight = (
            CAPACITY_FRACTION_OF_ADV * path["trailing_median_dollar_adv"] / capital
        )
        executed = min(target_weight, maximum_weight)
        if executed + 1.0e-15 < target_weight:
            cap_bound = True
        weights[security_id] = executed
        diagnostics[security_id] = {
            "target_weight": target_weight,
            "executed_weight": executed,
            "trailing_median_dollar_adv": path["trailing_median_dollar_adv"],
            "maximum_weight_at_five_percent_adv": maximum_weight,
        }
    return weights, not cap_bound, diagnostics


def _sleeve_result(
    target_security_ids: Sequence[str],
    allocatable_security_ids: Sequence[str],
    *,
    event: Mapping[str, Any],
    capital: float,
    prices: Mapping[str, Mapping[str, Mapping[str, Any]]],
    price_dates: Mapping[str, Tuple[str, ...]],
    target_denominator: int | None = None,
) -> Dict[str, Any]:
    denominator = len(target_security_ids) if target_denominator is None else target_denominator
    weights, capacity_pass, capacity_detail = _capacity_weights(
        allocatable_security_ids,
        target_denominator=denominator,
        capital=capital,
        event=event,
        prices=prices,
        price_dates=price_dates,
    )
    paths = {
        security_id: _security_return(
            security_id, event, prices=prices, price_dates=price_dates
        )
        for security_id in allocatable_security_ids
    }
    gross_weight = sum(weights.values())
    gross_returns = {
        horizon: sum(
            weights[security_id]
            * paths[security_id]["{}_session_total_return".format(horizon)]
            for security_id in allocatable_security_ids
        )
        for horizon in ("one", "three", "five")
    }
    gross_return = gross_returns["five"]
    return {
        "target_count": denominator,
        "allocatable_count": len(allocatable_security_ids),
        "overlap_cash_slots": denominator - len(allocatable_security_ids),
        "executed_gross_weight": gross_weight,
        "cash_weight": 1.0 - gross_weight,
        "gross_return": gross_return,
        "base_net_return": gross_return - 2.0 * BASE_ONE_WAY_COST * gross_weight,
        "stress_net_return": gross_return - 2.0 * STRESS_ONE_WAY_COST * gross_weight,
        "capacity_pass": capacity_pass,
        "capacity_detail": capacity_detail,
        "decay": {
            "one_session_base_net_return": (
                gross_returns["one"] - 2.0 * BASE_ONE_WAY_COST * gross_weight
            ),
            "three_session_base_net_return": (
                gross_returns["three"] - 2.0 * BASE_ONE_WAY_COST * gross_weight
            ),
            "five_session_base_net_return": (
                gross_return - 2.0 * BASE_ONE_WAY_COST * gross_weight
            ),
        },
        "constituent_returns": {
            security_id: paths[security_id]["five_session_total_return"]
            for security_id in allocatable_security_ids
        },
        "average_prior_20_session_return": (
            statistics.fmean(
                paths[security_id]["prior_20_session_return"]
                for security_id in allocatable_security_ids
            )
            if allocatable_security_ids
            else None
        ),
    }


def _allocatable(
    security_ids: Sequence[str],
    *,
    entry_session: str,
    open_until: Mapping[str, str],
) -> List[str]:
    return [
        security_id
        for security_id in security_ids
        if open_until.get(security_id, "") < entry_session
    ]


def _record_open_positions(
    security_ids: Sequence[str],
    *,
    exit_session: str,
    open_until: MutableMapping[str, str],
) -> None:
    for security_id in security_ids:
        open_until[security_id] = exit_session


def _aggregate_inference_units(
    clusters: Sequence[Mapping[str, Any]], metric: str
) -> Tuple[List[float], Dict[str, List[str]]]:
    grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for cluster in clusters:
        unit_key = "{}|{}|{}".format(
            cluster["reporter_set_id"],
            cluster["sic4"],
            cluster["reaction_quarter"],
        )
        grouped[unit_key].append(cluster)
    values: List[float] = []
    lineage: Dict[str, List[str]] = {}
    for unit_key in sorted(grouped):
        records = grouped[unit_key]
        values.append(statistics.fmean(float(item[metric]) for item in records))
        lineage[unit_key] = sorted(str(item["event_cluster_id"]) for item in records)
    return values, lineage


def _shares(clusters: Sequence[Mapping[str, Any]], field: str) -> Dict[str, float]:
    counts: Dict[str, int] = defaultdict(int)
    for cluster in clusters:
        counts[str(cluster[field])] += 1
    total = len(clusters)
    return {
        key: count / total for key, count in sorted(counts.items())
    } if total else {}


def _reporter_active_concentration(
    clusters: Sequence[Mapping[str, Any]], metric: str
) -> Dict[str, Any]:
    contributions: Dict[str, float] = defaultdict(float)
    absolute_total = 0.0
    for cluster in clusters:
        reporters = list(cluster["reporter_security_ids"])
        if not reporters:
            continue
        split = float(cluster[metric]) / len(reporters)
        for reporter in reporters:
            contributions[str(reporter)] += split
            absolute_total += abs(split)
    shares = {
        key: abs(value) / absolute_total
        for key, value in sorted(contributions.items())
    } if absolute_total else {key: 0.0 for key in sorted(contributions)}
    maximum = max(shares.values(), default=0.0)
    return {
        "method": "absolute active contribution split equally across reporter set",
        "shares": shares,
        "maximum_share": maximum,
        "pass": maximum <= MAXIMUM_REPORTER_ACTIVE_SHARE,
    }


def _drawdown(values: Sequence[float]) -> float | None:
    if not values:
        return None
    wealth = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        wealth *= 1.0 + value
        peak = max(peak, wealth)
        worst = min(worst, wealth / peak - 1.0)
    return worst


def _solve_linear_system(matrix: List[List[float]], vector: List[float]) -> List[float]:
    size = len(vector)
    augmented = [list(matrix[row]) + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1.0e-12:
            raise ArithmeticError("singular design")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            multiple = augmented[row][column]
            augmented[row] = [
                augmented[row][item] - multiple * augmented[column][item]
                for item in range(size + 1)
            ]
    return [augmented[row][-1] for row in range(size)]


def _ols(rows: Sequence[Sequence[float]], values: Sequence[float], names: Sequence[str]) -> Dict[str, Any]:
    if not rows:
        return {"status": "NOT_EVALUABLE", "reason": "insufficient_observations"}
    retained = [0]
    for column in range(1, len(names)):
        values_in_column = [row[column] for row in rows]
        if max(values_in_column) - min(values_in_column) > 1.0e-15:
            retained.append(column)
    reduced_names = [names[item] for item in retained]
    reduced_rows = [[row[item] for item in retained] for row in rows]
    if len(reduced_rows) <= len(reduced_names):
        return {"status": "NOT_EVALUABLE", "reason": "insufficient_observations"}
    dimension = len(reduced_names)
    xtx = [[0.0] * dimension for _ in range(dimension)]
    xty = [0.0] * dimension
    for row, outcome in zip(reduced_rows, values):
        for left in range(dimension):
            xty[left] += row[left] * outcome
            for right in range(dimension):
                xtx[left][right] += row[left] * row[right]
    try:
        coefficients = _solve_linear_system(xtx, xty)
    except ArithmeticError:
        return {"status": "NOT_EVALUABLE", "reason": "singular_design"}
    predictions = [
        sum(a * b for a, b in zip(row, coefficients)) for row in reduced_rows
    ]
    mean = statistics.fmean(values)
    residual_sum = sum((actual - predicted) ** 2 for actual, predicted in zip(values, predictions))
    total_sum = sum((actual - mean) ** 2 for actual in values)
    return {
        "status": "EVALUATED",
        "observation_count": len(values),
        "coefficients": dict(zip(reduced_names, coefficients)),
        "dropped_zero_variance_controls": [
            names[item] for item in range(1, len(names)) if item not in retained
        ],
        "r_squared": 1.0 - residual_sum / total_sum if total_sum else None,
    }


def _factor_attribution(
    clusters: Sequence[Mapping[str, Any]], factors: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    factor_by_date: Dict[str, Dict[str, float]] = {}
    for raw in factors:
        observed_date = _iso_date(raw["date"], "factor date")
        if observed_date >= CHALLENGE_START:
            raise ContractValidationError("challenge-period factor access is forbidden")
        factor_by_date[observed_date] = {
            name: _finite_number(raw[name], name) for name in FACTOR_NAMES
        }
    ordered_factor_dates = tuple(sorted(factor_by_date))
    names = (
        "intercept",
        *FACTOR_NAMES,
        "two_digit_sic_return",
        "prior_20_session_return",
        "market_volatility_20",
    )
    design: List[List[float]] = []
    outcomes: List[float] = []
    for cluster in clusters:
        holding_dates = [
            item
            for item in ordered_factor_dates
            if cluster["entry_session"] <= item <= cluster["exit_session"]
        ]
        prior_dates = [
            item for item in ordered_factor_dates if item <= cluster["reaction_session"]
        ][-20:]
        prior_return = cluster.get("candidate_prior_20_session_return")
        if len(holding_dates) != 5 or len(prior_dates) != 20 or prior_return is None:
            continue
        market_values = [factor_by_date[item]["MKT_RF"] for item in prior_dates]
        market_vol = statistics.stdev(market_values) if len(market_values) > 1 else 0.0
        row = [1.0]
        row.extend(
            sum(factor_by_date[item][name] for item in holding_dates)
            for name in FACTOR_NAMES
        )
        row.extend(
            [
                float(cluster["industry_gross_return"]),
                float(prior_return),
                market_vol,
            ]
        )
        design.append(row)
        outcomes.append(float(cluster["base_active_return"]))
    result = _ols(design, outcomes, names)
    result["controls_are_evaluation_only"] = True
    return result


def _phase_summary(clusters: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not clusters:
        return {"event_cluster_count": 0}
    base = [float(item["base_active_return"]) for item in clusters]
    stress = [float(item["stress_active_return"]) for item in clusters]
    return {
        "event_cluster_count": len(clusters),
        "mean_base_active_return": statistics.fmean(base),
        "mean_stress_active_return": statistics.fmean(stress),
        "mean_active_return_decay": {
            "one_session": statistics.fmean(
                float(item["one_session_base_active_return"]) for item in clusters
            ),
            "three_session": statistics.fmean(
                float(item["three_session_base_active_return"]) for item in clusters
            ),
            "five_session": statistics.fmean(base),
        },
        "base_hit_rate": sum(value > 0.0 for value in base) / len(base),
        "stress_hit_rate": sum(value > 0.0 for value in stress) / len(stress),
        "mean_candidate_gross_return": statistics.fmean(
            float(item["candidate_gross_return"]) for item in clusters
        ),
        "mean_industry_gross_return": statistics.fmean(
            float(item["industry_gross_return"]) for item in clusters
        ),
        "mean_reporter_base_net_return": statistics.fmean(
            float(item["reporter_base_net_return"]) for item in clusters
        ),
        "mean_raw_momentum_base_net_return": statistics.fmean(
            float(item["raw_momentum_base_net_return"]) for item in clusters
        ),
        "mean_candidate_round_trip_turnover": statistics.fmean(
            2.0 * float(item["candidate_executed_gross_weight"])
            for item in clusters
        ),
        "event_sequence_max_drawdown": _drawdown(base),
    }


def _build_directional_clusters(
    event_rows: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    prices: Mapping[str, Mapping[str, Mapping[str, Any]]],
    price_dates: Mapping[str, Tuple[str, ...]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in event_rows:
        grouped[(str(row["reaction_session"]), str(row["sic4"]))].append(row)
    open_until: Dict[str, Dict[str, str]] = {
        "candidate": {},
        "industry": {},
        "reporter": {},
        "momentum": {},
    }
    clusters: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for group_key in sorted(grouped):
        group = grouped[group_key]
        peer_sets = {tuple(row["peer_security_ids"]) for row in group}
        control_sets = {tuple(row["industry_control_security_ids"]) for row in group}
        if len(peer_sets) != 1 or len(control_sets) != 1:
            raise ContractValidationError(
                "same-session/SIC reporters require one structural peer/control inventory"
            )
        by_reporter: Dict[str, List[ReporterSignal]] = defaultdict(list)
        for row in group:
            signal = _reaction_signal(row, prices=prices, price_dates=price_dates)
            by_reporter[signal.security_id].append(signal)
        qualifying: List[ReporterSignal] = []
        qualifying_accessions: List[str] = []
        for security_id in sorted(by_reporter):
            signals = by_reporter[security_id]
            if direction == "POSITIVE":
                selected = [
                    item
                    for item in signals
                    if item.reaction_return >= REPORTER_RETURN_THRESHOLD
                    and item.abnormal_volume_ratio >= ABNORMAL_VOLUME_THRESHOLD
                ]
            else:
                selected = [
                    item
                    for item in signals
                    if item.reaction_return <= -REPORTER_RETURN_THRESHOLD
                    and item.abnormal_volume_ratio >= ABNORMAL_VOLUME_THRESHOLD
                ]
            if selected:
                first = sorted(selected, key=lambda item: item.accessions)[0]
                all_accessions = sorted(
                    {accession for item in selected for accession in item.accessions}
                )
                qualifying.append(
                    ReporterSignal(
                        security_id=first.security_id,
                        cik=first.cik,
                        accessions=tuple(all_accessions),
                        reaction_return=first.reaction_return,
                        abnormal_volume_ratio=first.abnormal_volume_ratio,
                    )
                )
                qualifying_accessions.extend(all_accessions)
        if not qualifying:
            continue
        reporter_ids = sorted(item.security_id for item in qualifying)
        reporter_set_id = canonical_hash(reporter_ids)
        event_cluster_id = canonical_hash(
            {
                "reaction_session": group_key[0],
                "sic4": group_key[1],
                "reporter_set_id": reporter_set_id,
                "qualifying_accessions": sorted(set(qualifying_accessions)),
            }
        )
        representative = dict(group[0])
        all_session_reporters = {str(row["reporter_security_id"]) for row in group}
        candidate_targets = [
            item
            for item in representative["peer_security_ids"]
            if item not in all_session_reporters
        ]
        industry_targets = [
            item
            for item in representative["industry_control_security_ids"]
            if item not in all_session_reporters and item not in candidate_targets
        ]
        entry_session = str(representative["entry_session"])
        exit_session = str(representative["exit_session"])
        candidate_allocatable = _allocatable(
            candidate_targets,
            entry_session=entry_session,
            open_until=open_until["candidate"],
        )
        if len(candidate_allocatable) < MINIMUM_NEW_PEERS:
            failures.append(
                {
                    "event_cluster_id": event_cluster_id,
                    "reaction_session": group_key[0],
                    "sic4": group_key[1],
                    "reason": "FEWER_THAN_THREE_NEWLY_ALLOCATABLE_PEERS",
                }
            )
            continue
        if not industry_targets:
            failures.append(
                {
                    "event_cluster_id": event_cluster_id,
                    "reaction_session": group_key[0],
                    "sic4": group_key[1],
                    "reason": "MISSING_PRIMARY_BASELINE",
                }
            )
            continue
        industry_allocatable = _allocatable(
            industry_targets,
            entry_session=entry_session,
            open_until=open_until["industry"],
        )
        if not industry_allocatable:
            failures.append(
                {
                    "event_cluster_id": event_cluster_id,
                    "reaction_session": group_key[0],
                    "sic4": group_key[1],
                    "reason": "MISSING_PRIMARY_BASELINE_AFTER_OVERLAP",
                }
            )
            continue
        reporter_allocatable = _allocatable(
            reporter_ids,
            entry_session=entry_session,
            open_until=open_until["reporter"],
        )
        momentum_ranked = sorted(
            industry_targets,
            key=lambda security_id: (
                -_security_return(
                    security_id,
                    representative,
                    prices=prices,
                    price_dates=price_dates,
                )["prior_20_session_return"],
                security_id,
            ),
        )[: len(candidate_targets)]
        momentum_allocatable = _allocatable(
            momentum_ranked,
            entry_session=entry_session,
            open_until=open_until["momentum"],
        )
        candidate = _sleeve_result(
            candidate_targets,
            candidate_allocatable,
            event=representative,
            capital=PRIMARY_CAPITAL,
            prices=prices,
            price_dates=price_dates,
        )
        industry = _sleeve_result(
            industry_targets,
            industry_allocatable,
            event=representative,
            capital=PRIMARY_CAPITAL,
            prices=prices,
            price_dates=price_dates,
        )
        reporter = _sleeve_result(
            reporter_ids,
            reporter_allocatable,
            event=representative,
            capital=PRIMARY_CAPITAL,
            prices=prices,
            price_dates=price_dates,
        )
        momentum = _sleeve_result(
            momentum_ranked,
            momentum_allocatable,
            event=representative,
            capital=PRIMARY_CAPITAL,
            prices=prices,
            price_dates=price_dates,
            target_denominator=len(candidate_targets),
        )
        capacity: Dict[str, Any] = {}
        for capital in CAPITAL_LEVELS:
            candidate_level = _sleeve_result(
                candidate_targets,
                candidate_allocatable,
                event=representative,
                capital=capital,
                prices=prices,
                price_dates=price_dates,
            )
            industry_level = _sleeve_result(
                industry_targets,
                industry_allocatable,
                event=representative,
                capital=capital,
                prices=prices,
                price_dates=price_dates,
            )
            capacity[str(int(capital))] = {
                "candidate_pass": candidate_level["capacity_pass"],
                "industry_control_pass": industry_level["capacity_pass"],
                "primary_pair_pass": (
                    candidate_level["capacity_pass"]
                    and industry_level["capacity_pass"]
                ),
                "candidate_executed_gross_weight": candidate_level[
                    "executed_gross_weight"
                ],
                "industry_executed_gross_weight": industry_level[
                    "executed_gross_weight"
                ],
            }
        reaction_returns = [item.reaction_return for item in qualifying]
        volume_ratios = [item.abnormal_volume_ratio for item in qualifying]
        contamination_set = set(
            representative.get("peer_report_during_hold_security_ids", [])
        )
        no_peer_report_allocatable = [
            item for item in candidate_allocatable if item not in contamination_set
        ]
        no_peer_report_candidate = _sleeve_result(
            candidate_targets,
            no_peer_report_allocatable,
            event=representative,
            capital=PRIMARY_CAPITAL,
            prices=prices,
            price_dates=price_dates,
        )
        contamination_returns = [
            candidate["constituent_returns"][item]
            for item in candidate_allocatable
            if item in contamination_set
        ]
        clean_returns = [
            candidate["constituent_returns"][item]
            for item in candidate_allocatable
            if item not in contamination_set
        ]
        cluster = {
            "event_cluster_id": event_cluster_id,
            "reporter_set_id": reporter_set_id,
            "reporter_security_ids": reporter_ids,
            "reporter_accessions": sorted(set(qualifying_accessions)),
            "reaction_session": group_key[0],
            "entry_session": entry_session,
            "exit_session": exit_session,
            "reaction_quarter": _quarter(group_key[0]),
            "calendar_year": group_key[0][:4],
            "sic4": group_key[1],
            "sic2": str(representative["sic2"]),
            "reporter_reaction_min": min(reaction_returns),
            "reporter_reaction_mean": statistics.fmean(reaction_returns),
            "reporter_reaction_max": max(reaction_returns),
            "reporter_abnormal_volume_min": min(volume_ratios),
            "reporter_abnormal_volume_mean": statistics.fmean(volume_ratios),
            "reporter_abnormal_volume_max": max(volume_ratios),
            "candidate_security_ids": candidate_allocatable,
            "candidate_target_security_ids": candidate_targets,
            "candidate_executed_gross_weight": candidate["executed_gross_weight"],
            "candidate_gross_return": candidate["gross_return"],
            "candidate_base_net_return": candidate["base_net_return"],
            "candidate_stress_net_return": candidate["stress_net_return"],
            "candidate_prior_20_session_return": candidate[
                "average_prior_20_session_return"
            ],
            "industry_gross_return": industry["gross_return"],
            "industry_base_net_return": industry["base_net_return"],
            "industry_stress_net_return": industry["stress_net_return"],
            "reporter_base_net_return": reporter["base_net_return"],
            "raw_momentum_base_net_return": momentum["base_net_return"],
            "candidate_minus_raw_momentum_base_net_return": (
                candidate["base_net_return"] - momentum["base_net_return"]
            ),
            "base_active_return": (
                candidate["base_net_return"] - industry["base_net_return"]
            ),
            "one_session_base_active_return": (
                candidate["decay"]["one_session_base_net_return"]
                - industry["decay"]["one_session_base_net_return"]
            ),
            "three_session_base_active_return": (
                candidate["decay"]["three_session_base_net_return"]
                - industry["decay"]["three_session_base_net_return"]
            ),
            "stress_active_return": (
                candidate["stress_net_return"] - industry["stress_net_return"]
            ),
            "capacity": capacity,
            "primary_one_million_capacity_pass": capacity["1000000"][
                "primary_pair_pass"
            ],
            "peer_report_contamination_count": len(contamination_returns),
            "no_peer_report_candidate_base_net_return": no_peer_report_candidate[
                "base_net_return"
            ],
            "no_peer_report_base_active_return": (
                no_peer_report_candidate["base_net_return"]
                - industry["base_net_return"]
            ),
            "peer_report_contamination_mean_return": (
                statistics.fmean(contamination_returns)
                if contamination_returns
                else None
            ),
            "no_peer_report_mean_return": (
                statistics.fmean(clean_returns) if clean_returns else None
            ),
        }
        clusters.append(cluster)
        _record_open_positions(
            candidate_allocatable,
            exit_session=exit_session,
            open_until=open_until["candidate"],
        )
        _record_open_positions(
            industry_allocatable,
            exit_session=exit_session,
            open_until=open_until["industry"],
        )
        _record_open_positions(
            reporter_allocatable,
            exit_session=exit_session,
            open_until=open_until["reporter"],
        )
        _record_open_positions(
            momentum_allocatable,
            exit_session=exit_session,
            open_until=open_until["momentum"],
        )
    return clusters, failures


def _adverse_sensitivity(
    validation_clusters: Sequence[Mapping[str, Any]],
    excluded_potential_clusters: Iterable[Mapping[str, Any]],
    *,
    included_breadth_pass: bool,
    included_capacity_pass: bool,
    included_concentration_pass: bool,
    included_reporter_concentration_pass: bool,
) -> Dict[str, Any]:
    excluded: Dict[str, Mapping[str, Any]] = {}
    for raw in excluded_potential_clusters:
        if raw.get("adverse_sensitivity_eligible") is not True:
            continue
        key = str(
            raw.get("potential_cluster_key") or raw.get("adverse_sensitivity_key") or ""
        )
        if not key:
            raise ContractValidationError(
                "excluded potential cluster requires potential_cluster_key"
            )
        reaction_session = (
            _iso_date(raw["reaction_session"], "reaction_session")
            if raw.get("reaction_session")
            else None
        )
        year = str(raw.get("year") or (reaction_session or "")[:4])
        if not (len(year) == 4 and year.isdigit()):
            raise ContractValidationError("excluded potential cluster requires causal year")
        if year >= CHALLENGE_START[:4]:
            raise ContractValidationError("challenge-period exclusion access is forbidden")
        if VALIDATION_START[:4] <= year <= VALIDATION_END[:4]:
            normalized = dict(raw)
            normalized["potential_cluster_key"] = key
            normalized["year"] = year
            normalized["reaction_session"] = reaction_session
            excluded.setdefault(key, normalized)
    if not validation_clusters:
        return {"status": "NOT_EVALUABLE", "reason": "no_included_validation_cluster"}
    minimum_base = min(float(item["base_active_return"]) for item in validation_clusters)
    minimum_stress = min(float(item["stress_active_return"]) for item in validation_clusters)
    synthetic = []
    for key, raw in sorted(excluded.items()):
        synthetic.append(
            {
                "event_cluster_id": "ADVERSE-{}".format(canonical_hash(key)[:16]),
                "reporter_set_id": str(raw.get("reporter_set_id") or "MISSING-{}".format(key)),
                "sic4": str(raw.get("sic4") or raw.get("sic") or "UNKNOWN"),
                "reaction_quarter": str(
                    raw.get("reaction_quarter")
                    or (
                        _quarter(str(raw["reaction_session"]))
                        if raw.get("reaction_session")
                        else "UNKNOWN-{}".format(canonical_hash(key)[:16])
                    )
                ),
                "calendar_year": str(raw["year"]),
                "reporter_security_ids": [
                    "EXCLUDED-{}".format(str(raw.get("issuer_cik") or key))
                ],
                "base_active_return": minimum_base,
                "stress_active_return": minimum_stress,
            }
        )
    combined = list(validation_clusters) + synthetic
    base_values, _ = _aggregate_inference_units(combined, "base_active_return")
    stress_values, _ = _aggregate_inference_units(combined, "stress_active_return")
    base_inference = deterministic_cluster_inference(base_values)
    stress_inference = deterministic_cluster_inference(stress_values)
    adverse_year_shares = _shares(combined, "calendar_year")
    adverse_sic_shares = _shares(combined, "sic4")
    adverse_reporter_concentration = _reporter_active_concentration(
        combined, "base_active_return"
    )
    adverse_concentration_pass = (
        max(adverse_year_shares.values(), default=0.0) <= MAXIMUM_YEAR_SHARE
        and max(adverse_sic_shares.values(), default=0.0) <= MAXIMUM_SIC_SHARE
        and adverse_reporter_concentration["pass"]
    )
    missing_count = len(synthetic)
    included_values, _ = _aggregate_inference_units(
        validation_clusters, "base_active_return"
    )
    break_even = None
    if missing_count:
        break_even = (
            (len(included_values) + missing_count) * ECONOMIC_HURDLE
            - sum(included_values)
        ) / missing_count
    return {
        "status": "EVALUATED",
        "unique_excluded_potential_cluster_count": missing_count,
        "synthetic_base_return": minimum_base,
        "synthetic_stress_return": minimum_stress,
        "base_inference": base_inference,
        "stress_inference": stress_inference,
        "year_shares": adverse_year_shares,
        "sic4_shares": adverse_sic_shares,
        "reporter_active_contribution": adverse_reporter_concentration,
        "adverse_concentration_pass": adverse_concentration_pass,
        "included_only_gates": {
            "breadth_pass": included_breadth_pass,
            "capacity_pass": included_capacity_pass,
            "concentration_pass": included_concentration_pass,
            "reporter_concentration_pass": included_reporter_concentration_pass,
        },
        "break_even_missing_cluster_mean_for_0_50_percent_hurdle": break_even,
        "pass": (
            bool(base_inference.get("economic_hurdle_pass"))
            and bool(base_inference.get("positive_lcb_pass"))
            and bool(base_inference.get("holm_reject_at_0_10"))
            and float(stress_inference.get("mean", float("-inf"))) > 0.0
            and bool(base_inference.get("effective_sample_floor_pass"))
            and included_breadth_pass
            and included_capacity_pass
            and included_concentration_pass
            and included_reporter_concentration_pass
            and adverse_concentration_pass
        ),
    }


def evaluate_primary_v1(
    event_rows: Iterable[Mapping[str, Any]],
    price_rows: Iterable[Mapping[str, Any]],
    factor_rows: Iterable[Mapping[str, Any]],
    *,
    excluded_potential_clusters: Iterable[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """Evaluate the single frozen discovery/validation variant.

    The caller must supply a validation-only price slice.  Any 2025-or-later
    event, price, factor, or excluded-cluster row is rejected before a result is
    returned.
    """

    events = _validate_events(event_rows)
    prices, price_dates = _index_prices(price_rows)
    positive_clusters, failures = _build_directional_clusters(
        events,
        direction="POSITIVE",
        prices=prices,
        price_dates=price_dates,
    )
    negative_clusters, negative_failures = _build_directional_clusters(
        events,
        direction="NEGATIVE",
        prices=prices,
        price_dates=price_dates,
    )
    discovery = [
        item
        for item in positive_clusters
        if DISCOVERY_START <= item["reaction_session"] <= DISCOVERY_END
    ]
    validation = [
        item
        for item in positive_clusters
        if VALIDATION_START <= item["reaction_session"] <= VALIDATION_END
    ]
    validation_base_values, unit_lineage = _aggregate_inference_units(
        validation, "base_active_return"
    )
    validation_stress_values, _ = _aggregate_inference_units(
        validation, "stress_active_return"
    )
    validation_no_peer_report_values, _ = _aggregate_inference_units(
        validation, "no_peer_report_base_active_return"
    )
    validation_raw_momentum_values, _ = _aggregate_inference_units(
        validation, "candidate_minus_raw_momentum_base_net_return"
    )
    primary_inference = deterministic_cluster_inference(validation_base_values)
    stress_inference = deterministic_cluster_inference(validation_stress_values)
    no_peer_report_inference = deterministic_cluster_inference(
        validation_no_peer_report_values
    )
    raw_momentum_inference = deterministic_cluster_inference(
        validation_raw_momentum_values
    )
    unique_peers = sorted(
        {
            security_id
            for cluster in validation
            for security_id in cluster["candidate_security_ids"]
        }
    )
    unique_sics = sorted({str(item["sic4"]) for item in validation})
    year_shares = _shares(validation, "calendar_year")
    sic_shares = _shares(validation, "sic4")
    reporter_concentration = _reporter_active_concentration(
        validation, "base_active_return"
    )
    capacity_pass = bool(validation) and all(
        bool(item["primary_one_million_capacity_pass"]) for item in validation
    )
    concentration_pass = (
        max(year_shares.values(), default=0.0) <= MAXIMUM_YEAR_SHARE
        and max(sic_shares.values(), default=0.0) <= MAXIMUM_SIC_SHARE
        and reporter_concentration["pass"]
    )
    breadth_pass = (
        len(validation_base_values) >= MINIMUM_VALIDATION_UNITS
        and len(unique_peers) >= MINIMUM_VALIDATION_PEERS
        and len(unique_sics) >= MINIMUM_VALIDATION_SICS
    )
    adverse = _adverse_sensitivity(
        validation,
        excluded_potential_clusters,
        included_breadth_pass=breadth_pass,
        included_capacity_pass=capacity_pass,
        included_concentration_pass=concentration_pass,
        included_reporter_concentration_pass=reporter_concentration["pass"],
    )
    contamination = [
        float(item["peer_report_contamination_mean_return"])
        for item in validation
        if item["peer_report_contamination_mean_return"] is not None
    ]
    uncontaminated = [
        float(item["no_peer_report_mean_return"])
        for item in validation
        if item["no_peer_report_mean_return"] is not None
    ]
    negative_validation = [
        item
        for item in negative_clusters
        if VALIDATION_START <= item["reaction_session"] <= VALIDATION_END
    ]
    annual_expanding = {}
    for end_year in range(2019, 2025):
        selected = [
            item for item in validation if int(item["calendar_year"]) <= end_year
        ]
        values, _ = _aggregate_inference_units(selected, "base_active_return")
        annual_expanding[str(end_year)] = deterministic_cluster_inference(values)
    contamination_sign_reversal = (
        primary_inference.get("mean") is not None
        and no_peer_report_inference.get("mean") is not None
        and float(primary_inference["mean"])
        * float(no_peer_report_inference["mean"])
        < 0.0
    )
    factor_attribution = _factor_attribution(validation, factor_rows)
    factor_intercept = factor_attribution.get("coefficients", {}).get("intercept")
    factor_explanation_gate_pass = (
        factor_attribution.get("status") == "EVALUATED"
        and factor_intercept is not None
        and float(factor_intercept) > 0.0
    )
    raw_momentum_gate_pass = (
        raw_momentum_inference.get("mean") is not None
        and float(raw_momentum_inference["mean"]) > 0.0
    )
    primary_pass = (
        breadth_pass
        and capacity_pass
        and concentration_pass
        and bool(primary_inference.get("economic_hurdle_pass"))
        and bool(primary_inference.get("positive_lcb_pass"))
        and bool(primary_inference.get("holm_reject_at_0_10"))
        and float(stress_inference.get("mean", float("-inf"))) > 0.0
        and adverse.get("pass") is True
        and not contamination_sign_reversal
        and raw_momentum_gate_pass
        and factor_explanation_gate_pass
    )
    return {
        "schema_version": "caerus_alpha_lab_hyp_2026_015_primary_v1_result_v1",
        "hypothesis_id": "HYP-2026-015",
        "experiment_id": "EXP-2026-0015",
        "family_id": "FAMILY-2026-0015",
        "variant_id": PRIMARY_VARIANT_ID,
        "variant_count": 1,
        "primary_metric_name": "mean_5_session_base_cost_net_peer_minus_industry_return",
        "primary_metric_value": primary_inference.get("mean"),
        "discovery": _phase_summary(discovery),
        "validation": _phase_summary(validation),
        "primary_inference": primary_inference,
        "stress_inference": stress_inference,
        "annual_expanding_validation": annual_expanding,
        "inference_unit_lineage": unit_lineage,
        "breadth": {
            "independent_unit_count": len(validation_base_values),
            "minimum_independent_units": MINIMUM_VALIDATION_UNITS,
            "unique_peer_count": len(unique_peers),
            "minimum_unique_peers": MINIMUM_VALIDATION_PEERS,
            "unique_sic4_count": len(unique_sics),
            "minimum_unique_sic4": MINIMUM_VALIDATION_SICS,
            "pass": breadth_pass,
        },
        "concentration": {
            "year_shares": year_shares,
            "maximum_year_share": max(year_shares.values(), default=None),
            "maximum_allowed_year_share": MAXIMUM_YEAR_SHARE,
            "sic4_shares": sic_shares,
            "maximum_sic4_share": max(sic_shares.values(), default=None),
            "maximum_allowed_sic4_share": MAXIMUM_SIC_SHARE,
            "reporter_active_contribution": reporter_concentration,
            "pass": concentration_pass,
        },
        "capacity": {
            "levels_dollars": [int(item) for item in CAPITAL_LEVELS],
            "primary_level_dollars": int(PRIMARY_CAPITAL),
            "primary_pair_pass_for_every_validation_cluster": capacity_pass,
        },
        "factor_industry_momentum_attribution": factor_attribution,
        "raw_momentum_comparison": {
            "candidate_minus_raw_momentum_inference": raw_momentum_inference,
            "positive_classification_gate_pass": raw_momentum_gate_pass,
        },
        "factor_explanation_gate_pass": factor_explanation_gate_pass,
        "peer_report_contamination": {
            "contaminated_cluster_count": len(contamination),
            "mean_contaminated_peer_return": (
                statistics.fmean(contamination) if contamination else None
            ),
            "mean_nonreporting_peer_return": (
                statistics.fmean(uncontaminated) if uncontaminated else None
            ),
            "no_peer_report_active_inference": no_peer_report_inference,
            "validation_active_return_sign_reversal": contamination_sign_reversal,
            "positive_classification_gate_pass": not contamination_sign_reversal,
        },
        "negative_shock_symmetry": {
            "classification": "DESCRIPTIVE_ONLY",
            "validation_cluster_count": len(negative_validation),
            "mean_base_active_return": (
                statistics.fmean(
                    float(item["base_active_return"]) for item in negative_validation
                )
                if negative_validation
                else None
            ),
            "failed_cluster_count": len(negative_failures),
        },
        "adverse_missingness_sensitivity": adverse,
        "failed_positive_cluster_count": len(failures),
        "failed_positive_clusters": failures,
        "event_cluster_results": positive_clusters,
        "primary_validation_pass": primary_pass,
        "challenge_period_accessed": False,
        "orders_submitted": False,
        "promotion_performed": False,
        "trading_behavior_changed": False,
        "portfolio_utility_comparators": {
            "status": "INCOMPLETE_EVIDENCE_NOT_PROVIDED",
            "comparators": ["Polaris", "Orion", "Lyra", "SPY"],
            "used_for_selection": False,
        },
        "secondary_diagnostic_completeness": {
            "one_three_five_session_decay": True,
            "matched_date_portfolio_utility_comparators": False,
            "complete_alpha_card_eligible": False,
        },
    }
