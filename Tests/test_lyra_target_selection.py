from __future__ import annotations

import copy
import datetime as dt

import pytest

from core.lyra_target_selection import (
    LyraTargetSelectionError,
    build_lyra_target_selection_evidence,
    validate_lyra_target_selection_evidence,
)
from core.sleeve_decision import content_hash
from core.governed_xnys_calendar import previous_xnys_session


def _rows():
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "GAP", "NEW"]
    dates = ["2026-08-24"]
    while len(dates) < 253:
        dates.append(previous_xnys_session(dates[-1]))
    dates.reverse()
    rows = []
    for index, symbol in enumerate(symbols):
        if symbol == "NEW":
            available = dates[-100:]
        elif symbol == "GAP":
            available = [previous_xnys_session(dates[0]), *dates[1:]]
        else:
            available = dates
        for day_index, date in enumerate(available):
            rows.append({
                "date": date, "ticker": symbol,
                "close": 100.0 * ((1.001 + index * 0.0001) ** day_index),
            })
    return symbols, rows


def test_partial_history_member_is_recorded_but_not_ranked() -> None:
    symbols, rows = _rows()
    evidence = build_lyra_target_selection_evidence(
        execution_session="2026-08-25", signal_as_of="2026-08-24",
        captured_at="2026-08-25T11:00:00+00:00",
        source_path="outputs/research/flow_detection_v1/price_panel.parquet",
        source_sha256="a" * 64, universe_freeze_hash="b" * 64,
        universe_source_hash="c" * 64,
        frozen_universe_symbols=symbols, price_rows=rows,
    )
    assert evidence["frozen_member_count"] == 7
    assert evidence["eligible_member_count"] == 5
    new = next(row for row in evidence["member_availability"] if row["symbol"] == "NEW")
    assert new == {
        "symbol": "NEW", "observation_count": 100,
        "first_observation": evidence["close_histories"][-1]["observations"][0]["date"],
        "last_observation": "2026-08-24",
        "status": "INELIGIBLE_INSUFFICIENT_HISTORY",
    }
    assert "NEW" not in {row["symbol"] for row in evidence["ranked_candidates"]}
    gap = next(row for row in evidence["member_availability"] if row["symbol"] == "GAP")
    assert gap["observation_count"] == 253
    assert gap["status"] == "INELIGIBLE_CALENDAR_MISMATCH"
    assert "GAP" not in {row["symbol"] for row in evidence["ranked_candidates"]}
    assert validate_lyra_target_selection_evidence(evidence) == evidence


def test_resealed_omission_or_rank_override_is_rejected() -> None:
    symbols, rows = _rows()
    evidence = build_lyra_target_selection_evidence(
        execution_session="2026-08-25", signal_as_of="2026-08-24",
        captured_at="2026-08-25T11:00:00+00:00",
        source_path="outputs/research/flow_detection_v1/price_panel.parquet",
        source_sha256="a" * 64, universe_freeze_hash="b" * 64,
        universe_source_hash="c" * 64,
        frozen_universe_symbols=symbols, price_rows=rows,
    )
    forged = copy.deepcopy(evidence)
    forged["ranked_candidates"] = forged["ranked_candidates"][1:]
    forged["content_hash"] = content_hash({
        key: value for key, value in forged.items() if key != "content_hash"
    })
    with pytest.raises(LyraTargetSelectionError, match="not recomputed"):
        validate_lyra_target_selection_evidence(forged)


def test_resealed_partial_history_date_reordering_is_rejected() -> None:
    symbols, rows = _rows()
    evidence = build_lyra_target_selection_evidence(
        execution_session="2026-08-25", signal_as_of="2026-08-24",
        captured_at="2026-08-25T11:00:00+00:00",
        source_path="outputs/research/flow_detection_v1/price_panel.parquet",
        source_sha256="a" * 64, universe_freeze_hash="b" * 64,
        universe_source_hash="c" * 64,
        frozen_universe_symbols=symbols, price_rows=rows,
    )
    forged = copy.deepcopy(evidence)
    partial = forged["close_histories"][-1]["observations"]
    partial[0], partial[1] = partial[1], partial[0]
    forged["content_hash"] = content_hash({
        key: value for key, value in forged.items() if key != "content_hash"
    })
    with pytest.raises(LyraTargetSelectionError, match="unique, ordered"):
        validate_lyra_target_selection_evidence(forged)


@pytest.mark.parametrize(
    ("signal_as_of", "execution_session", "message"),
    [
        ("2026-08-21", "2026-08-24", "Monday XNYS"),
        ("2026-08-24", "2026-08-26", "immediate XNYS"),
    ],
)
def test_weekly_signal_and_execution_chronology_fail_closed(
    signal_as_of: str, execution_session: str, message: str,
) -> None:
    symbols, rows = _rows()
    with pytest.raises(LyraTargetSelectionError, match=message):
        build_lyra_target_selection_evidence(
            execution_session=execution_session, signal_as_of=signal_as_of,
            captured_at="2026-08-25T11:00:00+00:00",
            source_path="price-panel.parquet", source_sha256="a" * 64,
            universe_freeze_hash="b" * 64,
            universe_source_hash="c" * 64,
            frozen_universe_symbols=symbols, price_rows=rows,
        )
