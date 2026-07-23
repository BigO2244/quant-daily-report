from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.factory import ResearchBoundaryError
from projects.alpha_lab.options_proxy.boundary import build_boundary_attestation
from projects.alpha_lab.options_proxy.config import default_config_path, load_config
from projects.alpha_lab.options_proxy.evaluation import build_scoreboard, evaluate_signal
from projects.alpha_lab.options_proxy.features import (
    black_scholes_delta,
    build_feature_rows,
    build_signal,
)
from projects.alpha_lab.options_proxy.automation import (
    maturation_readiness,
    mature_all,
    run_daily,
)
from projects.alpha_lab.options_proxy.market_calendar import session_for
from projects.alpha_lab.options_proxy.pipeline import collect_and_build, collect_snapshot, mature_signal
from projects.alpha_lab.options_proxy.storage import (
    output_root,
    read_json,
    research_run_lock,
    require_research_path,
    write_immutable_json,
)


UTC = timezone.utc
CONFIG = load_config(default_config_path())


def _contract(
    *,
    symbol: str,
    option_type: str,
    strike: float,
    volume: int,
    iv: float,
    expiration: str,
    index: int,
):
    return {
        "contract_symbol": "{}{}{}".format(symbol, option_type[0].upper(), index),
        "option_type": option_type,
        "expiration": expiration,
        "strike": strike,
        "bid": 1.0,
        "ask": 1.2,
        "last_price": 1.1,
        "volume": volume,
        "open_interest": 500,
        "implied_volatility": iv,
        "last_trade_at": "2026-07-15T19:00:00Z",
        "in_the_money": False,
        "contract_size": "REGULAR",
        "currency": "USD",
    }


def _chain(
    symbol: str,
    *,
    as_of: date,
    bullish: bool = True,
    call_iv: float = 0.36,
):
    expiration = (as_of + timedelta(days=30)).isoformat()
    contracts = []
    for index in range(12):
        contracts.append(
            _contract(
                symbol=symbol,
                option_type="call",
                strike=105.0,
                volume=100 if bullish else 10,
                iv=call_iv if bullish else 0.30,
                expiration=expiration,
                index=index,
            )
        )
        contracts.append(
            _contract(
                symbol=symbol,
                option_type="put",
                strike=97.0,
                volume=10 if bullish else 100,
                iv=0.30 if bullish else 0.36,
                expiration=expiration,
                index=index,
            )
        )
    return {
        "symbol": symbol,
        "spot": 100.0,
        "expirations_considered": [expiration],
        "contracts": contracts,
    }


def _snapshot(as_of: date, *, prior_ready: bool = True):
    chains = [_chain(symbol, as_of=as_of) for symbol in CONFIG.symbols]
    return {
        "schema_version": "caerus_options_proxy_snapshot_v1",
        "snapshot_id": "snapshot-{}".format(as_of.isoformat()),
        "snapshot_hash": "a" * 64,
        "as_of_date": as_of.isoformat(),
        "available_at": "2026-07-15T20:01:00Z",
        "collection_window_status": "DECISION_TIME_ELIGIBLE",
        "source_success_count": len(chains),
        "chains": chains,
        "config_hash": CONFIG.config_hash,
    }


def _prior_features(level: float = 0.01):
    return {
        symbol: {"call_minus_put_iv_skew_level": level}
        for symbol in CONFIG.symbols
    }


def test_config_is_standalone_research_only_and_not_a_strategy_registry():
    assert CONFIG.automation_scope == "standalone_research_only"
    assert CONFIG.production_scheduler_integration is False
    assert CONFIG.hypothesis_id == "HYP-2026-004"
    assert "DOES_NOT_SATISFY" in CONFIG.experiment_relationship
    assert len(CONFIG.symbols) == 50
    assert CONFIG.benchmark_symbol == "SPY"


