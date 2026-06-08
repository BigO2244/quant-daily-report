"""Differentiation diagnostic — Workstream 4.

Breaks down each strategy pair (Lyra/Orion, Lyra/Polaris, Orion/Polaris)
into its constituent differentiation signals and produces a per-pair
verdict:

  TRUE_WEAK_DIFFERENTIATION   — multiple signals confirm weak diff with
                                a usable observation window (>=40 days).
  POSSIBLE_DATA_LIMITATION    — one or more signal inputs are missing
                                (factor exposures, position contributions,
                                regime overlap data), so the WEAK label
                                may be an artifact rather than truth.
  INSUFFICIENT_HISTORY        — observation window < 40 days; signal
                                cannot be trusted regardless of value.

Signals consumed (when available):

  - holdings_overlap_percentage      from strategy_differentiation.json
  - daily_return_correlation         from strategy_differentiation.json
  - contribution_correlation         from strategy_differentiation.json
  - factor_exposure_similarity       from strategy_differentiation.json
  - sector_overlap                   from strategy_differentiation.json
  - average_active_share_proxy       from strategy_differentiation.json
  - regime_overlap                   from regime_attribution.json

Research-only.
"""

from __future__ import annotations

import argparse
import json
import math
from itertools import combinations
from pathlib import Path
from typing import Any

from core.strategy_registry import active_shadow_security_selection_ids

SCHEMA_VERSION = "caerus_differentiation_diagnostic_v1"

STRATEGIES = active_shadow_security_selection_ids()

VERDICT_TRUE_WEAK = "TRUE_WEAK_DIFFERENTIATION"
VERDICT_POSSIBLE_DATA = "POSSIBLE_DATA_LIMITATION"
VERDICT_INSUFFICIENT = "INSUFFICIENT_HISTORY"

# Thresholds that match promotion_governance.py.
MAX_CORRELATION_VS_INCUMBENT = 0.90
MIN_ACTIVE_SHARE = 0.50
MAX_HOLDINGS_OVERLAP = 0.50
MIN_OBSERVATION_FOR_TRUE_WEAK = 40


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


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


def _round(value: Any, digits: int = 6) -> float | None:
    f = _safe_float(value)
    return round(f, digits) if f is not None else None


def _regime_overlap(left: str, right: str, regime_payload: dict[str, Any] | None) -> float | None:
    """Cosine-style overlap of regime distribution. Returns None if regime
    data is missing or insufficient."""
    if not isinstance(regime_payload, dict) or not regime_payload.get("available"):
        return None
    strategies = regime_payload.get("strategies") or {}
    left_regimes = (strategies.get(left) or {}).get("regimes") or {}
    right_regimes = (strategies.get(right) or {}).get("regimes") or {}
    if not left_regimes or not right_regimes:
        return None
    # Convert per-regime observation counts into a probability vector,
    # then take the cosine similarity.
    keys = sorted(set(left_regimes.keys()) | set(right_regimes.keys()))
    left_vec = [float((left_regimes.get(k) or {}).get("observation_count") or 0) for k in keys]
    right_vec = [float((right_regimes.get(k) or {}).get("observation_count") or 0) for k in keys]
    left_norm = math.sqrt(sum(v * v for v in left_vec))
    right_norm = math.sqrt(sum(v * v for v in right_vec))
    if left_norm == 0 or right_norm == 0:
        return None
    dot = sum(a * b for a, b in zip(left_vec, right_vec))
    return dot / (left_norm * right_norm)


def _max_obs_for_strategy(promotion_payload: dict[str, Any] | None, strategy: str) -> int:
    if not isinstance(promotion_payload, dict):
        return 0
    windows = ((promotion_payload.get("strategies") or {}).get(strategy) or {}).get("windows") or {}
    obs = 0
    for row in windows.values():
        v = _safe_float((row or {}).get("observation_count"))
        if v is not None:
            obs = max(obs, int(v))
    return obs


