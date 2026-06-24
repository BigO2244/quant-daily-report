from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.security_master import SymbolResolutionResult
from paper.paper_broker import PaperConfig
from scripts import certify_execution_readiness as readiness


class EnumStyleActive:
    def __str__(self) -> str:
        return "AccountStatus.ACTIVE"


class FakeBroker:
    def __init__(self, account: dict[str, object]) -> None:
        self.account = account
        self.submit_calls = 0
        self.asset_lookups: list[str] = []

    def get_account(self) -> dict[str, object]:
        return dict(self.account)

    def get_asset(self, symbol: str) -> dict[str, object]:
        self.asset_lookups.append(str(symbol).upper())
        return {"symbol": str(symbol).upper(), "status": "active", "tradable": True}

    def submit_market_order(self, *_args, **_kwargs):
        self.submit_calls += 1
        raise AssertionError("certification must not submit market orders")

    def submit_limit_order(self, *_args, **_kwargs):
        self.submit_calls += 1
        raise AssertionError("certification must not submit limit orders")


def _cfg(*, min_trade_dollars: float = 100.0) -> PaperConfig:
    return PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=True,
        min_trade_dollars=min_trade_dollars,
        trading_mode="paper",
    )


def _account(
    *,
    status: object = None,
    cash: str = "552.49",
    equity: str = "10951.63",
    buying_power: str = "31327.56",
) -> dict[str, object]:
    return {
        "status": EnumStyleActive() if status is None else status,
        "cash": cash,
        "equity": equity,
        "portfolio_value": equity,
        "buying_power": buying_power,
    }


def _write_payload(
    root: Path,
    trade_date: str,
    trades: list[dict[str, object]],
    *,
    status: str = "PLANNED",
) -> Path:
    path = root / "outputs" / "precompute" / trade_date / "planned_execution_payload.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "trade_date": trade_date,
        "execution_status": status,
        "planned_trade_count": len(trades),
        "trades": trades,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _incident_trades() -> list[dict[str, object]]:
    return [
        {"ticker": "MO", "side": "SELL", "quantity": 1, "price": 125.17146630208889},
        {"ticker": "NEE", "side": "SELL", "quantity": 1, "price": 107.55845683686977},
        {"ticker": "NSC", "side": "BUY", "quantity": 2, "price": 303.3900146484375},
        {"ticker": "SNPS", "side": "BUY", "quantity": 1, "price": 461.5},
        {"ticker": "VZ", "side": "BUY", "quantity": 11, "price": 46.72999954223633},
    ]


def _patch_symbol_resolution(monkeypatch) -> None:
    def _resolve(trades, **_kwargs):
        return SymbolResolutionResult(
            trades=[dict(trade) for trade in trades],
            status="PASS",
            reason="all_symbols_resolved",
            symbol_aliases_applied={},
            alias_resolutions=[],
            unknown_symbols=[],
            inactive_symbols=[],
            non_tradable_symbols=[],
            stale_universe=False,
            universe_asof_date="2026-06-24",
            security_master_path="test-security-master",
            warnings=[],
        )

    monkeypatch.setattr(readiness, "resolve_trade_plan_symbols", _resolve)


def _certify(
    root: Path,
    broker: FakeBroker,
    *,
    trade_date: str = "2026-06-24",
    min_trade_dollars: float = 100.0,
) -> dict[str, object]:
    return readiness.certify_execution_readiness(
        trade_date=trade_date,
        mode="paper",
        no_submit=True,
        write_artifact=True,
        broker=broker,
        cfg=_cfg(min_trade_dollars=min_trade_dollars),
        repo_root=root,
        now_utc=dt.datetime(2026, 6, 24, 12, 0, tzinfo=dt.timezone.utc),
    )


