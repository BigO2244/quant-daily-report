from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

from research_data.adapters.nasdaq_sharadar import NasdaqSharadarAdapter
from research_data.hydration import BaseHydrationAdapter, HydrationContext, HydrationResult
from scripts.data_hydration.run_data_hydration_swarm import run_swarm


class ExplodingAdapter(BaseHydrationAdapter):
    source_name = "yahoo_chart_public"
    source_type = "free_public_market_data"

    def supports(self, dataset_id: str) -> bool:
        return True

    def hydrate(self, dataset: dict, context: HydrationContext) -> HydrationResult:
        raise RuntimeError("simulated source failure")


def test_adapter_exception_is_logged_and_swarm_continues(tmp_path: Path) -> None:
    payload = run_swarm(
        repo_root=tmp_path,
        limit_sample=True,
        as_of_date="2026-06-24",
        dataset_ids={"ohlcv_prices"},
        adapter_registry={"yahoo_chart_public": ExplodingAdapter()},
    )

    attempts = payload["attempts"]
    assert any(attempt["status"] == "FAILED_UNKNOWN" for attempt in attempts)
    assert any("simulated source failure" in attempt["failure_reason"] for attempt in attempts)
    assert payload["summary"]["dataset_count"] == 1
    assert payload["summary"]["broker_submission_invoked"] is False
    assert (tmp_path / "data" / "hydration_logs" / "latest_hydration_swarm.json").exists()


def test_blocked_dataset_has_user_action(tmp_path: Path) -> None:
    payload = run_swarm(
        repo_root=tmp_path,
        limit_sample=True,
        as_of_date="2026-06-24",
        dataset_ids={"analyst_estimate_revisions"},
        adapter_registry={},
    )

    row = payload["datasets"][0]
    assert row["hydration_succeeded"] is False
    assert row["blocker_reason"]
    assert row["next_best_action"]


