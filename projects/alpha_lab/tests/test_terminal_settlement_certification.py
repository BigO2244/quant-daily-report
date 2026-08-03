from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from projects.alpha_lab.data_spine.storage import sha256_file
from projects.alpha_lab.data_spine.terminal_settlement_certification import (
    EVIDENCE_SCHEMA_VERSION,
    POPULATION_RULE,
    audit_terminal_settlements,
)


AS_OF = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)


def _fixture(tmp_path: Path, *, outcome: str = "FINAL_CASH", proceeds: float = 12.0):
    master = tmp_path / "security_master.csv"
    master.write_text(
        "security_id,ticker,category,effective_end\n"
        "SEC-OLD,OLD,Domestic Common Stock,2020-01-06\n"
        "SEC-LIVE,LIVE,Domestic Common Stock,\n",
        encoding="utf-8",
    )
    prices = tmp_path / "prices.csv"
    prices.write_text(
        "security_id,date,close,last_observed_total_return\n"
        "SEC-OLD,2020-01-02,10.0,0.01\n"
        "SEC-OLD,2020-01-03,11.0,0.10\n"
        "SEC-LIVE,2020-01-03,20.0,0.02\n",
        encoding="utf-8",
    )
    source = tmp_path / "sources" / "closing-8k.txt"
    source.parent.mkdir()
    source.write_text("Final cash consideration: $12 per share.", encoding="utf-8")
    terminations = tmp_path / "termination_population.jsonl"
    terminations.write_text(
        json.dumps(
            {
                "security_id": "SEC-OLD",
                "termination_action_id": "ACTION-1",
                "termination_type": "ACQUISITION",
                "termination_effective_date": "2020-01-06",
                "source_document_ids": ["closing-8k"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "settlements.jsonl"
    evidence.write_text(
        json.dumps(
            {
                "security_id": "SEC-OLD",
                "outcome_type": outcome,
                "finality_basis": "CLOSING_PAYMENT",
                "currency": "USD",
                "terminal_proceeds_per_pre_action_share": proceeds,
                "settlement_effective_date": "2020-01-06",
                "evidence_available_at": "2020-01-06T14:00:00Z",
                "source_document_ids": ["closing-8k"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
                "population_rule": POPULATION_RULE,
                "scope": {"start": "2012-01-01", "end": "2024-12-31"},
                "price_extract_contract": {
                    "prechallenge_extract": True,
                    "maximum_observation_date": "2024-12-31",
                },
                "security_master_sha256": sha256_file(master),
                "price_panel_sha256": sha256_file(prices),
                "price_provider_id": "sharadar.sep",
                "price_basis": {
                    "field": "close",
                    "semantics": "UNADJUSTED_LAST_OBSERVED_TRADE",
                    "terminal_proceeds_included": False,
                    "terminal_return_application": "AFTER_LAST_OBSERVED_RETURN_ONLY",
                },
                "evidence_file": {
                    "path": evidence.name,
                    "sha256": sha256_file(evidence),
                },
                "termination_population_file": {
                    "path": terminations.name,
                    "sha256": sha256_file(terminations),
                },
                "population_completeness_attestation": {
                    "reviewer": "independent-reviewer",
                    "reviewed_at": "2026-08-03T17:00:00Z",
                    "conclusion": "COMPLETE_FOR_SCOPE",
                    "independent_of_evidence_preparer": True,
                    "methodology": "Compared all eligible official terminal actions with the hashed PIT master.",
                },
                "source_documents": [
                    {
                        "document_id": "closing-8k",
                        "path": "sources/closing-8k.txt",
                        "sha256": sha256_file(source),
                        "source_uri": "https://www.sec.gov/Archives/example.txt",
                        "authority": "SEC_FILING",
                        "provider_id": "sec.edgar",
                        "published_at": "2020-01-06T13:45:00Z",
                        "pinpoint_locator": "Item 2.01, paragraph 3",
                        "extracted_term": "$12.00 cash for each outstanding share",
                        "reviewer_attestation": {
                            "reviewer": "independent-reviewer",
                            "reviewed_at": "2026-08-03T17:00:00Z",
                            "conclusion": "VERIFIED_EXACT_TERM",
                            "independent_of_source_and_price_provider": True,
                        },
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest, master, prices, evidence


def _audit(manifest: Path, master: Path, prices: Path):
    return audit_terminal_settlements(
        evidence_manifest_path=manifest,
        security_master_path=master,
        price_panel_path=prices,
        scope_start=date(2012, 1, 1),
        scope_end=date(2024, 12, 31),
        as_of=AS_OF,
    )


def test_exact_final_cash_can_certify_complete_scope(tmp_path):
    manifest, master, prices, _ = _fixture(tmp_path)
    result = _audit(manifest, master, prices)

    assert result["status"] == "CERTIFIED_READY"
    assert result["terminal_settlement_certified"] is True
    assert result["population_security_count"] == 1
    assert result["certified_security_count"] == 1
    assert result["blockers"] == []
    assert result["verified_terminal_returns"][0]["verified_terminal_return"] == pytest.approx(
        12.0 / 11.0 - 1.0
    )
    assert result["last_trade_used_as_settlement"] is False
    assert result["provider_return_double_count_permitted"] is False


def test_missing_case_specific_evidence_fails_closed(tmp_path):
    manifest, master, prices, evidence = _fixture(tmp_path)
    evidence.write_text("", encoding="utf-8")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["evidence_file"]["sha256"] = sha256_file(evidence)
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = _audit(manifest, master, prices)

    assert result["status"] == "NOT_CERTIFIED"
    assert result["terminal_settlement_certified"] is False
    assert {item["code"] for item in result["blockers"]} == {
        "SETTLEMENT_EVIDENCE_MISSING"
    }


def test_stock_or_contingent_consideration_is_not_flattened_without_exact_valuation(
    tmp_path,
):
    manifest, master, prices, _ = _fixture(
        tmp_path, outcome="FINAL_SECURITY", proceeds=12.0
    )

    result = _audit(manifest, master, prices)

    assert result["status"] == "NOT_CERTIFIED"
    assert "NONCASH_OR_CONTINGENT_OUTCOME_NOT_EXACTLY_VALUED" in {
        item["code"] for item in result["blockers"]
    }
    assert result["verified_terminal_returns"] == []


def test_price_overlap_contract_is_mandatory(tmp_path):
    manifest, master, prices, _ = _fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload.pop("price_basis")
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = _audit(manifest, master, prices)

    assert result["status"] == "NOT_CERTIFIED"
    assert "PRICE_RETURN_OVERLAP_NOT_DISPROVEN" in {
        item["code"] for item in result["blockers"]
    }


def test_separate_prechallenge_price_extract_is_mandatory(tmp_path):
    manifest, master, prices, _ = _fixture(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload.pop("price_extract_contract")
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = _audit(manifest, master, prices)

    assert result["status"] == "NOT_CERTIFIED"
    assert "PRECHALLENGE_PRICE_EXTRACT_NOT_CERTIFIED" in {
        item["code"] for item in result["blockers"]
    }


def test_source_hash_mismatch_fails_closed(tmp_path):
    manifest, master, prices, _ = _fixture(tmp_path)
    (tmp_path / "sources" / "closing-8k.txt").write_text(
        "changed", encoding="utf-8"
    )

    result = _audit(manifest, master, prices)

    assert result["status"] == "NOT_CERTIFIED"
    codes = {item["code"] for item in result["blockers"]}
    assert "SOURCE_DOCUMENT_HASH_MISMATCH" in codes
    assert "TERMINATION_ELIGIBILITY_SOURCE_UNVERIFIED" in codes


def test_empty_population_never_receives_vacuous_certification(tmp_path):
    manifest, master, prices, _ = _fixture(tmp_path)
    result = audit_terminal_settlements(
        evidence_manifest_path=manifest,
        security_master_path=master,
        price_panel_path=prices,
        scope_start=date(2021, 1, 1),
        scope_end=date(2024, 12, 31),
        as_of=AS_OF,
    )

    assert result["status"] == "NOT_CERTIFIED"
    assert result["population_security_count"] == 0
