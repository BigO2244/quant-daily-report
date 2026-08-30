from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pandas as pd
import pytest

from core.orion_decision_lineage import LINEAGE_SCHEMA, canonical_hash, require_clean_git_sha
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


def _complete_orion_source(trade_date: str = "2026-05-04", *, salt: str = "current") -> dict:
    weights = {"AAA": 0.5, "BBB": 0.5}
    market_hash = canonical_hash(["market", salt])
    panel_hash = canonical_hash(["panel", salt])
    feature_hash = canonical_hash(["features", salt])
    history_hash = canonical_hash(["history", salt])
    rank_hash = canonical_hash(["rank", salt])
    diagnostics = {
        stage: {
            "stage": stage,
            "source_identity": f"test.{stage}",
            "row_count": 2,
            "symbol_count": 2,
            "max_market_timestamp": trade_date,
        }
        for stage in (
            "market_data", "normalized_panel", "features", "full_rank_history",
            "current_rank_table", "target_weights",
        )
    }
    lineage = {
        "schema_version": LINEAGE_SCHEMA,
        "trade_date": trade_date,
        "effective_trade_date": trade_date,
        "market_data_asof": trade_date,
        "market_data_hash": market_hash,
        "normalized_panel_hash": panel_hash,
        "feature_hash": feature_hash,
        "full_rank_history_hash": history_hash,
        "rank_table_hash": rank_hash,
        "target_weights_hash": canonical_hash(weights),
        "generated_at_utc": f"{trade_date}T22:00:00Z",
        "model_version": "orion_test_v1",
        "source_variant": "orion_test_v1",
        "parent_artifact_hashes": {
            "normalized_panel": market_hash,
            "features": panel_hash,
            "full_rank_history": feature_hash,
            "current_rank_table": history_hash,
            "target_weights": rank_hash,
        },
        "coverage": {
            "status": "OK",
            "current_session": trade_date,
            "required_anchor_dates": ["2026-05-01" if trade_date == "2026-05-04" else "2026-04-30"],
            "missing_current_session_symbols": [],
            "missing_required_anchor_symbols": {},
        },
        "selection_trace": [{"ticker": "AAA", "action": "KEEP"}],
        "stage_diagnostics": diagnostics,
    }
    return {
        "trade_date": trade_date,
        "effective_trade_date": trade_date,
        "source_variant": "orion_test_v1",
        "decision_eligible": True,
        "observation_status": "OK",
        "data_status": "OK",
        "coverage_status": "OK",
        "target_weights": weights,
        "decision_lineage": lineage,
    }


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


@pytest.mark.parametrize(
    ("cache_publish_status", "catchup_status", "expected_reason"),
    [
        ("BLOCKED_UNCHANGED", "OK", "canonical_cache_publication_blocked"),
        ("NOT_NEEDED", "INCOMPLETE", "downloaded_session_continuity_incomplete"),
    ],
)
def test_strict_fails_when_publication_control_is_non_ok_despite_current_max(
    tmp_path: Path,
    monkeypatch,
    cache_publish_status: str,
    catchup_status: str,
    expected_reason: str,
) -> None:
    universe = tmp_path / "universe.csv"
    cache = tmp_path / "price_panel.parquet"
    status_dir = tmp_path / "status"
    _write_universe(universe)
    _write_panel(cache, end_date="2026-05-04")

    def fake_ensure_price_panel(**_kwargs):
        return pd.DataFrame(), {
            "download_performed": True,
            "coverage_validation": {"status": "OK"},
            "catchup_validation": {
                "status": catchup_status,
                "missing_sessions_by_symbol": {"BBB": ["2026-05-01"]},
            },
            "cache_publish": {
                "status": cache_publish_status,
                "reason_codes": ["catchup_session_coverage_incomplete"],
            },
        }

    monkeypatch.setattr(script, "ensure_price_panel", fake_ensure_price_panel)
    monkeypatch.setattr(
        script,
        "resolve_completed_trading_day",
        lambda explicit_trade_date=None: explicit_trade_date or "2026-05-04",
    )

    rc = script.main(
        [
            "--trade-date", "2026-05-04", "--universe-path", str(universe),
            "--cache-path", str(cache), "--status-dir", str(status_dir), "--strict",
        ]
    )

    payload = json.loads((status_dir / "2026-05-04" / "status.json").read_text())
    assert rc == 1
    assert payload["max_cache_date"] == "2026-05-04"
    assert payload["status"] == "PARTIAL"
    assert payload["publication_validation"] == {
        "status": "INCOMPLETE",
        "reason_codes": [expected_reason],
    }


