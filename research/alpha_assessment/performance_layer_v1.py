from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


REQUIRED_COLUMNS = [
	"date",
	"strategy_nav",
	"strategy_return",
	"spy_close",
	"spy_return",
	"excess_return",
	"vix_close",
	"vix_regime",
	"gross_exposure",
	"net_exposure",
	"cash_weight",
	"turnover",
	"holdings_count",
	"realized_pnl",
	"unrealized_pnl",
	"premarket_score",
	"overlay_signal",
	"active_overlay",
	"notes_source_flags",
]

FIELD_REQUIREMENTS: dict[str, bool] = {
	"date": True,
	"strategy_nav": True,
	"strategy_return": True,
	"spy_close": False,
	"spy_return": True,
	"excess_return": True,
	"vix_close": True,
	"vix_regime": True,
	"gross_exposure": False,
	"net_exposure": False,
	"cash_weight": False,
	"turnover": False,
	"holdings_count": False,
	"realized_pnl": False,
	"unrealized_pnl": False,
	"premarket_score": False,
	"overlay_signal": True,
	"active_overlay": True,
	"notes_source_flags": False,
}


@dataclass(frozen=True)
class SourcePaths:
	nav_timeseries_csv: Path
	inception_nav_csv: Path | None
	benchmark_close_csv: Path
	vix_regime_csv: Path
	trades_csv: Path
	holdings_mtm_dir: Path
	signals_dir: Path
	premarket_analyzer_csv: Path
	execution_email_dir: Path


def default_source_paths(repo_root: Path) -> SourcePaths:
	inception_candidates = sorted((repo_root / "outputs" / "perf").glob("inception_nav_*.csv"))
	inception_path = inception_candidates[-1] if inception_candidates else None
	return SourcePaths(
		nav_timeseries_csv=repo_root / "outputs" / "perf" / "nav_timeseries.csv",
		inception_nav_csv=inception_path,
		benchmark_close_csv=repo_root / "outputs" / "perf" / "benchmark_close_history.csv",
		vix_regime_csv=repo_root / "outputs" / "vix_regime" / "regime_history.csv",
		trades_csv=repo_root / "outputs" / "ledger" / "trades.csv",
		holdings_mtm_dir=repo_root / "outputs" / "perf",
		signals_dir=repo_root / "signals",
		premarket_analyzer_csv=repo_root / "outputs" / "perf" / "premarket_analyzer_scores.csv",
		execution_email_dir=repo_root / "outputs" / "execution_email",
	)


def _empty_canonical_frame() -> pd.DataFrame:
	return pd.DataFrame(columns=REQUIRED_COLUMNS)


def _safe_read_csv(path: Path | None) -> pd.DataFrame:
	if path is None or not path.exists() or path.stat().st_size == 0:
		return pd.DataFrame()
	try:
		return pd.read_csv(path)
	except Exception as exc:
		logger.warning("[ALPHA_ASSESSMENT] Failed to read csv=%s err=%s", path, exc)
		return pd.DataFrame()


def _normalize_date_column(df: pd.DataFrame, col: str) -> pd.DataFrame:
	if col not in df.columns:
		return df
	out = df.copy()
	out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d")
	return out


def _safe_float(value: Any) -> float | None:
	try:
		if value is None or pd.isna(value):
			return None
		return float(value)
	except Exception:
		return None


def _load_strategy_nav(nav_path: Path) -> pd.DataFrame:
	nav = _safe_read_csv(nav_path)
	if nav.empty:
		return pd.DataFrame(columns=["date", "strategy_nav", "strategy_return", "gross_exposure", "net_exposure", "cash_weight", "turnover"])
	nav = _normalize_date_column(nav, "date")
	for col in ("equity", "return_1d", "gross_exposure", "net_exposure", "turnover", "turnover_pct", "cash"):
		if col in nav.columns:
			nav[col] = pd.to_numeric(nav[col], errors="coerce")
	if "turnover" not in nav.columns and "turnover_pct" in nav.columns:
		nav["turnover"] = nav["turnover_pct"]
	if "return_1d" not in nav.columns and "equity" in nav.columns:
		nav = nav.sort_values("date")
		nav["return_1d"] = nav["equity"].pct_change()
	if "cash" in nav.columns and "equity" in nav.columns:
		nav["cash_weight"] = nav["cash"] / nav["equity"].replace(0, pd.NA)
	else:
		nav["cash_weight"] = pd.NA
	out = nav[[
		"date",
		"equity",
		"return_1d",
		"gross_exposure",
		"net_exposure",
		"cash_weight",
		"turnover",
	]].rename(columns={"equity": "strategy_nav", "return_1d": "strategy_return"})
	out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
	return out


