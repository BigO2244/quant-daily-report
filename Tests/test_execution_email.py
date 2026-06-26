import json

from paper.build_execution_email import build_execution_email_html, build_execution_email_text


def test_execution_email_body_is_defined_and_formats_shadow_payload_status():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "execution_payload_status": "NOT GENERATED (Expected in SHADOW)",
    }

    subject, body = build_execution_email_text(payload)

    assert subject == "TRADE EXECUTION — 2026-02-05 (SHADOW)"
    assert "• Execution Payload: NOT GENERATED (EXPECTED IN SHADOW)" in body


def test_execution_email_no_trades_includes_min_trade_filter_reason_and_counts():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "no_trades_reason": "No executable trades after rounding and $100 minimum trade filter",
        "proposed_trades_intent_count": 3,
        "executable_trades_count": 0,
    }

    _, body = build_execution_email_text(payload)

    assert "No executable trades after rounding and $100 minimum trade filter" in body
    assert "Proposed Trades (Intent) | 3" in body
    assert "Executable Trades | 0" in body


def test_execution_email_includes_turnover_scaling_risk_note():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "turnover_note": "Turnover cap applied: requested $4,679.07, cap $3,023.97, scale 0.6463.",
    }

    _, body = build_execution_email_text(payload)

    assert "Risk Note: Turnover cap applied: requested $4,679.07, cap $3,023.97, scale 0.6463." in body


def test_execution_email_html_contains_buy_sell_tables_and_headers():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 10,
                "entry_price": 180.0,
                "stop_loss": 170.0,
                "take_profit": 200.0,
                "notional": 1800.0,
            },
            {
                "ticker": "MSFT",
                "side": "SELL",
                "shares": 5,
                "entry_price": 400.0,
                "stop_loss": 420.0,
                "take_profit": 360.0,
                "notional": 2000.0,
                "reason": "removed_from_targets",
            },
        ],
    }
    _, html = build_execution_email_html(payload)

    assert "<h3>Buy Orders</h3>" in html
    assert "<h3>Sell / Close Orders</h3>" in html
    assert "Entry (X)" in html
    assert "Stop (Y)" in html
    assert "Target (Z)" in html
    assert "AAPL" in html
    assert "MSFT" in html


def test_execution_email_includes_portfolio_risk_summary_values():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "risk_summary": {
            "Turnover requested ($)": "$4,679.07",
            "Turnover cap ($)": "$3,023.97",
            "Turnover scale": "0.6463",
        },
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "PORTFOLIO RISK SUMMARY" in body
    assert "Turnover requested ($): $4,679.07" in body
    assert "Portfolio Risk Summary" in html
    assert "Turnover cap ($)" in html


def test_execution_email_surfaces_operator_execution_and_timing_status():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "ALPACA",
        "execution_status": "HALTED",
        "operator_execution_status": "partial",
        "timing_status": "degraded_late",
        "halt_reason": "post_submit_artifact_failure",
        "trades": [],
    }

    _, body = build_execution_email_text(payload)

    assert "Execution Outcome: PARTIAL" in body
    assert "Timing Status: degraded_late" in body


