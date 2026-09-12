import json
from pathlib import Path
import pytest
from core.shadow_health_report import build_shadow_health_report
from core.portfolio_learning_report import ARTIFACT_NAMES
from scripts.build_shadow_health_report import main

DATE = "2026-09-11"


def write(root, name, payload, date=DATE):
    path = root / "outputs/shadow_candidates" / date / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"trade_date": date, **payload}))


@pytest.fixture
def healthy(tmp_path):
    source = Path(__file__).resolve().parents[1] / "config/research/strategy_registry.json"
    registry = json.loads(source.read_text())
    entries = [e for e in registry["strategies"] if e["strategy_id"] in {"caerus_lyra", "caerus_phoenix"}]
    registry["strategies"] = entries
    target = tmp_path / "config/research/strategy_registry.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(registry))
    metrics = {"data_status": "OK", "daily_return": 0.01, "cumulative_return": 0.05, "rolling_count_of_valid_days": 1}
    write(tmp_path, "shadow_evaluation.json", {"strategies": {"caerus_lyra": metrics, "spy_benchmark": metrics}})
    write(tmp_path, "comparison.json", {"strategies": {"caerus_lyra": {"holdings": []}}})
    write(tmp_path, "feedback_loop_summary.json", {"strategies": {"lyra": {"learning_readiness": "MEDIUM"}}})
    write(tmp_path, "promotion_readiness.json", {"strategies": {"caerus_lyra": {"readiness_state": "CONTINUE_SHADOW", "reason_codes": ["insufficient_history"]}}})
    write(tmp_path, "caerus_lyra.json", {"strategy_slug": "caerus_lyra", "holdings": [{"ticker": "SPY", "target_weight": 1.0}]})
    for name in ARTIFACT_NAMES:
        write(tmp_path, "lyra/" + name, {"status": "OK", "prior_positions_available": True, "changes_vs_prior": {k: [] for k in ["new_entries", "exits", "weight_increases", "weight_decreases"]}})
    return tmp_path


def report(root, start=DATE):
    return build_shadow_health_report(repo_root=root, trade_date=DATE, observation_start=start)


def test_complete_evidence_and_research_disposition(healthy):
    result = report(healthy)
    assert result["status"] == "PASS"
    assert result["expected_sleeves"] == ["caerus_lyra"]
    lyra, phoenix = result["sleeves"]
    assert lyra["observation_days"] == 1
    assert lyra["trades"]["count"] == 0
    assert lyra["daily_return"] == 0.01
    assert lyra["promotion_authorized"] is False
    assert phoenix["registry_status"] == "RESEARCH"
    assert phoenix["positions"] is None and phoenix["daily_return"] is None
    assert phoenix["status"] == "NOT_EXPECTED"


@pytest.mark.parametrize("filename", ["caerus_lyra.json", "shadow_evaluation.json", "lyra/attribution.json", "promotion_readiness.json"])
def test_missing_required_artifact_is_pipeline_failure(healthy, filename):
    (healthy / "outputs/shadow_candidates" / DATE / filename).unlink()
    result = report(healthy)
    assert result["status"] == "PIPELINE_FAILURE"
    assert result["sleeves"][0]["promotion_readiness"] == "BLOCKED_PIPELINE"


def test_missing_registered_sleeve_cannot_hide_in_good_file(healthy):
    write(healthy, "shadow_evaluation.json", {"strategies": {}})
    assert "expected_sleeve_missing" in report(healthy)["sleeves"][0]["reason"]


def test_gaps_count_sessions_not_weekends_or_labor_day(healthy):
    result = report(healthy, "2026-09-04")
    assert result["sleeves"][0]["missing_days"] == ["2026-09-04", "2026-09-08", "2026-09-09", "2026-09-10"]
    assert result["sleeves"][0]["observation_days"] == 1


def test_wrong_date_and_nonfinite_returns_fail_closed(healthy):
    write(healthy, "caerus_lyra.json", {"trade_date": "2026-09-10", "strategy_slug": "caerus_lyra", "holdings": []})
    write(healthy, "shadow_evaluation.json", {"strategies": {"caerus_lyra": {"data_status": "OK", "daily_return": float("nan")}}})
    result = report(healthy)
    assert result["status"] == "PIPELINE_FAILURE"
    assert result["sleeves"][0]["daily_return"] is None
    json.dumps(result, allow_nan=False)


def test_cli_writes_failure_evidence_and_exits_nonzero(healthy, tmp_path):
    output = tmp_path / "report.json"
    assert main(["--repo-root", str(healthy), "--trade-date", DATE, "--output", str(output), "--strict"]) == 1
    assert json.loads(output.read_text())["status"] == "PIPELINE_FAILURE"


def test_missing_registry_never_uses_ambient_default(tmp_path):
    with pytest.raises(FileNotFoundError):
        report(tmp_path)


def test_baseline_control_is_not_a_missing_promotion_candidate(healthy):
    registry_path = healthy / "config/research/strategy_registry.json"
    registry = json.loads(registry_path.read_text())
    lyra = next(e for e in registry["strategies"] if e["strategy_id"] == "caerus_lyra")
    lyra["role"] = "baseline"
    lyra["eligible_for_promotion"] = False
    registry_path.write_text(json.dumps(registry))
    write(healthy, "promotion_readiness.json", {"active_baseline": "caerus_lyra", "strategies": {}})
    result = report(healthy)
    assert result["status"] == "PASS"
    assert result["sleeves"][0]["promotion_readiness"] == "NOT_APPLICABLE_CONTROL"
    assert result["sleeves"][0]["promotion_authorized"] is False
    # The narrow control disposition does not conceal missing performance.
    write(healthy, "shadow_evaluation.json", {"strategies": {}})
    assert report(healthy)["status"] == "PIPELINE_FAILURE"


@pytest.mark.parametrize("defect", ["wrong_baseline", "promotable", "missing_artifact"])
def test_control_disposition_requires_matching_registry_and_readiness(healthy, defect):
    registry_path = healthy / "config/research/strategy_registry.json"
    registry = json.loads(registry_path.read_text())
    lyra = next(e for e in registry["strategies"] if e["strategy_id"] == "caerus_lyra")
    lyra["role"] = "baseline"
    lyra["eligible_for_promotion"] = defect == "promotable"
    registry_path.write_text(json.dumps(registry))
    write(healthy, "promotion_readiness.json", {"active_baseline": "wrong" if defect == "wrong_baseline" else "caerus_lyra", "strategies": {}})
    if defect == "missing_artifact":
        (healthy / "outputs/shadow_candidates" / DATE / "promotion_readiness.json").unlink()
    assert report(healthy)["status"] == "PIPELINE_FAILURE"
