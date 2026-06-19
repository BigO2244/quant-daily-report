from __future__ import annotations

from research.evidence_hardening import FULL_EVIDENCE, LOW_EVIDENCE, reliability_capital_eligible


def test_green_low_evidence_is_not_capital_eligible() -> None:
    assert (
        reliability_capital_eligible(
            reliability_signal="RELIABILITY_GREEN",
            evidence_coverage=LOW_EVIDENCE,
            fail_count=0,
            warn_count=0,
        )
        is False
    )


def test_green_full_evidence_without_warnings_is_capital_eligible_signal() -> None:
    assert (
        reliability_capital_eligible(
            reliability_signal="RELIABILITY_GREEN",
            evidence_coverage=FULL_EVIDENCE,
            fail_count=0,
            warn_count=0,
        )
        is True
    )


def test_green_full_evidence_with_warning_is_not_capital_eligible() -> None:
    assert (
        reliability_capital_eligible(
            reliability_signal="RELIABILITY_GREEN",
            evidence_coverage=FULL_EVIDENCE,
            fail_count=0,
            warn_count=1,
        )
        is False
    )