def test_black_scholes_delta_has_expected_call_put_signs():
    call = black_scholes_delta(
        spot=100,
        strike=100,
        time_years=30 / 365,
        volatility=0.30,
        option_type="call",
        risk_free_rate=0.0,
        dividend_yield=0.0,
    )
    put = black_scholes_delta(
        spot=100,
        strike=100,
        time_years=30 / 365,
        volatility=0.30,
        option_type="put",
        risk_free_rate=0.0,
        dividend_yield=0.0,
    )
    assert 0 < call < 1
    assert -1 < put < 0
    assert call - put == pytest.approx(1.0)


def test_first_snapshot_collects_features_but_fails_closed_without_prior_skew():
    snapshot = _snapshot(date(2026, 7, 15))
    rows = build_feature_rows(snapshot, config=CONFIG)
    assert len(rows) == 50
    assert all("prior_iv_skew_unavailable" in row["blockers"] for row in rows)
    signal = build_signal(snapshot=snapshot, feature_rows=rows, config=CONFIG)
    assert signal["decision_eligible"] is False
    assert signal["research_targets"] == []
    assert signal["alpha_claim_permitted"] is False


def test_second_snapshot_builds_ranked_research_targets_without_orders():
    snapshot = _snapshot(date(2026, 7, 16))
    rows = build_feature_rows(
        snapshot,
        config=CONFIG,
        previous_features=_prior_features(),
    )
    signal = build_signal(snapshot=snapshot, feature_rows=rows, config=CONFIG)
    assert signal["decision_eligible"] is True
    assert signal["scoreable_coverage"] == pytest.approx(1.0)
    assert len(signal["research_targets"]) == 5
    assert sum(row["research_target_weight"] for row in signal["research_targets"]) == pytest.approx(0.5)
    assert signal["cash_weight"] == pytest.approx(0.5)
    assert signal["trading_or_order_artifact"] is False
    assert signal["alpha_claim_permitted"] is False
    assert "no_trade_aggressor_side" in signal["limitations"]


class _FakeSource:
    source_version = "test-source"

    def collect_chain(self, *, symbol, as_of_date, minimum_dte, maximum_dte):
        assert minimum_dte == CONFIG.minimum_dte
        assert maximum_dte == CONFIG.maximum_dte
        return _chain(
            symbol,
            as_of=as_of_date,
            call_iv=0.36 if as_of_date.day == 15 else 0.38,
        )

    def daily_bars(self, *, symbol, start, end):
        return [
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": 100.0,
                "close": 101.0 + index,
                "volume": 1_000_000,
            }
            for index in range(6)
        ]


class _CountingSource(_FakeSource):
    def __init__(self):
        self.collect_calls = 0

    def collect_chain(self, **kwargs):
        self.collect_calls += 1
        return super().collect_chain(**kwargs)


class _RetryOnceSource(_FakeSource):
    def __init__(self):
        self.calls = {}

    def collect_chain(self, **kwargs):
        symbol = kwargs["symbol"]
        self.calls[symbol] = self.calls.get(symbol, 0) + 1
        if symbol == CONFIG.symbols[0] and self.calls[symbol] == 1:
            raise TimeoutError("transient")
        return super().collect_chain(**kwargs)


def test_calendar_fails_closed_and_handles_official_2026_sessions():
    assert session_for(date(2026, 7, 4)).status == "CLOSED_WEEKEND"
    assert session_for(date(2026, 7, 3)).status == "CLOSED_HOLIDAY"
    early = session_for(date(2026, 11, 27))
    assert early.status == "OPEN_EARLY_CLOSE"
    assert early.decision_not_before.hour == 12
    assert early.decision_not_before.minute == 45
    with pytest.raises(Exception, match="not approved"):
        session_for(date(2029, 1, 2))