def test_strict_postclose_refresh_writes_orion_readiness_marker(tmp_path: Path, monkeypatch) -> None:
    universe = tmp_path / "universe.csv"
    cache = tmp_path / "price_panel.parquet"
    status_dir = tmp_path / "status"
    shadow_dir = tmp_path / "shadow"
    _write_universe(universe)

    def fake_ensure_price_panel(**kwargs):
        _write_panel(Path(kwargs["cache_path"]), end_date="2026-05-04")
        return pd.DataFrame(), {"download_performed": True, "coverage_validation": {"status": "OK"}}

    source = _complete_orion_source()
    lineage = source["decision_lineage"]

    def fake_shadow_refresh_main(_argv):
        dated = shadow_dir / "2026-05-04"
        dated.mkdir(parents=True, exist_ok=True)
        (dated / "caerus_orion.json").write_text(
            json.dumps(source),
            encoding="utf-8",
        )
        previous = shadow_dir / "2026-05-01" / "caerus_orion.json"
        previous.parent.mkdir(parents=True, exist_ok=True)
        previous.write_text(
            json.dumps(_complete_orion_source("2026-05-01", salt="previous")),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(script, "ensure_price_panel", fake_ensure_price_panel)
    monkeypatch.setattr(script.refresh_shadow_scorecard_artifacts, "main", fake_shadow_refresh_main)
    monkeypatch.setattr(script, "resolve_completed_trading_day", lambda explicit_trade_date=None: "2026-05-04")
    monkeypatch.setattr(script, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr("core.orion_decision_lineage.require_clean_git_sha", lambda _root: "a" * 40)

    rc = script.main(
        [
            "--trade-date", "2026-05-04", "--universe-path", str(universe),
            "--cache-path", str(cache), "--status-dir", str(status_dir),
            "--shadow-output-dir", str(shadow_dir), "--refresh-shadow-artifacts", "--strict",
        ]
    )

    marker = json.loads((status_dir / "2026-05-04" / "orion_decision_ready.json").read_text())
    assert rc == 0
    assert marker["schema_version"] == "caerus.orion_decision_readiness.v1"
    assert marker["status"] == "READY"
    assert marker["effective_trade_date"] == "2026-05-04"
    assert marker["decision_lineage_hash"] == canonical_hash(lineage)
    assert marker["source_artifact"]["sha256"]
    assert marker["hydration_status"]["sha256"]
    assert marker["source_artifact"]["path"] == "shadow/2026-05-04/caerus_orion.json"
    assert marker["hydration_status"]["path"] == "status/2026-05-04/status.json"


def test_strict_postclose_refresh_rejects_incomplete_lineage(tmp_path: Path, monkeypatch) -> None:
    universe = tmp_path / "universe.csv"
    cache = tmp_path / "price_panel.parquet"
    status_dir = tmp_path / "status"
    shadow_dir = tmp_path / "shadow"
    _write_universe(universe)

    def fake_ensure_price_panel(**kwargs):
        _write_panel(Path(kwargs["cache_path"]), end_date="2026-05-04")
        return pd.DataFrame(), {"download_performed": True, "coverage_validation": {"status": "OK"}}

    source = _complete_orion_source()
    source["decision_lineage"].pop("feature_hash")

    def fake_shadow_refresh_main(_argv):
        path = shadow_dir / "2026-05-04" / "caerus_orion.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(source), encoding="utf-8")
        previous = shadow_dir / "2026-05-01" / "caerus_orion.json"
        previous.parent.mkdir(parents=True, exist_ok=True)
        previous.write_text(
            json.dumps(_complete_orion_source("2026-05-01", salt="previous")),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(script, "ensure_price_panel", fake_ensure_price_panel)
    monkeypatch.setattr(script.refresh_shadow_scorecard_artifacts, "main", fake_shadow_refresh_main)
    monkeypatch.setattr(script, "resolve_completed_trading_day", lambda explicit_trade_date=None: "2026-05-04")
    monkeypatch.setattr("core.orion_decision_lineage.require_clean_git_sha", lambda _root: "a" * 40)

    rc = script.main(
        [
            "--trade-date", "2026-05-04", "--universe-path", str(universe),
            "--cache-path", str(cache), "--status-dir", str(status_dir),
            "--shadow-output-dir", str(shadow_dir), "--refresh-shadow-artifacts", "--strict",
        ]
    )

    assert rc == 1
    assert not (status_dir / "2026-05-04" / "orion_decision_ready.json").exists()


def test_git_attestation_rejects_dirty_runtime_state(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=tmp_path, check=True, capture_output=True)
    tracked.write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dirty"):
        require_clean_git_sha(tmp_path)
    tracked.write_text("clean\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dirty"):
        require_clean_git_sha(tmp_path)


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
    stale_evaluation = output_dir / "2023-03-31" / "shadow_evaluation.json"
    stale_evaluation.write_text(
        json.dumps(
            {
                "trade_date": "2023-03-31",
                "strategies": {
                    slug: {"status": "NO_PRIOR", "data_status": "NO_DATA"}
                    for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra")
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
    comparison_markdown = (output_dir / "2023-03-31" / "comparison.md").read_text(encoding="utf-8")
    latest_feedback = output_dir / "latest" / "feedback_loop_summary.json"
    latest_comparison_markdown = output_dir / "latest" / "comparison.md"
    rolling_index = output_dir / "performance" / "feedback_loop_rolling_index.csv"

    assert set(comparison["strategies"]) == {"caerus_polaris", "caerus_orion", "caerus_lyra"}
    assert feedback["status"] != "NO_DATA"
    assert feedback["strategies"]["polaris"]["primary_learning_gap"] != "stale placeholder"
    assert "- Any NO_DATA: NO" in comparison_markdown
    assert "- Any NO_DATA: YES" not in comparison_markdown
    assert latest_comparison_markdown.read_text(encoding="utf-8") == comparison_markdown
    assert latest_feedback.exists()
    assert json.loads(latest_feedback.read_text(encoding="utf-8")) == feedback
    assert rolling_index.exists()