def _load_benchmark(inception_nav_path: Path | None) -> pd.DataFrame:
	bench = _safe_read_csv(inception_nav_path)
	if bench.empty:
		return pd.DataFrame(columns=["date", "spy_close", "spy_return"])
	bench = _normalize_date_column(bench, "date")
	if "spy_nav" in bench.columns:
		bench["spy_nav"] = pd.to_numeric(bench["spy_nav"], errors="coerce")
		bench = bench.sort_values("date")
		bench["spy_return"] = bench["spy_nav"].pct_change()
	elif "spy_return_since_inception" in bench.columns:
		bench["spy_return"] = pd.to_numeric(bench["spy_return_since_inception"], errors="coerce").diff()
	else:
		bench["spy_return"] = pd.NA
	# Repo stores SPY NAV, not raw close, so spy_close is null unless explicit close data is provided.
	bench["spy_close"] = pd.NA
	out = bench[["date", "spy_close", "spy_return"]]
	out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
	return out


def _load_benchmark_close_history(path: Path) -> pd.DataFrame:
	bench = _safe_read_csv(path)
	if bench.empty:
		return pd.DataFrame(columns=["date", "spy_close", "spy_return"])
	date_col = "date"
	if "as_of" in bench.columns:
		date_col = "as_of"
	bench = _normalize_date_column(bench, date_col)

	close_col = None
	for candidate in ("spy_close", "close", "benchmark_close", "spy_price"):
		if candidate in bench.columns:
			close_col = candidate
			break

	ret_col = None
	for candidate in ("spy_return", "return_1d", "benchmark_return"):
		if candidate in bench.columns:
			ret_col = candidate
			break

	out = pd.DataFrame({"date": bench[date_col]})
	out["spy_close"] = pd.to_numeric(bench[close_col], errors="coerce") if close_col else pd.NA
	if ret_col:
		out["spy_return"] = pd.to_numeric(bench[ret_col], errors="coerce")
	elif close_col:
		out = out.sort_values("date")
		out["spy_return"] = pd.to_numeric(out["spy_close"], errors="coerce").pct_change()
	else:
		out["spy_return"] = pd.NA
	return out.sort_values("date").drop_duplicates(subset=["date"], keep="last")


def _load_spy_close_from_holdings(holdings_dir: Path) -> pd.DataFrame:
	files = sorted(holdings_dir.glob("holdings_mtm_*.csv"))
	if not files:
		return pd.DataFrame(columns=["date", "spy_close"])
	rows: list[dict[str, Any]] = []
	for path in files:
		df = _safe_read_csv(path)
		if df.empty or "date" not in df.columns or "ticker" not in df.columns:
			continue
		df = _normalize_date_column(df, "date")
		spy = df[df["ticker"].astype(str).str.upper() == "SPY"]
		if spy.empty:
			continue
		price_col = "mtm_price" if "mtm_price" in spy.columns else "market_value"
		spy_close = pd.to_numeric(spy[price_col], errors="coerce")
		if spy_close.notna().any():
			rows.append({"date": str(spy.iloc[-1]["date"]), "spy_close": float(spy_close.dropna().iloc[-1])})
	if not rows:
		return pd.DataFrame(columns=["date", "spy_close"])
	out = pd.DataFrame(rows)
	return out.sort_values("date").drop_duplicates(subset=["date"], keep="last")


def _load_vix(vix_path: Path) -> pd.DataFrame:
	vix = _safe_read_csv(vix_path)
	if vix.empty:
		return pd.DataFrame(columns=["date", "vix_close", "vix_regime"])
	date_col = "as_of" if "as_of" in vix.columns else "date"
	vix = _normalize_date_column(vix, date_col)
	if "vix" in vix.columns:
		vix["vix"] = pd.to_numeric(vix["vix"], errors="coerce")
	out = vix[[date_col, "vix", "regime"]].rename(columns={date_col: "date", "vix": "vix_close", "regime": "vix_regime"})
	out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
	return out


