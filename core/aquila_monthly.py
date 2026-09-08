"""Pure prospective Aquila formation and daily quantity-hold contracts.

Inputs are accepted immutable captures, never downloaded or backfilled here.
This module creates intent only; reconciled broker fills establish ownership.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any, Mapping

from core.portfolio_operating_model import content_hash

SCHEMA = "caerus.aquila_quantity_contract.v1"
SHA = re.compile(r"^[0-9a-f]{64}$")


class AquilaContractError(ValueError):
    pass


def _positive(value: Any, name: str, *, zero: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AquilaContractError(f"invalid {name}") from exc
    if not math.isfinite(result) or result < 0 or (not zero and result == 0):
        raise AquilaContractError(f"invalid {name}")
    return result


def _stamp(value: Any) -> dt.datetime:
    try:
        result = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AquilaContractError("invalid timestamp") from exc
    if result.tzinfo is None:
        raise AquilaContractError("timestamp requires timezone")
    return result


def validate_quantity_contract(contract: Mapping[str, Any], *, trade_date: str) -> None:
    if contract.get("schema_version") != SCHEMA or contract.get("trade_date") != trade_date:
        raise AquilaContractError("quantity contract schema or daily freshness mismatch")
    body = dict(contract)
    digest = body.pop("content_hash", None)
    if digest != content_hash(body):
        raise AquilaContractError("quantity contract hash mismatch")
    for field in ("formation_hash", "ownership_snapshot_hash", "ownership_snapshot_sha256"):
        if not SHA.fullmatch(str(contract.get(field) or "")):
            raise AquilaContractError(f"missing {field}")
    if not contract.get("ownership_snapshot_path"):
        raise AquilaContractError("missing ownership snapshot path")
    if not contract.get("formation_id"):
        raise AquilaContractError("missing formation id")
    if _stamp(contract.get("generated_at")).date().isoformat() != trade_date:
        raise AquilaContractError("quantity contract generation date mismatch")
    previous_session = dt.date.fromisoformat(str(contract.get("previous_session")))
    marked_at = _stamp(contract.get("marks_as_of"))
    if previous_session >= dt.date.fromisoformat(trade_date) or marked_at.date() < previous_session or marked_at > _stamp(contract["generated_at"]):
        raise AquilaContractError("quantity contract marks freshness mismatch")
    nav = _positive(contract.get("account_equity"), "account equity")
    formation_session = dt.date.fromisoformat(str(contract.get("formation_session")))
    if formation_session > previous_session:
        raise AquilaContractError("formation session is after last completed session")
    action = contract.get("action")
    expected = {"MONTHLY_REBALANCE": "REBALANCE_WEIGHT", "HOLD_NO_REBALANCE": "FIXED_QUANTITY"}
    if action not in expected or contract.get("sizing_mode") != expected[action]:
        raise AquilaContractError("invalid Aquila action or sizing mode")
    marks = contract.get("marks")
    quantities = contract.get("target_quantities")
    if not isinstance(marks, Mapping) or not isinstance(quantities, Mapping) or not quantities:
        raise AquilaContractError("missing marks or quantities")
    for symbol, qty in quantities.items():
        if not symbol or str(symbol) != str(symbol).upper():
            raise AquilaContractError("invalid execution symbol")
        _positive(qty, "quantity")
        _positive(marks.get(symbol), "mark")
    if sum(float(q)*float(marks[s]) for s,q in quantities.items()) > nav + 1e-8:
        raise AquilaContractError("Aquila holdings exceed account equity")
    if action == "MONTHLY_REBALANCE" and len(quantities) != 10:
        raise AquilaContractError("monthly formation requires ten execution symbols")
    if action == "HOLD_NO_REBALANCE" and not SHA.fullmatch(str(contract.get("monthly_plan_sha256") or "")):
        raise AquilaContractError("missing reconciled monthly plan hash")


def build_aquila_source(*, trade_date: str, previous_session: str, generated_at: str,
                        account_equity: float, marks: Mapping[str, float], marks_as_of: str,
                        ownership: Mapping[str, Any], ranking: Mapping[str, Any] | None = None,
                        monthly_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build today's intent from caller-declared exchange sessions and captured inputs.

    Ownership: trade_date, reconciliation_status=PASS, content_hash, quantities.
    Ranking: accepted=True, formation_id, content_hash, formation_session,
    captured_at, unique descending issuer rows (at least 11), with issuer_id,
    execution_symbol and market_cap. Acceptance is performed upstream.
    Monthly state is the last reconciled fill state, never a proposed target.
    """
    day, prior = dt.date.fromisoformat(trade_date), dt.date.fromisoformat(previous_session)
    if prior >= day:
        raise AquilaContractError("previous session must precede trade date")
    timestamp = _stamp(generated_at)
    if timestamp.date() != day:
        raise AquilaContractError("generation date differs from trade date")
    mark_timestamp = _stamp(marks_as_of)
    if mark_timestamp > timestamp or mark_timestamp.date() < prior:
        raise AquilaContractError("marks are stale or future dated")
    if ownership.get("trade_date") != trade_date or ownership.get("reconciliation_status") != "PASS":
        raise AquilaContractError("ownership stale or unreconciled")
    if not SHA.fullmatch(str(ownership.get("content_hash") or "")):
        raise AquilaContractError("missing ownership hash")
    rebalance = monthly_state is None or day.strftime("%Y-%m") != prior.strftime("%Y-%m")
    nav = _positive(account_equity, "account equity")
    if rebalance:
        if not ranking or ranking.get("accepted") is not True or ranking.get("formation_session") != previous_session:
            raise AquilaContractError("missing accepted completed-session ranking")
        captured = _stamp(ranking.get("captured_at"))
        if captured > timestamp or captured.date() < prior:
            raise AquilaContractError("ranking captured after intent or stale")
        rows = ranking.get("issuers") or []
        if len(rows) < 11:
            raise AquilaContractError("rank cutoff requires at least eleven issuers")
        if len({r["issuer_id"] for r in rows}) != len(rows):
            raise AquilaContractError("duplicate issuer market capitalization")
        for row in rows:
            _positive(row.get("market_cap"), "market cap")
        ordered = sorted(rows, key=lambda r: (-float(r["market_cap"]), str(r["issuer_id"])))
        if list(rows) != ordered:
            raise AquilaContractError("rank ordering or deterministic tie break invalid")
        symbols = [r["execution_symbol"] for r in rows[:10]]
        if len(set(symbols)) != 10:
            raise AquilaContractError("duplicate execution symbol")
        quantities = {s: nav * .05 / _positive(marks.get(s), "mark") for s in symbols}
        formation_id, formation_hash = ranking.get("formation_id"), ranking.get("content_hash")
        formation_session = ranking["formation_session"]
        plan_hash = None
    else:
        if monthly_state.get("status") != "RECONCILED" or monthly_state.get("formation_month") != day.strftime("%Y-%m"):
            raise AquilaContractError("missing reconciled current-month formation state")
        quantities = {s: _positive(q, "frozen quantity", zero=True)
                      for s, q in (monthly_state.get("quantities") or {}).items()}
        quantities = {s: q for s, q in quantities.items() if q > 0}
        owned = {s: _positive(q, "owned quantity", zero=True)
                 for s, q in (ownership.get("quantities") or {}).items()}
        owned = {s: q for s, q in owned.items() if q > 0}
        if quantities != owned:
            raise AquilaContractError("frozen quantities differ from current logical ownership")
        formation_id, formation_hash = monthly_state.get("formation_id"), monthly_state.get("formation_hash")
        formation_session = monthly_state.get("formation_session")
        plan_hash = monthly_state.get("monthly_plan_sha256")
    contract = dict(schema_version=SCHEMA, trade_date=trade_date, generated_at=generated_at,
                    action="MONTHLY_REBALANCE" if rebalance else "HOLD_NO_REBALANCE",
                    sizing_mode="REBALANCE_WEIGHT" if rebalance else "FIXED_QUANTITY",
                    account_equity=nav, marks_as_of=marks_as_of, previous_session=previous_session,
                    marks={s: float(marks[s]) for s in quantities},
                    target_quantities=quantities, ownership_snapshot_hash=ownership["content_hash"],
                    ownership_snapshot_path=ownership.get("source_path"), ownership_snapshot_sha256=ownership.get("source_sha256"),
                    formation_id=formation_id, formation_hash=formation_hash, formation_session=formation_session,
                    monthly_plan_sha256=plan_hash)
    contract["content_hash"] = content_hash(contract)
    validate_quantity_contract(contract, trade_date=trade_date)
    values = {s: q * float(marks[s]) for s, q in quantities.items()}
    gross = sum(values.values())
    if gross <= 0:
        raise AquilaContractError("no reconciled Aquila holdings")
    return dict(strategy_slug="caerus_aquila", source_variant="TOP10_EQUAL", trade_date=trade_date,
                effective_trade_date=trade_date, generated_at_utc=generated_at, data_status="OK",
                observation_status="OK", decision_eligible=True,
                target_weights={s: v/gross for s,v in values.items()}, quantity_contract=contract)
