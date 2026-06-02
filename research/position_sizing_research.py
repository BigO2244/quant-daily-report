from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from research.risk_coverage import load_holdings_for_risk_coverage


SCHEMA_VERSION = "caerus_position_sizing_research_v1"


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
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except Exception:
        return None
    if out != out or math.isinf(out):
        return None
    return out


def _round(value: Any, digits: int = 10) -> float | None:
    value = _safe_float(value)
    return round(value, digits) if value is not None else None


def _attribution_dates(repo: Path, trade_date: str) -> list[str]:
    root = repo / "outputs" / "attribution"
    if not root.exists():
        return []
    dates: list[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            pd.Timestamp(child.name)
        except Exception:
            continue
        if child.name <= trade_date and (child / "position_attribution.json").exists():
            dates.append(child.name)
    return sorted(dates, reverse=True)


def _load_symbol_returns(repo: Path, trade_date: str) -> tuple[dict[str, dict[str, float]], list[str], list[str], str | None]:
    dates = _attribution_dates(repo, trade_date)
    if not dates:
        return {}, [], ["position_returns_missing"], None
    reasons: list[str] = []
    sources: list[str] = []
    for source_date in dates:
        path = repo / "outputs" / "attribution" / source_date / "position_attribution.json"
        payload = _read_json(path)
        if payload is None:
            continue
        sources.append(str(path))
        positions = payload.get("positions")
        if not isinstance(positions, list):
            reasons.append("position_returns_bad_schema")
            continue
        out: dict[str, dict[str, float]] = {}
        for row in positions:
            if not isinstance(row, dict):
                continue
            strategy = str(row.get("strategy") or row.get("strategy_id") or "")
            symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
            ret = _safe_float(row.get("return_pct"))
            if ret is None:
                ret = _safe_float(row.get("realized_return"))
            if strategy and symbol and ret is not None:
                out.setdefault(strategy, {})[symbol] = ret
        if out:
            selected_reasons: list[str] = [reason for reason in reasons if reason != "position_returns_empty"]
            if source_date != trade_date:
                selected_reasons.append("position_returns_date_differs_from_target")
            return out, [str(path)], sorted(set(selected_reasons)), source_date
        reasons.append("position_returns_empty")
    return {}, sorted(set(sources)), sorted(set(reasons + ["position_returns_missing"])), None


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(value, 0.0) for value in weights.values())
    if total <= 0:
        return {}
    return {symbol: round(max(value, 0.0) / total, 10) for symbol, value in sorted(weights.items())}


def _cap_and_redistribute(weights: dict[str, float], cap: float = 0.2) -> dict[str, float]:
    weights = _normalize(weights)
    if not weights:
        return {}
    capped = {symbol: min(weight, cap) for symbol, weight in weights.items()}
    leftover = 1.0 - sum(capped.values())
    uncapped = {symbol: weight for symbol, weight in capped.items() if weight < cap}
    while leftover > 1e-10 and uncapped:
        add = leftover / len(uncapped)
        next_uncapped: dict[str, float] = {}
        consumed = 0.0
        for symbol, weight in uncapped.items():
            room = cap - weight
            delta = min(add, room)
            capped[symbol] = weight + delta
            consumed += delta
            if capped[symbol] < cap - 1e-10:
                next_uncapped[symbol] = capped[symbol]
        if consumed <= 0:
            break
        leftover -= consumed
        uncapped = next_uncapped
    return _normalize(capped)


def _weights_for(method: str, current: dict[str, float], returns: dict[str, float]) -> dict[str, float]:
    symbols = sorted(current)
    if not symbols:
        return {}
    if method == "current_model_weights":
        return _normalize(current)
    if method == "equal_weight":
        return {symbol: round(1.0 / len(symbols), 10) for symbol in symbols}
    if method == "concentration_capped_proxy":
        return _cap_and_redistribute(current, cap=0.2)
    vol_proxy = {symbol: max(abs(float(returns.get(symbol, 0.0))), 0.01) for symbol in symbols}
    inverse = {symbol: 1.0 / vol_proxy[symbol] for symbol in symbols}
    if method in {"volatility_scaled_proxy", "simple_risk_parity_proxy"}:
        return _normalize(inverse)
    return {}