def _load_holdings_rollup(holdings_dir: Path) -> pd.DataFrame:
	files = sorted(holdings_dir.glob("holdings_mtm_*.csv"))
	if not files:
		return pd.DataFrame(columns=["date", "holdings_count", "realized_pnl", "unrealized_pnl"])
	rows: list[dict[str, Any]] = []
	for path in files:
		df = _safe_read_csv(path)
		if df.empty or "date" not in df.columns:
			continue
		df = _normalize_date_column(df, "date")
		for col in ("realized_pnl", "unrealized_pnl"):
			if col in df.columns:
				df[col] = pd.to_numeric(df[col], errors="coerce")
			else:
				df[col] = pd.NA
		grouped = df.groupby("date", dropna=False).agg(
			holdings_count=("ticker", "count"),
			realized_pnl=("realized_pnl", "sum"),
			unrealized_pnl=("unrealized_pnl", "sum"),
		)
		grouped = grouped.reset_index()
		rows.extend(grouped.to_dict(orient="records"))
	if not rows:
		return pd.DataFrame(columns=["date", "holdings_count", "realized_pnl", "unrealized_pnl"])
	out = pd.DataFrame(rows)
	out = out.groupby("date", as_index=False).agg(
		holdings_count=("holdings_count", "max"),
		realized_pnl=("realized_pnl", "max"),
		unrealized_pnl=("unrealized_pnl", "max"),
	)
	out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
	return out


def _extract_signal_overlay(path: Path) -> dict[str, Any] | None:
	try:
		payload = json.loads(path.read_text(encoding="utf-8"))
	except Exception:
		return None

	# Some archived snapshots are list payloads; only dict snapshots are canonical here.
	if not isinstance(payload, dict):
		return None

	date = str(payload.get("snapshot_date") or "").strip()
	if not date:
		return None
	breaker = payload.get("breaker") if isinstance(payload.get("breaker"), dict) else {}
	exposure_today = breaker.get("exposure_multiplier_today")
	overlay_signal = breaker.get("exposure_label_today") or breaker.get("mode")
	active_overlay = None
	try:
		active_overlay = float(exposure_today) < 1.0
	except Exception:
		active_overlay = None
	premarket_score = None
	# Optional analyzer keys if present in snapshot payloads.
	analyzer = payload.get("market_analyzer") if isinstance(payload.get("market_analyzer"), dict) else {}
	for key in ("score", "premarket_score", "pre_market_score"):
		if key in analyzer:
			premarket_score = analyzer.get(key)
			break
	return {
		"date": date,
		"premarket_score": premarket_score,
		"overlay_signal": overlay_signal,
		"active_overlay": active_overlay,
	}


def _load_premarket_analyzer(path: Path) -> pd.DataFrame:
	analyzer = _safe_read_csv(path)
	if analyzer.empty:
		return pd.DataFrame(columns=["date", "premarket_score"])
	date_col = "date"
	if "as_of" in analyzer.columns:
		date_col = "as_of"
	analyzer = _normalize_date_column(analyzer, date_col)
	score_col = None
	for candidate in ("premarket_score", "score", "market_analyzer_score"):
		if candidate in analyzer.columns:
			score_col = candidate
			break
	out = pd.DataFrame({"date": analyzer[date_col]})
	out["premarket_score"] = pd.to_numeric(analyzer[score_col], errors="coerce") if score_col else pd.NA
	return out.sort_values("date").drop_duplicates(subset=["date"], keep="last")


def _load_signal_overlay(signals_dir: Path) -> pd.DataFrame:
	files = sorted(signals_dir.glob("*.json"))
	rows = [row for row in (_extract_signal_overlay(p) for p in files) if row]
	if not rows:
		return pd.DataFrame(columns=["date", "premarket_score", "overlay_signal", "active_overlay"])
	out = pd.DataFrame(rows)
	out = _normalize_date_column(out, "date")
	out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
	return out