def test_sharadar_adapter_reports_expected_env_names_without_secret(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NASDAQ_DATA_LINK_API_KEY", raising=False)
    monkeypatch.delenv("QUANDL_API_KEY", raising=False)
    adapter = NasdaqSharadarAdapter()
    context = HydrationContext(repo_root=tmp_path, as_of_date="2026-06-24", limit_sample=True)
    dataset = {"dataset_id": "corporate_actions", "dataset_name": "Corporate actions", "fr_dh_reference": "FR-DH-013"}

    result = adapter.hydrate(dataset, context)

    assert result.status == "BLOCKED_CREDENTIALS"
    assert "NASDAQ_DATA_LINK_API_KEY" in result.failure_reason
    assert "QUANDL_API_KEY" in result.failure_reason
    assert "secret" not in result.failure_reason.lower()


def test_sharadar_adapter_classifies_429_as_rate_limited(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NASDAQ_DATA_LINK_API_KEY", "not-logged")

    def fake_get(table: str, params: dict, api_key: str) -> dict:
        raise urllib.error.HTTPError(
            url=f"https://data.nasdaq.com/api/v3/datatables/{table}.json",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=io.BytesIO(b""),
        )

    adapter = NasdaqSharadarAdapter(get_fn=fake_get)
    context = HydrationContext(repo_root=tmp_path, as_of_date="2026-06-24", limit_sample=True)
    dataset = {"dataset_id": "corporate_actions", "dataset_name": "Corporate actions", "fr_dh_reference": "FR-DH-013"}

    result = adapter.hydrate(dataset, context)

    assert result.status == "RATE_LIMITED"
    assert "not-logged" not in result.failure_reason


def test_sharadar_adapter_writes_partial_datatable_sample(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUANDL_API_KEY", "not-logged")

    def fake_get(table: str, params: dict, api_key: str) -> dict:
        assert api_key == "not-logged"
        return {
            "datatable": {
                "columns": [{"name": "ticker"}, {"name": "date"}, {"name": "action"}],
                "data": [["AAPL", "2020-08-31", "split"]],
            }
        }

    adapter = NasdaqSharadarAdapter(get_fn=fake_get)
    context = HydrationContext(repo_root=tmp_path, as_of_date="2026-06-24", limit_sample=True)
    dataset = {"dataset_id": "corporate_actions", "dataset_name": "Corporate actions", "fr_dh_reference": "FR-DH-013"}

    result = adapter.hydrate(dataset, context)

    assert result.status == "PARTIAL"
    assert result.records_written == 1
    assert result.artifact_path is not None
    assert Path(result.artifact_path).exists()


def _fundamentals_dataset() -> dict:
    return {"dataset_id": "fundamentals_pit", "dataset_name": "Fundamentals", "fr_dh_reference": "FR-DH-013"}


def _sf1_payload(rows: list[list[object]]) -> dict:
    return {
        "datatable": {
            "columns": [
                {"name": "ticker"},
                {"name": "calendardate"},
                {"name": "datekey"},
                {"name": "reportperiod"},
                {"name": "dimension"},
                {"name": "revenue"},
                {"name": "netinc"},
            ],
            "data": rows,
        }
    }


def test_sharadar_sf1_empty_first_query_success_second_query(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NASDAQ_DATA_LINK_API_KEY", "not-logged")
    calls: list[dict] = []

    def fake_get(table: str, params: dict, api_key: str) -> dict:
        calls.append(dict(params))
        if len(calls) == 1:
            return _sf1_payload([])
        return _sf1_payload([["AAPL", "2024-12-31", "2025-02-01", "2024-12-31", "ARY", 1, 2]])

    adapter = NasdaqSharadarAdapter(get_fn=fake_get)
    context = HydrationContext(repo_root=tmp_path, as_of_date="2026-06-24", limit_sample=True)

    result = adapter.hydrate(_fundamentals_dataset(), context)

    assert result.status == "PARTIAL"
    assert result.records_written == 1
    assert len(calls) == 2
    assert calls[0]["dimension"] == "ARQ"
    assert "dimension" not in calls[1]
    payload = json.loads(Path(result.artifact_path).read_text(encoding="utf-8"))
    assert len(payload["attempted_probes"]) == 2
    assert payload["sample_params"]["ticker"] == "AAPL"
    assert "api_key" not in json.dumps(payload).lower()


def test_sharadar_sf1_success_on_later_unfiltered_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NASDAQ_DATA_LINK_API_KEY", "not-logged")
    calls: list[dict] = []

    def fake_get(table: str, params: dict, api_key: str) -> dict:
        calls.append(dict(params))
        if len(calls) < 6:
            return _sf1_payload([])
        return _sf1_payload([["MSFT", "2024-12-31", "2025-01-29", "2024-12-31", "ARQ", 3, 4]])

    adapter = NasdaqSharadarAdapter(get_fn=fake_get)
    context = HydrationContext(repo_root=tmp_path, as_of_date="2026-06-24", limit_sample=True)

    result = adapter.hydrate(_fundamentals_dataset(), context)

    assert result.status == "PARTIAL"
    assert len(calls) == 6
    assert "ticker" not in calls[-1]
    assert "dimension" not in calls[-1]


def test_sharadar_sf1_true_empty_logs_all_attempted_probes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NASDAQ_DATA_LINK_API_KEY", "not-logged")
    calls: list[dict] = []

    def fake_get(table: str, params: dict, api_key: str) -> dict:
        calls.append(dict(params))
        return _sf1_payload([])

    adapter = NasdaqSharadarAdapter(get_fn=fake_get)
    context = HydrationContext(repo_root=tmp_path, as_of_date="2026-06-24", limit_sample=True)

    result = adapter.hydrate(_fundamentals_dataset(), context)

    assert result.status == "EMPTY_RESULT"
    assert len(calls) == 6
    assert "SHARADAR/SF1" in result.failure_reason
    assert "attempted_probes=" in result.failure_reason
    assert "AAPL" in result.failure_reason
    assert "MSFT" in result.failure_reason
    assert "qopts.columns" in result.failure_reason
    assert "not-logged" not in result.failure_reason
