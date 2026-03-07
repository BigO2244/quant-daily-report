from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from paper.perf_artifact_producers import (
    rebuild_premarket_analyzer_scores,
    update_benchmark_close_history,
    update_vix_close_history,
)
from research.alpha_assessment.metrics import summarize_performance
from research.alpha_assessment.performance_layer_v1 import (
    build_canonical_performance,
    write_canonical_outputs,
)
from research.alpha_assessment.plots import write_nav_preview_csv
from research.analyzer_validation import generate_analyzer_validation_summary

logger = logging.getLogger(__name__)


def _load_existing_canonical(path: Path) -> pd.DataFrame:
    if path.exists() and path.stat().st_size > 0:
        return pd.read_csv(path)
    return pd.DataFrame()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Alpha Assessment artifacts from canonical performance data")
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument("--strategy-csv", default=None, help="Optional local strategy CSV override")
    parser.add_argument("--benchmark-csv", default=None, help="Optional local benchmark CSV override")
    parser.add_argument("--rebuild-canonical", action="store_true", help="Force canonical rebuild from source artifacts")
    parser.add_argument("--allow-synthetic", action="store_true", help="Allow explicit synthetic fallback when no real data exists")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    repo_root = Path(args.repo_root).resolve()
    out_dir = repo_root / "outputs" / "alpha_assessment"
    canonical_path = out_dir / "canonical_performance.csv"

    # Refresh producer artifacts using existing repo outputs before canonical build.
    try:
        nav_ts_path = repo_root / "outputs" / "perf" / "nav_timeseries.csv"
        asof_date = None
        if nav_ts_path.exists() and nav_ts_path.stat().st_size > 0:
            nav_ts = pd.read_csv(nav_ts_path)
            if "date" in nav_ts.columns and not nav_ts.empty:
                asof_date = str(pd.to_datetime(nav_ts["date"], errors="coerce").dropna().max().strftime("%Y-%m-%d"))
        if asof_date:
            update_benchmark_close_history(asof_date=asof_date, output_path=repo_root / "outputs" / "perf" / "benchmark_close_history.csv")
            logger.info("[ALPHA_ASSESSMENT] refreshed benchmark_close_history asof=%s", asof_date)
            
            update_vix_close_history(asof_date=asof_date, output_path=repo_root / "outputs" / "perf" / "vix_close_history.csv")
            logger.info("[ALPHA_ASSESSMENT] refreshed vix_close_history asof=%s", asof_date)
    except Exception as e:
        logger.warning("[ALPHA_ASSESSMENT][WARN] benchmark/vix producer refresh failed: %s", e)
    try:
        analyzer_df = rebuild_premarket_analyzer_scores(
            signals_dir=repo_root / "signals",
            execution_email_dir=repo_root / "outputs" / "execution_email",
            output_path=repo_root / "outputs" / "perf" / "premarket_analyzer_scores.csv",
        )
        logger.info("[ALPHA_ASSESSMENT] refreshed premarket_analyzer_scores rows=%d", len(analyzer_df))
    except Exception as e:
        logger.warning("[ALPHA_ASSESSMENT][WARN] analyzer producer refresh failed: %s", e)

    if args.rebuild_canonical:
        df, meta = build_canonical_performance(
            repo_root,
            local_strategy_csv=Path(args.strategy_csv).resolve() if args.strategy_csv else None,
            local_benchmark_csv=Path(args.benchmark_csv).resolve() if args.benchmark_csv else None,
            allow_synthetic=bool(args.allow_synthetic),
        )
        paths = write_canonical_outputs(repo_root, df, meta)
        logger.info("[ALPHA_ASSESSMENT] Canonical outputs written: %s", paths)
    else:
        df = _load_existing_canonical(canonical_path)
        if df.empty:
            logger.warning("[ALPHA_ASSESSMENT] canonical_performance.csv missing; rebuilding from source artifacts")
            df, meta = build_canonical_performance(
                repo_root,
                local_strategy_csv=Path(args.strategy_csv).resolve() if args.strategy_csv else None,
                local_benchmark_csv=Path(args.benchmark_csv).resolve() if args.benchmark_csv else None,
                allow_synthetic=bool(args.allow_synthetic),
            )
            write_canonical_outputs(repo_root, df, meta)
        else:
            meta = {
                "synthetic_mode": False,
                "quality_warnings": [],
                "rows": int(len(df)),
            }

    if df.empty:
        raise RuntimeError("Alpha assessment has no data. Use --allow-synthetic for explicit synthetic mode.")

    if meta.get("synthetic_mode"):
        logger.warning("[ALPHA_ASSESSMENT] MODE=EXPLICIT SYNTHETIC MODE")
    else:
        logger.info("[ALPHA_ASSESSMENT] MODE=REAL DATA MODE")

    for warning in meta.get("quality_warnings") or []:
        if warning.startswith("benchmark_close_partial_coverage"):
            logger.warning("[ALPHA_ASSESSMENT] Missing/partial benchmark close coverage: %s", warning)
        elif warning.startswith("premarket_score_partial_coverage"):
            logger.warning("[ALPHA_ASSESSMENT] Missing/partial analyzer score coverage: %s", warning)
        elif warning.startswith("strategy_nav_partial_coverage"):
            logger.warning("[ALPHA_ASSESSMENT] Partial strategy NAV coverage: %s", warning)

    summary = summarize_performance(df)
    preview_path = write_nav_preview_csv(df, out_dir)
    logger.info("[ALPHA_ASSESSMENT] summary=%s", summary)
    logger.info("[ALPHA_ASSESSMENT] preview_csv=%s", preview_path)

    # Generate analyzer validation reports
    try:
        overlay_path = repo_root / "outputs" / "overlay_engine" / "overlay_backtest.csv"
        if not overlay_path.exists():
            overlay_path = None
        validation_outputs = generate_analyzer_validation_summary(
            canonical_path=canonical_path,
            overlay_path=overlay_path,
            output_dir=out_dir,
        )
        logger.info("[ALPHA_ASSESSMENT] analyzer validation outputs: %s", validation_outputs)
    except Exception as e:
        logger.warning("[ALPHA_ASSESSMENT][WARN] analyzer validation generation failed: %s", e)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
