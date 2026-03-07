from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from research.alpha_assessment.performance_layer_v1 import build_canonical_performance
from research.overlay_engine.overlay_backtest import run_overlay_backtest
from research.overlay_engine.overlay_signals import derive_overlay_signal_frame

logger = logging.getLogger(__name__)


def _load_or_build_canonical(
    repo_root: Path,
    canonical_csv: Path | None,
    strategy_csv: Path | None,
    benchmark_csv: Path | None,
    allow_synthetic: bool,
) -> pd.DataFrame:
    if canonical_csv and canonical_csv.exists() and canonical_csv.stat().st_size > 0:
        return pd.read_csv(canonical_csv)

    default_canonical = repo_root / "outputs" / "alpha_assessment" / "canonical_performance.csv"
    if default_canonical.exists() and default_canonical.stat().st_size > 0:
        return pd.read_csv(default_canonical)

    logger.warning("[OVERLAY_ENGINE] canonical performance missing, building from source artifacts")
    df, _meta = build_canonical_performance(
        repo_root,
        local_strategy_csv=strategy_csv,
        local_benchmark_csv=benchmark_csv,
        allow_synthetic=allow_synthetic,
    )
    if _meta.get("synthetic_mode"):
        logger.warning("[OVERLAY_ENGINE] MODE=EXPLICIT SYNTHETIC MODE")
    else:
        logger.info("[OVERLAY_ENGINE] MODE=REAL DATA MODE")
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="Build overlay engine outputs using canonical performance layer")
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument("--canonical-csv", default=None, help="Optional explicit canonical CSV input")
    parser.add_argument("--strategy-csv", default=None, help="Optional strategy CSV source")
    parser.add_argument("--benchmark-csv", default=None, help="Optional benchmark CSV source")
    parser.add_argument("--allow-synthetic", action="store_true", help="Allow explicit synthetic fallback")
    parser.add_argument("--no-lag", action="store_true", help="Disable lagging (debug only)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    repo_root = Path(args.repo_root).resolve()

    canonical = _load_or_build_canonical(
        repo_root=repo_root,
        canonical_csv=Path(args.canonical_csv).resolve() if args.canonical_csv else None,
        strategy_csv=Path(args.strategy_csv).resolve() if args.strategy_csv else None,
        benchmark_csv=Path(args.benchmark_csv).resolve() if args.benchmark_csv else None,
        allow_synthetic=bool(args.allow_synthetic),
    )
    if canonical.empty:
        raise RuntimeError("Overlay engine has no canonical data. Use --allow-synthetic for explicit fallback.")

    overlay_signals = derive_overlay_signal_frame(canonical)
    joined = canonical.merge(
        overlay_signals[["date", "overlay_multiplier"]],
        on="date",
        how="left",
    )
    overlay_mult = joined["overlay_multiplier"] if "overlay_multiplier" in joined.columns else pd.Series(1.0, index=joined.index)
    joined["overlay_multiplier"] = pd.to_numeric(overlay_mult, errors="coerce").fillna(1.0)

    result = run_overlay_backtest(joined, enforce_lag=not args.no_lag)

    out_dir = repo_root / "outputs" / "overlay_engine"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "overlay_backtest.csv"
    result.to_csv(out_csv, index=False)
    if args.no_lag:
        logger.warning("[OVERLAY_ENGINE] Lagging disabled via --no-lag (debug only)")
    else:
        logger.info("[OVERLAY_ENGINE] Lagging enforced (no-lookahead)")
    logger.info("[OVERLAY_ENGINE] output=%s rows=%d lag_enforced=%s", out_csv, len(result), not args.no_lag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
