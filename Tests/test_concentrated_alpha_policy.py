"""Phase 2 refactor: CONCENTRATION IS THE MODEL.

Covers:
- Regime-adaptive top-N: N = clamp(int(vix_regime.max_positions), 3, 7),
  fallback 5 when regime is unavailable; explicitly-set CAERUS_CONCENTRATED_TOP_N
  env remains an emergency override (wins ONLY when set).
- Hardwired concentration: no enable flag; concentration always applies.
- FAIL LOUD: a concentration failure raises out of build_daily_snapshot so the
  precompute exits nonzero, no bundle is written, and both execution lanes fail
  closed (HOLD day) instead of silently trading the broad book.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import daily_quant_report as dqr


# ---------------------------------------------------------------------------
# Regime state -> expected N
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("regime", "max_positions", "expected_n"),
    [
        ("LOW", 10, 7),       # clamped down to 7
        ("ELEVATED", 7, 7),
        ("HIGH", 4, 4),
        ("CRISIS", 2, 3),     # clamped up to 3
    ],
)
def test_top_n_derives_from_vix_regime(monkeypatch, regime, max_positions, expected_n):
    monkeypatch.delenv("CAERUS_CONCENTRATED_TOP_N", raising=False)
    top_n, source = dqr._concentrated_top_n(
        {"regime": regime, "max_positions": max_positions}
    )
    assert top_n == expected_n
    assert source == f"vix_regime:{regime}"


def test_top_n_from_real_regime_classifier(monkeypatch):
    """End-to-end through the actual VIX classifier the pipeline uses."""
    from research.vix_regime import classify_vix_regime

    monkeypatch.delenv("CAERUS_CONCENTRATED_TOP_N", raising=False)
    expected = {15.0: 7, 25.0: 7, 35.0: 4, 55.0: 3}  # LOW/ELEVATED/HIGH/CRISIS
    for vix, expected_n in expected.items():
        top_n, source = dqr._concentrated_top_n(classify_vix_regime(vix))
        assert top_n == expected_n, f"vix={vix}"
        assert source.startswith("vix_regime:")


@pytest.mark.parametrize("bad_regime", [None, {}, {"regime": "LOW"}, {"max_positions": "x"}])
def test_top_n_falls_back_to_five_when_regime_unavailable(monkeypatch, bad_regime):
    monkeypatch.delenv("CAERUS_CONCENTRATED_TOP_N", raising=False)
    top_n, source = dqr._concentrated_top_n(bad_regime)
    assert top_n == 5
    assert source == "fallback_default"


def test_top_n_env_override_wins_only_when_set(monkeypatch):
    regime = {"regime": "HIGH", "max_positions": 4}
    monkeypatch.setenv("CAERUS_CONCENTRATED_TOP_N", "6")
    assert dqr._concentrated_top_n(regime) == (6, "env_override")
    # Unset -> regime wins again.
    monkeypatch.delenv("CAERUS_CONCENTRATED_TOP_N", raising=False)
    assert dqr._concentrated_top_n(regime) == (4, "vix_regime:HIGH")
    # Empty string is NOT an explicit override.
    monkeypatch.setenv("CAERUS_CONCENTRATED_TOP_N", "")
    assert dqr._concentrated_top_n(regime) == (4, "vix_regime:HIGH")
    # Invalid value is ignored (regime-adaptive N with a warning).
    monkeypatch.setenv("CAERUS_CONCENTRATED_TOP_N", "banana")
    assert dqr._concentrated_top_n(regime) == (4, "vix_regime:HIGH")


# ---------------------------------------------------------------------------
# Hardwired concentration + fail-loud HOLD-day behavior in build_daily_snapshot
# ---------------------------------------------------------------------------


def _alloc_result(n_names: int = 8):
    rows = [
        {
            "ticker": f"TK{i:02d}",
            "target_weight": round(0.10 - i * 0.005, 4),
            "sleeve_name": "sleeve_2",
        }
        for i in range(n_names)
    ]
    invested = sum(r["target_weight"] for r in rows)
    return SimpleNamespace(
        combined_weights=pd.DataFrame(rows),
        cash_weight=max(0.0, 1.0 - invested),
        sleeve_allocations={"sleeve_2": invested},
        skipped_trades=[],
    )


def _patch_snapshot_io(monkeypatch, captured: dict):
    def _capture_signals(**kwargs):
        captured["df_targets"] = kwargs.get("df_targets")
        return "signals/2026-07-10.json"

    monkeypatch.setattr(dqr, "write_signals_snapshot", _capture_signals)
    monkeypatch.setattr(dqr, "persist_signal_snapshot", lambda *a, **k: None)

    def fake_prices(tickers, period="6mo", interval="1d"):
        return pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2026-07-10"),
                    "ticker": t,
                    "open": 100.0,
                    "high": 102.0,
                    "low": 99.0,
                    "close": 101.0,
                }
                for t in tickers
            ]
        )

    monkeypatch.setattr(dqr, "download_prices", fake_prices)
    monkeypatch.setattr(
        dqr, "_build_atr_map", lambda prices, asof: {t: 1.0 for t in prices.get("ticker", [])}
    )


def test_concentration_always_applies_without_any_flag(monkeypatch):
    """No CAERUS_CONCENTRATED_ALPHA gate: the emitted book is the concentrated
    top-N for the regime (HIGH -> 4 names), not the broad book."""
    monkeypatch.delenv("CAERUS_CONCENTRATED_ALPHA", raising=False)
    monkeypatch.delenv("CAERUS_CONCENTRATED_TOP_N", raising=False)
    captured: dict = {}
    _patch_snapshot_io(monkeypatch, captured)

    snapshot = dqr.build_daily_snapshot(
        report_date=pd.Timestamp("2026-07-10"),
        alloc_result=_alloc_result(8),
        portfolio_stats={"equity": 10000.0},
        st_equity=pd.DataFrame(),
        s2_equity=pd.DataFrame(),
        st_signals=pd.DataFrame(),
        s2_details={},
        cm_details={},
        vix_regime={"regime": "HIGH", "vix": 35.0, "position_scale": 0.5, "max_positions": 4},
    )

    emitted = captured["df_targets"]
    assert emitted is not None and len(emitted) == 4
    # Top-conviction names survive, ordered by conviction.
    assert list(emitted["ticker"])[:2] == ["TK00", "TK01"]
    assert snapshot["signals_snapshot_path"] == "signals/2026-07-10.json"


def test_concentration_failure_fails_loud_and_closed(monkeypatch):
    """A concentration failure must RAISE (precompute exits nonzero -> no bundle
    -> both lanes HOLD), never silently fall back to the broad book."""
    import core.concentration as concentration_mod

    monkeypatch.delenv("CAERUS_CONCENTRATED_TOP_N", raising=False)
    captured: dict = {}
    _patch_snapshot_io(monkeypatch, captured)

    def _boom(*args, **kwargs):
        raise ValueError("synthetic concentration failure")

    monkeypatch.setattr(concentration_mod, "concentrate_targets", _boom)

    with pytest.raises(RuntimeError, match="failing closed"):
        dqr.build_daily_snapshot(
            report_date=pd.Timestamp("2026-07-10"),
            alloc_result=_alloc_result(8),
            portfolio_stats={"equity": 10000.0},
            st_equity=pd.DataFrame(),
            s2_equity=pd.DataFrame(),
            st_signals=pd.DataFrame(),
            s2_details={},
            cm_details={},
            vix_regime={"regime": "LOW", "vix": 15.0, "position_scale": 1.0, "max_positions": 10},
        )

    # Fail-closed means nothing was emitted for execution.
    assert "df_targets" not in captured


def test_empty_concentrated_book_also_fails_loud(monkeypatch):
    import core.concentration as concentration_mod

    monkeypatch.delenv("CAERUS_CONCENTRATED_TOP_N", raising=False)
    captured: dict = {}
    _patch_snapshot_io(monkeypatch, captured)
    monkeypatch.setattr(
        concentration_mod, "concentrate_targets", lambda *a, **k: pd.DataFrame()
    )

    with pytest.raises(RuntimeError, match="failing closed"):
        dqr.build_daily_snapshot(
            report_date=pd.Timestamp("2026-07-10"),
            alloc_result=_alloc_result(3),
            portfolio_stats={"equity": 10000.0},
            st_equity=pd.DataFrame(),
            s2_equity=pd.DataFrame(),
            st_signals=pd.DataFrame(),
            s2_details={},
            cm_details={},
        )
    assert "df_targets" not in captured


# ---------------------------------------------------------------------------
# Unified position-cap source of truth (core.risk_controls._default_max_position_pct)
# ---------------------------------------------------------------------------


def test_default_max_position_pct_is_concentration_ceiling_no_flag(monkeypatch):
    """No flag check: the cap default is the concentration ceiling even with
    CAERUS_CONCENTRATED_ALPHA unset (concentration is always on)."""
    from core.risk_controls import _default_max_position_pct

    monkeypatch.delenv("MAX_POSITION_PCT", raising=False)
    monkeypatch.delenv("CAERUS_CONCENTRATED_ALPHA", raising=False)
    monkeypatch.delenv("CAERUS_CONCENTRATED_MAX_WEIGHT", raising=False)
    assert _default_max_position_pct() == pytest.approx(0.50)

    monkeypatch.setenv("CAERUS_CONCENTRATED_MAX_WEIGHT", "0.42")
    assert _default_max_position_pct() == pytest.approx(0.42)

    # Explicit MAX_POSITION_PCT always wins.
    monkeypatch.setenv("MAX_POSITION_PCT", "0.15")
    assert _default_max_position_pct() == pytest.approx(0.15)

    # Garbage ceiling falls back to 0.50.
    monkeypatch.delenv("MAX_POSITION_PCT", raising=False)
    monkeypatch.setenv("CAERUS_CONCENTRATED_MAX_WEIGHT", "banana")
    assert _default_max_position_pct() == pytest.approx(0.50)


def test_live_construction_policy_uses_shared_cap_default(monkeypatch):
    """_resolve_live_construction_policy must derive its max_position_weight
    default from core.risk_controls._default_max_position_pct (no more
    hardcoded 0.10/0.20 dual-cap confusion)."""
    monkeypatch.delenv("MAX_POSITION_PCT", raising=False)
    monkeypatch.setenv("CAERUS_CONCENTRATED_MAX_WEIGHT", "0.42")
    # Neutralize the env/config override layer so the DEFAULT is observable.
    monkeypatch.setattr(dqr, "_snapshot_risk_value", lambda name, default: default)

    for equity in (10000.0, 250000.0):  # small and large account paths
        policy = dqr._resolve_live_construction_policy(equity)
        assert policy["max_position_weight"] == pytest.approx(0.42), equity

    # Explicit MAX_POSITION_PCT env wins through the shared function too.
    monkeypatch.setenv("MAX_POSITION_PCT", "0.15")
    policy = dqr._resolve_live_construction_policy(10000.0)
    assert policy["max_position_weight"] == pytest.approx(0.15)
