"""Tier 3 Caerus promotion governance.

Reads Tier 1 + Tier 2 research artifacts and emits deterministic
strategy-level decisions (PROMOTE / WATCH / HOLD / DEMOTE / BLOCKED)
under six gates:

  1. observation window  (20 / 40 / 60 trading days)
  2. performance         (excess return vs Polaris, persistence, hit-rate
                          stability)
  3. differentiation     (no weak diff vs incumbent, correlation cap,
                          active-share floor)
  4. risk                (single-name, top-3/5, sector concentration)
  5. universe            (clean security master, no stale/unknown symbols)
  6. execution timing    (coverage available, no timing failures)

Research-only. Never writes production weights, never touches broker
or strategy config. Conservative by construction: PROMOTE is emitted
only when every gate passes; any missing input degrades the strategy
to BLOCKED with explicit reason codes.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "caerus_promotion_governance_v1"

STRATEGIES = ("caerus_polaris", "caerus_orion", "caerus_lyra")
CONTROL_STRATEGY = "caerus_polaris"

WINDOW_MIN_WATCH = 20
WINDOW_MIN_CANDIDATE = 40
WINDOW_MIN_PROMOTE = 60

# Risk thresholds. As of FR-040 the single-name / top-3 / top-5 caps
# are design-aware and come from research/governance_calibration.py so
# a 5-name equal-weight portfolio is not blocked by a 10% cap built for
# diversified portfolios. The legacy fixed constants below are kept
# only as the historical baseline used by the reclassification artifact.
MAX_SINGLE_NAME_WEIGHT = 0.10  # legacy; see calibrated_thresholds_for()
MAX_TOP3_CONCENTRATION = 0.40  # legacy
MAX_TOP5_CONCENTRATION = 0.60  # legacy
MAX_SECTOR_CONCENTRATION = 0.50

# Differentiation thresholds.
MAX_CORRELATION_VS_INCUMBENT = 0.90
MIN_ACTIVE_SHARE = 0.50

# Performance thresholds.
HIT_RATE_DETERIORATION_TOLERANCE = 0.05  # 5 percentage points

# Execution timing thresholds.
MIN_TIMING_COVERAGE_RATIO = 0.50

GATE_PASS = "PASS"
GATE_BLOCKED = "BLOCKED"
GATE_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

DECISION_PROMOTE = "PROMOTE"
DECISION_PROMOTION_CANDIDATE = "PROMOTION_CANDIDATE"
DECISION_WATCH = "WATCH"
DECISION_HOLD = "HOLD"
DECISION_DEMOTE = "DEMOTE"
DECISION_BLOCKED = "BLOCKED"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except Exception:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _round(value: Any, digits: int = 10) -> float | None:
    f = _safe_float(value)
    return round(f, digits) if f is not None else None


# ---------------------------------------------------------------------------
# Input loaders (each returns the loaded dict + a list of missing reasons).
# ---------------------------------------------------------------------------

def _load_input(repo: Path, *parts: str, fallback_artifact: str) -> tuple[dict[str, Any] | None, list[str], str]:
    path = repo / "outputs" / "research" / Path(*parts)
    payload = _read_json(path)
    if payload is None:
        return None, [f"missing_{fallback_artifact}"], str(path)
    return payload, [], str(path)


def _load_promotion_windows(repo: Path, trade_date: str) -> tuple[dict[str, Any] | None, list[str], str]:
    return _load_input(
        repo,
        "promotion_readiness",
        trade_date,
        "promotion_readiness_windows.json",
        fallback_artifact="promotion_readiness_windows",
    )


def _load_strategy_differentiation(repo: Path, trade_date: str) -> tuple[dict[str, Any] | None, list[str], str]:
    return _load_input(
        repo,
        "strategy_differentiation",
        trade_date,
        "strategy_differentiation.json",
        fallback_artifact="strategy_differentiation_deep",
    )


def _load_risk_coverage(repo: Path, trade_date: str) -> tuple[dict[str, Any] | None, list[str], str]:
    return _load_input(
        repo,
        "risk_coverage",
        trade_date,
        "risk_coverage.json",
        fallback_artifact="risk_coverage",
    )


def _load_universe_governance(repo: Path, trade_date: str) -> tuple[dict[str, Any] | None, list[str], str]:
    return _load_input(
        repo,
        "universe_governance",
        trade_date,
        "universe_governance.json",
        fallback_artifact="universe_governance",
    )


def _load_execution_timing(repo: Path, trade_date: str) -> tuple[dict[str, Any] | None, list[str], str]:
    return _load_input(
        repo,
        "execution_timing",
        trade_date,
        "execution_timing_summary.json",
        fallback_artifact="execution_timing_summary",
    )


def _load_position_sizing(repo: Path, trade_date: str) -> tuple[dict[str, Any] | None, list[str], str]:
    return _load_input(
        repo,
        "position_sizing",
        trade_date,
        "position_sizing_research.json",
        fallback_artifact="position_sizing_research",
    )


# ---------------------------------------------------------------------------
# Gate evaluators
# ---------------------------------------------------------------------------

def _evaluate_observation_window_gate(
    strategy: str,
    promotion_windows: dict[str, Any] | None,
) -> dict[str, Any]:
    if promotion_windows is None:
        return {
            "gate": "observation_window",
            "status": GATE_INSUFFICIENT_DATA,
            "max_observation_count": 0,
            "windows_present": [],
            "reason_codes": ["missing_promotion_readiness_windows"],
        }
    strategies = promotion_windows.get("strategies") or {}
    rows = (strategies.get(strategy) or {}).get("windows") or {}
    counts: list[int] = []
    windows_present: list[int] = []
    for window_label, row in sorted(rows.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        if not isinstance(row, dict):
            continue
        obs = _safe_float(row.get("observation_count"))
        if obs is None:
            continue
        windows_present.append(int(window_label) if str(window_label).isdigit() else 0)
        counts.append(int(obs))
    max_obs = max(counts) if counts else 0
    reasons: list[str] = []
    if max_obs < WINDOW_MIN_WATCH:
        status = GATE_BLOCKED
        reasons.append("insufficient_observation_window")
    else:
        status = GATE_PASS
    return {
        "gate": "observation_window",
        "status": status,
        "max_observation_count": int(max_obs),
        "windows_present": sorted(windows_present),
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _evaluate_performance_gate(
    strategy: str,
    promotion_windows: dict[str, Any] | None,
) -> dict[str, Any]:
    if promotion_windows is None:
        return {
            "gate": "performance",
            "status": GATE_INSUFFICIENT_DATA,
            "positive_excess_window_count": 0,
            "windows_evaluated": 0,
            "hit_rate_deterioration": None,
            "reason_codes": ["missing_promotion_readiness_windows"],
        }
    strategies = promotion_windows.get("strategies") or {}
    rows = (strategies.get(strategy) or {}).get("windows") or {}
    if not rows:
        return {
            "gate": "performance",
            "status": GATE_INSUFFICIENT_DATA,
            "positive_excess_window_count": 0,
            "windows_evaluated": 0,
            "hit_rate_deterioration": None,
            "reason_codes": ["no_window_metrics_for_strategy"],
        }
    reasons: list[str] = []
    positive_excess_windows = 0
    hit_rates: list[tuple[int, float]] = []
    windows_evaluated = 0
    for window_label, row in rows.items():
        if not isinstance(row, dict):
            continue
        windows_evaluated += 1
        excess = _safe_float(row.get("excess_return_vs_polaris"))
        if excess is not None and excess > 0.0:
            positive_excess_windows += 1
        hit_rate = _safe_float(row.get("hit_rate"))
        try:
            window_size = int(window_label)
        except Exception:
            window_size = 0
        if hit_rate is not None and window_size > 0:
            hit_rates.append((window_size, hit_rate))
    deterioration: float | None = None
    if len(hit_rates) >= 2:
        hit_rates.sort(key=lambda kv: kv[0])
        small_window_hit_rate = hit_rates[0][1]
        large_window_hit_rate = hit_rates[-1][1]
        deterioration = small_window_hit_rate - large_window_hit_rate
        if deterioration is not None and deterioration > HIT_RATE_DETERIORATION_TOLERANCE:
            reasons.append("hit_rate_deteriorated")
    if strategy != CONTROL_STRATEGY and positive_excess_windows < 2:
        reasons.append("excess_return_not_persistent")
    status = GATE_BLOCKED if reasons else GATE_PASS
    return {
        "gate": "performance",
        "status": status,
        "positive_excess_window_count": positive_excess_windows,
        "windows_evaluated": windows_evaluated,
        "hit_rate_deterioration": _round(deterioration),
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _evaluate_differentiation_gate(
    strategy: str,
    strategy_differentiation: dict[str, Any] | None,
) -> dict[str, Any]:
    if strategy != CONTROL_STRATEGY and strategy_differentiation is None:
        return {
            "gate": "differentiation",
            "status": GATE_INSUFFICIENT_DATA,
            "pairs_evaluated": 0,
            "weak_pair_count": 0,
            "max_correlation_vs_incumbent": None,
            "min_active_share_proxy": None,
            "reason_codes": ["missing_strategy_differentiation"],
        }
    if strategy == CONTROL_STRATEGY:
        # The benchmark/control strategy is differentiated by construction;
        # this gate does not apply.
        return {
            "gate": "differentiation",
            "status": GATE_PASS,
            "pairs_evaluated": 0,
            "weak_pair_count": 0,
            "max_correlation_vs_incumbent": None,
            "min_active_share_proxy": None,
            "reason_codes": ["control_strategy_not_evaluated"],
        }
    pairs = strategy_differentiation.get("pairs") or []
    relevant: list[dict[str, Any]] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        left = str(pair.get("left_strategy") or "")
        right = str(pair.get("right_strategy") or "")
        if strategy not in (left, right):
            continue
        relevant.append(pair)
    if not relevant:
        return {
            "gate": "differentiation",
            "status": GATE_INSUFFICIENT_DATA,
            "pairs_evaluated": 0,
            "weak_pair_count": 0,
            "max_correlation_vs_incumbent": None,
            "min_active_share_proxy": None,
            "reason_codes": ["no_differentiation_pairs_for_strategy"],
        }
    reasons: list[str] = []
    weak_pair_count = 0
    correlations: list[float] = []
    active_shares: list[float] = []
    for pair in relevant:
        flag = str(pair.get("differentiation_readiness_flag") or "").upper()
        if flag and flag != "STRONG":
            weak_pair_count += 1
            reasons.append("weak_differentiation")
        corr = _safe_float(pair.get("daily_return_correlation"))
        if corr is not None:
            correlations.append(corr)
            other = pair.get("right_strategy") if pair.get("left_strategy") == strategy else pair.get("left_strategy")
            if str(other) != strategy and corr > MAX_CORRELATION_VS_INCUMBENT:
                reasons.append("correlation_above_cap_vs_incumbent")
        active = _safe_float(pair.get("average_active_share_proxy"))
        if active is not None:
            active_shares.append(active)
            if active < MIN_ACTIVE_SHARE:
                reasons.append("active_share_below_floor")
    status = GATE_BLOCKED if reasons else GATE_PASS
    return {
        "gate": "differentiation",
        "status": status,
        "pairs_evaluated": len(relevant),
        "weak_pair_count": int(weak_pair_count),
        "max_correlation_vs_incumbent": _round(max(correlations) if correlations else None),
        "min_active_share_proxy": _round(min(active_shares) if active_shares else None),
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _evaluate_risk_gate(
    strategy: str,
    risk_coverage: dict[str, Any] | None,
) -> dict[str, Any]:
    # Imported here so research.governance_calibration can sit on top
    # of research.promotion_governance for the reclassification artifact
    # without a circular import at module load time.
    from research.governance_calibration import (
        CALIBRATION_STATUS_CLEAN,
        DESIGN_DIVERSIFIED,
        DESIGN_UNKNOWN,
        calibrated_thresholds_for,
        classify_design,
    )

    if risk_coverage is None or not bool(risk_coverage.get("available")):
        return {
            "gate": "risk",
            "status": GATE_INSUFFICIENT_DATA,
            "risk_level": "UNKNOWN",
            "max_single_name_weight": None,
            "top3_concentration": None,
            "top5_concentration": None,
            "sector_concentration": None,
            "design_class": DESIGN_UNKNOWN,
            "calibrated_thresholds": dict(calibrated_thresholds_for(None)),
            "calibration_status": CALIBRATION_STATUS_CLEAN,
            "reason_codes": ["missing_or_unavailable_risk_coverage"],
        }
    strategies = risk_coverage.get("strategies") or {}
    row = strategies.get(strategy)
    if not isinstance(row, dict) or not row.get("available"):
        return {
            "gate": "risk",
            "status": GATE_INSUFFICIENT_DATA,
            "risk_level": "UNKNOWN",
            "max_single_name_weight": None,
            "top3_concentration": None,
            "top5_concentration": None,
            "sector_concentration": None,
            "design_class": DESIGN_UNKNOWN,
            "calibrated_thresholds": dict(calibrated_thresholds_for(None)),
            "calibration_status": CALIBRATION_STATUS_CLEAN,
            "reason_codes": ["no_risk_row_for_strategy"],
        }
    reasons: list[str] = []
    max_name = _safe_float(row.get("max_single_name_weight"))
    top3 = _safe_float(row.get("top3_concentration"))
    top5 = _safe_float(row.get("top5_concentration"))
    sector = _safe_float(row.get("sector_concentration"))
    position_count = int(_safe_float(row.get("position_count")) or 0)
    design_class = classify_design(position_count if position_count > 0 else None)
    thresholds = calibrated_thresholds_for(position_count if position_count > 0 else None)
    if max_name is not None and max_name > thresholds["max_single_name_allowed"] + 1e-9:
        reasons.append("single_name_concentration_above_calibrated_cap")
    if top3 is not None and top3 > thresholds["top3_allowed"] + 1e-9:
        reasons.append("top3_concentration_above_calibrated_cap")
    if top5 is not None and top5 > thresholds["top5_allowed"] + 1e-9:
        reasons.append("top5_concentration_above_calibrated_cap")
    if sector is not None and sector > MAX_SECTOR_CONCENTRATION:
        reasons.append("sector_concentration_above_cap")
    risk_level = str(row.get("risk_level") or "UNKNOWN").upper()
    status = GATE_BLOCKED if reasons else GATE_PASS
    calibration_status = (
        "TRUE_CONCENTRATION_RISK"
        if any(r.endswith("_above_calibrated_cap") for r in reasons)
        else CALIBRATION_STATUS_CLEAN
    )
    return {
        "gate": "risk",
        "status": status,
        "risk_level": risk_level,
        "max_single_name_weight": _round(max_name),
        "top3_concentration": _round(top3),
        "top5_concentration": _round(top5),
        "sector_concentration": _round(sector),
        "position_count": position_count if position_count > 0 else None,
        "design_class": design_class,
        "calibrated_thresholds": {k: _round(v) for k, v in thresholds.items()},
        "calibration_status": calibration_status,
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _evaluate_universe_gate(
    universe_governance: dict[str, Any] | None,
) -> dict[str, Any]:
    if universe_governance is None:
        return {
            "gate": "universe",
            "status": GATE_INSUFFICIENT_DATA,
            "stale_universe": None,
            "blocker_count": 0,
            "reason_codes": ["missing_universe_governance"],
        }
    if not bool(universe_governance.get("available")):
        reasons = ["universe_governance_unavailable"]
        for code in universe_governance.get("blockers") or []:
            reasons.append(str(code))
        for code in universe_governance.get("reason_codes") or []:
            if code and code != "ok":
                reasons.append(str(code))
        return {
            "gate": "universe",
            "status": GATE_BLOCKED,
            "stale_universe": bool(universe_governance.get("stale_universe")),
            "blocker_count": len(universe_governance.get("blockers") or []),
            "reason_codes": sorted(set(reasons)) or ["ok"],
        }
    reasons: list[str] = []
    if bool(universe_governance.get("stale_universe")):
        reasons.append("stale_universe")
    blockers = universe_governance.get("blockers") or []
    for code in blockers:
        reasons.append(str(code))
    status = GATE_BLOCKED if reasons else GATE_PASS
    return {
        "gate": "universe",
        "status": status,
        "stale_universe": bool(universe_governance.get("stale_universe")),
        "blocker_count": len(blockers),
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def _evaluate_execution_timing_gate(
    execution_timing: dict[str, Any] | None,
) -> dict[str, Any]:
    if execution_timing is None:
        return {
            "gate": "execution_timing",
            "status": GATE_INSUFFICIENT_DATA,
            "coverage_ratio": None,
            "reason_codes": ["missing_execution_timing_summary"],
        }
    if not bool(execution_timing.get("available")):
        reasons = ["execution_timing_unavailable"]
        for code in execution_timing.get("reason_codes") or []:
            if code and code != "ok":
                reasons.append(str(code))
        return {
            "gate": "execution_timing",
            "status": GATE_BLOCKED,
            "coverage_ratio": _round(execution_timing.get("coverage_ratio")),
            "reason_codes": sorted(set(reasons)) or ["ok"],
        }
    reasons: list[str] = []
    coverage = _safe_float(execution_timing.get("coverage_ratio"))
    if coverage is None or coverage < MIN_TIMING_COVERAGE_RATIO:
        reasons.append("execution_timing_coverage_below_floor")
    missing_bars = execution_timing.get("symbols_missing_bars") or []
    if missing_bars:
        reasons.append("execution_timing_symbols_missing_bars")
    status = GATE_BLOCKED if reasons else GATE_PASS
    return {
        "gate": "execution_timing",
        "status": status,
        "coverage_ratio": _round(coverage),
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------

def _decide_strategy(
    strategy: str,
    *,
    observation_window: dict[str, Any],
    performance: dict[str, Any],
    differentiation: dict[str, Any],
    risk: dict[str, Any],
    universe: dict[str, Any],
    execution_timing: dict[str, Any],
) -> tuple[str, list[str]]:
    """Combine the six gates into a single decision and reason-code list."""
    blockers: list[str] = []
    insufficient: list[str] = []
    for gate in (observation_window, performance, differentiation, risk, universe, execution_timing):
        status = gate.get("status")
        gate_name = gate.get("gate", "unknown")
        if status == GATE_BLOCKED:
            for code in gate.get("reason_codes") or []:
                if code and code != "ok":
                    blockers.append(f"{gate_name}:{code}")
        elif status == GATE_INSUFFICIENT_DATA:
            for code in gate.get("reason_codes") or []:
                if code and code != "ok":
                    insufficient.append(f"{gate_name}:{code}")

    if insufficient:
        return DECISION_BLOCKED, sorted(set(insufficient + blockers))

    max_obs = int(observation_window.get("max_observation_count") or 0)

    # Control strategy stays control whatever happens, unless every gate
    # is wrecked — in which case it is BLOCKED for the day with reasons.
    if strategy == CONTROL_STRATEGY:
        if blockers:
            return DECISION_BLOCKED, sorted(set(blockers))
        return DECISION_HOLD, ["ok"]

    if blockers:
        # Map blockers to a decision based on which gates failed:
        #   observation_window / universe / execution_timing  → BLOCKED
        #     (data hygiene; cannot evaluate the candidate fairly)
        #   performance / risk                                 → DEMOTE
        #     (true regression evidence)
        #   differentiation only                               → HOLD
        #     (model concern, not a data or perf failure)
        data_blockers = [
            b for b in blockers
            if b.split(":", 1)[0] in ("observation_window", "universe", "execution_timing")
        ]
        perf_or_risk_blockers = [
            b for b in blockers if b.split(":", 1)[0] in ("performance", "risk")
        ]
        if data_blockers:
            return DECISION_BLOCKED, sorted(set(blockers))
        if perf_or_risk_blockers:
            return DECISION_DEMOTE, sorted(set(blockers))
        return DECISION_HOLD, sorted(set(blockers))

    if max_obs >= WINDOW_MIN_PROMOTE:
        return DECISION_PROMOTE, ["ok"]
    if max_obs >= WINDOW_MIN_CANDIDATE:
        return DECISION_PROMOTION_CANDIDATE, ["ok"]
    if max_obs >= WINDOW_MIN_WATCH:
        return DECISION_WATCH, ["ok"]
    return DECISION_BLOCKED, ["observation_window:insufficient_observation_window"]


def _evidence_strength(decision: str, blockers: list[str]) -> str:
    if decision in (DECISION_PROMOTE, DECISION_PROMOTION_CANDIDATE):
        return "HIGH"
    if decision == DECISION_WATCH:
        return "MEDIUM"
    if decision == DECISION_HOLD:
        return "MEDIUM" if not blockers else "LOW"
    return "LOW"


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_promotion_governance(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)

    promotion_windows, p_missing, p_path = _load_promotion_windows(repo, trade_date)
    differentiation, d_missing, d_path = _load_strategy_differentiation(repo, trade_date)
    risk_coverage, r_missing, r_path = _load_risk_coverage(repo, trade_date)
    universe_governance, u_missing, u_path = _load_universe_governance(repo, trade_date)
    execution_timing, e_missing, e_path = _load_execution_timing(repo, trade_date)
    position_sizing, _ps_missing, ps_path = _load_position_sizing(repo, trade_date)

    source_artifacts: list[str] = []
    for path, loaded in (
        (p_path, promotion_windows is not None),
        (d_path, differentiation is not None),
        (r_path, risk_coverage is not None),
        (u_path, universe_governance is not None),
        (e_path, execution_timing is not None),
        (ps_path, position_sizing is not None),
    ):
        if loaded:
            source_artifacts.append(path)

    aggregate_reasons: list[str] = []
    aggregate_reasons.extend(p_missing)
    aggregate_reasons.extend(d_missing)
    aggregate_reasons.extend(r_missing)
    aggregate_reasons.extend(u_missing)
    aggregate_reasons.extend(e_missing)

    strategy_decisions: dict[str, dict[str, Any]] = {}
    promote_candidates: list[str] = []
    demote_candidates: list[str] = []
    blocker_categories: set[str] = set()

    for strategy in STRATEGIES:
        obs_gate = _evaluate_observation_window_gate(strategy, promotion_windows)
        perf_gate = _evaluate_performance_gate(strategy, promotion_windows)
        diff_gate = _evaluate_differentiation_gate(strategy, differentiation)
        risk_gate = _evaluate_risk_gate(strategy, risk_coverage)
        univ_gate = _evaluate_universe_gate(universe_governance)
        timing_gate = _evaluate_execution_timing_gate(execution_timing)
        decision, decision_reasons = _decide_strategy(
            strategy,
            observation_window=obs_gate,
            performance=perf_gate,
            differentiation=diff_gate,
            risk=risk_gate,
            universe=univ_gate,
            execution_timing=timing_gate,
        )
        # Aggregate blocker categories for the top-level summary.
        for code in decision_reasons:
            if code == "ok":
                continue
            gate_name = str(code).split(":", 1)[0]
            blocker_categories.add(gate_name.upper())
        if decision == DECISION_PROMOTE:
            promote_candidates.append(strategy)
        elif decision == DECISION_DEMOTE:
            demote_candidates.append(strategy)
        strategy_decisions[strategy] = {
            "strategy": strategy,
            "decision": decision,
            "evidence_strength": _evidence_strength(decision, [c for c in decision_reasons if c != "ok"]),
            "reason_codes": sorted(set(decision_reasons)) or ["ok"],
            "gates": {
                "observation_window": obs_gate,
                "performance": perf_gate,
                "differentiation": diff_gate,
                "risk": risk_gate,
                "universe": univ_gate,
                "execution_timing": timing_gate,
            },
        }

    # Challenger ranking: sort non-control strategies by (decision rank,
    # max observation count, strategy name) for deterministic output.
    decision_rank = {
        DECISION_PROMOTE: 0,
        DECISION_PROMOTION_CANDIDATE: 1,
        DECISION_WATCH: 2,
        DECISION_HOLD: 3,
        DECISION_DEMOTE: 4,
        DECISION_BLOCKED: 5,
    }
    challenger_rankings: list[dict[str, Any]] = []
    for strategy in STRATEGIES:
        if strategy == CONTROL_STRATEGY:
            continue
        row = strategy_decisions[strategy]
        challenger_rankings.append(
            {
                "strategy": strategy,
                "decision": row["decision"],
                "rank_score": decision_rank.get(row["decision"], 99),
                "max_observation_count": row["gates"]["observation_window"].get("max_observation_count"),
                "evidence_strength": row["evidence_strength"],
            }
        )
    def _rank_key(row: dict[str, Any]) -> tuple[int, int, str]:
        score_value = row.get("rank_score")
        score = int(score_value) if score_value is not None else 99
        obs = int(row.get("max_observation_count") or 0)
        return (score, -obs, str(row.get("strategy") or ""))

    challenger_rankings.sort(key=_rank_key)
    # Re-number ranks 1..N after sorting.
    for index, row in enumerate(challenger_rankings):
        row["rank"] = index + 1

    promotion_recommendation = (
        promote_candidates[0]
        if len(promote_candidates) == 1
        else (
            "MULTIPLE_PROMOTE_CANDIDATES" if len(promote_candidates) > 1 else "NO_PROMOTION_RECOMMENDED"
        )
    )
    demotion_recommendation = demote_candidates[0] if len(demote_candidates) == 1 else (
        "NO_DEMOTION_RECOMMENDED" if not demote_candidates else "MULTIPLE_DEMOTE_CANDIDATES"
    )

    # Top-level reason codes aggregate the missing-input ones plus a
    # rolled-up summary token when at least one challenger is blocked.
    aggregate_reason_set: set[str] = set(aggregate_reasons)
    for strategy, row in strategy_decisions.items():
        for code in row["reason_codes"]:
            if code == "ok":
                continue
            aggregate_reason_set.add(f"{strategy}:{code}")
    aggregate_reason_codes = sorted(aggregate_reason_set) or ["ok"]

    available = promotion_windows is not None and risk_coverage is not None and differentiation is not None
    confidence = "HIGH" if (
        available
        and not aggregate_reasons
        and any(d["decision"] in (DECISION_PROMOTE, DECISION_PROMOTION_CANDIDATE) for d in strategy_decisions.values())
    ) else ("MEDIUM" if available else "LOW")

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": available,
        "confidence": confidence,
        "current_control_strategy": CONTROL_STRATEGY,
        "promotion_recommendation": promotion_recommendation,
        "demotion_recommendation": demotion_recommendation,
        "challenger_rankings": challenger_rankings,
        "strategies": strategy_decisions,
        "blocker_categories": sorted(blocker_categories) or ["NONE"],
        "evidence_strength": "HIGH" if confidence == "HIGH" else ("MEDIUM" if confidence == "MEDIUM" else "LOW"),
        "reason_codes": aggregate_reason_codes,
        "source_artifacts": sorted(set(source_artifacts)),
    }

    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "promotion_governance") / trade_date
    _write_json(out_dir / "promotion_governance.json", payload)
    _write_text(out_dir / "promotion_governance.md", render_markdown(payload))
    return payload


# ---------------------------------------------------------------------------
# Markdown render
# ---------------------------------------------------------------------------

def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Promotion Governance - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Current control: {payload.get('current_control_strategy')}",
        f"- Promotion recommendation: {payload.get('promotion_recommendation')}",
        f"- Demotion recommendation: {payload.get('demotion_recommendation')}",
        f"- Evidence strength: {payload.get('evidence_strength')}",
        f"- Blocker categories: {', '.join(payload.get('blocker_categories') or [])}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "## Challenger Rankings",
        "",
        "| Rank | Strategy | Decision | Evidence | Max Obs |",
        "|---:|---|---|---|---:|",
    ]
    for row in payload.get("challenger_rankings") or []:
        lines.append(
            f"| {row.get('rank')} | {row.get('strategy')} | {row.get('decision')} | {row.get('evidence_strength')} | {row.get('max_observation_count')} |"
        )
    lines += [
        "",
        "## Per-Strategy Gates",
        "",
    ]
    strategies = payload.get("strategies") or {}
    for strategy in sorted(strategies):
        row = strategies[strategy]
        lines += [
            f"### {strategy}",
            "",
            f"- Decision: {row.get('decision')}",
            f"- Evidence: {row.get('evidence_strength')}",
            f"- Reason codes: {', '.join(row.get('reason_codes') or [])}",
            "",
            "| Gate | Status | Reason Codes |",
            "|---|---|---|",
        ]
        for gate_name in ("observation_window", "performance", "differentiation", "risk", "universe", "execution_timing"):
            gate = (row.get("gates") or {}).get(gate_name) or {}
            lines.append(
                f"| {gate_name} | {gate.get('status')} | {', '.join(gate.get('reason_codes') or [])} |"
            )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Tier 3 promotion governance artifacts (research-only).")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_promotion_governance(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": args.date,
                "available": payload["available"],
                "confidence": payload["confidence"],
                "promotion_recommendation": payload["promotion_recommendation"],
                "demotion_recommendation": payload["demotion_recommendation"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
