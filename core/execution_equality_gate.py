from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from paper.run_manager import safe_write_text


SCHEMA_VERSION = "execution_equality_gate.v1"
GATE_VERSION = "pre_trade_equality_gate.observe.v1"
NORMALIZATION_VERSION = "caerus_broker_order_identity.v1"
HASH_ALGORITHM = "sha256"
EXPECTED_SOURCE = "planned_payload_exact"

DECISION_WOULD_PROCEED = "WOULD_PROCEED"
DECISION_HASH_MISMATCH = "WOULD_HALT_HASH_MISMATCH"
DECISION_SOURCE_MISMATCH = "WOULD_HALT_SOURCE_MISMATCH"
DECISION_PRICING_ASOF_MISMATCH = "WOULD_HALT_PRICING_ASOF_MISMATCH"
DECISION_OBSERVE_ERROR = "OBSERVE_ERROR"


_MARKET_TYPES = {"", "MKT", "MARKET"}
_LIMIT_TYPES = {"LMT", "LIMIT"}
_STOP_TYPES = {"STP", "STOP"}
_STOP_LIMIT_TYPES = {"STP_LMT", "STOP_LIMIT", "STOP-LIMIT"}
_SELL_ALIASES = {"SELL", "CLOSE", "REDUCE"}


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_value(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key not in mapping:
            continue
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _decimal_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid_decimal:{value!r}") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"non_finite_decimal:{value!r}")
    if decimal_value == 0:
        return "0"
    text = format(decimal_value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "y", "on"}


def _order_type(value: Any) -> str:
    raw = _text(value).upper()
    if raw in _LIMIT_TYPES:
        return "LIMIT"
    if raw in _STOP_TYPES:
        return "STOP"
    if raw in _STOP_LIMIT_TYPES:
        return "STOP_LIMIT"
    if raw in _MARKET_TYPES:
        return "MKT"
    return raw


def _side(value: Any) -> str:
    raw = _text(value).upper()
    if raw in _SELL_ALIASES:
        return "SELL"
    if raw == "BUY":
        return "BUY"
    return raw


def normalize_order_identity(order: Mapping[str, Any]) -> dict[str, Any]:
    """Return the broker-action identity for one order without mutating input."""
    if not isinstance(order, Mapping):
        raise TypeError("order_identity_requires_mapping")

    symbol = _text(_first_value(order, ("symbol", "ticker"))).upper()
    side = _side(_first_value(order, ("side", "action")))
    order_type = _order_type(_first_value(order, ("order_type", "type")))
    tif = _text(_first_value(order, ("time_in_force", "tif")) or "DAY").upper()
    quantity_type_raw = _text(order.get("quantity_type")).lower()

    quantity_value = _first_value(order, ("shares", "qty", "quantity"))
    notional_value = _first_value(order, ("notional", "notional_value"))
    quantity_type = "notional" if quantity_type_raw == "notional" else "shares"
    if quantity_type_raw not in {"", "share", "shares", "qty", "quantity", "notional"}:
        quantity_type = quantity_type_raw
    if quantity_value in (None, "") and notional_value not in (None, ""):
        quantity_type = "notional"

    normalized: dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "quantity_type": quantity_type,
        "order_type": order_type,
        "time_in_force": tif,
        "extended_hours": _bool_value(order.get("extended_hours")),
    }

    if not symbol:
        raise ValueError("order_symbol_missing")
    if side not in {"BUY", "SELL"}:
        raise ValueError(f"order_side_invalid:{side or 'missing'}")

    if quantity_type == "notional":
        notional = _decimal_string(notional_value)
        if notional is None:
            raise ValueError("order_notional_missing")
        normalized["notional"] = notional
    else:
        quantity = _decimal_string(quantity_value)
        if quantity is None:
            raise ValueError("order_quantity_missing")
        normalized["quantity"] = quantity

    limit_price = _first_value(order, ("limit_price", "limit"))
    if limit_price is None and order_type == "LIMIT":
        limit_price = order.get("price")
    if limit_price is not None:
        normalized["limit_price"] = _decimal_string(limit_price)

    stop_price = _first_value(order, ("stop_price", "stop"))
    if stop_price is not None:
        normalized["stop_price"] = _decimal_string(stop_price)

    return normalized


def canonical_order_set_envelope(
    orders: Sequence[Mapping[str, Any]],
    *,
    planning_price_basis: Any,
    pricing_asof: Any,
) -> dict[str, Any]:
    normalized_orders = [normalize_order_identity(order) for order in list(orders or [])]
    normalized_orders.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return {
        "normalization_version": NORMALIZATION_VERSION,
        "order_count": len(normalized_orders),
        "planning_price_basis": _text(planning_price_basis).upper(),
        "pricing_asof": _text(pricing_asof),
        "orders": normalized_orders,
    }