def _classify_pair(
    *,
    pair: dict[str, Any],
    regime_overlap: float | None,
    max_obs_left: int,
    max_obs_right: int,
    differentiation_payload_complete: bool,
) -> tuple[str, list[str], list[str]]:
    """Return ``(verdict, weak_signals, data_gaps)``."""
    max_obs = max(max_obs_left, max_obs_right)
    if max_obs < MIN_OBSERVATION_FOR_TRUE_WEAK:
        return VERDICT_INSUFFICIENT, [], [f"observation_window_{max_obs}d_below_{MIN_OBSERVATION_FOR_TRUE_WEAK}d"]
    weak_signals: list[str] = []
    data_gaps: list[str] = []
    overlap = _safe_float(pair.get("holdings_overlap_percentage"))
    if overlap is not None and overlap >= MAX_HOLDINGS_OVERLAP:
        weak_signals.append(f"holdings_overlap_{overlap:.2f}")
    elif overlap is None:
        data_gaps.append("holdings_overlap_missing")
    corr = _safe_float(pair.get("daily_return_correlation"))
    if corr is not None and corr >= MAX_CORRELATION_VS_INCUMBENT:
        weak_signals.append(f"daily_return_correlation_{corr:.2f}")
    elif corr is None:
        data_gaps.append("daily_return_correlation_missing")
    contrib = _safe_float(pair.get("contribution_correlation"))
    if contrib is None:
        data_gaps.append("contribution_correlation_missing")
    factor = _safe_float(pair.get("factor_exposure_similarity"))
    if factor is None:
        data_gaps.append("factor_exposure_similarity_missing")
    sector = _safe_float(pair.get("sector_overlap"))
    if sector is not None and sector >= 0.80:
        weak_signals.append(f"sector_overlap_{sector:.2f}")
    elif sector is None:
        data_gaps.append("sector_overlap_missing")
    active_share = _safe_float(pair.get("average_active_share_proxy"))
    if active_share is not None and active_share < MIN_ACTIVE_SHARE:
        weak_signals.append(f"active_share_{active_share:.2f}")
    elif active_share is None:
        data_gaps.append("active_share_missing")
    if regime_overlap is not None and regime_overlap >= 0.95:
        weak_signals.append(f"regime_overlap_{regime_overlap:.2f}")
    elif regime_overlap is None:
        data_gaps.append("regime_overlap_missing")
    # Need at least 3 confirming weak signals AND key inputs not missing
    # to call TRUE_WEAK_DIFFERENTIATION. Otherwise the label is shaky.
    if len(weak_signals) >= 3 and differentiation_payload_complete:
        return VERDICT_TRUE_WEAK, weak_signals, data_gaps
    if data_gaps:
        return VERDICT_POSSIBLE_DATA, weak_signals, data_gaps
    if weak_signals:
        return VERDICT_TRUE_WEAK, weak_signals, data_gaps
    return "STRONG_DIFFERENTIATION", weak_signals, data_gaps