def _metrics(weights: dict[str, float], current: dict[str, float], returns: dict[str, float]) -> dict[str, Any]:
    weights = _normalize(weights)
    if not weights:
        return {
            "available": False,
            "estimated_return": None,
            "realized_volatility": None,
            "drawdown": None,
            "turnover_proxy": None,
            "concentration": None,
            "top3_weight": None,
            "top5_weight": None,
            "risk_adjusted_score": None,
            "reason_codes": ["weights_missing"],
        }
    missing_returns = sorted(symbol for symbol in weights if symbol not in returns)
    weighted_returns = [weights[symbol] * returns.get(symbol, 0.0) for symbol in sorted(weights)]
    estimated_return = sum(weighted_returns)
    mean = estimated_return / max(len(weighted_returns), 1)
    realized_vol = math.sqrt(sum((value - mean) ** 2 for value in weighted_returns) / max(len(weighted_returns), 1))
    ranked = sorted(weights.values(), reverse=True)
    top3 = sum(ranked[:3])
    top5 = sum(ranked[:5])
    turnover = 0.5 * sum(abs(weights.get(symbol, 0.0) - current.get(symbol, 0.0)) for symbol in sorted(set(weights) | set(current)))
    score = estimated_return / max(realized_vol, 1e-6) - max(top3 - 0.6, 0.0)
    reasons = [f"missing_return:{symbol}" for symbol in missing_returns]
    return {
        "available": not missing_returns,
        "estimated_return": _round(estimated_return),
        "realized_volatility": _round(realized_vol),
        "drawdown": _round(min(0.0, estimated_return)),
        "turnover_proxy": _round(turnover),
        "concentration": _round(sum(value * value for value in weights.values())),
        "top3_weight": _round(top3),
        "top5_weight": _round(top5),
        "risk_adjusted_score": _round(score),
        "weights": weights,
        "reason_codes": sorted(set(reasons)) or ["ok"],
    }


def build_position_sizing_research(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root)
    holdings, holding_sources, holding_reasons, holdings_source_date = load_holdings_for_risk_coverage(repo, trade_date)
    returns, return_sources, return_reasons, returns_source_date = _load_symbol_returns(repo, trade_date)
    by_strategy: dict[str, dict[str, float]] = {}
    for row in holdings:
        strategy = str(row.get("strategy") or "")
        symbol = str(row.get("symbol") or "")
        weight = _safe_float(row.get("weight"))
        if strategy and symbol and weight is not None:
            by_strategy.setdefault(strategy, {})[symbol] = weight
    methods = (
        "current_model_weights",
        "equal_weight",
        "volatility_scaled_proxy",
        "concentration_capped_proxy",
        "simple_risk_parity_proxy",
    )
    strategies: dict[str, Any] = {}
    for strategy, current in sorted(by_strategy.items()):
        strategy_returns = returns.get(strategy, {})
        alternatives = {
            method: {
                "method": method,
                **_metrics(_weights_for(method, current, strategy_returns), _normalize(current), strategy_returns),
            }
            for method in methods
        }
        best = max(
            alternatives.values(),
            key=lambda row: (
                float(row.get("risk_adjusted_score") or -999999.0),
                str(row.get("method") or ""),
            ),
        )
        strategies[strategy] = {
            "available": bool(current) and any(row.get("available") for row in alternatives.values()),
            "holdings_count": len(current),
            "alternatives": alternatives,
            "best_research_alternative": best.get("method"),
            "confidence": "MEDIUM" if all(row.get("available") for row in alternatives.values()) else "LOW",
            "reason_codes": sorted(
                {
                    str(code)
                    for row in alternatives.values()
                    for code in list(row.get("reason_codes") or [])
                    if code != "ok"
                }
            ) or ["ok"],
        }
    available = bool(strategies) and any(row.get("available") for row in strategies.values())
    reason_codes = sorted(
        {
            str(code)
            for code in holding_reasons + return_reasons + [code for row in strategies.values() for code in list(row.get("reason_codes") or [])]
            if code != "ok"
        }
    ) or ["ok"]
    if not available and "position_sizing_research_unavailable" not in reason_codes:
        reason_codes.append("position_sizing_research_unavailable")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "available": available,
        "confidence": "LOW" if any(code != "ok" for code in reason_codes) else "MEDIUM",
        "holdings_source_date": holdings_source_date,
        "returns_source_date": returns_source_date,
        "strategies": strategies,
        "reason_codes": sorted(set(reason_codes)),
        "source_artifacts": sorted(set(holding_sources + return_sources)),
        "notes": "Research-only sizing alternatives; no production weights, execution payloads, or strategy selection are modified.",
    }
    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "position_sizing") / trade_date
    _write_json(out_dir / "position_sizing_research.json", payload)
    _write_text(out_dir / "position_sizing_research.md", render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Position Sizing Research - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "| Strategy | Method | Return | Vol | Drawdown | Turnover | Top 3 | Top 5 | Score | Reasons |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for strategy, strategy_payload in sorted((payload.get("strategies") or {}).items()):
        for method, row in sorted((strategy_payload.get("alternatives") or {}).items()):
            lines.append(
                f"| {strategy} | {method} | {row.get('estimated_return')} | {row.get('realized_volatility')} | {row.get('drawdown')} | {row.get('turnover_proxy')} | {row.get('top3_weight')} | {row.get('top5_weight')} | {row.get('risk_adjusted_score')} | {', '.join(row.get('reason_codes') or [])} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only position sizing research artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args(argv)
    payload = build_position_sizing_research(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    print(json.dumps({"date": args.date, "available": payload["available"], "confidence": payload["confidence"], "reason_codes": payload["reason_codes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
