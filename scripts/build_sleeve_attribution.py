"""Weekly per-sleeve attribution + promotion-gate skeleton.

Design
------
- Reads existing signals/ snapshots and outputs/perf artifacts.
- Computes per-sleeve contribution vs SPY where data allows.
- Emits outputs/perf/sleeve_attribution_<date>.json + a markdown summary with
  an explicit PROMOTION GATE section.
- Designed for cron; degrades to INSUFFICIENT_DATA loudly, never fabricates numbers.

Promotion gate criteria
-----------------------
  1. N_WEEKS_POSITIVE: >= MIN_POSITIVE_WEEKS weeks of positive attribution at
     regime weight (default: 4 weeks).
  2. LIVE_AFFORDABILITY: average daily notional per sleeve <= live affordability
     threshold (default: $500 pilot cap; a sleeve must be executable at that scale).
  3. REGIME_ROTATION: sleeve attribution must survive a regime rotation check
     (present in signals across at least two distinct VIX regime labels seen in the
     observation window).

Usage
-----
    # Cron (weekly, no args):
    .venv/bin/python3 scripts/build_sleeve_attribution.py

    # Explicit date:
    .venv/bin/python3 scripts/build_sleeve_attribution.py --date 2026-04-16

    # CI dry-run with fixture dir:
    .venv/bin/python3 scripts/build_sleeve_attribution.py --date 2026-04-16 \\
        --signals-dir /path/to/fixture/signals \\
        --perf-dir /path/to/fixture/perf \\
        --output-dir /tmp/out
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Repo root on sys.path so we can import shared utilities
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------
DEFAULT_SIGNALS_DIR = REPO_ROOT / "signals"
DEFAULT_PERF_DIR = REPO_ROOT / "outputs" / "perf"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "perf"

MIN_POSITIVE_WEEKS = 4          # Gate 1: minimum weeks of positive attribution
LIVE_AFFORDABILITY_CAP = 500.0  # Gate 2: max daily buy notional per sleeve ($)
MIN_REGIME_LABELS = 2           # Gate 3: distinct VIX regime labels required
LOOKBACK_WEEKS = 8              # How many weekly snapshots to analyze
SPY_TICKER = "SPY"

GATE_PASS = "PASS"
GATE_FAIL = "FAIL"
GATE_INSUFFICIENT = "INSUFFICIENT_DATA"
STATUS_INSUFFICIENT = "INSUFFICIENT_DATA"
STATUS_OK = "OK"


# ---------------------------------------------------------------------------
# Signal snapshot loading
# ---------------------------------------------------------------------------

def _load_signals_snapshots(
    signals_dir: Path,
    *,
    as_of: dt.date,
    lookback_weeks: int = LOOKBACK_WEEKS,
) -> list[dict[str, Any]]:
    """Load the most recent ``lookback_weeks`` weekly signals snapshots."""
    cutoff = as_of - dt.timedelta(weeks=lookback_weeks)
    snapshots: list[tuple[dt.date, dict[str, Any]]] = []
    for path in sorted(signals_dir.glob("*.json")):
        try:
            snap_date = dt.date.fromisoformat(path.stem)
        except ValueError:
            continue
        if snap_date < cutoff or snap_date > as_of:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            snapshots.append((snap_date, data))
        except Exception as exc:
            logger.warning("[SLEEVE_ATTR] Could not load signals snapshot %s: %s", path, exc)
    snapshots.sort(key=lambda x: x[0])
    return [s for _, s in snapshots]


def _sleeve_weights_from_snapshot(snapshot: dict[str, Any]) -> dict[str, float]:
    """Extract per-sleeve allocation weights from a signals snapshot."""
    sleeve_allocs = snapshot.get("sleeve_allocations") or {}
    if isinstance(sleeve_allocs, dict):
        out: dict[str, float] = {}
        for k, v in sleeve_allocs.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
        return out
    # Fallback: aggregate from per-signal rows
    signals = snapshot.get("signals") or []
    weights: dict[str, float] = {}
    for row in signals:
        if not isinstance(row, dict):
            continue
        sleeve = str(row.get("sleeve") or "").split(",")[0].strip()
        if not sleeve:
            continue
        tw = float(row.get("target_weight") or 0.0)
        weights[sleeve] = weights.get(sleeve, 0.0) + tw
    return weights


def _vix_label_from_snapshot(snapshot: dict[str, Any]) -> str | None:
    """Extract VIX regime label from a signals snapshot."""
    breaker = snapshot.get("breaker") or {}
    # Try market_analyzer
    ma = snapshot.get("market_analyzer") or {}
    vix_label = ma.get("vix_regime") or breaker.get("vix_regime_label")
    if vix_label:
        return str(vix_label).upper()
    # Try allocations block
    allocs = snapshot.get("allocations") or {}
    vix_label = allocs.get("vix_regime_label")
    if vix_label:
        return str(vix_label).upper()
    return None


# ---------------------------------------------------------------------------
# NAV / contribution loading
# ---------------------------------------------------------------------------

def _load_nav_timeseries(perf_dir: Path) -> "pd.DataFrame | None":
    """Load the nav_timeseries.csv if present and parseable."""
    if not HAS_PANDAS:
        return None
    path = perf_dir / "nav_timeseries.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(str(path))
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
        return df
    except Exception as exc:
        logger.warning("[SLEEVE_ATTR] Could not load nav_timeseries.csv: %s", exc)
        return None


def _load_contribution_sleeves(perf_dir: Path, as_of: dt.date, lookback_weeks: int) -> list[dict[str, Any]]:
    """Load all contribution_sleeves_<date>.csv files in the window."""
    if not HAS_PANDAS:
        return []
    cutoff = as_of - dt.timedelta(weeks=lookback_weeks)
    rows: list[dict[str, Any]] = []
    for path in sorted(perf_dir.glob("contribution_sleeves_*.csv")):
        try:
            date_str = path.stem.replace("contribution_sleeves_", "")
            contrib_date = dt.date.fromisoformat(date_str)
        except ValueError:
            continue
        if contrib_date < cutoff or contrib_date > as_of:
            continue
        try:
            df = pd.read_csv(str(path))
            # Normalize: some files have the sleeve column as literal "sleeve" name,
            # some as a numeric index alias.  Accept text sleeve values (dtype may be
            # 'object' in older pandas or 'str' / StringDtype in newer versions).
            sleeve_col = df.get("sleeve") if "sleeve" in df.columns else None
            sleeve_is_text = (
                sleeve_col is not None
                and str(sleeve_col.dtype) not in ("float64", "int64", "int32", "float32")
            )
            if sleeve_is_text:
                for _, row in df.iterrows():
                    rows.append({
                        "date": str(contrib_date),
                        "sleeve": str(row.get("sleeve") or "").strip(),
                        "weight_start": float(row.get("weight_start") or 0.0),
                        "sleeve_return": float(row.get("sleeve_return") or 0.0),
                        "contribution": float(row.get("contribution") or 0.0),
                    })
        except Exception as exc:
            logger.warning("[SLEEVE_ATTR] Could not load %s: %s", path, exc)
    return rows


def _load_benchmark_returns(perf_dir: Path, as_of: dt.date, lookback_weeks: int) -> dict[str, float]:
    """Load benchmark (SPY) daily returns from benchmark_close_history.csv."""
    if not HAS_PANDAS:
        return {}
    cutoff = as_of - dt.timedelta(weeks=lookback_weeks)
    path = perf_dir / "benchmark_close_history.csv"
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(str(path))
        if "date" not in df.columns or "close" not in df.columns:
            return {}
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date", "close"]).sort_values("date")
        df = df[(df["date"].dt.date >= cutoff) & (df["date"].dt.date <= as_of)]
        df["return"] = df["close"].pct_change()
        return {str(row["date"].date()): float(row["return"]) for _, row in df.dropna(subset=["return"]).iterrows()}
    except Exception as exc:
        logger.warning("[SLEEVE_ATTR] Could not load benchmark_close_history.csv: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Attribution computation
# ---------------------------------------------------------------------------

def _compute_sleeve_attribution(
    snapshots: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
    benchmark_returns: dict[str, float],
) -> dict[str, dict[str, Any]]:
    """Compute per-sleeve attribution summary.

    Returns dict[sleeve_name -> {
        observation_days: int,
        total_contribution: float | None,
        avg_daily_contribution: float | None,
        avg_weight: float | None,
        positive_days: int,
        benchmark_excess: float | None,   # vs SPY over window
        vix_regimes_seen: list[str],
    }]
    """
    # Per-sleeve daily contribution from perf artifacts
    sleeve_stats: dict[str, dict[str, Any]] = {}

    for row in contributions:
        sleeve = str(row.get("sleeve") or "").strip()
        if not sleeve:
            continue
        s = sleeve_stats.setdefault(sleeve, {
            "contributions": [],
            "weights": [],
            "dates": [],
        })
        contrib = row.get("contribution")
        weight = row.get("weight_start")
        if contrib is not None:
            s["contributions"].append(float(contrib))
            s["dates"].append(str(row.get("date") or ""))
        if weight is not None:
            s["weights"].append(float(weight))

    # Aggregate sleeve stats from signals snapshots (regime labels)
    vix_labels_by_sleeve: dict[str, set[str]] = {}
    for snap in snapshots:
        vix_label = _vix_label_from_snapshot(snap)
        sleeve_weights = _sleeve_weights_from_snapshot(snap)
        for sleeve in sleeve_weights:
            if vix_label:
                vix_labels_by_sleeve.setdefault(sleeve, set()).add(vix_label)

    # Build summary per sleeve
    total_bench_return = sum(benchmark_returns.values()) if benchmark_returns else None
    result: dict[str, dict[str, Any]] = {}

    all_sleeves = set(sleeve_stats) | set(vix_labels_by_sleeve)
    for sleeve in all_sleeves:
        s = sleeve_stats.get(sleeve, {})
        contribs = s.get("contributions", [])
        weights = s.get("weights", [])
        vix_seen = sorted(vix_labels_by_sleeve.get(sleeve, set()))

        total_contrib = sum(contribs) if contribs else None
        avg_contrib = (total_contrib / len(contribs)) if contribs else None
        avg_weight = (sum(weights) / len(weights)) if weights else None
        positive_days = sum(1 for c in contribs if c > 0) if contribs else 0

        benchmark_excess: float | None = None
        if total_contrib is not None and total_bench_return is not None and total_bench_return != 0:
            # Excess = sleeve total contribution vs benchmark return (aggregate, not risk-adjusted)
            benchmark_excess = round(float(total_contrib) - float(total_bench_return), 6)

        result[sleeve] = {
            "observation_days": len(contribs),
            "total_contribution": round(total_contrib, 6) if total_contrib is not None else None,
            "avg_daily_contribution": round(avg_contrib, 6) if avg_contrib is not None else None,
            "avg_weight": round(avg_weight, 4) if avg_weight is not None else None,
            "positive_days": positive_days,
            "benchmark_excess": benchmark_excess,
            "vix_regimes_seen": vix_seen,
        }

    return result


# ---------------------------------------------------------------------------
# Promotion gate evaluation
# ---------------------------------------------------------------------------

def _positive_weeks(sleeve_attr: dict[str, Any], snapshots: list[dict[str, Any]]) -> tuple[int, str]:
    """Estimate number of weeks with positive contribution.

    Uses observation_days to infer weeks (5 trading days/week).
    Returns (count, gate_status).
    """
    obs_days = sleeve_attr.get("observation_days", 0)
    if obs_days == 0:
        return 0, GATE_INSUFFICIENT
    # Estimate weeks at regime weight using positive_days / ~5 days/week
    positive_days = sleeve_attr.get("positive_days", 0)
    # Need at least MIN_POSITIVE_WEEKS * 5 observation days to evaluate
    required_obs = MIN_POSITIVE_WEEKS * 5
    if obs_days < required_obs:
        return int(positive_days / 5), GATE_INSUFFICIENT
    positive_weeks_est = int(positive_days / 5)
    status = GATE_PASS if positive_weeks_est >= MIN_POSITIVE_WEEKS else GATE_FAIL
    return positive_weeks_est, status


def _affordability_gate(sleeve_attr: dict[str, Any], *, cap: float = LIVE_AFFORDABILITY_CAP) -> tuple[str, str]:
    """Check if average weight * cap is a plausible execution notional.

    Live affordability: average allocated weight * capital_cap should be
    > $0 (non-trivial position) and the resulting notional must be executable
    on the approved live-pilot scale.
    """
    avg_weight = sleeve_attr.get("avg_weight")
    if avg_weight is None:
        return "n/a", GATE_INSUFFICIENT
    notional = round(avg_weight * cap, 2)
    # A position < $10 is below the min_trade floor; > cap is a configuration error
    if avg_weight <= 0.0:
        return f"${notional:.2f}", GATE_FAIL
    if notional < 10.0:
        return f"${notional:.2f}", GATE_FAIL
    return f"${notional:.2f}", GATE_PASS


def _regime_rotation_gate(sleeve_attr: dict[str, Any]) -> tuple[str, str]:
    """Check sleeve attribution across >= MIN_REGIME_LABELS distinct VIX regimes."""
    vix_seen = sleeve_attr.get("vix_regimes_seen") or []
    n = len(vix_seen)
    if n == 0:
        return "0 regimes seen", GATE_INSUFFICIENT
    if n < MIN_REGIME_LABELS:
        return f"{n} regime(s) seen ({', '.join(vix_seen)})", GATE_INSUFFICIENT
    return f"{n} regimes ({', '.join(vix_seen)})", GATE_PASS


def _evaluate_gates(sleeve: str, sleeve_attr: dict[str, Any], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate all three promotion gates for a sleeve."""
    pos_weeks, gate1_status = _positive_weeks(sleeve_attr, snapshots)
    notional_str, gate2_status = _affordability_gate(sleeve_attr)
    regime_str, gate3_status = _regime_rotation_gate(sleeve_attr)

    overall = GATE_PASS
    for g in (gate1_status, gate2_status, gate3_status):
        if g == GATE_INSUFFICIENT:
            overall = GATE_INSUFFICIENT
            break
        if g == GATE_FAIL:
            overall = GATE_FAIL

    return {
        "sleeve": sleeve,
        "gate_overall": overall,
        "gate_1_positive_attribution_weeks": {
            "criteria": f">= {MIN_POSITIVE_WEEKS} weeks positive attribution at regime weight",
            "value": pos_weeks,
            "status": gate1_status,
        },
        "gate_2_live_affordability": {
            "criteria": f"avg weight * ${LIVE_AFFORDABILITY_CAP:.0f} cap >= $10 and <= cap",
            "value": notional_str,
            "status": gate2_status,
        },
        "gate_3_regime_rotation": {
            "criteria": f">= {MIN_REGIME_LABELS} distinct VIX regimes in observation window",
            "value": regime_str,
            "status": gate3_status,
        },
    }


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------

