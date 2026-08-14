"""Canonical contracts for the Caerus portfolio operating model.

The module is deliberately independent of signal-generation and broker APIs.
It turns the registry control-plane observations into immutable daily sleeve
decisions, applies the owner-configured capital budgets, and produces one
account-level allocation while retaining causal sleeve contributions.

Investment intent has exactly one direction of travel:

    session -> sleeve decisions -> portfolio allocation -> Decision authority

No function in this module submits orders or promotes a sleeve.  Enabling a
new capital sleeve remains an explicit registry/governance change.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


SESSION_SCHEMA = "caerus.session_manifest.v1"
SLEEVE_DECISION_SCHEMA = "caerus.sleeve_decision.v1"
SLEEVE_DECISION_BATCH_SCHEMA = "caerus.sleeve_decision_batch.v1"
ALLOCATION_SCHEMA = "caerus.portfolio_allocation.v1"
AUDIT_MANIFEST_SCHEMA = "caerus.portfolio_audit_manifest.v1"
ALLOCATION_POLICY_SCHEMA = "caerus.paper_allocation_policy.v1"

SLEEVE_OUTCOMES = frozenset(
    {"RECOMMENDATION", "NO_TRADE", "UNAVAILABLE", "FROZEN", "OBSERVATION"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PortfolioOperatingModelError(RuntimeError):
    """Raised when a canonical portfolio boundary cannot be proven."""


def canonical_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PortfolioOperatingModelError(
            f"portfolio artifact is not canonical JSON: {exc}"
        ) from exc


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def file_hash(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PortfolioOperatingModelError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PortfolioOperatingModelError(f"{path} must contain a JSON object")
    return payload


def _iso_date(value: object, *, label: str) -> str:
    raw = str(value or "").strip()
    try:
        dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise PortfolioOperatingModelError(f"{label} must be an ISO date") from exc
    return raw


def _iso_timestamp(value: object, *, label: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PortfolioOperatingModelError(
            f"{label} must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise PortfolioOperatingModelError(f"{label} must include a timezone")
    return raw


def _finite_weight(value: object, *, label: str, maximum: float = 1.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioOperatingModelError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or result < 0.0 or result > maximum + 1e-12:
        raise PortfolioOperatingModelError(f"{label} is outside [0, {maximum}]")
    return result


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _resolve_path(repo_root: Path, value: object) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise PortfolioOperatingModelError("source artifact path is blank")
    path = Path(raw)
    return path if path.is_absolute() else repo_root / path


def _normalized_weights(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], float]:
    raw_weights = payload.get("target_weights")
    if not isinstance(raw_weights, Mapping):
        return [], 0.0
    weights: dict[str, float] = {}
    source_cash = 0.0
    for raw_symbol, raw_value in raw_weights.items():
        symbol = str(raw_symbol or "").strip().upper()
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise PortfolioOperatingModelError(
                f"nonnumeric target weight for {symbol or '<blank>'}"
            ) from exc
        if not math.isfinite(value) or value < 0.0:
            raise PortfolioOperatingModelError(
                f"invalid target weight for {symbol or '<blank>'}"
            )
        if symbol == "CASH":
            source_cash += value
        elif symbol and value > 0.0:
            if symbol in weights:
                raise PortfolioOperatingModelError(
                    f"duplicate target symbol in sleeve recommendation: {symbol}"
                )
            weights[symbol] = value
    gross = sum(weights.values())
    if gross <= 0.0:
        return [], source_cash
    return (
        [
            {
                "symbol": symbol,
                "target_weight": round(weight / gross, 12),
                "source_target_weight": weight,
            }
            for symbol, weight in sorted(weights.items())
        ],
        source_cash,
    )


def build_session_manifest(
    *,
    trade_date: str,
    run_id: str,
    as_of: str,
    repo_root: Path,
    inputs: Sequence[Mapping[str, Any]],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build one immutable description of all data admitted to a session."""

    trade_date = _iso_date(trade_date, label="trade_date")
    as_of = _iso_timestamp(as_of, label="as_of")
    rows: list[dict[str, Any]] = []
    for raw in inputs:
        name = str(raw.get("name") or "").strip()
        path = _resolve_path(repo_root, raw.get("path"))
        required = bool(raw.get("required", True))
        if not name:
            raise PortfolioOperatingModelError("session input name is required")
        exists = path.is_file()
        if required and not exists:
            raise PortfolioOperatingModelError(
                f"required session input is missing: {path}"
            )
        declared_hash = str(raw.get("sha256") or "").strip().lower()
        observed_hash = file_hash(path) if exists else None
        if declared_hash and declared_hash != observed_hash:
            raise PortfolioOperatingModelError(
                f"session input hash mismatch: {path}"
            )
        source_as_of = str(raw.get("as_of") or "").strip() or None
        rows.append(
            {
                "name": name,
                "path": _display_path(repo_root, path),
                "sha256": observed_hash,
                "required": required,
                "exists": exists,
                "as_of": source_as_of,
                "freshness_status": str(
                    raw.get("freshness_status") or ("FRESH" if exists else "MISSING")
                ).upper(),
            }
        )
    body = {
        "schema_version": SESSION_SCHEMA,
        "session_id": f"session:{trade_date}:{content_hash({'run_id': run_id, 'as_of': as_of, 'inputs': rows})[:24]}",
        "trade_date": trade_date,
        "run_id": str(run_id),
        "as_of": as_of,
        "created_at": created_at or dt.datetime.now(dt.timezone.utc).isoformat(),
        "inputs": sorted(rows, key=lambda row: (row["name"], row["path"])),
    }
    body["content_hash"] = content_hash(body)
    return body