def test_execution_email_surfaces_candidate_trade_lifecycle():
    payload = {
        "trade_date": "2026-06-25",
        "mode": "ALPACA",
        "execution_status": "RECONCILED_SUCCESS",
        "trades": [],
        "planned_payload_trade_count": 8,
        "executable_filter_passed_count": 6,
        "intended_orders_count": 6,
        "submitted_count": 2,
        "accepted_count": 2,
        "orders_filled_count": 2,
        "rejected_count": 0,
        "candidate_trade_lifecycle_summary": {
            "precompute_candidates": 8,
            "passed_executable_filter": 6,
            "intended_orders": 6,
            "submitted": 2,
            "accepted": 2,
            "filled": 2,
            "rejected": 0,
            "clipped": 1,
            "suppressed": 6,
            "artifact_path": "outputs/runs/run/audit/candidate_trade_lifecycle_2026-06-25.json",
        },
        "candidate_trade_lifecycle": [
            {
                "ticker": "VZ",
                "side": "BUY",
                "submitted": False,
                "decision_reason": "buy_blocked_insufficient_buying_power",
            },
            {
                "ticker": "SPG",
                "side": "BUY",
                "submitted": True,
                "decision_reason": "post_sell_rebudget_capital_clipped",
            },
        ],
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "EXECUTION LIFECYCLE" in body
    assert "Planned Payload Trades | 8" in body
    assert "Executable Filter Passed | 6" in body
    assert "Intended Orders | 6" in body
    assert "Orders Submitted | 2" in body
    assert "Orders Accepted | 2" in body
    assert "Orders Filled | 2" in body
    assert "Clipped Candidates | 1" in body
    assert "Suppressed Candidates | 6" in body
    assert "VZ BUY:buy_blocked_insufficient_buying_power" in body
    assert "SPG BUY:post_sell_rebudget_capital_clipped" in body
    assert "candidate_trade_lifecycle_2026-06-25.json" in body
    assert "Execution Lifecycle" in html
    assert "Planned Payload Trades" in html
    assert "Candidate Reasons" in html
    assert "candidate_trade_lifecycle_2026-06-25.json" in html


def test_execution_email_surfaces_reliability_and_target_attainment_artifacts(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    reliability_path = audit_dir / "execution_reliability_report_2026-06-25.json"
    reliability_path.write_text(
        json.dumps(
            {
                "overall_status": "WARN",
                "classification": "YELLOW",
                "score": 84.5,
                "top_failure_reason": "cash_target_drift",
                "top_failure_invariant_id": "target_cash_drift",
                "trend_metrics": {"clean_run_streak": 2},
                "recommended_operator_actions": ["Review target-attainment cash drift"],
            }
        ),
        encoding="utf-8",
    )
    target_path = audit_dir / "execution_target_attainment_2026-06-25.json"
    target_path.write_text(
        json.dumps(
            {
                "status": "WARN_CASH_DRIFT",
                "target_cash_weight": 0.05,
                "achieved_cash_weight": 0.1234,
                "cash_target_drift": 0.0734,
                "submitted_buy_count": 2,
                "filled_buy_count": 1,
                "pending_buy_count": 1,
                "missing_intended_buys": [{"symbol": "VZ", "side": "BUY", "reason": "buy_blocked_insufficient_buying_power"}],
                "warnings": ["cash_target_drift"],
                "actual_posttrade_cash": 1234.56,
                "actual_posttrade_cash_source": "posttrade_account_snapshot",
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "repo_root": str(tmp_path),
        "trade_date": "2026-06-25",
        "mode": "ALPACA",
        "execution_status": "READY",
        "trades": [],
        "execution_reliability_artifact": "audit/execution_reliability_report_2026-06-25.json",
        "execution_target_attainment_artifact": "audit/execution_target_attainment_2026-06-25.json",
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "EXECUTION RELIABILITY" in body
    assert "Classification | YELLOW" in body
    assert "Top reason | cash_target_drift" in body
    assert "Recommended actions | Review target-attainment cash drift" in body
    assert "TARGET ATTAINMENT" in body
    assert "Status | WARN_CASH_DRIFT" in body
    assert "Target cash weight | 5.00%" in body
    assert "Achieved cash weight | 12.34%" in body
    assert "Missing intended buys | VZ:BUY:buy_blocked_insufficient_buying_power" in body
    assert "Execution Reliability" in html
    assert "Target Attainment" in html


def test_execution_email_marks_missing_reporting_artifacts_without_crashing(tmp_path):
    payload = {
        "repo_root": str(tmp_path),
        "trade_date": "2026-06-25",
        "mode": "ALPACA",
        "execution_status": "READY",
        "trades": [],
        "execution_reliability_artifact": "audit/missing_reliability.json",
        "execution_target_attainment_artifact": "audit/missing_target_attainment.json",
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "EXECUTION RELIABILITY" in body
    assert "Artifact status | MISSING" in body
    assert "audit/missing_reliability.json" in body
    assert "TARGET ATTAINMENT" in body
    assert "audit/missing_target_attainment.json" in body
    assert "Execution Reliability" in html
    assert "Target Attainment" in html
    assert "MISSING" in html


def test_execution_email_surfaces_construction_provenance_artifact(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    provenance_path = audit_dir / "construction_provenance_2026-06-26.json"
    provenance_path.write_text(
        json.dumps(
            {
                "schema_version": "construction_provenance.v1",
                "summary": {
                    "status": "OK",
                    "row_count": 2,
                    "action_counts": {"retained": 1, "skipped": 1},
                    "constraint_counts": {"executable_filter:min_notional": 1},
                    "score_backed_count": 1,
                    "unavailable_score_count": 1,
                },
                "source_artifacts": {
                    "signals": {"path": "signals/2026-06-26.json", "status": "FOUND"},
                    "planned_payload": {"path": "outputs/precompute/2026-06-26/planned_execution_payload.json", "status": "FOUND"},
                    "candidate_trade_lifecycle": {"path": "outputs/runs/run/audit/candidate_trade_lifecycle_2026-06-26.json", "status": "FOUND"},
                    "current_positions": {"path": "outputs/runs/run/broker/pretrade_positions.json", "status": "FOUND"},
                },
                "rows": [
                    {
                        "ticker": "AAA",
                        "construction_action": "retained",
                        "sleeve_sources": ["sleeve_trend"],
                        "current_weight": 0.05,
                        "final_target_weight": 0.08,
                        "raw_score": 0.91,
                        "score_source": "candidate_trade_lifecycle.conviction_score",
                        "suppression_block_reason": "unavailable",
                    },
                    {
                        "ticker": "BBB",
                        "construction_action": "skipped",
                        "sleeve_sources": ["sleeve_quality"],
                        "current_weight": 0.0,
                        "final_target_weight": 0.04,
                        "raw_score": "unavailable",
                        "score_source": "unavailable",
                        "suppression_block_reason": "min_notional",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "repo_root": str(tmp_path),
        "trade_date": "2026-06-26",
        "mode": "ALPACA",
        "execution_status": "READY",
        "trades": [],
        "construction_provenance_artifact": "audit/construction_provenance_2026-06-26.json",
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "CONSTRUCTION PROVENANCE" in body
    assert "Status | OK" in body
    assert "Actions | retained=1, skipped=1" in body
    assert "Score-backed rows | 1" in body
    assert "AAA retained sleeve=sleeve_trend current=5.00% target=8.00%" in body
    assert "BBB skipped sleeve=sleeve_quality current=0.00% target=4.00%" in body
    assert "Construction Provenance" in html
    assert "candidate_trade_lifecycle.conviction_score" in html


def test_execution_email_marks_missing_construction_provenance_artifact(tmp_path):
    payload = {
        "repo_root": str(tmp_path),
        "trade_date": "2026-06-26",
        "mode": "ALPACA",
        "execution_status": "READY",
        "trades": [],
        "construction_provenance_artifact": "audit/missing_construction_provenance.json",
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "CONSTRUCTION PROVENANCE" in body
    assert "Artifact status | MISSING" in body
    assert "audit/missing_construction_provenance.json" in body
    assert "Construction Provenance" in html
    assert "MISSING" in html


def test_execution_email_surfaces_fr105_readiness_without_recommendations(tmp_path):
    research_dir = tmp_path / "outputs" / "research" / "fr_105" / "2026-06-26"
    research_dir.mkdir(parents=True)
    completeness_path = research_dir / "phase01_artifact_completeness.json"
    completeness_path.write_text(
        json.dumps(
            {
                "schema_version": "fr105_phase01_artifact_completeness.v1",
                "summary": {
                    "status": "INCOMPLETE",
                    "complete": False,
                    "missing_fields": ["lifecycle_artifact"],
                    "unavailable_fields": ["candidate_pool", "score_source"],
                },
                "phase_status": {"phase0": "COMPLETE", "phase1": "MISSING"},
                "readiness": {"status": "BLOCKED_ARTIFACT_GAPS"},
            }
        ),
        encoding="utf-8",
    )
    framework_path = tmp_path / "outputs" / "research" / "fr_105" / "shadow_alpha_chase_framework.json"
    framework_path.write_text(
        json.dumps(
            {
                "schema_version": "fr105_shadow_alpha_chase_framework.v1",
                "metadata": {"enabled": False},
                "evaluation_status": {"status": "DISABLED"},
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "repo_root": str(tmp_path),
        "trade_date": "2026-06-26",
        "mode": "ALPACA",
        "execution_status": "READY",
        "trades": [],
        "fr105_phase01_completeness_artifact": str(completeness_path),
        "fr105_shadow_alpha_framework_artifact": str(framework_path),
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "FR-105 RESEARCH STATUS" in body
    assert "Research Status | INCOMPLETE" in body
    assert "Readiness | BLOCKED_ARTIFACT_GAPS" in body
    assert "Alpha Chase enabled | NO" in body
    assert "Recommendations | none; readiness only" in body
    assert "lifecycle_artifact" in body
    assert "FR-105 Research Status" in html
    assert "DISABLED" in html


def test_execution_email_no_trades_includes_drop_diagnostics_when_present():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "proposed_trades_intent_count": 4,
        "executable_trades_count": 0,
        "min_trade_dollars": 125.0,
        "filter_stats": {
            "raw": 4,
            "rounded": 4,
            "kept": 0,
            "dropped_zero_shares": 2,
            "dropped_min_notional": 1,
        },
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "Dropped Zero Shares | 2" in body
    assert "Dropped Min Notional | 1" in body
    assert "Min Trade Dollars | $125.00" in body
    assert "Dropped Zero Shares" in html
    assert "Dropped Min Notional" in html


def test_execution_email_no_trades_uses_unavailable_for_missing_diagnostics():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
    }

    _, body = build_execution_email_text(payload)

    assert "Proposed Trades (Intent) | unavailable" in body
    assert "Executable Trades | unavailable" in body
    assert "Dropped Zero Shares | unavailable" in body
    assert "Dropped Min Notional | unavailable" in body
    assert "Min Trade Dollars | unavailable" in body


def test_execution_email_no_trades_supports_alternate_dropped_zero_key():
    payload = {
        "trade_date": "2026-02-05",
        "mode": "SHADOW",
        "execution_status": "READY",
        "trades": [],
        "proposed_intent_count": 6,
        "executable_trades_count": 0,
        "min_trade_dollars": 150.0,
        "filter_stats": {
            "dropped_zero": 4,
            "dropped_min_notional": 2,
        },
    }

    _, body = build_execution_email_text(payload)
    _, html = build_execution_email_html(payload)

    assert "Proposed Trades (Intent) | 6" in body
    assert "Dropped Zero Shares | 4" in body
    assert "Dropped Min Notional | 2" in body
    assert "$150.00" in html
