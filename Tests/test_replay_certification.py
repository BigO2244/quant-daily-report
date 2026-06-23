from __future__ import annotations

import pandas as pd

from research.replay_certification import certify_security_id_price_panel


def _panel() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2020-01-02",
                "security_id": "SHARADAR:1",
                "display_ticker": "AAPL",
                "closeadj": 10.0,
                "source_ticker": "AAPL",
                "source_file_sha256": "abc",
                "price_source": "sharadar_sep_closeadj",
                "membership_family": "caerus_large_cap",
            }
        ]
    )


def _manifest(**overrides):
    payload = {
        "identity_key": "security_id",
        "ticker_role": "display_only",
        "universe_method": "pit_universe",
        "price_source": "sharadar_sep_closeadj",
        "duplicate_date_security_id_count": 0,
        "source_paths": {
            "security_master": "data/pit_universe/security_master.csv",
            "membership": "data/pit_universe/membership_universe_large_cap.csv",
            "sep_cache_dir": "data/research_cache/sharadar_sep",
        },
        "source_hashes": {"security_master_sha256": "abc", "membership_sha256": "def"},
        "membership_scale_precision": "PIT_APPROXIMATE_SCALE",
        "membership_certification_status": "FAIL",
        "membership_certification_methods": ["CURRENT_SCALE_APPROXIMATION"],
        "decision_grade_blockers": [
            "PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED",
            "CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE",
        ],
        "lineage_digest": "digest",
    }
    payload.update(overrides)
    return payload


def test_security_id_panel_passes_with_scale_warning() -> None:
    result = certify_security_id_price_panel(_panel(), _manifest())
    assert result.status == "PASS"
    assert result.decision_grade_status == "PARTIAL"
    assert result.findings == []
    assert result.warnings == ["MEMBERSHIP_NOT_DECISION_GRADE:FAIL"]


def test_pit_daily_marketcap_membership_can_be_decision_grade_for_price_panel() -> None:
    result = certify_security_id_price_panel(
        _panel(),
        _manifest(
            membership_scale_precision="PIT_EXACT_SCALE",
            membership_certification_status="PASS",
            membership_certification_methods=["PIT_DAILY_MARKETCAP"],
            decision_grade_blockers=[],
        ),
        require_decision_grade_membership=True,
    )
    assert result.status == "PASS"
    assert result.decision_grade_status == "PASS"


def test_pit_index_membership_can_be_decision_grade_without_daily_marketcap() -> None:
    result = certify_security_id_price_panel(
        _panel(),
        _manifest(
            membership_scale_precision="NO_SCALE_SOURCE",
            membership_certification_status="PASS",
            membership_certification_methods=["PIT_INDEX_MEMBERSHIP"],
            decision_grade_blockers=[],
        ),
        require_decision_grade_membership=True,
    )
    assert result.status == "PASS"
    assert result.decision_grade_status == "PASS"


def test_rejects_ticker_keyed_panel() -> None:
    panel = _panel().drop(columns=["security_id"]).assign(ticker="AAPL")
    result = certify_security_id_price_panel(panel, _manifest())
    assert result.status == "FAIL"
    assert "MISSING_PANEL_COLUMNS:security_id" in result.findings
    assert "TICKER_KEYED_PANEL" in result.findings


def test_rejects_duplicate_date_security_id() -> None:
    panel = pd.concat([_panel(), _panel()], ignore_index=True)
    result = certify_security_id_price_panel(panel, _manifest())
    assert result.status == "FAIL"
    assert "DUPLICATE_DATE_SECURITY_ID:1" in result.findings


def test_rejects_prohibited_legacy_inputs() -> None:
    result = certify_security_id_price_panel(
        _panel(),
        _manifest(input_paths=["data/universe.csv", "outputs/research/flow_detection_v1/price_panel.parquet"]),
    )
    assert result.status == "FAIL"
    assert "PROHIBITED_INPUT_PATH" in result.findings


def test_decision_grade_scale_requirement_fails_current_scale_family() -> None:
    result = certify_security_id_price_panel(_panel(), _manifest(), require_decision_grade_scale=True)
    assert result.status == "FAIL"
    assert "PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED" in result.findings
    assert "CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE" in result.findings


def test_decision_grade_membership_requirement_fails_current_scale_family() -> None:
    result = certify_security_id_price_panel(_panel(), _manifest(), require_decision_grade_membership=True)
    assert result.status == "FAIL"
    assert "PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED" in result.findings
    assert "CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE" in result.findings


def test_decision_tape_requirement_fails_when_absent() -> None:
    result = certify_security_id_price_panel(_panel(), _manifest(), require_decision_tape=True)
    assert result.status == "FAIL"
    assert "MISSING_DECISION_TAPE" in result.findings
