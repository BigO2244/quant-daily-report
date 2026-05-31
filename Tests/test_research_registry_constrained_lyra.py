"""Targeted coverage for the constrained_lyra research framework.

Pins:
- VariantSpec inventory + structure
- constraint application (position cap, sector cap, β cap)
- per-name β calibration so weighted sum matches regression β
- synthetic NAV computation (model-based; not a backtest)
- metrics (CAGR, Sharpe, Sortino, Max DD)
- cross-variant correlation
- artifact writers produce all three files
- fail-closed when artifacts are missing
- CLI entry point smoke
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from research_registry.research import constrained_lyra as cl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _baseline_fixture(tmp_path: Path) -> Path:
    """Write a minimal Lyra fixture under tmp_path/outputs/.

    Returns ``outputs_root`` (tmp_path / "outputs").
    """
    outputs = tmp_path / "outputs"
    shadow_root = outputs / "shadow_candidates"
    attribution_root = outputs / "attribution"

    # comparison.json with caerus_lyra holdings (5 names × 20%).
    date_dir = shadow_root / "2026-04-30"
    date_dir.mkdir(parents=True, exist_ok=True)
    holdings = [
        {"ticker": "WDC", "target_weight": 0.20, "momentum_score": 3.21},
        {"ticker": "MU", "target_weight": 0.20, "momentum_score": 1.86},
        {"ticker": "STX", "target_weight": 0.20, "momentum_score": 2.19},
        {"ticker": "GLW", "target_weight": 0.20, "momentum_score": 1.22},
        {"ticker": "WBD", "target_weight": 0.20, "momentum_score": 1.16},
    ]
    (date_dir / "comparison.json").write_text(
        json.dumps({
            "trade_date": "2026-04-30",
            "strategies": {
                "caerus_lyra": {
                    "strategy_name": "Caerus Lyra",
                    "weight_concentration": {
                        "holdings_count": 5, "max_weight": 0.20, "top3_concentration": 0.60,
                    },
                    "holdings": holdings,
                },
            },
        }, sort_keys=True),
        encoding="utf-8",
    )

    # factor_exposure.json with β + sector + per-ticker vol.
    fact_dir = attribution_root / "2026-04-30"
    fact_dir.mkdir(parents=True, exist_ok=True)
    (fact_dir / "factor_exposure.json").write_text(
        json.dumps({
            "trade_date": "2026-04-30",
            "strategies": {
                "caerus_lyra": {
                    "market_beta": 1.91,
                    "sector_exposure": {
                        "weights": {
                            "Information Technology": 0.80,
                            "Communication Services": 0.20,
                        },
                        "max_sector_weight": 0.80,
                    },
                    "volatility_exposure": {
                        "by_ticker": {
                            "WDC": 0.43, "MU": 0.58, "STX": 0.51,
                            "GLW": 0.67, "WBD": 0.09,
                        },
                    },
                },
            },
        }, sort_keys=True),
        encoding="utf-8",
    )
    return outputs


def _nav_fixture(outputs_root: Path, n_days: int = 400) -> Path:
    """Write a small NAV series so the synthesis runs end-to-end."""
    import random
    random.seed(7)
    perf_dir = outputs_root / "shadow_candidates" / "performance"
    perf_dir.mkdir(parents=True, exist_ok=True)
    path = perf_dir / "shadow_nav_series.csv"
    rows = ["date,caerus_polaris,caerus_orion,caerus_lyra,spy_benchmark"]
    polaris = orion = lyra = spy = 1.0
    start = _dt.date(2020, 1, 1)
    for i in range(n_days):
        date = (start + _dt.timedelta(days=i)).isoformat()
        # Lyra: high beta + idiosyncratic alpha
        common = random.gauss(0.0006, 0.012)
        spy_ret = common
        lyra_ret = 1.91 * common + random.gauss(0.0010, 0.005)
        polaris_ret = 1.83 * common + random.gauss(0.0008, 0.005)
        orion_ret = 2.04 * common + random.gauss(0.0009, 0.005)
        polaris *= (1.0 + polaris_ret)
        orion *= (1.0 + orion_ret)
        lyra *= (1.0 + lyra_ret)
        spy *= (1.0 + spy_ret)
        rows.append(f"{date},{polaris},{orion},{lyra},{spy}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Variant inventory
# ---------------------------------------------------------------------------


def test_variant_inventory_contains_five_named_variants():
    slugs = [spec.slug for spec in cl.VARIANT_SPECS]
    assert slugs == [
        "caerus_lyra",
        "caerus_lyra_broad",
        "caerus_lyra_beta_controlled",
        "caerus_lyra_sector_controlled",
        "caerus_lyra_fully_controlled",
    ]
    # Each variant declares an alpha dampening estimate.
    for spec in cl.VARIANT_SPECS:
        assert 0.0 < spec.alpha_dampening_estimate <= 1.0


def test_variant_constraints_match_spec():
    by_slug = {s.slug: s for s in cl.VARIANT_SPECS}
    # Baseline has no caps.
    bl = by_slug["caerus_lyra"]
    assert bl.max_position_weight is None
    assert bl.beta_cap is None
    # Broad
    br = by_slug["caerus_lyra_broad"]
    assert br.max_position_weight == 0.05
    assert br.target_holdings_min == 20
    assert br.beta_cap is None
    # Beta controlled
    bc = by_slug["caerus_lyra_beta_controlled"]
    assert bc.beta_cap == 1.5
    assert bc.max_sector_weight is None
    # Sector controlled
    sc = by_slug["caerus_lyra_sector_controlled"]
    assert sc.beta_cap == 1.5
    assert sc.max_sector_weight == 0.50
    # Fully controlled
    fc = by_slug["caerus_lyra_fully_controlled"]
    assert fc.beta_cap == 1.3
    assert fc.max_sector_weight == 0.40


# ---------------------------------------------------------------------------
# Baseline loading + β calibration
# ---------------------------------------------------------------------------


def test_load_lyra_baseline_returns_holdings_and_calibrated_beta(tmp_path):
    outputs_root = _baseline_fixture(tmp_path)
    baseline = cl.load_lyra_baseline(outputs_root=outputs_root)
    assert baseline is not None
    assert baseline.holdings == ("WDC", "MU", "STX", "GLW", "WBD")
    assert baseline.portfolio_beta == pytest.approx(1.91, abs=1e-3)
    # Calibrated per-name β: weighted sum should equal portfolio β.
    weighted = sum(
        baseline.weights[t] * baseline.beta_per_name[t]
        for t in baseline.weights
    )
    assert weighted == pytest.approx(1.91, abs=1e-2)


def test_load_lyra_baseline_returns_none_when_artifacts_absent(tmp_path):
    assert cl.load_lyra_baseline(outputs_root=tmp_path / "nope") is None


# ---------------------------------------------------------------------------
# Constraint application
# ---------------------------------------------------------------------------


def test_position_cap_caps_each_name(tmp_path):
    capped = cl._apply_position_cap({"A": 0.20, "B": 0.20, "C": 0.20}, 0.05)
    assert all(w == 0.05 for w in capped.values())


def test_position_cap_no_op_when_cap_none():
    capped = cl._apply_position_cap({"A": 0.20, "B": 0.20}, None)
    assert capped == {"A": 0.20, "B": 0.20}


def test_sector_cap_scales_over_concentrated_sector():
    weights = {"WDC": 0.20, "MU": 0.20, "STX": 0.20, "GLW": 0.20, "WBD": 0.20}
    sector_map = {
        "WDC": "Information Technology", "MU": "Information Technology",
        "STX": "Information Technology", "GLW": "Information Technology",
        "WBD": "Communication Services",
    }
    # IT total = 0.80 → cap at 0.50 → each IT name scales to 0.125.
    new_weights, _ = cl._apply_sector_cap(weights, sector_map, 0.50)
    it_total = sum(new_weights[t] for t in ["WDC", "MU", "STX", "GLW"])
    assert it_total == pytest.approx(0.50, abs=1e-6)
    # WBD untouched.
    assert new_weights["WBD"] == 0.20


def test_beta_cap_scales_portfolio_to_target():
    weights = {"A": 0.50, "B": 0.50}
    beta_map = {"A": 2.0, "B": 2.0}  # portfolio β = 2.0
    new_weights = cl._apply_beta_cap(weights, beta_map, 1.5)
    new_beta = sum(w * beta_map[t] for t, w in new_weights.items())
    assert new_beta == pytest.approx(1.5, abs=1e-6)
    # The shortfall becomes cash buffer (sum of weights < 1).
    assert sum(new_weights.values()) < 1.0


def test_apply_constraints_baseline_unchanged(tmp_path):
    outputs_root = _baseline_fixture(tmp_path)
    baseline = cl.load_lyra_baseline(outputs_root=outputs_root)
    baseline_spec = next(s for s in cl.VARIANT_SPECS if s.slug == "caerus_lyra")
    cp = cl.apply_constraints(baseline, baseline_spec)
    # Baseline has no caps → weights unchanged.
    assert cp.weights == baseline.weights
    assert cp.cash_buffer_pct == 0.0
    assert cp.constraint_violations == []


def test_apply_constraints_fully_controlled_imposes_caps(tmp_path):
    outputs_root = _baseline_fixture(tmp_path)
    baseline = cl.load_lyra_baseline(outputs_root=outputs_root)
    fc = next(s for s in cl.VARIANT_SPECS if s.slug == "caerus_lyra_fully_controlled")
    cp = cl.apply_constraints(baseline, fc)
    # Position cap is honoured.
    assert cp.max_position_weight <= 0.05 + 1e-6
    # Portfolio β after applying caps is at or below the cap.
    assert cp.portfolio_beta <= fc.beta_cap + 1e-3
    # Cash buffer is large because the current 5-name universe can't fill
    # 20+ holdings at 5%.
    assert cp.cash_buffer_pct > 0.5


# ---------------------------------------------------------------------------
# NAV synthesis (model-based)
# ---------------------------------------------------------------------------


def test_synthesize_variant_nav_baseline_passthrough():
    """Variant with β=baseline and dampening=1.0 should reproduce Lyra."""
    idx = pd.date_range("2020-01-01", periods=100, freq="D")
    lyra = pd.Series([0.001] * 100, index=idx)
    spy = pd.Series([0.0005] * 100, index=idx)
    stream = cl.synthesize_variant_nav(
        lyra_returns=lyra,
        spy_returns=spy,
        baseline_beta=1.91,
        variant_beta=1.91,
        alpha_dampening=1.0,
        variant_slug="caerus_lyra_test",
    )
    # r_variant = β × r_spy + α = 1.91 × r_spy + (r_lyra − 1.91 × r_spy)
    #          = r_lyra  → identity.
    pd.testing.assert_series_equal(
        stream.daily_returns, lyra, check_names=False, check_freq=False
    )


def test_synthesize_variant_nav_beta_reduced_scales_market_component():
    """When β_variant < β_baseline, the variant's return on a pure-market day
    is lower (because alpha = 0 on that day, so r_variant = β_var × r_spy)."""
    idx = pd.date_range("2020-01-01", periods=50, freq="D")
    spy = pd.Series([0.01] * 50, index=idx)
    # Lyra return = 1.91 × spy = 0.0191; alpha = 0
    lyra = pd.Series([0.0191] * 50, index=idx)
    stream = cl.synthesize_variant_nav(
        lyra_returns=lyra, spy_returns=spy,
        baseline_beta=1.91, variant_beta=1.3,
        alpha_dampening=1.0, variant_slug="x",
    )
    # Pure-market day: variant return should equal 1.3 × 0.01 = 0.013
    assert stream.daily_returns.iloc[10] == pytest.approx(0.013, abs=1e-9)


def test_synthesize_alpha_dampening_reduces_residual():
    idx = pd.date_range("2020-01-01", periods=50, freq="D")
    spy = pd.Series([0.0] * 50, index=idx)
    # All return is alpha (SPY flat).
    lyra = pd.Series([0.01] * 50, index=idx)
    full = cl.synthesize_variant_nav(
        lyra_returns=lyra, spy_returns=spy,
        baseline_beta=1.91, variant_beta=1.91,
        alpha_dampening=1.0, variant_slug="full",
    )
    damped = cl.synthesize_variant_nav(
        lyra_returns=lyra, spy_returns=spy,
        baseline_beta=1.91, variant_beta=1.91,
        alpha_dampening=0.5, variant_slug="damped",
    )
    # Damped variant returns half the alpha.
    assert damped.daily_returns.iloc[5] == pytest.approx(0.005, abs=1e-9)
    assert full.daily_returns.iloc[5] == pytest.approx(0.010, abs=1e-9)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_compute_nav_metrics_returns_finite_numbers():
    idx = pd.date_range("2020-01-01", periods=500, freq="D")
    import random
    random.seed(1)
    rets = pd.Series([random.gauss(0.0008, 0.012) for _ in range(500)], index=idx)
    m = cl.compute_nav_metrics(rets)
    assert m.n_observations == 500
    assert m.cagr is not None
    assert m.sharpe is not None
    assert m.sortino is not None
    assert m.max_drawdown is not None
    assert m.annualized_volatility is not None
    assert m.max_drawdown < 0  # there must be SOME drawdown


def test_compute_nav_metrics_handles_tiny_sample():
    rets = pd.Series([0.001, 0.002, -0.001], index=pd.date_range("2020-01-01", periods=3, freq="D"))
    m = cl.compute_nav_metrics(rets)
    assert m.cagr is None  # too few obs


# ---------------------------------------------------------------------------
# Full orchestrator + artifacts
# ---------------------------------------------------------------------------


def test_assess_constrained_lyra_writes_three_artifacts(tmp_path):
    outputs_root = _baseline_fixture(tmp_path)
    nav_path = _nav_fixture(outputs_root)
    assessment = cl.assess_constrained_lyra(
        outputs_root=outputs_root,
        run_date="2026-05-30",
        nav_series_path=nav_path,
    )
    assert assessment is not None
    out_dir = cl.write_artifacts(assessment, output_root=tmp_path / "research")
    assert (out_dir / "variants.json").exists()
    assert (out_dir / "comparison.json").exists()
    assert (out_dir / "summary.md").exists()
    # variants.json structure check.
    variants_payload = json.loads((out_dir / "variants.json").read_text())
    assert set(variants_payload["variants"].keys()) == {s.slug for s in cl.VARIANT_SPECS}
    # comparison.json has 5 rows.
    cmp_payload = json.loads((out_dir / "comparison.json").read_text())
    assert len(cmp_payload["comparison"]["rows"]) == 5
    # Methodology note is present and honest.
    assert "MODEL-BASED" in cmp_payload["methodology_note"]
    assert "not backtests" in cmp_payload["methodology_note"].lower()


def test_assess_baseline_metrics_are_realistic(tmp_path):
    outputs_root = _baseline_fixture(tmp_path)
    nav_path = _nav_fixture(outputs_root, n_days=600)
    assessment = cl.assess_constrained_lyra(
        outputs_root=outputs_root,
        run_date="2026-05-30",
        nav_series_path=nav_path,
    )
    rows = {r["slug"]: r for r in assessment.comparison["rows"]}
    # Baseline produces non-null metrics.
    base = rows["caerus_lyra"]
    assert base["cagr"] is not None
    assert base["sharpe"] is not None
    # Correlation to itself is 1.
    assert base["correlation_to_baseline_lyra"] == 1.0


def test_assess_lower_beta_variants_have_lower_cagr_and_smaller_drawdown(tmp_path):
    """The synth model should produce monotonic CAGR and Max DD as β drops."""
    outputs_root = _baseline_fixture(tmp_path)
    nav_path = _nav_fixture(outputs_root, n_days=800)
    assessment = cl.assess_constrained_lyra(
        outputs_root=outputs_root,
        run_date="2026-05-30",
        nav_series_path=nav_path,
    )
    rows = {r["slug"]: r for r in assessment.comparison["rows"]}
    base = rows["caerus_lyra"]
    beta_ctrl = rows["caerus_lyra_beta_controlled"]
    fully_ctrl = rows["caerus_lyra_fully_controlled"]
    # CAGR strictly decreases as β cap tightens.
    assert beta_ctrl["cagr"] < base["cagr"]
    assert fully_ctrl["cagr"] < beta_ctrl["cagr"]
    # Max drawdown strictly less negative as β cap tightens.
    assert beta_ctrl["max_drawdown"] > base["max_drawdown"]
    assert fully_ctrl["max_drawdown"] > beta_ctrl["max_drawdown"]
    # Effective β matches the spec.
    assert beta_ctrl["effective_beta"] == pytest.approx(1.50, abs=1e-3)
    assert fully_ctrl["effective_beta"] == pytest.approx(1.30, abs=1e-3)


def test_assess_returns_none_when_nothing_on_disk(tmp_path):
    assessment = cl.assess_constrained_lyra(
        outputs_root=tmp_path / "nope",
        run_date="2026-05-30",
        nav_series_path=tmp_path / "nope.csv",
    )
    assert assessment is None


def test_assess_handles_missing_nav_with_baseline_only(tmp_path):
    outputs_root = _baseline_fixture(tmp_path)
    # NAV missing — should still produce variant portfolios but no synth metrics.
    assessment = cl.assess_constrained_lyra(
        outputs_root=outputs_root,
        run_date="2026-05-30",
        nav_series_path=tmp_path / "missing.csv",
    )
    assert assessment is not None
    assert any("no_nav_series" in w for w in assessment.warnings)
    # Variant portfolios still computed.
    assert len(assessment.variants) == 5


def test_assess_correlation_to_baseline_is_high_under_model(tmp_path):
    """The synth model preserves Lyra's alpha residual; correlations to
    baseline should be very high. This is a known modelling limitation
    and is documented in the methodology note."""
    outputs_root = _baseline_fixture(tmp_path)
    nav_path = _nav_fixture(outputs_root, n_days=600)
    assessment = cl.assess_constrained_lyra(
        outputs_root=outputs_root,
        run_date="2026-05-30",
        nav_series_path=nav_path,
    )
    rows = {r["slug"]: r for r in assessment.comparison["rows"]}
    for slug in ("caerus_lyra_broad", "caerus_lyra_beta_controlled",
                 "caerus_lyra_fully_controlled"):
        assert rows[slug]["correlation_to_baseline_lyra"] > 0.85


# ---------------------------------------------------------------------------
# Methodology note ALWAYS surfaces the modelling caveat.
# ---------------------------------------------------------------------------


def test_methodology_note_warns_model_based(tmp_path):
    outputs_root = _baseline_fixture(tmp_path)
    nav_path = _nav_fixture(outputs_root)
    assessment = cl.assess_constrained_lyra(
        outputs_root=outputs_root,
        run_date="2026-05-30",
        nav_series_path=nav_path,
    )
    note = assessment.methodology_note
    assert "MODEL-BASED" in note
    assert "alpha_dampening" in note
    assert "not a substitute" in note.lower()


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_entry_point_writes_artifacts(tmp_path):
    outputs_root = _baseline_fixture(tmp_path)
    _nav_fixture(outputs_root)
    output_root = tmp_path / "research"
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable, "-m", "research_registry.research.constrained_lyra",
            "--outputs-root", str(outputs_root),
            "--output-root", str(output_root),
            "--nav-series-path", str(outputs_root / "shadow_candidates" / "performance" / "shadow_nav_series.csv"),
            "--run-date", "2026-05-30",
        ],
        cwd=str(repo_root),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    out_dir = output_root / "2026-05-30"
    assert (out_dir / "variants.json").exists()
    assert (out_dir / "comparison.json").exists()
    assert (out_dir / "summary.md").exists()


def test_cli_exits_nonzero_when_nothing_on_disk(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable, "-m", "research_registry.research.constrained_lyra",
            "--outputs-root", str(tmp_path / "empty"),
            "--output-root", str(tmp_path / "research"),
            "--nav-series-path", str(tmp_path / "missing.csv"),
            "--run-date", "2026-05-30",
        ],
        cwd=str(repo_root),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
