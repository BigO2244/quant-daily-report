from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts import hydrate_price_cache_only as script
from scripts import refresh_shadow_scorecard_artifacts as refresh_script


def _write_universe(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ticker\nAAA\nBBB\n", encoding="utf-8")


def _write_panel(path: Path, *, end_date: str = "2026-05-04") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "date": end_date,
                "ticker": ticker,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1000,
            }
            for ticker in ("AAA", "BBB", "SPY")
        ]
    ).to_parquet(path, index=False)


def test_dry_run_does_not_call_hydration_or_write_status(tmp_path: Path, monkeypatch, capsys) -> None:
    universe = tmp_path / "universe.csv"
    cache = tmp_path / "price_panel.parquet"
    status_dir = tmp_path / "status"
    _write_universe(universe)

    called = {"ensure": False}

    def fake_ensure_price_panel(**_kwargs):
        called["ensure"] = True
        raise AssertionError("dry run should not hydrate")

    monkeypatch.setattr(script, "ensure_price_panel", fake_ensure_price_panel)
    monkeypatch.setattr(script, "resolve_completed_trading_day", lambda explicit_trade_date=None: explicit_trade_date or "2026-05-04")

    rc = script.main(
        [
            "--trade-date",
            "2026-05-04",
            "--universe-path",
            str(universe),
            "--cache-path",
            str(cache),
            "--status-dir",
            str(status_dir),
            "--dry-run",
        ]
    )

    assert rc == 0
    assert called["ensure"] is False
    assert not (status_dir / "2026-05-04" / "status.json").exists()
    assert "artifact_only" in capsys.readouterr().out


def test_cache_only_hydrator_writes_status_with_source(tmp_path: Path, monkeypatch) -> None:
    universe = tmp_path / "universe.csv"
    cache = tmp_path / "price_panel.parquet"
    status_dir = tmp_path / "status"
    _write_universe(universe)
    _write_panel(cache, end_date="2026-05-01")

    def fake_ensure_price_panel(**kwargs):
        _write_panel(Path(kwargs["cache_path"]), end_date="2026-05-04")
        return pd.DataFrame(), {
            "download_performed": True,
            "download_failed_symbols": [],
            "symbols_requested": len(kwargs["symbols"]),
        }

    monkeypatch.setattr(script, "ensure_price_panel", fake_ensure_price_panel)
    monkeypatch.setattr(script, "resolve_completed_trading_day", lambda explicit_trade_date=None: explicit_trade_date or "2026-05-04")

    rc = script.main(
        [
            "--trade-date",
            "2026-05-04",
            "--universe-path",
            str(universe),
            "--cache-path",
            str(cache),
            "--status-dir",
            str(status_dir),
            "--hydration-source",
            "mac_studio_fallback",
        ]
    )

    payload = json.loads((status_dir / "2026-05-04" / "status.json").read_text())
    assert rc == 0
    assert payload["status"] == "OK"
    assert payload["max_cache_date"] == "2026-05-04"
    assert payload["hydration_source"] == "mac_studio_fallback"
    assert payload["cache_only"] is True
    assert payload["canonical_cache_path"] == str(cache)
    assert payload["before_max_cache_date"] == "2026-05-01"


def test_refresh_shadow_artifacts_runs_after_verified_cache_and_publishes_latest(tmp_path: Path, monkeypatch) -> None:
    universe = tmp_path / "universe.csv"
    cache = tmp_path / "price_panel.parquet"
    status_dir = tmp_path / "status"
    shadow_dir = tmp_path / "shadow"
    _write_universe(universe)
    _write_panel(cache, end_date="2026-05-01")

    def fake_ensure_price_panel(**kwargs):
        _write_panel(Path(kwargs["cache_path"]), end_date="2026-05-04")
        return pd.DataFrame(), {"download_performed": True}

    def fake_shadow_refresh_main(argv):
        assert "--trade-date" in argv
        assert "2026-05-04" in argv
        dated = shadow_dir / "2026-05-04"
        dated.mkdir(parents=True, exist_ok=True)
        for name in ("comparison.md", "comparison.json", "delta.json", "shadow_evaluation.json"):
            (dated / name).write_text(name, encoding="utf-8")
        return 0

    monkeypatch.setattr(script, "ensure_price_panel", fake_ensure_price_panel)
    monkeypatch.setattr(script.refresh_shadow_scorecard_artifacts, "main", fake_shadow_refresh_main)
    monkeypatch.setattr(script, "resolve_completed_trading_day", lambda explicit_trade_date=None: explicit_trade_date or "2026-05-04")

    rc = script.main(
        [
            "--trade-date",
            "2026-05-04",
            "--universe-path",
            str(universe),
            "--cache-path",
            str(cache),
            "--status-dir",
            str(status_dir),
            "--shadow-output-dir",
            str(shadow_dir),
            "--refresh-shadow-artifacts",
        ]
    )

    payload = json.loads((status_dir / "2026-05-04" / "status.json").read_text())
    assert rc == 0
    assert payload["status"] == "OK"
    assert payload["shadow_refresh"]["status"] == "OK"


