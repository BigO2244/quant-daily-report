from __future__ import annotations

from research.evidence_hardening import classify_pilot_checklist


def test_pilot_checklist_fails_closed_on_any_failed_section() -> None:
    sections = [
        {"section": "Data Trust", "status": "PASS"},
        {"section": "Operational Trust", "status": "FAIL"},
    ]

    assert classify_pilot_checklist(sections) == "PILOT_CAPITAL_NOT_READY"


def test_pilot_checklist_conditional_on_warning_only() -> None:
    sections = [
        {"section": "Data Trust", "status": "PASS"},
        {"section": "Risk controls", "status": "WARN"},
    ]

    assert classify_pilot_checklist(sections) == "PILOT_CAPITAL_CONDITIONALLY_READY"


def test_pilot_checklist_ready_only_when_all_pass() -> None:
    sections = [
        {"section": "Data Trust", "status": "PASS"},
        {"section": "Operational Trust", "status": "PASS"},
    ]

    assert classify_pilot_checklist(sections) == "PILOT_CAPITAL_READY"