def serialize_order_set(envelope: Mapping[str, Any]) -> str:
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_order_set(
    orders: Sequence[Mapping[str, Any]],
    *,
    planning_price_basis: Any,
    pricing_asof: Any,
) -> tuple[str, dict[str, Any], str]:
    envelope = canonical_order_set_envelope(
        orders,
        planning_price_basis=planning_price_basis,
        pricing_asof=pricing_asof,
    )
    serialized = serialize_order_set(envelope)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest(), envelope, serialized


def _hash_short(value: str | None) -> str | None:
    return value[:12] if value else None


def _first_divergence(planned: Mapping[str, Any], submission: Mapping[str, Any]) -> dict[str, Any] | None:
    planned_orders = list(planned.get("orders") or [])
    submission_orders = list(submission.get("orders") or [])
    if len(planned_orders) != len(submission_orders):
        return {
            "type": "order_count",
            "planned_count": len(planned_orders),
            "submission_count": len(submission_orders),
        }
    for index, (planned_order, submission_order) in enumerate(zip(planned_orders, submission_orders)):
        if planned_order == submission_order:
            continue
        keys = sorted(set(planned_order.keys()) | set(submission_order.keys()))
        changed_fields = [
            key
            for key in keys
            if planned_order.get(key) != submission_order.get(key)
        ]
        return {
            "type": "order_identity",
            "index": index,
            "changed_fields": changed_fields,
            "planned": planned_order,
            "submission": submission_order,
        }
    return None


def _divergence_summary(first_divergence: Mapping[str, Any] | None, *, hashes_equal: bool | None) -> str:
    if hashes_equal is True:
        return "no order identity divergence"
    if not first_divergence:
        return "hash divergence without localized order difference"
    if first_divergence.get("type") == "order_count":
        return (
            "order_count planned={planned} submission={submission}".format(
                planned=first_divergence.get("planned_count"),
                submission=first_divergence.get("submission_count"),
            )
        )
    return (
        "first_order_difference index={index} fields={fields}".format(
            index=first_divergence.get("index"),
            fields=",".join(str(item) for item in first_divergence.get("changed_fields") or []) or "unknown",
        )
    )


def _base_artifact(
    *,
    run_id: str,
    trade_date: str,
    execution_source: Any,
    planning_price_basis: Any,
    pricing_asof_planned: Any,
    pricing_asof_context: Any,
    artifact_refs: Mapping[str, Any] | None,
    timestamp_utc: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "gate_version": GATE_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "mode": "observe",
        "enforced": False,
        "submission_proceeded": True,
        "decision": DECISION_OBSERVE_ERROR,
        "would_block": None,
        "halt_reason": None,
        "observe_reason": None,
        "execution_source": _text(execution_source) or None,
        "expected_source": EXPECTED_SOURCE,
        "planned_hash": None,
        "submission_hash": None,
        "hashes_equal": None,
        "planned_hash_short": None,
        "submission_hash_short": None,
        "order_count_planned": None,
        "order_count_submission": None,
        "pricing_asof_planned": _text(pricing_asof_planned) or None,
        "pricing_asof_context": _text(pricing_asof_context) or None,
        "pricing_asof_match": None,
        "planning_price_basis": _text(planning_price_basis).upper() or None,
        "first_divergence": None,
        "divergence_summary": "observe evaluation did not complete",
        "observe_error": None,
        "run_id": str(run_id or ""),
        "trade_date": str(trade_date or ""),
        "timestamp_utc": timestamp_utc or _utc_now_iso(),
        "artifact_refs": dict(artifact_refs or {}),
    }