def test_cache_only_hydrator_strict_fails_when_cache_not_covered(tmp_path: Path, monkeypatch) -> None:
    universe = tmp_path / "universe.csv"
    cache = tmp_path / "price_panel.parquet"
    status_dir = tmp_path / "status"
    _write_universe(universe)
    _write_panel(cache, end_date="2026-05-01")

    def fake_ensure_price_panel(**_kwargs):
        return pd.DataFrame(), {"download_performed": True}

    monkeypatch.setattr(script, "ensure_price_panel", fake_ensure_price_panel)
    monkeypatch.setattr(script, "resolve_completed_trading_day", lambda explicit_trade_date=None: explicit_trade_date or "2026-05-04")

    rc = script.main(
        [
            "--trade-date",
            "2026-05-04",
            "--universe-path",
            str(universe),
            "--cache-path",
            str(cache),
            "--status-dir",
            str(status_dir),
            "--strict",
        ]
    )

    payload = json.loads((status_dir / "2026-05-04" / "status.json").read_text())
    assert rc == 1
    assert payload["status"] == "PARTIAL"
    assert payload["max_cache_date"] == "2026-05-01"


def test_cache_only_script_does_not_import_execution_modules() -> None:
    script_text = Path(script.__file__).read_text(encoding="utf-8")

    forbidden = (
        "run_precomputed_alpaca_execution",
        "execute_options_overlay",
        "submit_market_order",
        "submit_option_market_order",
        "brokers.alpaca_broker",
    )
    assert not any(token in script_text for token in forbidden)


def _shadow_panel() -> pd.DataFrame:
    dates = pd.date_range("2022-01-03", periods=340, freq="B")
    rows = []
    slopes = {
        "AAA": 0.0026,
        "BBB": 0.0017,
        "CCC": 0.0012,
        "DDD": 0.0007,
        "EEE": 0.0002,
        "FFF": -0.0002,
        "SPY": 0.0011,
    }
    for ticker, slope in slopes.items():
        price = 100.0
        for idx, value in enumerate(dates):
            price *= 1.0 + slope
            rows.append(
                {
                    "date": value,
                    "ticker": ticker,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 1_000_000 + 10_000 * (idx % 5),
                    "sector": "Tech",
                }
            )
    return pd.DataFrame(rows)


def test_shadow_scorecard_refresh_regenerates_feedback_before_latest_publish(tmp_path: Path, monkeypatch) -> None:
    panel = _shadow_panel()
    output_dir = tmp_path / "shadow"
    cache_path = tmp_path / "price_panel.parquet"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(cache_path, index=False)

    monkeypatch.setattr(refresh_script, "load_universe", lambda _path: ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"])

    def fake_ensure_price_panel(**_kwargs):
        return panel, {"download_performed": False}

    monkeypatch.setattr(refresh_script, "ensure_price_panel", fake_ensure_price_panel)

    assert refresh_script.main(
        [
            "--trade-date",
            "2023-03-30",
            "--start-date",
            "2022-01-03",
            "--output-dir",
            str(output_dir),
            "--price-cache-path",
            str(cache_path),
        ]
    ) == 1

    stale_feedback = output_dir / "2023-03-31" / "feedback_loop_summary.json"
    stale_feedback.parent.mkdir(parents=True, exist_ok=True)
    stale_feedback.write_text(
        json.dumps(
            {
                "trade_date": "2023-03-31",
                "status": "NO_DATA",
                "strategies": {
                    "polaris": {
                        "learning_readiness": "LOW",
                        "primary_learning_gap": "stale placeholder",
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    assert refresh_script.main(
        [
            "--trade-date",
            "2023-03-31",
            "--start-date",
            "2022-01-03",
            "--output-dir",
            str(output_dir),
            "--price-cache-path",
            str(cache_path),
        ]
    ) == 0

    feedback = json.loads(stale_feedback.read_text(encoding="utf-8"))
    comparison = json.loads((output_dir / "2023-03-31" / "comparison.json").read_text(encoding="utf-8"))
    latest_feedback = output_dir / "latest" / "feedback_loop_summary.json"
    rolling_index = output_dir / "performance" / "feedback_loop_rolling_index.csv"

    assert set(comparison["strategies"]) == {"caerus_polaris", "caerus_orion", "caerus_lyra"}
    assert feedback["status"] != "NO_DATA"
    assert feedback["strategies"]["polaris"]["primary_learning_gap"] != "stale placeholder"
    assert latest_feedback.exists()
    assert json.loads(latest_feedback.read_text(encoding="utf-8")) == feedback
    assert rolling_index.exists()