def build_differentiation_diagnostic(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    differentiation_payload = _read_json(repo / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json")
    regime_payload = _read_json(repo / "outputs" / "research" / "regime_attribution" / trade_date / "regime_attribution.json")
    promotion_payload = _read_json(repo / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json")

    differentiation_payload_complete = bool(
        differentiation_payload
        and differentiation_payload.get("factor_exposure_available")
        and differentiation_payload.get("position_contributions_available")
    )

    pairs_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    if isinstance(differentiation_payload, dict):
        for pair in differentiation_payload.get("pairs") or []:
            if not isinstance(pair, dict):
                continue
            left = str(pair.get("left_strategy") or "")
            right = str(pair.get("right_strategy") or "")
            key = tuple(sorted([left, right]))
            if all(key):
                pairs_by_key[key] = pair

    diagnostics: list[dict[str, Any]] = []
    verdict_counts: dict[str, int] = {}
    for left, right in combinations(sorted(STRATEGIES), 2):
        key = tuple(sorted([left, right]))
        pair = pairs_by_key.get(key) or {}
        regime_ovr = _regime_overlap(left, right, regime_payload)
        obs_left = _max_obs_for_strategy(promotion_payload, left)
        obs_right = _max_obs_for_strategy(promotion_payload, right)
        verdict, weak_signals, data_gaps = _classify_pair(
            pair=pair,
            regime_overlap=regime_ovr,
            max_obs_left=obs_left,
            max_obs_right=obs_right,
            differentiation_payload_complete=differentiation_payload_complete,
        )
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        diagnostics.append(
            {
                "left_strategy": left,
                "right_strategy": right,
                "verdict": verdict,
                "weak_signals": weak_signals,
                "data_gaps": data_gaps,
                "holdings_overlap_percentage": _round(pair.get("holdings_overlap_percentage")),
                "daily_return_correlation": _round(pair.get("daily_return_correlation")),
                "contribution_correlation": _round(pair.get("contribution_correlation")),
                "factor_exposure_similarity": _round(pair.get("factor_exposure_similarity")),
                "sector_overlap": _round(pair.get("sector_overlap")),
                "average_active_share_proxy": _round(pair.get("average_active_share_proxy")),
                "regime_overlap": _round(regime_ovr),
                "max_observation_count": max(obs_left, obs_right),
            }
        )

    reason_codes: list[str] = []
    if differentiation_payload is None:
        reason_codes.append("missing_strategy_differentiation")
    if regime_payload is None:
        reason_codes.append("missing_regime_attribution")
    if promotion_payload is None:
        reason_codes.append("missing_promotion_readiness_windows")
    if not differentiation_payload_complete and differentiation_payload is not None:
        reason_codes.append("strategy_differentiation_inputs_incomplete")
    if not reason_codes:
        reason_codes.append("ok")

    aggregate_verdict = (
        VERDICT_TRUE_WEAK if verdict_counts.get(VERDICT_TRUE_WEAK, 0) > 0
        else (
            VERDICT_POSSIBLE_DATA if verdict_counts.get(VERDICT_POSSIBLE_DATA, 0) > 0
            else (
                VERDICT_INSUFFICIENT if verdict_counts.get(VERDICT_INSUFFICIENT, 0) > 0
                else "STRONG_DIFFERENTIATION"
            )
        )
    )

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": differentiation_payload is not None,
        "confidence": "HIGH" if differentiation_payload_complete else "MEDIUM" if differentiation_payload is not None else "LOW",
        "differentiation_inputs_complete": differentiation_payload_complete,
        "aggregate_verdict": aggregate_verdict,
        "verdict_counts": verdict_counts,
        "pairs": diagnostics,
        "reason_codes": sorted(set(reason_codes)),
        "source_artifacts": sorted(
            p for p, present in [
                (f"outputs/research/strategy_differentiation/{trade_date}/strategy_differentiation.json", differentiation_payload is not None),
                (f"outputs/research/regime_attribution/{trade_date}/regime_attribution.json", regime_payload is not None),
                (f"outputs/research/promotion_readiness/{trade_date}/promotion_readiness_windows.json", promotion_payload is not None),
            ] if present
        ),
    }

    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "differentiation_diagnostic") / trade_date
    _write_json(out_dir / "differentiation_diagnostic.json", payload)
    _write_text(out_dir / "differentiation_diagnostic.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Differentiation Diagnostic - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Aggregate verdict: {payload.get('aggregate_verdict')}",
        f"- Inputs complete: {payload.get('differentiation_inputs_complete')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "## Per-Pair Diagnostics",
        "",
        "| Pair | Verdict | Overlap | DailyCorr | ContribCorr | FactorSim | Sector | Active | Regime | MaxObs | Weak Signals |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload.get("pairs") or []:
        lines.append(
            f"| {row.get('left_strategy')} vs {row.get('right_strategy')} | {row.get('verdict')} | {row.get('holdings_overlap_percentage')} | {row.get('daily_return_correlation')} | {row.get('contribution_correlation')} | {row.get('factor_exposure_similarity')} | {row.get('sector_overlap')} | {row.get('average_active_share_proxy')} | {row.get('regime_overlap')} | {row.get('max_observation_count')} | {', '.join(row.get('weak_signals') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose whether weak differentiation is real or a data limitation.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_differentiation_diagnostic(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(
        json.dumps(
            {
                "date": args.date,
                "available": payload["available"],
                "aggregate_verdict": payload["aggregate_verdict"],
                "verdict_counts": payload["verdict_counts"],
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
