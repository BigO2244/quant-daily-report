"""Point-in-time Lyra target-selection evidence.

This contract recomputes the canonical Lyra momentum score across every member
of the frozen governed universe.  A legacy rank table is comparison evidence,
never an input to the recomputation.
"""

from __future__ import annotations

import copy
import datetime as dt
import math
from typing import Any, Mapping, Sequence

from core.sleeve_decision import content_hash
from core.governed_xnys_calendar import (
    is_xnys_session,
    next_xnys_session,
    xnys_session_window,
)


LYRA_TARGET_SELECTION_SCHEMA = "caerus.lyra_target_selection_evidence.v1"
LYRA_TARGET_SELECTION_FORMULA = "0.5_R12_1_PLUS_0.3_R6_1_PLUS_0.2_R3_V1"
REQUIRED_CLOSE_OBSERVATIONS = 253
TOP_N = 5


class LyraTargetSelectionError(ValueError):
    """Raised when a complete point-in-time Lyra ranking cannot be proven."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return content_hash(body)


def _date(value: Any, *, label: str) -> str:
    raw = str(value or "")
    try:
        dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise LyraTargetSelectionError(f"{label} must be an ISO date") from exc
    return raw


def _sha(value: Any, *, label: str) -> str:
    raw = str(value or "")
    if len(raw) != 64 or any(char not in "0123456789abcdef" for char in raw):
        raise LyraTargetSelectionError(f"{label} must be a lowercase SHA-256")
    return raw


def _finite(value: Any, *, label: str, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise LyraTargetSelectionError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LyraTargetSelectionError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or (positive and number <= 0.0):
        raise LyraTargetSelectionError(f"{label} is invalid")
    return number


def _signals(closes: Sequence[float]) -> tuple[float, float, float, float]:
    if len(closes) != REQUIRED_CLOSE_OBSERVATIONS:
        raise LyraTargetSelectionError("target selection requires exactly 253 closes")
    r3 = closes[-1] / closes[-4] - 1.0
    r6_1 = closes[-22] / closes[-127] - 1.0
    r12_1 = closes[-22] / closes[-253] - 1.0
    score = 0.5 * r12_1 + 0.3 * r6_1 + 0.2 * r3
    return tuple(round(value, 12) for value in (r3, r6_1, r12_1, score))


def _expected_xnys_dates(signal_as_of: str, *, count: int) -> list[str]:
    if not is_xnys_session(signal_as_of):
        raise LyraTargetSelectionError("signal_as_of must be an XNYS session")
    return xnys_session_window(signal_as_of, count=count)


def _validate_weekly_chronology(*, signal_as_of: str, execution_session: str) -> None:
    signal = dt.date.fromisoformat(signal_as_of)
    if (
        signal.weekday() != 0
        or not is_xnys_session(signal_as_of)
        or next_xnys_session(signal_as_of) != execution_session
    ):
        raise LyraTargetSelectionError(
            "Lyra signal must be a Monday XNYS close followed by the immediate XNYS session"
        )


def build_lyra_target_selection_evidence(
    *, execution_session: str, signal_as_of: str, captured_at: str,
    source_path: str, source_sha256: str, universe_freeze_hash: str,
    universe_source_hash: str, frozen_universe_symbols: Sequence[str],
    price_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    execution_session = _date(execution_session, label="execution_session")
    signal_as_of = _date(signal_as_of, label="signal_as_of")
    if signal_as_of >= execution_session:
        raise LyraTargetSelectionError("selection signal must precede execution")
    _validate_weekly_chronology(
        signal_as_of=signal_as_of, execution_session=execution_session
    )
    try:
        captured = dt.datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise LyraTargetSelectionError("captured_at must be an ISO timestamp") from exc
    if captured.tzinfo is None or captured.date().isoformat() != execution_session:
        raise LyraTargetSelectionError("selection capture must occur on execution session")
    if not source_path or source_path.startswith("/") or ".." in source_path.split("/"):
        raise LyraTargetSelectionError("source_path must be a safe logical path")
    source_sha256 = _sha(source_sha256, label="source_sha256")
    freeze_hash = _sha(universe_freeze_hash, label="universe_freeze_hash")
    universe_hash = _sha(universe_source_hash, label="universe_source_hash")
    symbols = sorted({
        str(symbol or "").strip().upper() for symbol in frozen_universe_symbols
    })
    if len(symbols) < TOP_N or any(not symbol for symbol in symbols):
        raise LyraTargetSelectionError("eligible universe is invalid")
    by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    seen: set[tuple[str, str]] = set()
    for raw in price_rows:
        if not isinstance(raw, Mapping):
            raise LyraTargetSelectionError("price row must be an object")
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
        if symbol not in by_symbol:
            continue
        date = _date(raw.get("date"), label=f"{symbol}.date")
        if date > signal_as_of:
            continue
        key = (symbol, date)
        if key in seen:
            raise LyraTargetSelectionError("duplicate symbol/date in selection panel")
        seen.add(key)
        by_symbol[symbol].append({
            "date": date,
            "close": round(_finite(raw.get("close"), label=f"{symbol}.close", positive=True), 10),
        })
    histories: list[dict[str, Any]] = []
    availability: list[dict[str, Any]] = []
    eligible: list[str] = []
    expected_dates = _expected_xnys_dates(
        signal_as_of, count=REQUIRED_CLOSE_OBSERVATIONS
    )
    for symbol in symbols:
        rows = sorted(by_symbol[symbol], key=lambda row: row["date"])[-REQUIRED_CLOSE_OBSERVATIONS:]
        dates = [row["date"] for row in rows]
        if len(dates) != len(set(dates)):
            raise LyraTargetSelectionError("selection history dates are duplicated")
        is_eligible = dates == expected_dates
        status = (
            "ELIGIBLE_FULL_253"
            if is_eligible
            else (
                "INELIGIBLE_INSUFFICIENT_HISTORY"
                if len(rows) < REQUIRED_CLOSE_OBSERVATIONS
                else "INELIGIBLE_CALENDAR_MISMATCH"
            )
        )
        availability.append({
            "symbol": symbol, "observation_count": len(rows),
            "first_observation": dates[0] if dates else None,
            "last_observation": dates[-1] if dates else None,
            "status": status,
        })
        if is_eligible:
            eligible.append(symbol)
        histories.append({"symbol": symbol, "observations": rows})
    if len(eligible) < TOP_N:
        raise LyraTargetSelectionError("fewer than five universe members are signal ready")
    candidates = []
    for history in histories:
        if history["symbol"] not in eligible:
            continue
        r3, r6_1, r12_1, score = _signals(
            [float(row["close"]) for row in history["observations"]]
        )
        candidates.append({
            "symbol": history["symbol"], "r3": r3, "r6_1": r6_1,
            "r12_1": r12_1, "momentum_score": score,
        })
    candidates.sort(key=lambda row: (-row["momentum_score"], row["symbol"]))
    ranked = [
        {**row, "rank": index, "selected": index <= TOP_N}
        for index, row in enumerate(candidates, start=1)
    ]
    selected = [row["symbol"] for row in ranked if row["selected"]]
    targets = [{"symbol": symbol, "target_weight": 0.2} for symbol in sorted(selected)]
    body = {
        "schema_version": LYRA_TARGET_SELECTION_SCHEMA,
        "evidence_id": "pending", "execution_session": execution_session,
        "signal_as_of": signal_as_of, "captured_at": captured_at,
        "source_path": source_path, "source_sha256": source_sha256,
        "universe_freeze_hash": freeze_hash,
        "universe_source_hash": universe_hash,
        "formula_id": LYRA_TARGET_SELECTION_FORMULA,
        "required_close_observations": REQUIRED_CLOSE_OBSERVATIONS,
        "top_n": TOP_N, "frozen_universe_symbols": symbols,
        "frozen_member_count": len(symbols), "eligible_symbols": eligible,
        "eligible_member_count": len(eligible), "observation_dates": expected_dates,
        "member_availability": availability,
        "close_histories": histories,
        "availability_hash": content_hash(availability),
        "ranked_candidates": ranked, "target_rows": targets,
        "status": "PASS", "execution_authority": False,
        "activation_authority": False,
    }
    seed = _hash(body)
    body["evidence_id"] = f"lyra-target-selection:{execution_session}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_lyra_target_selection_evidence(body)


def validate_lyra_target_selection_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "evidence_id", "execution_session", "signal_as_of",
        "captured_at", "source_path", "source_sha256", "universe_freeze_hash",
        "universe_source_hash", "formula_id", "required_close_observations",
        "top_n", "frozen_universe_symbols", "frozen_member_count",
        "eligible_symbols", "eligible_member_count", "observation_dates",
        "member_availability", "close_histories", "availability_hash",
        "ranked_candidates", "target_rows",
        "status", "execution_authority", "activation_authority", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise LyraTargetSelectionError("target-selection evidence fields differ")
    if (
        payload.get("schema_version") != LYRA_TARGET_SELECTION_SCHEMA
        or payload.get("formula_id") != LYRA_TARGET_SELECTION_FORMULA
        or payload.get("required_close_observations") != REQUIRED_CLOSE_OBSERVATIONS
        or payload.get("top_n") != TOP_N or payload.get("status") != "PASS"
        or payload.get("execution_authority") is not False
        or payload.get("activation_authority") is not False
    ):
        raise LyraTargetSelectionError("target-selection evidence semantics differ")
    execution = _date(payload.get("execution_session"), label="execution_session")
    signal = _date(payload.get("signal_as_of"), label="signal_as_of")
    if signal >= execution:
        raise LyraTargetSelectionError("target-selection chronology differs")
    _validate_weekly_chronology(
        signal_as_of=signal, execution_session=execution
    )
    try:
        captured = dt.datetime.fromisoformat(
            str(payload.get("captured_at") or "").replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise LyraTargetSelectionError("captured_at must be an ISO timestamp") from exc
    if captured.tzinfo is None or captured.date().isoformat() != execution:
        raise LyraTargetSelectionError(
            "target-selection capture must occur on execution session"
        )
    source_path = payload.get("source_path")
    if (
        not isinstance(source_path, str) or not source_path
        or source_path.startswith("/") or ".." in source_path.split("/")
    ):
        raise LyraTargetSelectionError("source_path must be a safe logical path")
    for field in ("source_sha256", "universe_freeze_hash", "universe_source_hash"):
        _sha(payload.get(field), label=field)
    symbols = payload.get("frozen_universe_symbols")
    eligible = payload.get("eligible_symbols")
    availability = payload.get("member_availability")
    histories = payload.get("close_histories")
    dates = payload.get("observation_dates")
    expected_dates = _expected_xnys_dates(
        signal, count=REQUIRED_CLOSE_OBSERVATIONS
    )
    if (
        not isinstance(symbols, list) or symbols != sorted(set(symbols))
        or len(symbols) < TOP_N or payload.get("frozen_member_count") != len(symbols)
        or not isinstance(eligible, list) or eligible != sorted(set(eligible))
        or len(eligible) < TOP_N or payload.get("eligible_member_count") != len(eligible)
        or not isinstance(dates, list) or len(dates) != REQUIRED_CLOSE_OBSERVATIONS
        or dates != expected_dates
        or not isinstance(histories, list) or len(histories) != len(symbols)
        or not isinstance(availability, list) or len(availability) != len(symbols)
    ):
        raise LyraTargetSelectionError("target-selection PIT coverage differs")
    recomputed = []
    recomputed_availability = []
    recomputed_eligible = []
    for expected_symbol, history in zip(symbols, histories):
        if not isinstance(history, Mapping) or set(history) != {"symbol", "observations"}:
            raise LyraTargetSelectionError("target-selection history shape differs")
        if history["symbol"] != expected_symbol:
            raise LyraTargetSelectionError("target-selection history ordering differs")
        observations = history["observations"]
        if not isinstance(observations, list) or len(observations) > REQUIRED_CLOSE_OBSERVATIONS:
            raise LyraTargetSelectionError("target-selection history length differs")
        observed_dates: list[str] = []
        closes = []
        for row in observations:
            if not isinstance(row, Mapping) or set(row) != {"date", "close"}:
                raise LyraTargetSelectionError("target-selection close row differs")
            observed_dates.append(_date(row["date"], label="observation date"))
            closes.append(_finite(row["close"], label="close", positive=True))
        if observed_dates != sorted(set(observed_dates)) or any(
            value > signal for value in observed_dates
        ):
            raise LyraTargetSelectionError(
                "target-selection history dates must be unique, ordered, and point-in-time"
            )
        is_eligible = observed_dates == expected_dates
        recomputed_availability.append({
            "symbol": expected_symbol, "observation_count": len(observations),
            "first_observation": observed_dates[0] if observed_dates else None,
            "last_observation": observed_dates[-1] if observed_dates else None,
            "status": (
                "ELIGIBLE_FULL_253"
                if is_eligible
                else (
                    "INELIGIBLE_INSUFFICIENT_HISTORY"
                    if len(observations) < REQUIRED_CLOSE_OBSERVATIONS
                    else "INELIGIBLE_CALENDAR_MISMATCH"
                )
            ),
        })
        if not is_eligible:
            continue
        recomputed_eligible.append(expected_symbol)
        r3, r6_1, r12_1, score = _signals(closes)
        recomputed.append({
            "symbol": expected_symbol, "r3": r3, "r6_1": r6_1,
            "r12_1": r12_1, "momentum_score": score,
        })
    recomputed.sort(key=lambda row: (-row["momentum_score"], row["symbol"]))
    ranked = [
        {**row, "rank": index, "selected": index <= TOP_N}
        for index, row in enumerate(recomputed, start=1)
    ]
    selected = sorted(row["symbol"] for row in ranked if row["selected"])
    expected_targets = [{"symbol": symbol, "target_weight": 0.2} for symbol in selected]
    if (
        availability != recomputed_availability or eligible != recomputed_eligible
        or payload.get("ranked_candidates") != ranked
        or payload.get("target_rows") != expected_targets
    ):
        raise LyraTargetSelectionError("target selection is not recomputed from PIT closes")
    if payload.get("availability_hash") != content_hash(availability):
        raise LyraTargetSelectionError("target-selection availability hash differs")
    if payload.get("content_hash") != _hash(payload):
        raise LyraTargetSelectionError("target-selection content_hash mismatch")
    return copy.deepcopy(dict(payload))


__all__ = [
    "LYRA_TARGET_SELECTION_FORMULA", "LYRA_TARGET_SELECTION_SCHEMA",
    "LyraTargetSelectionError", "build_lyra_target_selection_evidence",
    "validate_lyra_target_selection_evidence",
]
