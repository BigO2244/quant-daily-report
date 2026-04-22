from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import pandas as pd

from alpha_stack.research.metrics import summarise_performance
from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import build_target_snapshot, run_backtest
from research.flow_detection.data import ensure_price_panel, load_universe

from .strategies import build_shadow_definitions


BENCHMARK_SYMBOL = "SPY"
BENCHMARK_SLUG = "spy_benchmark"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DEV-only shadow tracking for Caerus momentum candidates")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--end-date", default=pd.Timestamp.today().normalize().strftime("%Y-%m-%d"))
    parser.add_argument("--output-dir", default="outputs/shadow_candidates")
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    parser.add_argument("--allow-download", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    universe = load_universe("data/universe.csv")
    panel, panel_meta = ensure_price_panel(
        symbols=sorted(set(universe + [BENCHMARK_SYMBOL])),
        start_date=args.start_date,
        end_date=args.end_date,
        cache_path=args.price_cache_path,
        allow_download=bool(args.allow_download),
    )
    signals = build_alpha_lab_signal_frame(panel)
    trade_date = resolve_trade_date(signals, requested_trade_date=args.trade_date, end_date=args.end_date)
    dated_dir = output_root / trade_date
    dated_dir.mkdir(parents=True, exist_ok=True)

    definitions = build_shadow_definitions()
    strategy_payloads = {}
    backtest_results = {}
    for definition in definitions:
        snapshot = build_target_snapshot(signals, definition.spec, trade_date=trade_date, start_date=args.start_date)
        backtest = run_backtest(signals, definition.spec, start_date=args.start_date, end_date=trade_date)
        backtest_results[definition.strategy_slug] = backtest
        payload = build_strategy_payload(
            definition=definition,
            snapshot=snapshot,
            backtest=backtest,
            trade_date=trade_date,
        )
        strategy_payloads[definition.strategy_slug] = payload
        (dated_dir / f"{definition.strategy_slug}.json").write_text(json.dumps(payload, indent=2))

    comparison_payload = build_comparison_payload(strategy_payloads, trade_date=trade_date)
    (dated_dir / "comparison.json").write_text(json.dumps(comparison_payload, indent=2))
    (dated_dir / "comparison.md").write_text(build_comparison_markdown(comparison_payload))

    nav_series, summary = build_performance_artifacts(
        panel=panel,
        trade_date=trade_date,
        start_date=args.start_date,
        output_root=output_root,
        strategy_payloads=strategy_payloads,
        backtest_results=backtest_results,
        panel_meta=panel_meta,
    )
    (output_root / "performance").mkdir(parents=True, exist_ok=True)
    nav_series.to_csv(output_root / "performance" / "shadow_nav_series.csv", index=False)
    (output_root / "performance" / "shadow_summary.json").write_text(json.dumps(summary, indent=2))
    return 0


def resolve_trade_date(signals: pd.DataFrame, *, requested_trade_date: str | None, end_date: str) -> str:
    dates = pd.DatetimeIndex(pd.to_datetime(signals["date"])).sort_values().unique()
    upper = pd.Timestamp(requested_trade_date or end_date)
    eligible = dates[dates <= upper]
    if len(eligible) == 0:
        raise ValueError("No eligible trade date available in signal frame")
    return str(eligible[-1].date())


def build_strategy_payload(*, definition, snapshot: dict, backtest: dict, trade_date: str) -> dict:
    weights = snapshot["weights"]
    weights = weights[weights > 0].sort_values(ascending=False)
    rank_table = snapshot["rank_table"]
    holdings = []
    for ticker, weight in weights.items():
        row = rank_table[rank_table["ticker"] == ticker]
        momentum_rank = float(row["momentum_rank"].iloc[0]) if not row.empty else None
        momentum_score = float(row["momentum_score"].iloc[0]) if not row.empty else None
        holdings.append(
            {
                "ticker": ticker,
                "target_weight": round(float(weight), 6),
                "momentum_rank": momentum_rank,
                "momentum_score": round(momentum_score, 6) if momentum_score is not None else None,
                "estimated_holding_period_days": snapshot["holding_period_by_ticker"].get(str(ticker)),
            }
        )

    concentration = {
        "holdings_count": int(len(weights)),
        "max_weight": round(float(weights.max()), 6) if not weights.empty else 0.0,
        "top3_concentration": round(float(weights.head(3).sum()), 6) if not weights.empty else 0.0,
    }
    return {
        "strategy_name": definition.strategy_name,
        "strategy_slug": definition.strategy_slug,
        "source_variant": definition.source_variant,
        "trade_date": trade_date,
        "effective_trade_date": snapshot["effective_trade_date"],
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "holdings": holdings,
        "target_weights": {str(ticker): round(float(weight), 6) for ticker, weight in weights.items()},
        "rank_table": rank_table.head(15).to_dict(orient="records"),
        "expected_turnover": snapshot["expected_turnover"],
        "estimated_holding_period_days": snapshot["estimated_holding_period_days"],
        "weight_concentration": concentration,
        "performance_summary": backtest["summary"],
    }


def build_comparison_payload(strategy_payloads: dict[str, dict], *, trade_date: str) -> dict:
    pairwise = []
    for left_slug, right_slug in combinations(strategy_payloads.keys(), 2):
        left = strategy_payloads[left_slug]
        right = strategy_payloads[right_slug]
        pairwise.append(compare_two_strategies(left, right))

    polaris = strategy_payloads["caerus_polaris"]
    orion = strategy_payloads["caerus_orion"]
    lyra = strategy_payloads["caerus_lyra"]
    return {
        "trade_date": trade_date,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "shadow_methodology": "model_portfolio",
        "strategies": {
            slug: {
                "strategy_name": payload["strategy_name"],
                "expected_turnover": payload["expected_turnover"],
                "estimated_holding_period_days": payload["estimated_holding_period_days"],
                "weight_concentration": payload["weight_concentration"],
                "holdings": payload["holdings"],
            }
            for slug, payload in strategy_payloads.items()
        },
        "pairwise_overlap": pairwise,
        "differences_vs_polaris": {
            "caerus_orion": compare_two_strategies(polaris, orion),
            "caerus_lyra": compare_two_strategies(polaris, lyra),
        },
        "broker_context": _load_broker_context(strategy_payloads),
    }


def compare_two_strategies(left: dict, right: dict) -> dict:
    left_weights = pd.Series(left["target_weights"], dtype=float)
    right_weights = pd.Series(right["target_weights"], dtype=float)
    common = left_weights.index.intersection(right_weights.index)
    overlap_pct = float(pd.concat([left_weights.reindex(common), right_weights.reindex(common)], axis=1).min(axis=1).sum()) if len(common) else 0.0
    left_only = sorted(set(left_weights.index) - set(right_weights.index))
    right_only = sorted(set(right_weights.index) - set(left_weights.index))
    return {
        "left_strategy": left["strategy_name"],
        "left_slug": left["strategy_slug"],
        "right_strategy": right["strategy_name"],
        "right_slug": right["strategy_slug"],
        "overlap_weight_pct": round(overlap_pct, 6),
        "shared_names": sorted(common.tolist()),
        "left_unique_names": left_only,
        "right_unique_names": right_only,
    }


def build_comparison_markdown(comparison: dict) -> str:
    strategies = comparison["strategies"]
    lines = [
        "# Shadow Candidates Comparison",
        "",
        f"## Trade Date",
        f"- {comparison['trade_date']}",
        "",
        "## Polaris vs Orion",
    ]
    lines.extend(_pairwise_lines(comparison, "caerus_polaris", "caerus_orion"))
    lines.extend(["", "## Polaris vs Lyra"])
    lines.extend(_pairwise_lines(comparison, "caerus_polaris", "caerus_lyra"))
    lines.extend(["", "## Orion vs Lyra"])
    lines.extend(_pairwise_lines(comparison, "caerus_orion", "caerus_lyra"))
    lines.extend(["", "## Current Top Holdings"])
    for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra"):
        payload = strategies[slug]
        top = ", ".join(f"{item['ticker']} ({item['target_weight']:.2%})" for item in payload["holdings"][:5])
        lines.append(f"- {payload['strategy_name']}: {top}")
    lines.extend(["", "## Turnover / Concentration Summary"])
    for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra"):
        payload = strategies[slug]
        conc = payload["weight_concentration"]
        lines.append(
            f"- {payload['strategy_name']}: turnover {payload['expected_turnover']}, max weight {conc['max_weight']}, top-3 concentration {conc['top3_concentration']}, est. holding period {payload['estimated_holding_period_days']}"
        )
    broker_context = comparison.get("broker_context") or {}
    if broker_context:
        lines.extend(["", "## Broker Context Appendix"])
        lines.append("- Informational only. Shadow portfolios remain model-portfolio based, not broker-authoritative.")
        lines.append(
            f"- Snapshot as of {broker_context.get('as_of') or 'unknown'} with {broker_context.get('positions_count') or 0} broker positions."
        )
        for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra"):
            overlap = (broker_context.get("strategy_overlap") or {}).get(slug) or {}
            lines.append(
                f"- {comparison['strategies'][slug]['strategy_name']} overlap with broker: {overlap.get('overlap_names_count', 0)} names; broker-only names: {', '.join(overlap.get('broker_only_names') or []) or 'None'}"
            )
    lines.extend(["", "## Benchmark Note", f"- Benchmark symbol: {comparison['benchmark_symbol']}"])
    return "\n".join(lines) + "\n"


def _pairwise_lines(comparison: dict, left_slug: str, right_slug: str) -> list[str]:
    item = next(
        pair
        for pair in comparison["pairwise_overlap"]
        if pair["left_slug"] == left_slug and pair["right_slug"] == right_slug
        or pair["left_slug"] == right_slug and pair["right_slug"] == left_slug
    )
    left_unique = item["left_unique_names"] if item["left_slug"] == left_slug else item["right_unique_names"]
    right_unique = item["right_unique_names"] if item["right_slug"] == right_slug else item["left_unique_names"]
    return [
        f"- Overlap weight: {item['overlap_weight_pct']:.2%}",
        f"- {comparison['strategies'][left_slug]['strategy_name']} unique: {', '.join(left_unique) or 'None'}",
        f"- {comparison['strategies'][right_slug]['strategy_name']} unique: {', '.join(right_unique) or 'None'}",
    ]


def build_performance_artifacts(
    *,
    panel: pd.DataFrame,
    trade_date: str,
    start_date: str,
    output_root: Path,
    strategy_payloads: dict[str, dict],
    backtest_results: dict[str, dict],
    panel_meta: dict,
) -> tuple[pd.DataFrame, dict]:
    nav_frame = pd.DataFrame()
    summary = {
        "schema_version": "shadow_candidates_v1",
        "trade_date": trade_date,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "data": panel_meta,
        "strategies": {},
    }
    for slug, result in backtest_results.items():
        nav = result["nav"].copy()
        nav["date"] = pd.to_datetime(nav["date"])
        nav = nav.rename(columns={"nav": slug})
        nav_frame = nav if nav_frame.empty else nav_frame.merge(nav, on="date", how="outer")
        summary["strategies"][slug] = {
            "strategy_name": strategy_payloads[slug]["strategy_name"],
            "summary": result["summary"],
        }

    spy = panel[panel["ticker"] == BENCHMARK_SYMBOL].copy()
    spy["date"] = pd.to_datetime(spy["date"])
    spy = spy[(spy["date"] >= pd.Timestamp(start_date)) & (spy["date"] <= pd.Timestamp(trade_date))].sort_values("date")
    spy["return"] = spy["close"].pct_change().shift(-1)
    spy = spy.iloc[:-1].copy()
    spy["spy_benchmark"] = (1.0 + spy["return"].fillna(0.0)).cumprod()
    spy_series = spy[["date", "spy_benchmark"]]
    nav_frame = spy_series if nav_frame.empty else nav_frame.merge(spy_series, on="date", how="outer")
    nav_frame = nav_frame.sort_values("date").reset_index(drop=True)
    nav_frame["date"] = nav_frame["date"].dt.strftime("%Y-%m-%d")

    if not spy.empty:
        returns = pd.Series(spy["return"].values, index=pd.to_datetime(spy["date"]), name="return")
        nav = pd.Series(spy["spy_benchmark"].values, index=pd.to_datetime(spy["date"]), name="nav")
        summary["strategies"][BENCHMARK_SLUG] = {
            "strategy_name": "SPY",
            "summary": summarise_performance(nav, returns=returns, benchmark_returns=None, label="SPY"),
        }
    return nav_frame, summary


def _load_broker_context(strategy_payloads: dict[str, dict]) -> dict:
    positions_path = Path("outputs/broker/posttrade_positions.json")
    snapshot_path = Path("outputs/broker/broker_snapshot_latest.json")
    if not positions_path.exists():
        return {}
    try:
        positions_payload = json.loads(positions_path.read_text())
        snapshot_payload = json.loads(snapshot_path.read_text()) if snapshot_path.exists() else {}
    except Exception:
        return {}

    broker_names = sorted(
        str(item.get("symbol") or "").upper()
        for item in positions_payload.get("positions") or []
        if str(item.get("symbol") or "").strip()
    )
    broker_set = set(broker_names)
    strategy_overlap = {}
    for slug, payload in strategy_payloads.items():
        model_set = {str(item["ticker"]).upper() for item in payload.get("holdings") or []}
        strategy_overlap[slug] = {
            "overlap_names_count": int(len(model_set & broker_set)),
            "shared_names": sorted(model_set & broker_set),
            "broker_only_names": sorted(broker_set - model_set),
            "model_only_names": sorted(model_set - broker_set),
        }
    return {
        "informational_only": True,
        "source": "outputs/broker/posttrade_positions.json",
        "as_of": snapshot_payload.get("as_of") or positions_payload.get("captured_at"),
        "trade_date": snapshot_payload.get("trade_date"),
        "positions_count": int(len(broker_names)),
        "broker_holdings": broker_names,
        "strategy_overlap": strategy_overlap,
    }


if __name__ == "__main__":
    raise SystemExit(main())