def test_collection_retries_transient_symbol_error(tmp_path):
    source = _RetryOnceSource()
    result = collect_snapshot(
        repo_root=tmp_path,
        config=CONFIG,
        source=source,
        collected_at=datetime(2026, 7, 15, 20, 0, tzinfo=UTC),
        clock=lambda: datetime(2026, 7, 15, 20, 1, tzinfo=UTC),
        sleeper=lambda _seconds: None,
    )
    assert result["snapshot"]["source_error_count"] == 0
    assert result["snapshot"]["attempts_by_symbol"][CONFIG.symbols[0]] == 2


def test_daily_run_is_idempotent_and_writes_health(tmp_path):
    source = _CountingSource()
    first_now = datetime(2026, 7, 15, 20, 0, tzinfo=UTC)
    first = run_daily(
        repo_root=tmp_path,
        config=CONFIG,
        source=source,
        now=first_now,
        clock=lambda: first_now + timedelta(minutes=1),
        sleeper=lambda _seconds: None,
    )
    assert first["health"]["overall_status"] == "HEALTHY"
    assert first["health"]["observation"]["status"] == "COLLECTED"
    assert source.collect_calls == len(CONFIG.symbols)
    second_now = first_now + timedelta(minutes=2)
    second = run_daily(
        repo_root=tmp_path,
        config=CONFIG,
        source=source,
        now=second_now,
        clock=lambda: second_now + timedelta(minutes=1),
        sleeper=lambda _seconds: None,
    )
    assert second["health"]["observation"]["status"] == "SKIPPED_ALREADY_OBSERVED"
    assert source.collect_calls == len(CONFIG.symbols)
    assert second["health_path"].exists()


def test_research_lock_rejects_overlapping_run(tmp_path):
    with research_run_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already holds"):
            with research_run_lock(tmp_path):
                pass


def test_pipeline_writes_only_immutable_research_artifacts(tmp_path):
    first_start = datetime(2026, 7, 15, 20, 0, tzinfo=UTC)
    first_available = datetime(2026, 7, 15, 20, 1, tzinfo=UTC)
    first = collect_and_build(
        repo_root=tmp_path,
        config=CONFIG,
        source=_FakeSource(),
        collected_at=first_start,
        clock=lambda: first_available,
        sleeper=lambda _seconds: None,
    )
    assert first["signal"]["decision_eligible"] is False
    second_start = datetime(2026, 7, 16, 20, 0, tzinfo=UTC)
    second_available = datetime(2026, 7, 16, 20, 1, tzinfo=UTC)
    second = collect_and_build(
        repo_root=tmp_path,
        config=CONFIG,
        source=_FakeSource(),
        collected_at=second_start,
        clock=lambda: second_available,
        sleeper=lambda _seconds: None,
    )
    assert second["signal"]["decision_eligible"] is True
    root = output_root(tmp_path)
    assert second["snapshot_path"].is_relative_to(root)
    assert second["features_path"].is_relative_to(root)
    assert second["signal_path"].is_relative_to(root)
    with pytest.raises(FileExistsError):
        write_immutable_json(
            second["signal_path"],
            {"different": True},
            repo_root=tmp_path,
        )
    with pytest.raises(ResearchBoundaryError):
        require_research_path(tmp_path / "paper" / "orders.json", repo_root=tmp_path)

    matured = mature_signal(
        repo_root=tmp_path,
        config=CONFIG,
        signal_path=second["signal_path"],
        through_date=date(2026, 7, 30),
        source=_FakeSource(),
        generated_at=datetime(2026, 7, 30, 22, 0, tzinfo=UTC),
    )
    assert matured["evaluation"]["status"] == "MATURE_COMPLETE"
    assert matured["scoreboard"]["mature_observation_count"] == 1
    rematured = mature_signal(
        repo_root=tmp_path,
        config=CONFIG,
        signal_path=second["signal_path"],
        through_date=date(2026, 7, 31),
        source=_FakeSource(),
        generated_at=datetime(2026, 7, 31, 22, 0, tzinfo=UTC),
    )
    assert rematured["evaluation_path"] != matured["evaluation_path"]
    assert rematured["scoreboard"]["mature_observation_count"] == 1
    batch = mature_all(
        repo_root=tmp_path,
        config=CONFIG,
        through_date=date(2026, 8, 3),
        source=_FakeSource(),
        generated_at=datetime(2026, 8, 3, 22, 0, tzinfo=UTC),
    )
    assert any(row["reason"] == "ALREADY_MATURE" for row in batch["batch"]["skipped"])
    assert batch["batch"]["errors"] == []


