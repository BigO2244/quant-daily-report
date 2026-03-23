from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_PATH = Path("/tmp/planned_execution_payload_2026-03-23.json")
SNAPSHOT_PATH = Path("/tmp/precompute_daily_snapshot_2026-03-23.json")
RESULTS_PATH = ROOT / "research" / "regime_aware_tpsl_results_2026-03-23.json"

CURRENT_STOP_MULT = 2.0
CURRENT_TP_MULT = 3.0


@dataclass(frozen=True)
class Scenario:
    name: str
    stop_mult: float
    tp_mult: float
    size_scale: float
    rationale: str


SCENARIOS = [
    Scenario(
        name="current_static",
        stop_mult=2.0,
        tp_mult=3.0,
        size_scale=1.0,
        rationale="Current live template: 2 ATR stop, 3 ATR target.",
    ),
    Scenario(
        name="elevated_defensive_balanced",
        stop_mult=2.5,
        tp_mult=3.75,
        size_scale=CURRENT_STOP_MULT / 2.5,
        rationale="Recommended for elevated/defensive: widen stop, preserve 1.5 R:R, reduce size to keep dollar stop risk flat.",
    ),
    Scenario(
        name="high_volatility_stress",
        stop_mult=3.0,
        tp_mult=4.5,
        size_scale=CURRENT_STOP_MULT / 3.0,
        rationale="Stress variant for very unstable conditions: materially wider stop with further size reduction.",
    ),
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_direction(entry: float, stop: float, take: float) -> str | None:
    if take > entry > stop:
        return "long"
    if stop > entry > take:
        return "short"
    return None


def infer_atr(entry: float, stop: float, take: float, direction: str) -> float | None:
    if direction == "long":
        stop_atr = (entry - stop) / CURRENT_STOP_MULT
        take_atr = (take - entry) / CURRENT_TP_MULT
    else:
        stop_atr = (stop - entry) / CURRENT_STOP_MULT
        take_atr = (entry - take) / CURRENT_TP_MULT
    if stop_atr <= 0 or take_atr <= 0:
        return None
    if abs(stop_atr - take_atr) > 0.02:
        return None
    return float((stop_atr + take_atr) / 2.0)


def scenario_levels(entry: float, atr: float, direction: str, scenario: Scenario) -> tuple[float, float]:
    if direction == "long":
        stop = entry - scenario.stop_mult * atr
        take = entry + scenario.tp_mult * atr
    else:
        stop = entry + scenario.stop_mult * atr
        take = entry - scenario.tp_mult * atr
    return float(stop), float(take)


def summarize_payload(payload: dict, snapshot: dict) -> dict:
    trades = payload.get("trades") or []
    market = snapshot.get("market_analyzer") or {}
    regime = str(market.get("regime") or "UNKNOWN")
    bucket = str(market.get("signal_bucket") or "UNKNOWN")
    recommended = "elevated_defensive_balanced" if regime == "ELEVATED" and bucket == "DEFENSIVE" else "current_static"

    trade_rows: list[dict[str, object]] = []
    scenario_totals: dict[str, dict[str, float]] = {
        scenario.name: {
            "current_notional": 0.0,
            "adjusted_notional": 0.0,
            "current_stop_risk_dollars": 0.0,
            "adjusted_stop_risk_dollars": 0.0,
            "rounded_share_delta": 0.0,
        }
        for scenario in SCENARIOS
    }

    for trade in trades:
        entry = trade.get("entry_price")
        stop = trade.get("stop_loss")
        take = trade.get("take_profit")
        shares = float(trade.get("shares") or 0.0)
        notional = float(trade.get("notional") or 0.0)
        if entry is None or stop is None or take is None:
            trade_rows.append(
                {
                    "ticker": trade.get("ticker"),
                    "side": trade.get("side"),
                    "shares": shares,
                    "notional": notional,
                    "classification": "no_risk_levels",
                    "reason": trade.get("reason"),
                }
            )
            continue

        entry = float(entry)
        stop = float(stop)
        take = float(take)
        direction = infer_direction(entry, stop, take)
        atr = infer_atr(entry, stop, take, direction) if direction else None
        if direction is None or atr is None:
            trade_rows.append(
                {
                    "ticker": trade.get("ticker"),
                    "side": trade.get("side"),
                    "shares": shares,
                    "notional": notional,
                    "classification": "unclassified",
                    "reason": trade.get("reason"),
                }
            )
            continue

        current_stop_risk_share = abs(entry - stop)
        current_tp_reward_share = abs(take - entry)

        row = {
            "ticker": trade.get("ticker"),
            "side": trade.get("side"),
            "shares": shares,
            "notional": notional,
            "classification": "atr_based",
            "reason": trade.get("reason"),
            "direction": direction,
            "atr_inferred": atr,
            "current_sl_pct": current_stop_risk_share / entry,
            "current_tp_pct": current_tp_reward_share / entry,
            "current_rr": current_tp_reward_share / current_stop_risk_share if current_stop_risk_share > 0 else None,
            "scenarios": {},
        }

        for scenario in SCENARIOS:
            scenario_stop, scenario_take = scenario_levels(entry, atr, direction, scenario)
            adjusted_notional = notional * scenario.size_scale
            adjusted_shares_float = shares * scenario.size_scale
            adjusted_shares_floor = math.floor(adjusted_shares_float + 1e-9)
            adjusted_stop_risk_share = abs(entry - scenario_stop)
            adjusted_tp_reward_share = abs(scenario_take - entry)

            row["scenarios"][scenario.name] = {
                "stop_mult": scenario.stop_mult,
                "tp_mult": scenario.tp_mult,
                "size_scale": scenario.size_scale,
                "stop_loss": scenario_stop,
                "take_profit": scenario_take,
                "sl_pct": adjusted_stop_risk_share / entry,
                "tp_pct": adjusted_tp_reward_share / entry,
                "rr": adjusted_tp_reward_share / adjusted_stop_risk_share if adjusted_stop_risk_share > 0 else None,
                "adjusted_notional": adjusted_notional,
                "adjusted_shares_float": adjusted_shares_float,
                "adjusted_shares_floor": adjusted_shares_floor,
                "current_stop_risk_dollars": shares * current_stop_risk_share,
                "adjusted_stop_risk_dollars_float": adjusted_shares_float * adjusted_stop_risk_share,
                "adjusted_stop_risk_dollars_floor": adjusted_shares_floor * adjusted_stop_risk_share,
            }

            totals = scenario_totals[scenario.name]
            totals["current_notional"] += notional
            totals["adjusted_notional"] += adjusted_notional
            totals["current_stop_risk_dollars"] += shares * current_stop_risk_share
            totals["adjusted_stop_risk_dollars"] += adjusted_shares_float * adjusted_stop_risk_share
            totals["rounded_share_delta"] += adjusted_shares_floor - shares

        trade_rows.append(row)

    return {
        "trade_date": payload.get("trade_date"),
        "market_regime": {
            "regime": regime,
            "signal_bucket": bucket,
            "vix": market.get("vix"),
            "recommended_scenario": recommended,
        },
        "scenarios": [asdict(scenario) for scenario in SCENARIOS],
        "scenario_totals": scenario_totals,
        "trades": trade_rows,
    }


def print_summary(results: dict) -> None:
    regime = results["market_regime"]
    print(f"Regime: {regime['regime']} / {regime['signal_bucket']} (VIX={regime['vix']})")
    print(f"Recommended scenario: {regime['recommended_scenario']}")
    print("")
    for scenario in results["scenarios"]:
        totals = results["scenario_totals"][scenario["name"]]
        print(
            f"{scenario['name']}: stop={scenario['stop_mult']:.2f} ATR "
            f"tp={scenario['tp_mult']:.2f} ATR "
            f"size_scale={scenario['size_scale']:.2f} "
            f"adj_notional=${totals['adjusted_notional']:.2f} "
            f"adj_risk=${totals['adjusted_stop_risk_dollars']:.2f}"
        )


def main() -> int:
    payload = load_json(PAYLOAD_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    results = summarize_payload(payload, snapshot)
    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print_summary(results)
    print(f"Saved results to {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
