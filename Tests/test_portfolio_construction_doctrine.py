from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_portfolio_construction_doctrine_covers_required_governance_topics() -> None:
    path = REPO_ROOT / "docs" / "governance" / "portfolio_construction_doctrine.md"
    text = path.read_text(encoding="utf-8")

    for phrase in (
        "Purpose",
        "Investment Objective",
        "Relationship To Investment Doctrine",
        "Current Sleeve-Merge Philosophy",
        "Future Alpha Chase Philosophy",
        "Concentration Philosophy",
        "Diversification Philosophy",
        "When Conviction Overrides Diversification",
        "Required Risk Controls",
        "Promotion Requirements",
        "Default Governance Position",
    ):
        assert phrase in text
    assert "Alpha Chase is disabled by default" in text
    assert "No report may describe target weights or allocation weights as alpha scores" in text
    assert (REPO_ROOT / "docs" / "governance" / "caerus_investment_doctrine.md").exists()
    assert (
        REPO_ROOT / "docs" / "governance" / "decision_records" / "ADR-001_portfolio_construction_strategy.md"
    ).exists()


def test_portfolio_construction_adr_records_current_decision_and_rejected_alternatives() -> None:
    path = REPO_ROOT / "docs" / "governance" / "decision_records" / "ADR-001_portfolio_construction_strategy.md"
    text = path.read_text(encoding="utf-8")

    for phrase in (
        "Current Architecture",
        "Why The Current Architecture Exists",
        "Observed Limitations",
        "Evidence Gathered",
        "Decision",
        "Alpha Chase Evaluation Rationale",
        "Success Metrics",
        "Promotion Gates",
        "Risks",
        "Rejected Alternatives",
    ):
        assert phrase in text
    assert "Keep sleeve-merge as the current trading baseline" in text
    assert "Evaluate Alpha Chase through FR-105 research and shadow artifacts only" in text