def _load_trades_turnover(trades_path: Path) -> pd.DataFrame:
	trades = _safe_read_csv(trades_path)
	if trades.empty or "trade_date" not in trades.columns:
		return pd.DataFrame(columns=["date", "turnover"])
	trades = _normalize_date_column(trades, "trade_date")
	for col in ("notional", "quantity", "fill_price"):
		if col in trades.columns:
			trades[col] = pd.to_numeric(trades[col], errors="coerce")
	if "notional" not in trades.columns and {"quantity", "fill_price"}.issubset(trades.columns):
		trades["notional"] = (trades["quantity"] * trades["fill_price"]).abs()
	roll = trades.groupby("trade_date", dropna=False).agg(turnover=("notional", "sum")).reset_index()
	out = roll.rename(columns={"trade_date": "date"})
	out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
	return out


def _synthesize_deterministic() -> pd.DataFrame:
	base_dates = pd.date_range("2026-01-05", periods=5, freq="B")
	nav = [10000.0, 10025.0, 10010.0, 10040.0, 10030.0]
	spy = [10000.0, 10005.0, 10001.0, 10018.0, 10016.0]
	rows: list[dict[str, Any]] = []
	prev_nav = None
	prev_spy = None
	for idx, dt in enumerate(base_dates):
		strategy_return = None if prev_nav is None else (nav[idx] / prev_nav) - 1.0
		spy_return = None if prev_spy is None else (spy[idx] / prev_spy) - 1.0
		rows.append(
			{
				"date": dt.strftime("%Y-%m-%d"),
				"strategy_nav": nav[idx],
				"strategy_return": strategy_return,
				"spy_close": pd.NA,
				"spy_return": spy_return,
				"excess_return": None if strategy_return is None or spy_return is None else strategy_return - spy_return,
				"vix_close": 20.0,
				"vix_regime": "ELEVATED",
				"gross_exposure": 0.5,
				"net_exposure": 0.5,
				"cash_weight": 0.5,
				"turnover": 0.0,
				"holdings_count": 5,
				"realized_pnl": 0.0,
				"unrealized_pnl": 0.0,
				"premarket_score": pd.NA,
				"overlay_signal": "SYNTHETIC",
				"active_overlay": False,
				"notes_source_flags": "synthetic_mode=true",
			}
		)
		prev_nav = nav[idx]
		prev_spy = spy[idx]
	return pd.DataFrame(rows)


def _build_field_coverage(df: pd.DataFrame, field_sources: dict[str, str]) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	total = int(len(df))
	for col in REQUIRED_COLUMNS:
		non_null = int(df[col].notna().sum()) if col in df.columns else 0
		null_count = total - non_null
		fill_rate = 0.0 if total == 0 else round((non_null / total) * 100.0, 2)
		rows.append(
			{
				"field": col,
				"required": "required" if FIELD_REQUIREMENTS.get(col, False) else "optional",
				"non_null_count": non_null,
				"null_count": null_count,
				"fill_rate_pct": fill_rate,
				"source_used": field_sources.get(col, "unknown"),
			}
		)
	return rows