def test_certification_passes_20260624_fixed_failure_shape(tmp_path: Path, monkeypatch) -> None:
    _patch_symbol_resolution(monkeypatch)
    payload_path = _write_payload(tmp_path, "2026-06-24", _incident_trades())
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["planner_intended_trades_count"] = 14
    payload["execution_eligible_trades_count"] = 5
    payload_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    broker = FakeBroker(_account())

    result = _certify(tmp_path, broker)

    assert result["readiness_status"] == "PASS"
    assert result["planned_trade_count"] == 5
    assert result["sell_count"] == 2
    assert result["buy_count"] == 3
    assert result["buy_budget_basis"] == "broker_buying_power"
    assert result["account_status_clean_for_buying_power"] is True
    assert result["executable_trade_count"] == 5
    assert result["expected_submissions"] == 5
    assert result["broker_submission_invoked"] is False
    assert broker.submit_calls == 0


def test_certification_fails_if_all_planned_trades_are_dropped(tmp_path: Path, monkeypatch) -> None:
    _patch_symbol_resolution(monkeypatch)
    _write_payload(
        tmp_path,
        "2026-06-24",
        [{"ticker": "NSC", "side": "BUY", "quantity": 1, "price": 50.0}],
    )
    broker = FakeBroker(_account())

    result = _certify(tmp_path, broker)

    assert result["readiness_status"] == "FAIL"
    assert result["planned_trade_count"] == 1
    assert result["executable_trade_count"] == 0
    assert result["expected_submissions"] == 0
    assert result["dropped_min_notional_count"] == 1
    assert "planned_trades_dropped_to_zero_expected_submissions" in result["fail_reasons"]
    assert broker.submit_calls == 0


def test_certification_reports_budget_basis_and_budget_skip_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_symbol_resolution(monkeypatch)
    _write_payload(
        tmp_path,
        "2026-06-24",
        [
            {"ticker": "NSC", "side": "BUY", "quantity": 1, "price": 300.0},
            {"ticker": "SNPS", "side": "BUY", "quantity": 1, "price": 400.0},
        ],
    )
    broker = FakeBroker(_account(buying_power="650.00"))

    result = _certify(tmp_path, broker)

    assert result["readiness_status"] == "WARN"
    assert result["buy_budget_basis"] == "broker_buying_power"
    assert result["expected_submissions"] == 1
    assert result["skipped_trade_count"] == 1
    skipped = result["per_trade_diagnostics"]["budget_skipped_orders"]
    assert skipped[0]["ticker"] == "SNPS"
    assert skipped[0]["buy_budget_basis"] == "broker_buying_power"
    assert skipped[0]["account_status"] == "AccountStatus.ACTIVE"
    assert skipped[0]["account_status_clean_for_buying_power"] is True
    assert broker.submit_calls == 0


def test_certification_fails_cash_fallback_when_buying_power_is_valid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_symbol_resolution(monkeypatch)
    _write_payload(
        tmp_path,
        "2026-06-24",
        [{"ticker": "NSC", "side": "BUY", "quantity": 1, "price": 300.0}],
    )
    monkeypatch.setattr(
        readiness,
        "_compute_buy_budget",
        lambda _account, _cfg: (452.49, "cash"),
    )
    broker = FakeBroker(_account())

    result = _certify(tmp_path, broker)

    assert result["readiness_status"] == "FAIL"
    assert result["buy_budget_basis"] == "cash"
    assert "buy_budget_cash_fallback_with_valid_buying_power" in result["fail_reasons"]
    assert broker.submit_calls == 0


def test_certification_emits_per_trade_retained_and_skipped_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_symbol_resolution(monkeypatch)
    _write_payload(
        tmp_path,
        "2026-06-24",
        [
            {"ticker": "NSC", "side": "BUY", "quantity": 1, "price": 300.0},
            {"ticker": "VZ", "side": "BUY", "quantity": 1, "price": 50.0},
        ],
    )
    broker = FakeBroker(_account())

    result = _certify(tmp_path, broker)

    diagnostics = result["per_trade_diagnostics"]
    assert len(diagnostics["execution_filter"]) == 2
    assert any(row["ticker"] == "NSC" and row["retained"] for row in diagnostics["retained"])
    assert any(row["ticker"] == "VZ" and row["skip_reason"] == "min_notional" for row in diagnostics["skipped"])
    assert result["readiness_status"] == "WARN"
    assert broker.submit_calls == 0