def _render_markdown(
    as_of: str,
    attribution: dict[str, dict[str, Any]],
    gates: list[dict[str, Any]],
    data_status: str,
    warnings: list[str],
) -> str:
    lines = [
        "# Weekly Sleeve Attribution Report",
        "",
        f"As of: `{as_of}`",
        f"Data status: `{data_status}`",
        "",
    ]

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines += [
        "## Per-Sleeve Attribution",
        "",
        "| Sleeve | Obs Days | Total Contribution | Avg Daily | Avg Weight | Positive Days | Benchmark Excess |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    if attribution:
        for sleeve, a in sorted(attribution.items()):
            obs = a.get("observation_days", 0)
            total = f"{a.get('total_contribution', 0.0):.4f}" if a.get("total_contribution") is not None else "N/A"
            avg = f"{a.get('avg_daily_contribution', 0.0):.5f}" if a.get("avg_daily_contribution") is not None else "N/A"
            wt = f"{a.get('avg_weight', 0.0):.3f}" if a.get("avg_weight") is not None else "N/A"
            pos = a.get("positive_days", 0)
            excess = f"{a.get('benchmark_excess', 0.0):.4f}" if a.get("benchmark_excess") is not None else "N/A"
            lines.append(f"| `{sleeve}` | {obs} | {total} | {avg} | {wt} | {pos} | {excess} |")
    else:
        lines.append("| — | — | — | — | — | — | — |")
    lines.append("")

    lines += [
        "## PROMOTION GATE",
        "",
        "> Criteria: N weeks positive attribution at regime weight | executable at live affordability | survives regime rotation",
        "",
    ]
    if gates:
        for g in sorted(gates, key=lambda x: x["sleeve"]):
            sleeve = g["sleeve"]
            overall = g["gate_overall"]
            lines.append(f"### `{sleeve}` — {overall}")
            lines.append("")
            for gkey, gdata in [
                ("gate_1_positive_attribution_weeks", "Gate 1: Positive Attribution Weeks"),
                ("gate_2_live_affordability", "Gate 2: Live Affordability"),
                ("gate_3_regime_rotation", "Gate 3: Regime Rotation"),
            ]:
                gd = g.get(gkey, {})
                status = gd.get("status", GATE_INSUFFICIENT)
                value = gd.get("value", "n/a")
                criteria = gd.get("criteria", "")
                lines.append(f"- **{gdata}**: {status} | value={value} | criteria: {criteria}")
            lines.append("")
    else:
        lines.append("No sleeves with sufficient data for gate evaluation.")
        lines.append("")

    if data_status == STATUS_INSUFFICIENT:
        lines += [
            "---",
            "",
            "> **INSUFFICIENT_DATA**: Attribution requires signals/ snapshots and outputs/perf/ contribution",
            "> artifacts. One or more data sources were absent or below the minimum lookback window.",
            "> No numbers have been fabricated — all missing values are reported as INSUFFICIENT_DATA.",
            "",
        ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_sleeve_attribution(
    *,
    as_of: dt.date | None = None,
    signals_dir: Path = DEFAULT_SIGNALS_DIR,
    perf_dir: Path = DEFAULT_PERF_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    lookback_weeks: int = LOOKBACK_WEEKS,
) -> dict[str, Any]:
    """Build weekly per-sleeve attribution and promotion gate report.

    Returns the attribution artifact dict. Degrades gracefully to
    INSUFFICIENT_DATA when artifacts are missing — never fabricates numbers.
    """
    if as_of is None:
        as_of = dt.date.today()

    as_of_str = as_of.isoformat()
    warnings: list[str] = []
    data_status = STATUS_OK

    logger.info("[SLEEVE_ATTR] Building sleeve attribution as_of=%s", as_of_str)

    # --- Load signals snapshots ---
    snapshots: list[dict[str, Any]] = []
    if signals_dir.exists():
        snapshots = _load_signals_snapshots(signals_dir, as_of=as_of, lookback_weeks=lookback_weeks)
        logger.info("[SLEEVE_ATTR] Loaded %d signals snapshots", len(snapshots))
    else:
        warnings.append(f"signals_dir not found: {signals_dir}")

    if not snapshots:
        warnings.append("No signals snapshots found in lookback window — attribution will be INSUFFICIENT_DATA")
        data_status = STATUS_INSUFFICIENT

    # --- Load contribution artifacts ---
    contributions: list[dict[str, Any]] = []
    if HAS_PANDAS and perf_dir.exists():
        contributions = _load_contribution_sleeves(perf_dir, as_of=as_of, lookback_weeks=lookback_weeks)
        logger.info("[SLEEVE_ATTR] Loaded %d sleeve-contribution rows", len(contributions))
    elif not HAS_PANDAS:
        warnings.append("pandas not available — contribution artifact loading skipped")
        data_status = STATUS_INSUFFICIENT
    else:
        warnings.append(f"perf_dir not found: {perf_dir}")

    if not contributions:
        warnings.append("No sleeve contribution rows found — attribution will be INSUFFICIENT_DATA")
        data_status = STATUS_INSUFFICIENT

    # --- Load benchmark returns ---
    benchmark_returns: dict[str, float] = {}
    if HAS_PANDAS and perf_dir.exists():
        benchmark_returns = _load_benchmark_returns(perf_dir, as_of=as_of, lookback_weeks=lookback_weeks)
        if not benchmark_returns:
            warnings.append("No benchmark returns loaded — benchmark excess will be N/A")
    else:
        warnings.append("Benchmark returns unavailable")

    # --- Compute per-sleeve attribution ---
    attribution: dict[str, dict[str, Any]] = {}
    if snapshots or contributions:
        attribution = _compute_sleeve_attribution(snapshots, contributions, benchmark_returns)
        logger.info("[SLEEVE_ATTR] Attribution computed for %d sleeves", len(attribution))
    else:
        logger.warning("[SLEEVE_ATTR] No data sources available — all attribution is INSUFFICIENT_DATA")
        data_status = STATUS_INSUFFICIENT

    # --- Evaluate promotion gates ---
    gates: list[dict[str, Any]] = []
    for sleeve, sleeve_attr in sorted(attribution.items()):
        gate = _evaluate_gates(sleeve, sleeve_attr, snapshots)
        gates.append(gate)

    # Warn loudly when data is insufficient for gate evaluation
    if all(g["gate_overall"] == GATE_INSUFFICIENT for g in gates) and gates:
        logger.warning(
            "[SLEEVE_ATTR] INSUFFICIENT_DATA: all %d sleeves lack enough data for promotion gate evaluation. "
            "Requires >= %d weeks of contribution data and >= %d VIX regime labels.",
            len(gates), MIN_POSITIVE_WEEKS, MIN_REGIME_LABELS,
        )
        data_status = STATUS_INSUFFICIENT

    for w in warnings:
        logger.warning("[SLEEVE_ATTR] %s", w)

    # --- Build artifact ---
    artifact: dict[str, Any] = {
        "schema": "caerus.sleeve_attribution.v1",
        "as_of": as_of_str,
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "data_status": data_status,
        "lookback_weeks": lookback_weeks,
        "signals_snapshot_count": len(snapshots),
        "contribution_rows": len(contributions),
        "benchmark_return_days": len(benchmark_returns),
        "sleeve_attribution": attribution,
        "promotion_gates": gates,
        "warnings": warnings,
        "parameters": {
            "min_positive_weeks": MIN_POSITIVE_WEEKS,
            "live_affordability_cap_usd": LIVE_AFFORDABILITY_CAP,
            "min_regime_labels": MIN_REGIME_LABELS,
        },
    }

    # --- Write outputs ---
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"sleeve_attribution_{as_of_str}.json"
    md_path = output_dir / f"sleeve_attribution_{as_of_str}.md"

    json_path.write_text(json.dumps(artifact, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    md_content = _render_markdown(as_of_str, attribution, gates, data_status, warnings)
    md_path.write_text(md_content, encoding="utf-8")

    logger.info("[SLEEVE_ATTR] Wrote %s", json_path)
    logger.info("[SLEEVE_ATTR] Wrote %s", md_path)

    artifact["json_path"] = str(json_path)
    artifact["markdown_path"] = str(md_path)
    return artifact


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build weekly per-sleeve attribution + promotion gate report")
    parser.add_argument("--date", default=None, help="As-of date (YYYY-MM-DD); default today")
    parser.add_argument("--signals-dir", default=str(DEFAULT_SIGNALS_DIR))
    parser.add_argument("--perf-dir", default=str(DEFAULT_PERF_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--lookback-weeks", type=int, default=LOOKBACK_WEEKS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    as_of = dt.date.fromisoformat(args.date) if args.date else None
    result = build_sleeve_attribution(
        as_of=as_of,
        signals_dir=Path(args.signals_dir),
        perf_dir=Path(args.perf_dir),
        output_dir=Path(args.output_dir),
        lookback_weeks=args.lookback_weeks,
    )
    print(json.dumps({
        "data_status": result.get("data_status"),
        "sleeve_attribution_keys": sorted(result.get("sleeve_attribution", {}).keys()),
        "promotion_gates": [
            {"sleeve": g["sleeve"], "gate_overall": g["gate_overall"]}
            for g in result.get("promotion_gates", [])
        ],
        "json_path": result.get("json_path"),
        "markdown_path": result.get("markdown_path"),
    }, indent=2))
    # Always exit 0: insufficient data is WARN, not fatal (cron should not alert on cold-start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