def build_canonical_performance(
	repo_root: Path,
	*,
	local_strategy_csv: Path | None = None,
	local_benchmark_csv: Path | None = None,
	allow_synthetic: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
	paths = default_source_paths(repo_root)

	strategy = _load_strategy_nav(local_strategy_csv if local_strategy_csv else paths.nav_timeseries_csv)
	benchmark_core = _load_benchmark(local_benchmark_csv if local_benchmark_csv else paths.inception_nav_csv)
	benchmark_close = _load_benchmark_close_history(paths.benchmark_close_csv)
	spy_close_holdings = _load_spy_close_from_holdings(paths.holdings_mtm_dir)
	vix = _load_vix(paths.vix_regime_csv)
	holdings = _load_holdings_rollup(paths.holdings_mtm_dir)
	signal_overlay = _load_signal_overlay(paths.signals_dir)
	premarket_analyzer = _load_premarket_analyzer(paths.premarket_analyzer_csv)
	trade_turnover = _load_trades_turnover(paths.trades_csv)

	benchmark = benchmark_core[["date", "spy_return"]].rename(columns={"spy_return": "spy_return_from_inception"}) if not benchmark_core.empty else pd.DataFrame(columns=["date", "spy_return_from_inception"])
	if not benchmark_close.empty:
		benchmark = benchmark.merge(
			benchmark_close.rename(columns={"spy_return": "spy_return_from_close"}),
			on="date",
			how="outer",
		)
	else:
		benchmark["spy_close"] = pd.NA
		benchmark["spy_return_from_close"] = pd.NA
	if not spy_close_holdings.empty:
		benchmark = benchmark.merge(spy_close_holdings.rename(columns={"spy_close": "spy_close_from_holdings"}), on="date", how="outer")
		if "spy_close" not in benchmark.columns:
			benchmark["spy_close"] = pd.NA
		benchmark["spy_close"] = benchmark["spy_close"].where(benchmark["spy_close"].notna(), benchmark["spy_close_from_holdings"])
		benchmark = benchmark.drop(columns=["spy_close_from_holdings"])
	if "spy_return_from_inception" not in benchmark.columns:
		benchmark["spy_return_from_inception"] = pd.NA
	if "spy_return_from_close" not in benchmark.columns:
		benchmark["spy_return_from_close"] = pd.NA
	benchmark["spy_return"] = benchmark["spy_return_from_inception"].where(
		benchmark["spy_return_from_inception"].notna(),
		benchmark["spy_return_from_close"],
	)
	if "spy_close" not in benchmark.columns:
		benchmark["spy_close"] = pd.NA
	benchmark = benchmark[["date", "spy_close", "spy_return"]].sort_values("date").drop_duplicates(subset=["date"], keep="last")

	sources_used = {
		"strategy": str(local_strategy_csv if local_strategy_csv else paths.nav_timeseries_csv),
		"benchmark": str(local_benchmark_csv if local_benchmark_csv else paths.inception_nav_csv) if (local_benchmark_csv or paths.inception_nav_csv) else None,
		"benchmark_close": str(paths.benchmark_close_csv),
		"benchmark_close_holdings_fallback": str(paths.holdings_mtm_dir),
		"vix": str(paths.vix_regime_csv),
		"holdings": str(paths.holdings_mtm_dir),
		"signals": str(paths.signals_dir),
		"premarket_analyzer": str(paths.premarket_analyzer_csv),
		"trades": str(paths.trades_csv),
	}
	field_sources = {
		"date": "union(all discovered source dates)",
		"strategy_nav": "outputs/perf/nav_timeseries.csv:equity",
		"strategy_return": "outputs/perf/nav_timeseries.csv:return_1d (or pct_change(equity))",
		"spy_close": "outputs/perf/benchmark_close_history.csv:spy_close|close -> fallback outputs/perf/holdings_mtm_*.csv:SPY.mtm_price",
		"spy_return": "inception_nav_*.csv:spy_nav pct_change -> fallback benchmark_close_history.csv:return_1d",
		"excess_return": "derived strategy_return - spy_return",
		"vix_close": "outputs/vix_regime/regime_history.csv:vix",
		"vix_regime": "outputs/vix_regime/regime_history.csv:regime",
		"gross_exposure": "outputs/perf/nav_timeseries.csv:gross_exposure",
		"net_exposure": "outputs/perf/nav_timeseries.csv:net_exposure",
		"cash_weight": "derived cash / equity from nav_timeseries.csv",
		"turnover": "outputs/perf/nav_timeseries.csv:turnover -> fallback outputs/ledger/trades.csv:notional sum",
		"holdings_count": "outputs/perf/holdings_mtm_*.csv grouped by date",
		"realized_pnl": "outputs/perf/holdings_mtm_*.csv grouped by date",
		"unrealized_pnl": "outputs/perf/holdings_mtm_*.csv grouped by date",
		"premarket_score": "outputs/perf/premarket_analyzer_scores.csv -> fallback signals/*.json:market_analyzer.*",
		"overlay_signal": "signals/*.json:breaker.exposure_label_today|mode",
		"active_overlay": "signals/*.json:breaker.exposure_multiplier_today<1",
		"notes_source_flags": "canonical row-level missing field flags",
	}

	# Deterministic date index from all sources that have date keys.
	dates = set()
	for frame in (strategy, benchmark, vix, holdings, signal_overlay, trade_turnover):
		if not frame.empty and "date" in frame.columns:
			dates.update([str(d) for d in frame["date"].dropna().tolist()])

	if not dates:
		if not allow_synthetic:
			raise RuntimeError(
				"No real-data sources available for canonical performance. "
				"Re-run with allow_synthetic=True to use deterministic fallback."
			)
		logger.warning("[ALPHA_ASSESSMENT] Falling back to deterministic synthetic mode")
		synthetic = _synthesize_deterministic()
		meta = {
			"synthetic_mode": True,
			"source_warnings": ["No real-data dates discovered across canonical sources."],
			"sources_used": sources_used,
			"field_sources": field_sources,
			"field_coverage": _build_field_coverage(synthetic, field_sources),
			"quality_warnings": [],
			"missing_contracts": [],
			"rows": int(len(synthetic)),
		}
		return synthetic, meta

	if strategy.empty and not allow_synthetic:
		raise RuntimeError(
			"Missing required strategy source data in outputs/perf/nav_timeseries.csv. "
			"Synthetic mode is disabled unless --allow-synthetic is provided."
		)

	base = pd.DataFrame({"date": sorted(dates)})
	canonical = base.merge(strategy, on="date", how="left")
	canonical = canonical.merge(benchmark, on="date", how="left")
	canonical = canonical.merge(vix, on="date", how="left")
	canonical = canonical.merge(holdings, on="date", how="left")
	canonical = canonical.merge(signal_overlay, on="date", how="left")
	canonical = canonical.merge(
		premarket_analyzer.rename(columns={"premarket_score": "premarket_score_from_analyzer"}),
		on="date",
		how="left",
	)
	canonical = canonical.merge(trade_turnover.rename(columns={"turnover": "turnover_from_trades"}), on="date", how="left")

	if "turnover" not in canonical.columns:
		canonical["turnover"] = pd.NA
	canonical["turnover"] = canonical["turnover"].where(canonical["turnover"].notna(), canonical.get("turnover_from_trades"))
	if "premarket_score" not in canonical.columns:
		canonical["premarket_score"] = pd.NA
	if "premarket_score_from_analyzer" not in canonical.columns:
		canonical["premarket_score_from_analyzer"] = pd.NA
	canonical["premarket_score"] = canonical["premarket_score_from_analyzer"].where(
		canonical["premarket_score_from_analyzer"].notna(),
		canonical["premarket_score"],
	)
	canonical = canonical.drop(columns=[c for c in ["turnover_from_trades"] if c in canonical.columns])
	canonical = canonical.drop(columns=[c for c in ["premarket_score_from_analyzer"] if c in canonical.columns])

	canonical["excess_return"] = canonical["strategy_return"] - canonical["spy_return"]
	canonical["notes_source_flags"] = ""

	missing_msgs = []
	for col in REQUIRED_COLUMNS:
		if col not in canonical.columns:
			canonical[col] = pd.NA
			missing_msgs.append(f"missing_column={col}")

	# Build row-level missing flags with explicit required/optional split.
	for idx in canonical.index:
		missing_required: list[str] = []
		missing_optional: list[str] = []
		for col in REQUIRED_COLUMNS:
			if col == "notes_source_flags" or col not in canonical.columns:
				continue
			if pd.isna(canonical.at[idx, col]):
				if FIELD_REQUIREMENTS.get(col, False):
					missing_required.append(col)
				else:
					missing_optional.append(col)
		flags = []
		if missing_required:
			flags.append("missing_required=" + "|".join(sorted(missing_required)))
		if missing_optional:
			flags.append("missing_optional=" + "|".join(sorted(missing_optional)))
		canonical.at[idx, "notes_source_flags"] = ";".join(flags) if flags else "ok"

	canonical = canonical[REQUIRED_COLUMNS].sort_values("date").reset_index(drop=True)

	coverage_rows = _build_field_coverage(canonical, field_sources)
	coverage_df = pd.DataFrame(coverage_rows)
	quality_warnings: list[str] = []
	for _, row in coverage_df.iterrows():
		required_label = str(row.get("required", "optional"))
		fill_rate = _safe_float(row.get("fill_rate_pct"))
		if fill_rate is None:
			continue
		if required_label == "required" and fill_rate < 100.0:
			quality_warnings.append(f"required_field_partial={row['field']} fill_rate_pct={fill_rate:.2f}")
		if required_label == "optional" and fill_rate == 0.0:
			quality_warnings.append(f"optional_field_empty={row['field']}")

	missing_contracts: list[str] = []
	if not paths.benchmark_close_csv.exists():
		missing_contracts.append(
			"missing_contract=outputs/perf/benchmark_close_history.csv "
			"expected_columns=date,spy_close[,spy_return]"
		)
	if not paths.premarket_analyzer_csv.exists():
		missing_contracts.append(
			"missing_contract=outputs/perf/premarket_analyzer_scores.csv "
			"expected_columns=date,premarket_score"
		)

	strategy_nav_fill = coverage_df[coverage_df["field"] == "strategy_nav"]["fill_rate_pct"]
	if not strategy_nav_fill.empty and float(strategy_nav_fill.iloc[0]) < 100.0:
		quality_warnings.append(f"strategy_nav_partial_coverage fill_rate_pct={float(strategy_nav_fill.iloc[0]):.2f}")
	spy_close_fill = coverage_df[coverage_df["field"] == "spy_close"]["fill_rate_pct"]
	if not spy_close_fill.empty and float(spy_close_fill.iloc[0]) < 100.0:
		quality_warnings.append(f"benchmark_close_partial_coverage fill_rate_pct={float(spy_close_fill.iloc[0]):.2f}")
	premarket_fill = coverage_df[coverage_df["field"] == "premarket_score"]["fill_rate_pct"]
	if not premarket_fill.empty and float(premarket_fill.iloc[0]) < 100.0:
		quality_warnings.append(f"premarket_score_partial_coverage fill_rate_pct={float(premarket_fill.iloc[0]):.2f}")

	meta = {
		"synthetic_mode": False,
		"source_warnings": missing_msgs,
		"sources_used": sources_used,
		"field_sources": field_sources,
		"field_coverage": coverage_rows,
		"quality_warnings": quality_warnings,
		"missing_contracts": missing_contracts,
		"rows": int(len(canonical)),
	}
	return canonical, meta


def write_canonical_outputs(repo_root: Path, df: pd.DataFrame, meta: dict[str, Any]) -> dict[str, str]:
	out_dir = repo_root / "outputs" / "alpha_assessment"
	out_dir.mkdir(parents=True, exist_ok=True)

	csv_path = out_dir / "canonical_performance.csv"
	json_path = out_dir / "canonical_performance.json"
	report_path = out_dir / "real_data_integration_report.md"
	coverage_path = out_dir / "canonical_field_coverage.csv"

	df.to_csv(csv_path, index=False)
	json_path.write_text(df.to_json(orient="records", date_format="iso", indent=2) + "\n", encoding="utf-8")

	report_lines = [
		"# Real Data Integration Report",
		"",
		f"- rows: {meta.get('rows')}",
		f"- synthetic_mode: {meta.get('synthetic_mode')}",
		"- sources:",
	]
	for key, value in (meta.get("sources_used") or {}).items():
		report_lines.append(f"  - {key}: {value}")
	warnings = meta.get("source_warnings") or []
	if warnings:
		report_lines.extend(["- warnings:"] + [f"  - {w}" for w in warnings])
	quality_warnings = meta.get("quality_warnings") or []
	if quality_warnings:
		report_lines.extend(["- quality_warnings:"] + [f"  - {w}" for w in quality_warnings])
	missing_contracts = meta.get("missing_contracts") or []
	if missing_contracts:
		report_lines.extend(["- missing_contracts:"] + [f"  - {w}" for w in missing_contracts])

	coverage_rows = meta.get("field_coverage") or []
	coverage_df = pd.DataFrame(coverage_rows)
	if not coverage_df.empty:
		coverage_df.to_csv(coverage_path, index=False)
		report_lines.extend(
			[
				"",
				"## Data Quality Coverage",
				"",
				"| field | required | non_null_count | null_count | fill_rate_pct | source_used |",
				"|---|---|---:|---:|---:|---|",
			]
		)
		for row in coverage_rows:
			report_lines.append(
				"| {field} | {required} | {non_null_count} | {null_count} | {fill_rate_pct:.2f} | {source_used} |".format(
					field=row.get("field"),
					required=row.get("required"),
					non_null_count=row.get("non_null_count"),
					null_count=row.get("null_count"),
					fill_rate_pct=float(row.get("fill_rate_pct", 0.0)),
					source_used=row.get("source_used"),
				)
			)

	report_lines.extend(["", "## Notes", "", "- Nulls indicate unavailable upstream fields.", "- `notes_source_flags` uses `missing_required=` and `missing_optional=` segments per row."])
	report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

	return {
		"canonical_csv": str(csv_path),
		"canonical_json": str(json_path),
		"integration_report": str(report_path),
		"coverage_csv": str(coverage_path),
	}