def build_sleeve_decision_batch(
    *,
    evaluation_batch: Mapping[str, Any],
    session_manifest: Mapping[str, Any],
    repo_root: Path,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Convert each control-plane envelope to one standardized daily result."""

    trade_date = _iso_date(evaluation_batch.get("trade_date"), label="trade_date")
    if trade_date != str(session_manifest.get("trade_date") or ""):
        raise PortfolioOperatingModelError(
            "sleeve evaluation and session trade dates differ"
        )
    session_hash = str(session_manifest.get("content_hash") or "")
    if not _SHA256.fullmatch(session_hash):
        raise PortfolioOperatingModelError("session manifest hash is invalid")
    envelopes = evaluation_batch.get("envelopes")
    if not isinstance(envelopes, list):
        raise PortfolioOperatingModelError("sleeve evaluation envelopes are missing")
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for envelope in envelopes:
        if not isinstance(envelope, Mapping):
            raise PortfolioOperatingModelError("sleeve envelope must be an object")
        sleeve_id = str(envelope.get("sleeve_id") or "").strip()
        if not sleeve_id or sleeve_id in seen:
            raise PortfolioOperatingModelError(
                f"blank or duplicate sleeve decision identity: {sleeve_id!r}"
            )
        seen.add(sleeve_id)
        evaluation = envelope.get("evaluation") or {}
        opportunity = envelope.get("opportunity") or {}
        eligibility = envelope.get("eligibility") or {}
        lifecycle = envelope.get("lifecycle") or {}
        status = str(evaluation.get("status") or "").upper()
        strategy_type = str(envelope.get("strategy_type") or "")
        if bool(lifecycle.get("frozen")):
            outcome = "FROZEN"
        elif strategy_type in {"benchmark", "reference_portfolio"}:
            outcome = "OBSERVATION" if status == "OK" else "UNAVAILABLE"
        elif status == "OK" and bool(opportunity.get("available")):
            outcome = "RECOMMENDATION"
        elif status == "NO_OPPORTUNITY":
            outcome = "NO_TRADE"
        else:
            outcome = "UNAVAILABLE"
        source_rows = (envelope.get("provenance") or {}).get("source_artifacts") or []
        source_refs: list[dict[str, Any]] = []
        target_rows: list[dict[str, Any]] = []
        source_cash_weight = 0.0
        for source in source_rows:
            if not isinstance(source, Mapping):
                continue
            path = _resolve_path(repo_root, source.get("path"))
            exists = path.is_file()
            observed_hash = file_hash(path) if exists else None
            declared_hash = str(source.get("sha256") or "").strip().lower() or None
            if declared_hash and observed_hash != declared_hash:
                outcome = "UNAVAILABLE"
            source_refs.append(
                {
                    "path": _display_path(repo_root, path),
                    "sha256": observed_hash,
                    "exists": exists,
                }
            )
            if exists and not target_rows:
                target_rows, source_cash_weight = _normalized_weights(
                    _read_object(path)
                )
        if outcome == "RECOMMENDATION" and not target_rows:
            allocation_weight = opportunity.get("allocation_weight")
            if allocation_weight is None:
                outcome = "UNAVAILABLE"
        decision_body = {
            "schema_version": SLEEVE_DECISION_SCHEMA,
            "trade_date": trade_date,
            "session_id": session_manifest.get("session_id"),
            "session_hash": session_hash,
            "sleeve_id": sleeve_id,
            "display_name": envelope.get("display_name"),
            "strategy_type": strategy_type,
            "family": envelope.get("family"),
            "lifecycle_status": lifecycle.get("status"),
            "mode": (
                "PAPER"
                if bool(eligibility.get("paper_execution_eligible"))
                else "SHADOW"
                if str(lifecycle.get("status") or "") == "shadow"
                else "RESEARCH"
            ),
            "outcome": outcome,
            "capital_eligible": bool(eligibility.get("capital_eligible")),
            "execution_eligible": bool(
                eligibility.get("paper_execution_eligible")
            ),
            "effective_as_of": opportunity.get("effective_trade_date")
            or trade_date,
            "source_variant": opportunity.get("source_variant"),
            "source_cash_weight": source_cash_weight,
            "target_rows": target_rows,
            "allocation_hint": opportunity.get("allocation_weight"),
            "source_artifacts": source_refs,
            "reason_codes": list(envelope.get("reason_codes") or []),
            "message": evaluation.get("message"),
        }
        decision_hash = content_hash(decision_body)
        decision_body["decision_id"] = (
            f"sleeve-decision:{trade_date}:{sleeve_id}:{decision_hash[:24]}"
        )
        decision_body["content_hash"] = content_hash(decision_body)
        decisions.append(decision_body)
    expected = list(evaluation_batch.get("expected_non_frozen_sleeve_ids") or [])
    actual = [row["sleeve_id"] for row in decisions]
    if expected != actual:
        raise PortfolioOperatingModelError(
            "daily sleeve decisions do not cover the complete evaluated registry"
        )
    return {
        "schema_version": SLEEVE_DECISION_BATCH_SCHEMA,
        "trade_date": trade_date,
        "session_id": session_manifest.get("session_id"),
        "session_hash": session_hash,
        "generated_at": generated_at or dt.datetime.now(dt.timezone.utc).isoformat(),
        "complete_registry_coverage": True,
        "expected_sleeve_ids": expected,
        "outcome_counts": {
            outcome: sum(1 for row in decisions if row["outcome"] == outcome)
            for outcome in sorted(SLEEVE_OUTCOMES)
        },
        "decisions": decisions,
        "content_hash": content_hash(decisions),
    }


def allocate_portfolio(
    *,
    decision_batch: Mapping[str, Any],
    allocation_policy: Mapping[str, Any],
    allocated_at: str | None = None,
) -> dict[str, Any]:
    """Apply configured risk budgets and retain each sleeve's causal claim."""

    if allocation_policy.get("schema_version") != ALLOCATION_POLICY_SCHEMA:
        raise PortfolioOperatingModelError("unsupported paper allocation policy")
    if str(allocation_policy.get("method") or "") != "configured_risk_budget":
        raise PortfolioOperatingModelError("unsupported paper allocation method")
    if str(allocation_policy.get("unavailable_policy") or "") != "fail_closed":
        raise PortfolioOperatingModelError(
            "paper allocation must fail closed on unavailable capital sleeves"
        )
    cash_weight = _finite_weight(
        allocation_policy.get("target_cash_weight"),
        label="target_cash_weight",
    )
    raw_budgets = allocation_policy.get("sleeve_risk_budgets")
    if not isinstance(raw_budgets, Mapping) or not raw_budgets:
        raise PortfolioOperatingModelError("sleeve risk budgets are required")
    budgets = {
        str(sleeve_id): _finite_weight(value, label=f"budget.{sleeve_id}")
        for sleeve_id, value in raw_budgets.items()
    }
    if abs(sum(budgets.values()) - 1.0) > 1e-10:
        raise PortfolioOperatingModelError("sleeve risk budgets must sum to one")
    decisions = {
        str(row.get("sleeve_id") or ""): row
        for row in decision_batch.get("decisions") or []
        if isinstance(row, Mapping)
    }
    capital_decisions = {
        sleeve_id: row
        for sleeve_id, row in decisions.items()
        if bool(row.get("capital_eligible"))
    }
    if set(budgets) != set(capital_decisions):
        raise PortfolioOperatingModelError(
            "allocation budget identities must exactly match capital-eligible sleeves"
        )
    investable = 1.0 - cash_weight
    symbol_contributions: dict[str, list[dict[str, Any]]] = {}
    allocation_rows: list[dict[str, Any]] = []
    for sleeve_id in sorted(budgets):
        decision = capital_decisions[sleeve_id]
        if decision.get("outcome") != "RECOMMENDATION":
            raise PortfolioOperatingModelError(
                f"capital sleeve {sleeve_id} is not recommendation-ready"
            )
        target_rows = decision.get("target_rows")
        if not isinstance(target_rows, list) or not target_rows:
            raise PortfolioOperatingModelError(
                f"capital sleeve {sleeve_id} has no target recommendation"
            )
        budget = budgets[sleeve_id]
        allocation_rows.append(
            {
                "sleeve_id": sleeve_id,
                "risk_budget": budget,
                "account_target_weight": round(budget * investable, 12),
                "decision_id": decision.get("decision_id"),
                "decision_hash": decision.get("content_hash"),
            }
        )
        for target in target_rows:
            symbol = str(target.get("symbol") or "").strip().upper()
            sleeve_target = _finite_weight(
                target.get("target_weight"),
                label=f"{sleeve_id}.{symbol}.target_weight",
            )
            contribution = round(investable * budget * sleeve_target, 12)
            if contribution <= 0.0:
                continue
            symbol_contributions.setdefault(symbol, []).append(
                {
                    "sleeve_id": sleeve_id,
                    "target_weight": contribution,
                    "sleeve_internal_weight": sleeve_target,
                    "decision_id": decision.get("decision_id"),
                    "decision_hash": decision.get("content_hash"),
                }
            )
    targets = []
    for symbol in sorted(symbol_contributions):
        contributions = sorted(
            symbol_contributions[symbol], key=lambda row: row["sleeve_id"]
        )
        dominant = max(
            contributions,
            key=lambda row: (float(row["target_weight"]), row["sleeve_id"]),
        )["sleeve_id"]
        targets.append(
            {
                "symbol": symbol,
                "ticker": symbol,
                "sleeve": dominant,
                "target_weight": round(
                    sum(float(row["target_weight"]) for row in contributions), 12
                ),
                "sleeve_contributions": contributions,
            }
        )
    if not targets:
        raise PortfolioOperatingModelError("allocation produced no invested targets")
    gross = sum(float(row["target_weight"]) for row in targets)
    if abs(gross - investable) > 1e-9:
        raise PortfolioOperatingModelError(
            f"allocated gross {gross} does not equal investable weight {investable}"
        )
    body = {
        "schema_version": ALLOCATION_SCHEMA,
        "trade_date": decision_batch.get("trade_date"),
        "session_id": decision_batch.get("session_id"),
        "session_hash": decision_batch.get("session_hash"),
        "allocator_id": str(allocation_policy.get("allocator_id") or ""),
        "allocator_version": str(allocation_policy.get("allocator_version") or ""),
        "method": "configured_risk_budget",
        "unavailable_policy": "fail_closed",
        "target_cash_weight": cash_weight,
        "invested_target_weight": investable,
        "sleeve_allocations": allocation_rows,
        "targets": targets,
        "decision_batch_hash": decision_batch.get("content_hash"),
        "allocated_at": allocated_at or dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    allocation_seed_hash = content_hash(body)
    body["allocation_id"] = (
        f"allocation:{body['trade_date']}:{allocation_seed_hash[:24]}"
    )
    body["content_hash"] = content_hash(body)
    return body


def build_audit_manifest(
    *,
    trade_date: str,
    session_id: str,
    approved_target_hash: str,
    repo_root: Path,
    artifacts: Sequence[Mapping[str, Any]],
    generated_at: str | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for raw in artifacts:
        name = str(raw.get("name") or "").strip()
        path = _resolve_path(repo_root, raw.get("path"))
        if not name or not path.is_file():
            raise PortfolioOperatingModelError(
                f"audit artifact missing or unnamed: {name or path}"
            )
        rows.append(
            {
                "name": name,
                "path": _display_path(repo_root, path),
                "sha256": file_hash(path),
            }
        )
    body = {
        "schema_version": AUDIT_MANIFEST_SCHEMA,
        "trade_date": _iso_date(trade_date, label="trade_date"),
        "session_id": str(session_id),
        "approved_target_hash": str(approved_target_hash),
        "generated_at": generated_at or dt.datetime.now(dt.timezone.utc).isoformat(),
        "artifacts": sorted(rows, key=lambda row: row["name"]),
        "status": "SEALED",
    }
    body["content_hash"] = content_hash(body)
    return body


def validate_operating_model_lineage(
    *,
    session_manifest: Mapping[str, Any],
    decision_batch: Mapping[str, Any],
    allocation: Mapping[str, Any],
) -> list[str]:
    failures: list[str] = []
    if session_manifest.get("schema_version") != SESSION_SCHEMA:
        failures.append("operating_model:session_schema")
    session_hash = str(session_manifest.get("content_hash") or "")
    session_body = dict(session_manifest)
    session_body.pop("content_hash", None)
    if session_hash != content_hash(session_body):
        failures.append("operating_model:session_hash")
    if decision_batch.get("schema_version") != SLEEVE_DECISION_BATCH_SCHEMA:
        failures.append("operating_model:decision_batch_schema")
    if decision_batch.get("session_hash") != session_hash:
        failures.append("operating_model:decision_session_lineage")
    decisions = decision_batch.get("decisions") or []
    if list(decision_batch.get("expected_sleeve_ids") or []) != [
        row.get("sleeve_id") for row in decisions if isinstance(row, Mapping)
    ]:
        failures.append("operating_model:decision_registry_coverage")
    for row in decisions:
        if not isinstance(row, Mapping):
            failures.append("operating_model:decision_row")
            continue
        row_body = dict(row)
        declared = str(row_body.pop("content_hash", ""))
        if declared != content_hash(row_body):
            failures.append(
                f"operating_model:decision_hash:{row.get('sleeve_id')}"
            )
        if row.get("outcome") not in SLEEVE_OUTCOMES:
            failures.append(
                f"operating_model:decision_outcome:{row.get('sleeve_id')}"
            )
    if allocation.get("schema_version") != ALLOCATION_SCHEMA:
        failures.append("operating_model:allocation_schema")
    if allocation.get("session_hash") != session_hash:
        failures.append("operating_model:allocation_session_lineage")
    allocation_body = dict(allocation)
    declared_allocation_hash = str(allocation_body.pop("content_hash", ""))
    if declared_allocation_hash != content_hash(allocation_body):
        failures.append("operating_model:allocation_hash")
    decision_hashes = {
        str(row.get("content_hash") or "") for row in decisions if isinstance(row, Mapping)
    }
    for row in allocation.get("sleeve_allocations") or []:
        if str(row.get("decision_hash") or "") not in decision_hashes:
            failures.append(
                f"operating_model:allocation_decision_lineage:{row.get('sleeve_id')}"
            )
    return sorted(set(failures))