def evaluate_observe_decision(
    *,
    planned_orders: Sequence[Mapping[str, Any]],
    submission_orders: Sequence[Mapping[str, Any]],
    execution_source: Any,
    planning_price_basis: Any,
    pricing_asof_planned: Any,
    pricing_asof_context: Any,
    run_id: str = "",
    trade_date: str = "",
    artifact_refs: Mapping[str, Any] | None = None,
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    artifact = _base_artifact(
        run_id=run_id,
        trade_date=trade_date,
        execution_source=execution_source,
        planning_price_basis=planning_price_basis,
        pricing_asof_planned=pricing_asof_planned,
        pricing_asof_context=pricing_asof_context,
        artifact_refs=artifact_refs,
        timestamp_utc=timestamp_utc,
    )
    try:
        planned_hash, planned_envelope, _planned_serialized = hash_order_set(
            planned_orders,
            planning_price_basis=planning_price_basis,
            pricing_asof=pricing_asof_planned,
        )
        submission_hash, submission_envelope, _submission_serialized = hash_order_set(
            submission_orders,
            planning_price_basis=planning_price_basis,
            pricing_asof=pricing_asof_context,
        )
        hashes_equal = planned_hash == submission_hash
        pricing_asof_match = _text(pricing_asof_planned) == _text(pricing_asof_context)
        source_match = _text(execution_source) == EXPECTED_SOURCE
        first_divergence = _first_divergence(planned_envelope, submission_envelope)

        if not source_match:
            decision = DECISION_SOURCE_MISMATCH
            halt_reason = "execution_source_mismatch"
            observe_reason = None
        elif not pricing_asof_match:
            decision = DECISION_PRICING_ASOF_MISMATCH
            halt_reason = "pricing_asof_mismatch"
            observe_reason = None
        elif not hashes_equal:
            decision = DECISION_HASH_MISMATCH
            halt_reason = "order_hash_mismatch"
            observe_reason = None
        else:
            decision = DECISION_WOULD_PROCEED
            halt_reason = None
            observe_reason = "hashes_match"

        artifact.update(
            {
                "decision": decision,
                "would_block": decision != DECISION_WOULD_PROCEED,
                "halt_reason": halt_reason,
                "observe_reason": observe_reason,
                "planned_hash": planned_hash,
                "submission_hash": submission_hash,
                "hashes_equal": hashes_equal,
                "planned_hash_short": _hash_short(planned_hash),
                "submission_hash_short": _hash_short(submission_hash),
                "order_count_planned": int(planned_envelope.get("order_count") or 0),
                "order_count_submission": int(submission_envelope.get("order_count") or 0),
                "pricing_asof_match": pricing_asof_match,
                "first_divergence": first_divergence,
                "divergence_summary": _divergence_summary(first_divergence, hashes_equal=hashes_equal),
                "observe_error": None,
            }
        )
    except Exception as exc:
        artifact.update(
            {
                "decision": DECISION_OBSERVE_ERROR,
                "would_block": None,
                "observe_reason": "observe_error",
                "observe_error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
            }
        )
        try:
            artifact["order_count_planned"] = len(list(planned_orders or []))
            artifact["order_count_submission"] = len(list(submission_orders or []))
        except Exception:
            pass
    return artifact


def build_observe_error_artifact(
    *,
    run_id: str,
    trade_date: str,
    execution_source: Any,
    planning_price_basis: Any,
    pricing_asof_planned: Any,
    pricing_asof_context: Any,
    observe_error: BaseException | str,
    artifact_refs: Mapping[str, Any] | None = None,
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    artifact = _base_artifact(
        run_id=run_id,
        trade_date=trade_date,
        execution_source=execution_source,
        planning_price_basis=planning_price_basis,
        pricing_asof_planned=pricing_asof_planned,
        pricing_asof_context=pricing_asof_context,
        artifact_refs=artifact_refs,
        timestamp_utc=timestamp_utc,
    )
    if isinstance(observe_error, BaseException):
        error_type = observe_error.__class__.__name__
        error_message = str(observe_error)
    else:
        error_type = "ObserveError"
        error_message = str(observe_error)
    artifact.update(
        {
            "decision": DECISION_OBSERVE_ERROR,
            "would_block": None,
            "observe_reason": "observe_error",
            "observe_error": {
                "type": error_type,
                "message": error_message,
            },
        }
    )
    return artifact


def render_equality_gate_markdown(artifact: Mapping[str, Any]) -> str:
    observe_error = artifact.get("observe_error")
    lines = [
        "# Pre-trade Equality Gate Observe",
        "",
        f"- run_id: `{artifact.get('run_id') or ''}`",
        f"- trade_date: `{artifact.get('trade_date') or ''}`",
        f"- mode: `{artifact.get('mode') or ''}`",
        f"- enforced: `{str(bool(artifact.get('enforced'))).lower()}`",
        f"- submission_proceeded: `{str(bool(artifact.get('submission_proceeded'))).lower()}`",
        f"- decision: `{artifact.get('decision') or ''}`",
        f"- would_block: `{artifact.get('would_block')}`",
        f"- execution_source: `{artifact.get('execution_source') or ''}`",
        f"- expected_source: `{artifact.get('expected_source') or ''}`",
        f"- planned_hash: `{artifact.get('planned_hash_short') or ''}`",
        f"- submission_hash: `{artifact.get('submission_hash_short') or ''}`",
        f"- hashes_equal: `{artifact.get('hashes_equal')}`",
        f"- order_count_planned: `{artifact.get('order_count_planned')}`",
        f"- order_count_submission: `{artifact.get('order_count_submission')}`",
        f"- pricing_asof_planned: `{artifact.get('pricing_asof_planned') or ''}`",
        f"- pricing_asof_context: `{artifact.get('pricing_asof_context') or ''}`",
        f"- pricing_asof_match: `{artifact.get('pricing_asof_match')}`",
        f"- planning_price_basis: `{artifact.get('planning_price_basis') or ''}`",
        f"- divergence_summary: {artifact.get('divergence_summary') or ''}",
    ]
    if observe_error:
        if isinstance(observe_error, Mapping):
            lines.append(f"- observe_error: `{observe_error.get('type')}: {observe_error.get('message')}`")
        else:
            lines.append(f"- observe_error: `{observe_error}`")
    lines.extend(["", "Observe only; broker submission was not changed by this artifact.", ""])
    return "\n".join(lines)


def write_equality_gate_artifacts(
    *,
    run_root: str | Path,
    artifact: Mapping[str, Any],
) -> tuple[Path, Path]:
    root = Path(run_root)
    json_path = root / "equality_gate.json"
    md_path = root / "equality_gate.md"
    safe_write_text(
        json_path,
        json.dumps(dict(artifact), indent=2, sort_keys=True, default=str) + "\n",
        allow_overwrite=True,
    )
    safe_write_text(
        md_path,
        render_equality_gate_markdown(artifact),
        allow_overwrite=True,
    )
    return json_path, md_path


def write_equality_gate_observe_artifacts(
    *,
    run_root: str | Path,
    planned_orders: Sequence[Mapping[str, Any]],
    submission_orders: Sequence[Mapping[str, Any]],
    execution_source: Any,
    planning_price_basis: Any,
    pricing_asof_planned: Any,
    pricing_asof_context: Any,
    run_id: str,
    trade_date: str,
    artifact_refs: Mapping[str, Any] | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    artifact = evaluate_observe_decision(
        planned_orders=planned_orders,
        submission_orders=submission_orders,
        execution_source=execution_source,
        planning_price_basis=planning_price_basis,
        pricing_asof_planned=pricing_asof_planned,
        pricing_asof_context=pricing_asof_context,
        run_id=run_id,
        trade_date=trade_date,
        artifact_refs=artifact_refs,
    )
    json_path, md_path = write_equality_gate_artifacts(run_root=run_root, artifact=artifact)
    return json_path, md_path, artifact


def write_equality_gate_observe_error_artifacts(
    *,
    run_root: str | Path,
    run_id: str,
    trade_date: str,
    execution_source: Any,
    planning_price_basis: Any,
    pricing_asof_planned: Any,
    pricing_asof_context: Any,
    observe_error: BaseException | str,
    artifact_refs: Mapping[str, Any] | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    artifact = build_observe_error_artifact(
        run_id=run_id,
        trade_date=trade_date,
        execution_source=execution_source,
        planning_price_basis=planning_price_basis,
        pricing_asof_planned=pricing_asof_planned,
        pricing_asof_context=pricing_asof_context,
        observe_error=observe_error,
        artifact_refs=artifact_refs,
    )
    json_path, md_path = write_equality_gate_artifacts(run_root=run_root, artifact=artifact)
    return json_path, md_path, artifact


def classify_equality_gate_observe_status(artifact: Mapping[str, Any] | None) -> str:
    if not isinstance(artifact, Mapping) or not artifact:
        return "unavailable"
    decision = _text(artifact.get("decision"))
    if decision == DECISION_WOULD_PROCEED:
        return "ok"
    if decision == DECISION_OBSERVE_ERROR:
        return "observe_degraded"
    if decision.startswith("WOULD_HALT_"):
        return "divergence_observed"
    return "unavailable"


def operator_summary_block_from_artifact(
    artifact: Mapping[str, Any],
    *,
    artifact_ref: str | Path,
) -> dict[str, Any]:
    decision = _text(artifact.get("decision")) or None
    divergence_brief = _text(
        artifact.get("divergence_summary")
        or artifact.get("halt_reason")
        or artifact.get("observe_reason")
    )
    if decision == DECISION_SOURCE_MISMATCH:
        divergence_brief = "execution_source mismatch"
    elif decision == DECISION_PRICING_ASOF_MISMATCH:
        divergence_brief = "pricing_asof mismatch"
    elif decision == DECISION_OBSERVE_ERROR:
        observe_error = artifact.get("observe_error")
        if isinstance(observe_error, Mapping):
            divergence_brief = f"observe error: {observe_error.get('type')}"
        else:
            divergence_brief = "observe error"
    return {
        "mode": "observe",
        "decision": decision,
        "would_block": artifact.get("would_block"),
        "hashes_equal": artifact.get("hashes_equal"),
        "planned_hash_short": artifact.get("planned_hash_short"),
        "submission_hash_short": artifact.get("submission_hash_short"),
        "order_count_planned": artifact.get("order_count_planned"),
        "order_count_submission": artifact.get("order_count_submission"),
        "pricing_asof_match": artifact.get("pricing_asof_match"),
        "execution_source": artifact.get("execution_source"),
        "divergence_brief": divergence_brief or "unavailable",
        "artifact_ref": str(artifact_ref),
    }
