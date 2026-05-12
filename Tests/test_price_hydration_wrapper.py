from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from core.price_hydration import build_status_payload, cache_max_date, resolve_completed_trading_day


ET = ZoneInfo("America/New_York")
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "hydrate_shadow_price_cache_vm.sh"


def test_monday_morning_resolves_prior_friday() -> None:
    now = dt.datetime(2026, 5, 4, 8, 15, tzinfo=ET)

    assert resolve_completed_trading_day(now=now) == "2026-05-01"


def test_weekday_1500_resolves_prior_day() -> None:
    now = dt.datetime(2026, 5, 5, 15, 0, tzinfo=ET)

    assert resolve_completed_trading_day(now=now) == "2026-05-04"


def test_weekday_1830_resolves_same_day() -> None:
    now = dt.datetime(2026, 5, 5, 18, 30, tzinfo=ET)

    assert resolve_completed_trading_day(now=now) == "2026-05-05"


def test_weekend_resolves_prior_trading_day() -> None:
    now = dt.datetime(2026, 5, 10, 12, 0, tzinfo=ET)

    assert resolve_completed_trading_day(now=now) == "2026-05-08"


def test_missing_parquet_status_failed(tmp_path: Path) -> None:
    max_date = cache_max_date(tmp_path / "missing.parquet")
    payload = build_status_payload(as_of_date="2026-05-05", max_cache_date=max_date)

    assert payload["status"] == "FAILED"
    assert payload["reason"] == "price cache missing or unreadable"


def test_partial_cache_status_partial(tmp_path: Path) -> None:
    cache = tmp_path / "price_panel.parquet"
    pd.DataFrame(
        [
            {"date": "2026-05-04", "ticker": "SPY", "close": 100.0},
            {"date": "2026-05-04", "ticker": "AAA", "close": 50.0},
        ]
    ).to_parquet(cache, index=False)

    payload = build_status_payload(as_of_date="2026-05-05", max_cache_date=cache_max_date(cache))

    assert payload["status"] == "PARTIAL"
    assert payload["max_cache_date"] == "2026-05-04"


def test_nonzero_hydration_with_verified_cache_is_partial() -> None:
    payload = build_status_payload(
        as_of_date="2026-05-05",
        max_cache_date="2026-05-05",
        hydration_exit_code=137,
    )

    assert payload["status"] == "PARTIAL"
    assert "cache coverage is verified" in payload["reason"]


def test_script_does_not_import_execution_modules() -> None:
    text = SCRIPT_PATH.read_text(encoding="utf-8")

    forbidden = [
        "run_precomputed_alpaca_execution",
        "execute_options_overlay",
        "submit_market_order",
        "submit_option_market_order",
        "brokers/alpaca_broker",
    ]
    assert not any(token in text for token in forbidden)
