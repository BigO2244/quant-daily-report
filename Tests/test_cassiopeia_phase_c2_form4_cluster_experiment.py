from __future__ import annotations

import json
from pathlib import Path

from scripts.research.build_cassiopeia_phase_c2_form4_cluster_experiment import (
    build_cluster_experiment,
    write_artifacts,
)


def _event(
    accession: str,
    *,
    ticker: str = "AAA",
    transaction_type: str = "purchase",
    tradable_date: str = "2024-01-03",
    period_of_report: str = "2024-01-02",
    role: str = "ceo",
    role_weight: float = 2.0,
    purchase_value: float = 200_000.0,
    sale_value: float = 0.0,
    excess_20d: float = 0.05,
    excess_60d: float = 0.10,
    cost_bps: float = 10.0,
) -> dict:
    return {
        "accession_number": accession,
        "ticker": ticker,
        "sector": "Technology",
        "transaction_type": transaction_type,
        "tradable_date": tradable_date,
        "period_of_report": period_of_report,
        "acceptance_datetime_utc": f"{tradable_date}T13:00:00+00:00",
        "pit_validity_flag": True,
        "insider_role": role,
        "role_weight": role_weight,
        "purchase_value": purchase_value,
        "sale_value": sale_value,
        "transactions": [{"transaction_date": period_of_report, "transaction_value": purchase_value or sale_value}],
        "implementation_shortfall_proxy_bps": cost_bps,
        "excess_return_vs_spy_1d": 0.01,
        "excess_return_vs_spy_5d": 0.02,
        "excess_return_vs_spy_20d": excess_20d,
        "excess_return_vs_spy_60d": excess_60d,
    }


def _payload(events: list[dict]) -> dict:
    return {
        "schema_version": "caerus_cassiopeia_phase_c_form4_event_tape_v1",
        "artifact_date": "fixture",
        "pit_validity": {"pit_safe": True},
        "event_tape": {"events": events},
    }


def test_cluster_experiment_clusters_nearby_purchase_filings_and_uses_first_anchor() -> None:
    events = [
        _event("0001", tradable_date="2024-01-03", purchase_value=200_000.0, excess_20d=0.05, excess_60d=0.10),
        _event("0002", tradable_date="2024-01-05", role="cfo", role_weight=1.8, purchase_value=150_000.0, excess_20d=-0.50, excess_60d=-0.50),
        _event("0003", tradable_date="2024-01-15", role="director", role_weight=1.0, purchase_value=300_000.0, excess_20d=0.04, excess_60d=0.08),
    ]

    payload = build_cluster_experiment(
        _payload(events),
        output_date="test",
        cluster_window_days=5,
        min_purchase_value=100_000.0,
        min_cluster_count=1,
    )

    clusters = payload["cluster_tape"]["clusters"]
    assert payload["cluster_tape"]["purchase_cluster_count"] == 2
    assert payload["cluster_tape"]["eligible_purchase_cluster_count"] == 2
    assert clusters[0]["raw_event_count"] == 2
    assert clusters[0]["purchase_value"] == 350_000.0
    assert clusters[0]["anchor_accession_number"] == "0001"
    assert clusters[0]["excess_return_vs_spy_20d"] == 0.05
    assert clusters[0]["net_excess_return_vs_spy_20d"] == 0.048
    assert payload["classification"]["classification"] == "CASSIOPEIA_PHASE_C2_PROMISING"
    assert payload["pit_validity"]["pit_safe"] is True


def test_cluster_experiment_filters_purchase_clusters_by_role_and_value() -> None:
    events = [
        _event("low-value", ticker="AAA", purchase_value=50_000.0, role="ceo", role_weight=2.0),
        _event("weak-role", ticker="BBB", purchase_value=500_000.0, role="other", role_weight=0.7),
        _event("eligible", ticker="CCC", purchase_value=250_000.0, role="cfo", role_weight=1.8),
    ]

    payload = build_cluster_experiment(
        _payload(events),
        output_date="test",
        min_purchase_value=100_000.0,
        min_cluster_count=30,
    )

    assert payload["cluster_tape"]["purchase_cluster_count"] == 3
    assert payload["cluster_tape"]["eligible_purchase_cluster_count"] == 1
    assert payload["cluster_tape"]["purchase_filter_rejections"] == {
        "purchase_value_below_threshold": 1,
        "role_quality_failed": 1,
    }
    assert payload["classification"]["reason_codes"] == [
        "eligible_purchase_cluster_sample_below_minimum",
        "continue_bounded_research",
    ]


def test_cluster_experiment_classifies_sale_only_sample_without_promotion_claim() -> None:
    events = [
        _event(
            "sale-1",
            transaction_type="sale",
            purchase_value=0.0,
            sale_value=400_000.0,
            role="president",
            role_weight=1.6,
        )
    ]

    payload = build_cluster_experiment(_payload(events), output_date="test", min_cluster_count=1)

    assert payload["cluster_tape"]["cluster_count"] == 1
    assert payload["cluster_tape"]["purchase_cluster_count"] == 0
    assert payload["classification"] == {
        "classification": "CASSIOPEIA_PHASE_C2_NEEDS_DEEPER_EVIDENCE",
        "reason_codes": ["no_purchase_clusters", "sale_only_sample"],
    }
    assert payload["runtime_change"] is False
    assert "no execution changes" in payload["non_goals"]


def test_write_artifacts_writes_research_only_c2_outputs(tmp_path: Path) -> None:
    payload = build_cluster_experiment(_payload([_event("0001")]), output_date="2026-06-29", min_cluster_count=1)

    json_path, md_path = write_artifacts(tmp_path, payload)

    assert json_path == tmp_path / "outputs/research/cassiopeia/cassiopeia_phase_c2_form4_cluster_experiment_2026-06-29.json"
    assert md_path.exists()
    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["schema_version"] == "caerus_cassiopeia_phase_c2_form4_cluster_experiment_v1"
    assert written["research_only"] is True
    assert written["execution_impact"] == "NON_EXECUTIONAL"
