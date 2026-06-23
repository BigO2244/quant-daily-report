import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_format_precompute_email_prefers_regime_summary_and_marks_missing_vix(tmp_path):
    trade_date = "2026-04-24"
    bundle_dir = tmp_path / "outputs" / "precompute" / trade_date
    bundle_dir.mkdir(parents=True)

    planned_execution_payload = {
        "market_analyzer": {
            "signal_bucket": "RISK_ON",
        },
        "equity": 10000,
        "achieved_cash_weight": 0.05,
        "risk_summary": {
            "Gross exposure (%)": "95.0%",
            "# positions": 5,
        },
        "risk_meta": {
            "turnover_requested": 1000,
            "turnover_cap": 0,
            "turnover_scaled": False,
        },
        "pricing_asof": "2026-04-23",
        "trades": [],
    }
    daily_snapshot = {
        "regime_summary": {
            "composite_regime": "risk_on_trending",
        }
    }

    (bundle_dir / "planned_execution_payload.json").write_text(json.dumps(planned_execution_payload))
    (bundle_dir / "daily_snapshot.json").write_text(json.dumps(daily_snapshot))

    result = subprocess.run(
        [sys.executable, "-m", "scripts.format_precompute_email"],
        cwd=str(tmp_path),
        env={
            **os.environ,
            "REPORT_DATE": trade_date,
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "State:         risk_on_trending" in result.stdout
    assert "Signal bucket: RISK_ON" in result.stdout
    assert "VIX:           UNAVAILABLE (degraded: VIX regime skipped)" in result.stdout


def test_format_precompute_email_uses_market_analyzer_vix_when_present(tmp_path):
    trade_date = "2026-04-24"
    bundle_dir = tmp_path / "outputs" / "precompute" / trade_date
    bundle_dir.mkdir(parents=True)

    planned_execution_payload = {
        "market_analyzer": {
            "regime": "LOW",
            "signal_bucket": "RISK_ON",
            "vix": 18.125,
        },
        "equity": 10000,
        "achieved_cash_weight": 0.05,
        "risk_summary": {
            "Gross exposure (%)": "95.0%",
            "# positions": 5,
        },
        "risk_meta": {
            "turnover_requested": 1000,
            "turnover_cap": 0,
            "turnover_scaled": False,
        },
        "pricing_asof": "2026-04-23",
        "trades": [],
    }

    (bundle_dir / "planned_execution_payload.json").write_text(json.dumps(planned_execution_payload))

    result = subprocess.run(
        [sys.executable, "-m", "scripts.format_precompute_email"],
        cwd=str(tmp_path),
        env={
            **os.environ,
            "REPORT_DATE": trade_date,
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "State:         LOW" in result.stdout
    assert "VIX:           18.12" in result.stdout


def test_format_precompute_email_handles_null_numeric_payload_fields(tmp_path):
    trade_date = "2026-04-24"
    bundle_dir = tmp_path / "outputs" / "precompute" / trade_date
    bundle_dir.mkdir(parents=True)

    planned_execution_payload = {
        "market_analyzer": {
            "regime": "LOW",
            "signal_bucket": "RISK_ON",
            "vix": 19.100000381469727,
        },
        "equity": None,
        "achieved_cash_weight": None,
        "risk_summary": {
            "Gross exposure (%)": "100.00%",
            "# positions": "19",
        },
        "risk_meta": {
            "turnover_requested": None,
            "turnover_cap": None,
            "turnover_scaled": False,
        },
        "pricing_asof": "2026-04-24",
        "trades": [],
    }

    (bundle_dir / "planned_execution_payload.json").write_text(json.dumps(planned_execution_payload))

    result = subprocess.run(
        [sys.executable, "-m", "scripts.format_precompute_email"],
        cwd=str(tmp_path),
        env={
            **os.environ,
            "REPORT_DATE": trade_date,
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Equity:        $0.00" in result.stdout
    assert "Cash:          0.0%" in result.stdout
    assert "VIX:           19.10" in result.stdout
    assert "Turnover: $0.00 (no cap applied)" in result.stdout


def test_format_precompute_email_includes_dynamic_alpha_sleeves(tmp_path):
    trade_date = "2026-06-23"
    bundle_dir = tmp_path / "outputs" / "precompute" / trade_date
    bundle_dir.mkdir(parents=True)

    planned_execution_payload = {
        "market_analyzer": {"regime": "LOW", "signal_bucket": "RISK_ON", "vix": 19.1},
        "equity": 10000,
        "achieved_cash_weight": 0.05,
        "risk_summary": {"Gross exposure (%)": "95.0%", "# positions": 5},
        "risk_meta": {"turnover_requested": 100, "turnover_cap": 0, "turnover_scaled": False},
        "pricing_asof": "2026-06-22",
        "trades": [],
    }
    (bundle_dir / "planned_execution_payload.json").write_text(json.dumps(planned_execution_payload))
    registry_path = tmp_path / "config" / "research" / "strategy_registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "caerus_strategy_registry_v1",
                "strategies": [
                    {
                        "strategy_id": "caerus_polaris_alpha",
                        "display_name": "Polaris_Alpha",
                        "short_name": "polaris_alpha",
                        "strategy_type": "security_selection",
                        "family": "core_momentum",
                        "status": "shadow",
                        "role": "challenger",
                        "eligible_for_shadow": True,
                        "eligible_for_promotion": True,
                        "benchmark": "SPY",
                        "execution_impact": "NON_EXECUTIONAL",
                        "display_order": 1,
                        "capabilities": {"produces_holdings": True, "produces_nav": True},
                        "shadow_tracking": {"enabled": True, "baseline_strategy_id": "caerus_polaris"},
                    }
                ],
            }
        )
    )
    manifest_path = tmp_path / "research_registry" / "sleeves" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "caerus_sleeve_manifest_v1",
                "sleeves": [
                    {
                        "sleeve_id": "polaris_alpha",
                        "strategy_id": "caerus_polaris_alpha",
                        "display_name": "Polaris_Alpha",
                        "lifecycle_stage": "shadow_observed",
                    }
                ],
            }
        )
    )
    shadow_dir = tmp_path / "outputs" / "shadow_candidates" / trade_date
    shadow_dir.mkdir(parents=True)
    (shadow_dir / "caerus_polaris_alpha.json").write_text("{}")
    (shadow_dir / "shadow_evaluation.json").write_text(
        json.dumps(
            {
                "strategies": {
                    "caerus_polaris_alpha": {
                        "status": "OK",
                        "data_status": "OK",
                        "daily_return": 0.02,
                        "cumulative_return": 0.20,
                    }
                }
            }
        )
    )

    result = subprocess.run(
        [sys.executable, "-m", "scripts.format_precompute_email"],
        cwd=str(tmp_path),
        env={
            **os.environ,
            "REPORT_DATE": trade_date,
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Dynamic Sleeve Inventory" in result.stdout
    assert "Polaris_Alpha | shadow | alpha" in result.stdout
