from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from research.flow_detection.data import ensure_price_panel, load_universe
from research.phoenix.artifacts import write_phoenix_research_artifacts
from research.phoenix.strategy import BENCHMARK_SYMBOL, PhoenixConfig
from research_registry.research.phoenix import build_phoenix_model_quality_research


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research-only Phoenix crisis-reversal artifacts")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--output-dir", default="outputs/research/phoenix")
    parser.add_argument("--model-quality-output-root", default="outputs/model_quality")
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    parser.add_argument("--universe-path", default="data/universe.csv")
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--min-history-days", type=int, default=252)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    trade_date = str(pd.Timestamp(args.trade_date).date())
    end_date = str(pd.Timestamp(args.end_date or trade_date).date())
    universe = load_universe(args.universe_path)
    panel, panel_meta = ensure_price_panel(
        symbols=sorted(set(universe + [BENCHMARK_SYMBOL])),
        start_date=args.start_date,
        end_date=end_date,
        cache_path=args.price_cache_path,
        allow_download=bool(args.allow_download),
    )
    config = PhoenixConfig(top_n=int(args.top_n), min_history_days=int(args.min_history_days))
    manifest = write_phoenix_research_artifacts(
        panel=panel,
        trade_date=trade_date,
        start_date=args.start_date,
        output_dir=Path(args.output_dir),
        config=config,
    )
    model_quality = build_phoenix_model_quality_research(
        panel=panel,
        trade_date=trade_date,
        start_date=args.start_date,
        repo_root=Path("."),
        output_root=Path(args.model_quality_output_root),
        config=config,
    )
    manifest["data"] = panel_meta
    manifest["model_quality_artifacts"] = {
        "json": str(Path(args.model_quality_output_root) / trade_date / "phoenix_research.json"),
        "markdown": str(Path(args.model_quality_output_root) / trade_date / "phoenix_research.md"),
        "status": model_quality.get("status"),
        "reason_codes": model_quality.get("reason_codes"),
    }
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
