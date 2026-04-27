from __future__ import annotations

import json

import pandas as pd

from research.vix_regime import classify_vix_regime, get_current_regime


def test_classify_vix_regime_thresholds():
    assert classify_vix_regime(19.99) == {
        "regime": "LOW",
        "position_scale": 1.0,
        "max_positions": 10,
    }
    assert classify_vix_regime(20.0) == {
        "regime": "ELEVATED",
        "position_scale": 0.75,
        "max_positions": 7,
    }
    assert classify_vix_regime(30.0) == {
        "regime": "HIGH",
        "position_scale": 0.5,
        "max_positions": 4,
    }
    assert classify_vix_regime(40.0) == {
        "regime": "CRISIS",
        "position_scale": 0.25,
        "max_positions": 2,
    }


def test_get_current_regime_persists_override_payload(tmp_path):
    out_dir = tmp_path / "outputs" / "vix_regime"

    payload = get_current_regime(
        vix_override=18.125,
        as_of_date="2026-04-24",
        output_dir=out_dir,
    )

    assert payload["date"] == "2026-04-24"
    assert payload["vix"] == 18.125
    assert payload["regime"] == "LOW"
    assert payload["position_scale"] == 1.0
    assert payload["max_positions"] == 10
    assert payload["source"] == "override"
    assert payload["fallback_used"] is False

    current = json.loads((out_dir / "regime_current.json").read_text())
    history = pd.read_csv(out_dir / "regime_history.csv")

    assert current == payload
    assert len(history) == 1
    assert history.iloc[0]["date"] == "2026-04-24"
    assert float(history.iloc[0]["vix"]) == 18.125
    assert history.iloc[0]["regime"] == "LOW"


def test_get_current_regime_uses_fallback_when_fetch_fails(tmp_path):
    out_dir = tmp_path / "outputs" / "vix_regime"

    payload = get_current_regime(
        as_of_date="2026-04-24",
        output_dir=out_dir,
        fetch_vix_fn=lambda: (_ for _ in ()).throw(RuntimeError("network down")),
    )

    assert payload["date"] == "2026-04-24"
    assert payload["vix"] == 25.0
    assert payload["regime"] == "ELEVATED"
    assert payload["position_scale"] == 0.75
    assert payload["max_positions"] == 7
    assert payload["source"] == "fallback"
    assert payload["fallback_used"] is True


def test_get_current_regime_appends_to_legacy_as_of_history(tmp_path):
    out_dir = tmp_path / "outputs" / "vix_regime"
    out_dir.mkdir(parents=True)
    (out_dir / "regime_history.csv").write_text(
        "as_of,regime,vix,position_scale,max_positions\n"
        "2026-03-31,HIGH,30.61,0.5,4\n"
    )

    payload = get_current_regime(
        vix_override=18.0,
        as_of_date="2026-04-24",
        output_dir=out_dir,
    )

    history = pd.read_csv(out_dir / "regime_history.csv")
    assert list(history.columns) == [
        "date",
        "vix",
        "regime",
        "position_scale",
        "max_positions",
        "source",
        "fallback_used",
    ]
    assert history.iloc[-1]["date"] == "2026-04-24"
    assert float(history.iloc[-1]["vix"]) == payload["vix"]