def test_maturation_status_explains_remaining_sessions_and_first_ready_date(tmp_path):
    first = collect_and_build(
        repo_root=tmp_path,
        config=CONFIG,
        source=_FakeSource(),
        collected_at=datetime(2026, 7, 15, 20, 0, tzinfo=UTC),
        clock=lambda: datetime(2026, 7, 15, 20, 1, tzinfo=UTC),
        sleeper=lambda _seconds: None,
    )
    assert first["signal"]["decision_eligible"] is False
    second = collect_and_build(
        repo_root=tmp_path,
        config=CONFIG,
        source=_FakeSource(),
        collected_at=datetime(2026, 7, 16, 20, 0, tzinfo=UTC),
        clock=lambda: datetime(2026, 7, 16, 20, 1, tzinfo=UTC),
        sleeper=lambda _seconds: None,
    )
    assert second["signal"]["decision_eligible"] is True
    status = maturation_readiness(
        repo_root=tmp_path,
        config=CONFIG,
        through_date=date(2026, 7, 22),
    )
    by_date = {row["decision_date"]: row for row in status["cohorts"]}
    assert by_date["2026-07-15"]["status"] == "SIGNAL_NOT_ELIGIBLE"
    assert by_date["2026-07-16"]["status"] == "WAITING_FOR_HOLDING_WINDOW"
    assert by_date["2026-07-16"]["later_sessions_observed"] == 4
    assert by_date["2026-07-16"]["sessions_remaining"] == 1
    assert by_date["2026-07-16"]["earliest_maturity_date"] == "2026-07-23"
    assert status["next_maturity_date"] == "2026-07-23"


def test_forward_evaluation_is_separate_and_never_permits_alpha_claim():
    snapshot = _snapshot(date(2026, 7, 16))
    rows = build_feature_rows(
        snapshot,
        config=CONFIG,
        previous_features=_prior_features(),
    )
    signal = build_signal(snapshot=snapshot, feature_rows=rows, config=CONFIG)
    bars = {
        symbol: [
            {
                "date": (date(2026, 7, 17) + timedelta(days=index)).isoformat(),
                "open": 100.0,
                "close": 101.0 + index,
            }
            for index in range(5)
        ]
        for symbol in CONFIG.symbols
    }
    evaluation = evaluate_signal(signal=signal, bars_by_symbol=bars, config=CONFIG)
    assert evaluation["status"] == "MATURE_COMPLETE"
    assert evaluation["return_data_used_for_signal"] is False
    assert evaluation["alpha_claim_permitted"] is False
    scoreboard = build_scoreboard([evaluation])
    assert scoreboard["status"] == "INSUFFICIENT_OBSERVATIONS"
    assert scoreboard["overlapping_cohort_returns_are_not_portfolio_nav"] is True
    assert scoreboard["spend_authorized"] is False
    assert scoreboard["promotion_authorized"] is False


def test_boundary_attestation_and_ast_have_no_trading_surface():
    package_root = Path(__file__).parents[1] / "options_proxy"
    attestation = build_boundary_attestation(package_root)
    assert attestation["production_boundary_status"] == "CLEAN"
    assert attestation["findings"] == []
    assert attestation["production_scheduler_or_cron_modified"] is False
    assert attestation["standalone_research_automation_permitted"] is True
    assert attestation["capital_path_touched"] is False

    forbidden_strings = (
        "from brokers",
        "import brokers",
        "from paper",
        "import paper",
        "submit_market_order(",
        "submit_option_market_order(",
    )
    for path in package_root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden_strings), path
        ast.parse(text)
