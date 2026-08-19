"""Prospective, factual Lyra v2 decision capture.

This module never upgrades a legacy evaluation-only row. It validates that row
as provenance, then creates a new governed session whose explicit inputs bind
the frozen universe, completed-close market data, and formula-derived risk,
capacity, liquidity, and turnover evidence.
"""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import Any, Mapping

from core.governed_universe_freeze import (
    read_governed_universe_symbols,
    validate_governed_universe_freeze,
)
from core.lyra_governed_evidence import (
    CAPACITY_FORMULA,
    LIQUIDITY_FORMULA,
    RISK_FORMULA,
    TURNOVER_FORMULA,
    build_lyra_capacity_evidence,
    build_lyra_forecast_risk_evidence,
    build_lyra_governed_session_snapshot,
    build_lyra_liquidity_evidence,
    governed_evidence_source_artifacts,
    normalized_target_rows,
    target_hash,
    validate_lyra_capacity_evidence,
    validate_lyra_forecast_risk_evidence,
    validate_lyra_forecast_risk_policy,
    validate_lyra_forecast_risk_policy_owner_decision,
    validate_lyra_forecast_risk_policy_proposal,
    validate_lyra_governed_session_snapshot,
    validate_lyra_market_data_snapshot,
)
from core.lyra_target_selection import validate_lyra_target_selection_evidence
from core.portfolio_operating_model import content_hash as legacy_content_hash
from core.owner_decision import OwnerDecisionError, parse_owner_decision
from core.sleeve_decision import canonical_json, seal_sleeve_decision, validate_sleeve_decision


GENERIC_LYRA_SOURCE_METHOD = "governed_lyra_current_session_v1"
GENERIC_LYRA_CAPTURE_RESULT_SCHEMA = "caerus.generic_lyra_v2_capture_result.v1"
GENERIC_LYRA_READINESS_SCHEMA = "caerus.generic_lyra_v2_readiness.v1"
LYRA_VARIANT = "h1_weekly_h6_top5"
LYRA_SLEEVE_ID = "caerus_lyra"


class GenericLyraV2ProducerError(ValueError):
    """Raised when a current governed Lyra decision cannot be proven."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return legacy_content_hash(body)


def _sha(value: Any, *, label: str) -> str:
    raw = str(value or "")
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise GenericLyraV2ProducerError(f"{label} must be a lowercase SHA-256 digest")
    return raw


def _timestamp(value: Any, *, label: str) -> tuple[str, dt.datetime]:
    raw = str(value or "")
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenericLyraV2ProducerError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise GenericLyraV2ProducerError(f"{label} must include a timezone")
    return raw, parsed


def _validate_live_owner_policy_binding(
    *, live_owner_decision: Mapping[str, Any],
    policy_proposal: Mapping[str, Any],
    policy_owner_decision: Mapping[str, Any], policy: Mapping[str, Any],
    execution_session: str, generated_at: str,
) -> dict[str, Any]:
    """Use the independently protected session owner decision as trust anchor."""

    try:
        owner = parse_owner_decision(live_owner_decision)
    except (OwnerDecisionError, TypeError, ValueError) as exc:
        raise GenericLyraV2ProducerError(
            "Live owner policy trust anchor is invalid"
        ) from exc
    patch = owner.approved_policy_patch
    _, proposed = _timestamp(
        policy_proposal.get("proposed_at"), label="policy proposed_at"
    )
    _, policy_decided = _timestamp(
        policy_owner_decision.get("decided_at"), label="policy decided_at"
    )
    _, live_decided = _timestamp(owner.decided_at, label="Live owner decided_at")
    _, captured = _timestamp(generated_at, label="capture generated_at")
    _, live_expires = _timestamp(owner.expires_at, label="Live owner expires_at")
    expected = {
        "lyra_evidence_policy_proposal_hash": policy_proposal["content_hash"],
        "lyra_evidence_policy_owner_decision_hash": (
            policy_owner_decision["content_hash"]
        ),
        "lyra_evidence_policy_terms": policy_proposal["policy_terms"],
    }
    if (
        not owner.approved
        or owner.owner != "Brett Olson"
        or owner.effective_session != execution_session
        or any(patch.get(key) != value for key, value in expected.items())
        or policy["owner_decision_hash"] != policy_owner_decision["content_hash"]
        or policy["live_owner_decision_hash"] != owner.content_hash
        or not proposed <= policy_decided <= live_decided <= captured <= live_expires
    ):
        raise GenericLyraV2ProducerError(
            "Live owner decision does not bind the exact evidence-policy chain"
        )
    return owner.to_dict()


def _validate_source_session(payload: Mapping[str, Any], *, trade_date: str) -> dict[str, Any]:
    fields = {
        "schema_version", "session_id", "trade_date", "run_id", "as_of",
        "created_at", "inputs", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise GenericLyraV2ProducerError("source session manifest fields differ")
    if payload.get("schema_version") != "caerus.session_manifest.v1":
        raise GenericLyraV2ProducerError("source session manifest schema differs")
    if payload.get("trade_date") != trade_date or payload.get("content_hash") != _hash(payload):
        raise GenericLyraV2ProducerError("source session manifest identity/hash differs")
    _, as_of = _timestamp(payload.get("as_of"), label="source session as_of")
    if as_of.date().isoformat() != trade_date:
        raise GenericLyraV2ProducerError("source session as_of differs from trade date")
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or any(not isinstance(row, Mapping) for row in inputs):
        raise GenericLyraV2ProducerError("source session inputs are invalid")
    return copy.deepcopy(dict(payload))


def _single_input_hash(
    session: Mapping[str, Any], *, name: str | None = None, prefix: str | None = None,
) -> str:
    rows = [
        row for row in session["inputs"]
        if (name is not None and row.get("name") == name)
        or (prefix is not None and str(row.get("name") or "").startswith(prefix))
    ]
    if len(rows) != 1 or rows[0].get("exists") is not True:
        raise GenericLyraV2ProducerError(f"source session does not bind exactly one {name or prefix}")
    if rows[0].get("freshness_status") not in {"FRESH", "GOVERNED"}:
        raise GenericLyraV2ProducerError(f"source session input is not fresh: {name or prefix}")
    return _sha(rows[0].get("sha256"), label=f"{name or prefix} source hash")


def _lyra_evaluation(
    batch: Mapping[str, Any], *, trade_date: str, evaluation_file_hash: str,
    lyra_source_hash: str,
) -> dict[str, Any]:
    if (
        batch.get("schema_version") != "caerus_all_sleeve_evaluation_v1"
        or batch.get("trade_date") != trade_date
        or batch.get("all_non_frozen_evaluated") is not True
    ):
        raise GenericLyraV2ProducerError("evaluation batch is incomplete or unsupported")
    expected = batch.get("expected_non_frozen_sleeve_ids")
    envelopes = batch.get("envelopes")
    if not isinstance(expected, list) or not isinstance(envelopes, list):
        raise GenericLyraV2ProducerError("evaluation registry coverage is absent")
    actual = [row.get("sleeve_id") for row in envelopes if isinstance(row, Mapping)]
    if actual != expected or len(actual) != len(set(actual)):
        raise GenericLyraV2ProducerError("evaluation does not exactly cover the registry")
    rows = [row for row in envelopes if row.get("sleeve_id") == LYRA_SLEEVE_ID]
    if len(rows) != 1:
        raise GenericLyraV2ProducerError("evaluation must contain exactly one Lyra envelope")
    row = rows[0]
    if (
        (row.get("evaluation") or {}).get("status") != "OK"
        or (row.get("opportunity") or {}).get("available") is not True
        or (row.get("opportunity") or {}).get("decision_eligible") is not True
    ):
        raise GenericLyraV2ProducerError("Lyra evaluation does not contain an available opportunity")
    sources = (row.get("provenance") or {}).get("source_artifacts")
    if not isinstance(sources, list) or len(sources) != 1 or sources[0].get("sha256") != lyra_source_hash:
        raise GenericLyraV2ProducerError("Lyra evaluation source hash differs")
    reasons = set(row.get("reason_codes") or [])
    if not {"EVALUATION_ONLY", "NON_DECISION_GRADE_UNIVERSE"}.issubset(reasons):
        raise GenericLyraV2ProducerError("Lyra legacy evaluation blockers are not explicit")
    canonical_json(batch)
    _sha(evaluation_file_hash, label="evaluation_file_hash")
    return copy.deepcopy(dict(row))


def _shadow_targets(payload: Mapping[str, Any], *, expected_before: str) -> tuple[list[dict[str, Any]], str]:
    if payload.get("strategy_slug") != LYRA_SLEEVE_ID or payload.get("source_variant") != LYRA_VARIANT:
        raise GenericLyraV2ProducerError("Lyra source identity/variant differs")
    effective = str(payload.get("effective_trade_date") or "")
    try:
        effective_date = dt.date.fromisoformat(effective)
    except ValueError as exc:
        raise GenericLyraV2ProducerError("Lyra source effective date is invalid") from exc
    if effective_date >= dt.date.fromisoformat(expected_before):
        raise GenericLyraV2ProducerError("Lyra source is not from a completed prior close")
    if payload.get("decision_eligible") is False or payload.get("observation_status") == "PENDING_SESSION_CLOSE":
        raise GenericLyraV2ProducerError("Lyra source is not decision eligible")
    raw_weights = payload.get("target_weights")
    if not isinstance(raw_weights, Mapping):
        raise GenericLyraV2ProducerError("Lyra source target_weights are absent")
    targets = normalized_target_rows([
        {"symbol": symbol, "target_weight": weight}
        for symbol, weight in raw_weights.items()
    ])
    if len(targets) != 5 or any(abs(row["target_weight"] - 0.2) > 1e-12 for row in targets):
        raise GenericLyraV2ProducerError("Lyra source is not the approved equal-weight top-five economics")
    selected = sorted(
        str(row.get("ticker") or "").upper()
        for row in (payload.get("rank_table") or [])
        if isinstance(row, Mapping) and row.get("is_selected") is True
    )
    if selected != [row["symbol"] for row in targets]:
        raise GenericLyraV2ProducerError("Lyra selected ranks differ from target weights")
    holdings = sorted(
        (str(row.get("ticker") or "").upper(), round(float(row.get("target_weight")), 12))
        for row in (payload.get("holdings") or []) if isinstance(row, Mapping)
    )
    if holdings != [(row["symbol"], row["target_weight"]) for row in targets]:
        raise GenericLyraV2ProducerError("Lyra holdings differ from target weights")
    return targets, effective


def _legacy_lyra_decision(
    batch: Mapping[str, Any], *, session: Mapping[str, Any], targets: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = batch.get("decisions")
    if (
        batch.get("schema_version") != "caerus.sleeve_decision_batch.v1"
        or batch.get("trade_date") != session["trade_date"]
        or batch.get("session_id") != session["session_id"]
        or batch.get("session_hash") != session["content_hash"]
        or not isinstance(rows, list)
        or batch.get("content_hash") != legacy_content_hash(rows)
    ):
        raise GenericLyraV2ProducerError("legacy decision batch/session lineage differs")
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("sleeve_id") == LYRA_SLEEVE_ID]
    if len(matches) != 1:
        raise GenericLyraV2ProducerError("legacy decision batch must contain exactly one Lyra row")
    row = matches[0]
    body = copy.deepcopy(dict(row))
    declared = body.pop("content_hash", None)
    if declared != legacy_content_hash(body):
        raise GenericLyraV2ProducerError("legacy Lyra decision hash differs")
    observed = normalized_target_rows(row.get("target_rows"))
    if observed != targets:
        raise GenericLyraV2ProducerError("legacy Lyra decision targets differ from source")
    if not {"EVALUATION_ONLY", "NON_DECISION_GRADE_UNIVERSE"}.issubset(set(row.get("reason_codes") or [])):
        raise GenericLyraV2ProducerError("legacy Lyra decision blockers are not explicit")
    return copy.deepcopy(dict(row))


def _turnover(current: list[dict[str, Any]], prior: list[dict[str, Any]]) -> float:
    current_map = {row["symbol"]: row["target_weight"] for row in current}
    prior_map = {row["symbol"]: row["target_weight"] for row in prior}
    symbols = set(current_map) | set(prior_map)
    return round(
        sum(
            abs(current_map.get(symbol, 0.0) - prior_map.get(symbol, 0.0))
            for symbol in symbols
        ),
        12,
    )


def validate_governed_lyra_v2_decision(decision: Mapping[str, Any]) -> dict[str, Any]:
    failures = validate_sleeve_decision(decision)
    if failures:
        raise GenericLyraV2ProducerError("governed Lyra decision is invalid: " + ",".join(failures))
    if (
        decision.get("sleeve_id") != LYRA_SLEEVE_ID
        or decision.get("source_method") != GENERIC_LYRA_SOURCE_METHOD
        or decision.get("outcome") != "RECOMMENDATION"
        or decision.get("decision_grade") != "READY"
        or decision.get("confidence") != 1.0
        or decision.get("liquidity_status") != "PASS"
    ):
        raise GenericLyraV2ProducerError("governed Lyra decision semantics differ")
    risk = validate_lyra_forecast_risk_evidence(decision.get("forecast_risk"))
    capacity = validate_lyra_capacity_evidence(decision.get("capacity"))
    liquidity = capacity["liquidity_evidence"]
    target = target_hash(decision["target_rows"])
    if risk["status"] != "PASS" or capacity["status"] != "PASS" or liquidity["status"] != "PASS":
        raise GenericLyraV2ProducerError("governed Lyra evidence is not PASS")
    if any(
        artifact.get(field) != expected
        for artifact in (risk, capacity, liquidity)
        for field, expected in (
            ("trade_date", decision["trade_date"]),
            ("session_hash", decision["session_hash"]),
            ("target_hash", target),
        )
    ):
        raise GenericLyraV2ProducerError("governed Lyra evidence lineage differs")
    reasons = set(decision.get("reason_codes") or [])
    required_reasons = {
        "PROSPECTIVE_GOVERNED_EVIDENCE_TRANSITION",
        "LEGACY_EVALUATION_NOT_RELABELED",
        "FORECAST_RISK_20D_FORMULA_BOUND",
        "LIQUIDITY_20D_FORMULA_BOUND",
        "CAPACITY_5PCT_ADV_FORMULA_BOUND",
        "TURNOVER_FULL_L1_FORMULA_BOUND",
    }
    if not required_reasons.issubset(reasons) or reasons & {"EVALUATION_ONLY", "NON_DECISION_GRADE_UNIVERSE"}:
        raise GenericLyraV2ProducerError("governed Lyra transition reasons differ")
    sources = decision.get("source_artifacts")
    required_hashes = {
        risk["content_hash"], capacity["content_hash"], liquidity["content_hash"],
        risk["market_data_snapshot_hash"], decision["session_hash"],
        risk["risk_policy"]["content_hash"],
        risk["risk_policy_proposal"]["content_hash"],
        risk["risk_policy_owner_decision"]["content_hash"],
        risk["target_selection_evidence"]["content_hash"],
    }
    observed_hashes = {
        row.get("content_hash") for row in sources if isinstance(row, Mapping)
    }
    if not required_hashes.issubset(observed_hashes):
        raise GenericLyraV2ProducerError("governed Lyra decision omits factual evidence hashes")
    return copy.deepcopy(dict(decision))


def build_generic_lyra_v2_decision_batch(
    *, source_session_manifest: Mapping[str, Any],
    evaluation_batch: Mapping[str, Any], evaluation_file_hash: str,
    legacy_decision_batch: Mapping[str, Any], legacy_decision_file_hash: str,
    lyra_source: Mapping[str, Any], lyra_source_hash: str,
    prior_lyra_source: Mapping[str, Any], prior_lyra_source_hash: str,
    universe_freeze: Mapping[str, Any], universe_path: Path | str,
    market_data_snapshot: Mapping[str, Any],
    target_selection_evidence: Mapping[str, Any],
    forecast_risk_policy: Mapping[str, Any],
    forecast_risk_policy_proposal: Mapping[str, Any],
    forecast_risk_policy_owner_decision: Mapping[str, Any],
    live_owner_decision: Mapping[str, Any],
    session_as_of: str,
    generated_at: str,
) -> dict[str, Any]:
    """Build one strict capture result containing a factual Lyra v2 decision."""

    _, generated = _timestamp(generated_at, label="generated_at")
    trade_date = str(source_session_manifest.get("trade_date") or "")
    session = _validate_source_session(source_session_manifest, trade_date=trade_date)
    if _single_input_hash(session, name="sleeve_evaluations") != evaluation_file_hash:
        raise GenericLyraV2ProducerError("session/evaluation file hash differs")
    if _single_input_hash(session, prefix="sleeve_source:caerus_lyra:") != lyra_source_hash:
        raise GenericLyraV2ProducerError("session/Lyra source file hash differs")
    evaluation = _lyra_evaluation(
        evaluation_batch, trade_date=trade_date,
        evaluation_file_hash=evaluation_file_hash, lyra_source_hash=lyra_source_hash,
    )
    current_targets, effective_date = _shadow_targets(lyra_source, expected_before=trade_date)
    prior_targets, prior_effective_date = _shadow_targets(prior_lyra_source, expected_before=effective_date)
    if prior_effective_date >= effective_date:
        raise GenericLyraV2ProducerError("prior Lyra source is not earlier than current source")
    legacy = _legacy_lyra_decision(legacy_decision_batch, session=session, targets=current_targets)
    if (evaluation.get("opportunity") or {}).get("effective_trade_date") != effective_date:
        raise GenericLyraV2ProducerError("evaluation/source effective date differs")
    freeze = validate_governed_universe_freeze(
        universe_freeze, universe_path=universe_path, session_as_of=session_as_of,
    )
    freeze_effective = dt.datetime.fromisoformat(
        freeze["effective_from"].replace("Z", "+00:00")
    ).date()
    freeze_cutoff = dt.date.fromisoformat(freeze["no_retroactive_use_before"])
    if freeze_effective > dt.date.fromisoformat(effective_date) or freeze_cutoff > dt.date.fromisoformat(effective_date):
        raise GenericLyraV2ProducerError(
            "Lyra signal predates the governed universe freeze"
        )
    universe_members = read_governed_universe_symbols(
        freeze=freeze, universe_path=universe_path, session_as_of=session_as_of,
    )
    members = set(universe_members)
    if any(row["symbol"] not in members for row in current_targets):
        raise GenericLyraV2ProducerError("Lyra target is outside frozen universe membership")
    selection = validate_lyra_target_selection_evidence(target_selection_evidence)
    policy = validate_lyra_forecast_risk_policy(forecast_risk_policy)
    policy_proposal = validate_lyra_forecast_risk_policy_proposal(
        forecast_risk_policy_proposal
    )
    policy_owner_decision = validate_lyra_forecast_risk_policy_owner_decision(
        forecast_risk_policy_owner_decision,
        proposal=policy_proposal,
        as_of=generated_at,
    )
    live_owner = _validate_live_owner_policy_binding(
        live_owner_decision=live_owner_decision,
        policy_proposal=policy_proposal,
        policy_owner_decision=policy_owner_decision,
        policy=policy,
        execution_session=trade_date,
        generated_at=generated_at,
    )
    if (
        selection["frozen_universe_symbols"] != sorted(members)
        or selection["universe_freeze_hash"] != freeze["content_hash"]
        or selection["universe_source_hash"] != freeze["source_sha256"]
        or selection["target_rows"] != current_targets
        or selection["execution_session"] != trade_date
        or selection["signal_as_of"] != effective_date
    ):
        raise GenericLyraV2ProducerError(
            "Lyra source targets are not reproduced by the complete PIT universe ranking"
        )
    market = validate_lyra_market_data_snapshot(market_data_snapshot)
    if (
        market["trade_date"] != trade_date or market["data_as_of"] != effective_date
        or market["required_symbols"] != [row["symbol"] for row in current_targets]
        or market["source_sha256"] != selection["source_sha256"]
    ):
        raise GenericLyraV2ProducerError("market data capture scope differs")
    _, source_as_of = _timestamp(session_as_of, label="session_as_of")
    if source_as_of.date().isoformat() != trade_date or generated < source_as_of:
        raise GenericLyraV2ProducerError("governed capture timing differs")
    governed_session = build_lyra_governed_session_snapshot(
        trade_date=trade_date, execution_session=trade_date,
        signal_as_of=effective_date, effective_target_date=effective_date,
        as_of=session_as_of, captured_at=generated_at,
        source_session_id=session["session_id"], source_session_hash=session["content_hash"],
        evaluation_file_hash=_sha(evaluation_file_hash, label="evaluation_file_hash"),
        legacy_decision_file_hash=_sha(legacy_decision_file_hash, label="legacy_decision_file_hash"),
        legacy_lyra_decision_hash=legacy["content_hash"],
        lyra_source_hash=_sha(lyra_source_hash, label="lyra_source_hash"),
        prior_lyra_source_hash=_sha(prior_lyra_source_hash, label="prior_lyra_source_hash"),
        universe_freeze_hash=freeze["content_hash"], universe_source_hash=freeze["source_sha256"],
        market_data_snapshot_hash=market["content_hash"],
        target_selection_evidence_hash=selection["content_hash"],
        forecast_risk_policy_hash=policy["content_hash"],
        forecast_risk_policy_proposal_hash=policy_proposal["content_hash"],
        forecast_risk_policy_owner_decision_hash=policy_owner_decision["content_hash"],
    )
    risk = build_lyra_forecast_risk_evidence(
        session_snapshot=governed_session, market_data_snapshot=market,
        target_rows=current_targets, risk_policy=policy,
        risk_policy_proposal=policy_proposal,
        risk_policy_owner_decision=policy_owner_decision,
        target_selection_evidence=selection,
    )
    liquidity = build_lyra_liquidity_evidence(
        session_snapshot=governed_session, market_data_snapshot=market,
        target_rows=current_targets,
        governed_policy=policy,
        governed_policy_proposal=policy_proposal,
        governed_policy_owner_decision=policy_owner_decision,
    )
    capacity = build_lyra_capacity_evidence(liquidity_evidence=liquidity)
    if risk["status"] != "PASS" or liquidity["status"] != "PASS" or capacity["status"] != "PASS":
        raise GenericLyraV2ProducerError("factual risk/capacity/liquidity evidence is not green")
    sources = governed_evidence_source_artifacts(
        session_snapshot=governed_session, market_data_snapshot=market,
        forecast_risk=risk, capacity=capacity,
    )
    sources.extend([
        {"artifact_type": "source_session_manifest", "schema_version": session["schema_version"], "content_hash": session["content_hash"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "legacy_evaluation_file", "schema_version": evaluation_batch["schema_version"], "content_hash": evaluation_file_hash, "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "legacy_decision_file", "schema_version": legacy_decision_batch["schema_version"], "content_hash": legacy_decision_file_hash, "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "legacy_lyra_decision", "schema_version": legacy["schema_version"], "content_hash": legacy["content_hash"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "current_lyra_shadow_source", "schema_version": "legacy_shadow_snapshot_json", "content_hash": lyra_source_hash, "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "prior_lyra_shadow_source", "schema_version": "legacy_shadow_snapshot_json", "content_hash": prior_lyra_source_hash, "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "governed_universe_freeze", "schema_version": freeze["schema_version"], "content_hash": freeze["content_hash"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "governed_universe_bytes", "schema_version": "csv", "content_hash": freeze["source_sha256"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "live_owner_policy_anchor", "schema_version": "caerus.owner_decision.v1", "content_hash": live_owner["content_hash"], "sleeve_id": LYRA_SLEEVE_ID},
    ])
    sources = sorted(sources, key=canonical_json)
    decision_body = {
        "schema_version": "caerus.sleeve_decision.v2",
        "trade_date": trade_date, "session_id": governed_session["session_id"],
        "session_hash": governed_session["content_hash"], "sleeve_id": LYRA_SLEEVE_ID,
        "outcome": "RECOMMENDATION", "confidence": 1.0,
        "forecast_risk": risk, "capacity": capacity,
        "expected_turnover": _turnover(current_targets, prior_targets),
        "liquidity_status": liquidity["status"], "source_method": GENERIC_LYRA_SOURCE_METHOD,
        "decision_grade": "READY", "target_rows": current_targets,
        "reason_codes": sorted({
            "PROSPECTIVE_GOVERNED_EVIDENCE_TRANSITION",
            "LEGACY_EVALUATION_NOT_RELABELED",
            "CONFIDENCE_IS_COMPLETE_GOVERNED_EVIDENCE",
            "FORECAST_RISK_20D_FORMULA_BOUND",
            "LIQUIDITY_20D_FORMULA_BOUND",
            "CAPACITY_5PCT_ADV_FORMULA_BOUND",
            "TURNOVER_FULL_L1_FORMULA_BOUND",
            f"RISK_FORMULA:{RISK_FORMULA}", f"LIQUIDITY_FORMULA:{LIQUIDITY_FORMULA}",
            f"CAPACITY_FORMULA:{CAPACITY_FORMULA}", f"TURNOVER_FORMULA:{TURNOVER_FORMULA}",
        }),
        "source_artifacts": sources, "decision_id": "pending",
    }
    identity = legacy_content_hash(decision_body)
    decision_body["decision_id"] = f"sleeve-decision:v2:{trade_date}:caerus_lyra:{identity[:24]}"
    decision = validate_governed_lyra_v2_decision(seal_sleeve_decision(decision_body))
    readiness = build_generic_lyra_v2_readiness(
        trade_date=trade_date, evaluated_at=generated_at,
        session_snapshot=governed_session, decision=decision,
        evidence_hashes=[
            risk["content_hash"], liquidity["content_hash"], capacity["content_hash"],
            market["content_hash"], selection["content_hash"], policy["content_hash"],
            policy_proposal["content_hash"], policy_owner_decision["content_hash"],
            live_owner["content_hash"],
        ],
    )
    result = {
        "schema_version": GENERIC_LYRA_CAPTURE_RESULT_SCHEMA,
        "trade_date": trade_date, "execution_session": trade_date,
        "signal_as_of": effective_date, "effective_target_date": effective_date,
        "captured_at": generated_at,
        "status": "READY_NO_SUBMIT", "market_data_snapshot": market,
        "target_selection_evidence": selection,
        "forecast_risk_policy": policy,
        "forecast_risk_policy_proposal": policy_proposal,
        "forecast_risk_policy_owner_decision": policy_owner_decision,
        "live_owner_decision": live_owner,
        "universe_freeze": freeze, "universe_members": universe_members,
        "prior_target_rows": prior_targets,
        "session_snapshot": governed_session, "forecast_risk": risk,
        "liquidity": liquidity, "capacity": capacity, "decision": decision,
        "readiness": readiness, "write_enabled": False,
        "broker_call_performed": False, "broker_write_performed": False,
        "submission_allowed": False, "execution_authority": False,
        "activation_authority": False,
    }
    result["content_hash"] = _hash(result)
    return validate_generic_lyra_v2_capture_result(result)


def build_generic_lyra_v2_readiness(
    *, trade_date: str, evaluated_at: str,
    session_snapshot: Mapping[str, Any] | None,
    decision: Mapping[str, Any] | None, evidence_hashes: list[str],
    blocker_codes: list[str] | None = None,
) -> dict[str, Any]:
    blockers = sorted(set(blocker_codes or []))
    if not blockers:
        if session_snapshot is None or decision is None:
            raise GenericLyraV2ProducerError("green readiness requires session and decision")
        session = validate_lyra_governed_session_snapshot(session_snapshot)
        checked = validate_governed_lyra_v2_decision(decision)
        if checked["session_hash"] != session["content_hash"]:
            raise GenericLyraV2ProducerError("readiness decision/session binding differs")
        status = "READY_FOR_EXACT_PLAN_NO_SUBMIT"
        session_hash = session["content_hash"]
        decision_hash = checked["content_hash"]
    else:
        status = "BLOCKED_NO_SUBMIT"
        session_hash = None if session_snapshot is None else session_snapshot.get("content_hash")
        decision_hash = None if decision is None else decision.get("content_hash")
    for value in evidence_hashes:
        _sha(value, label="evidence_hash")
    body = {
        "schema_version": GENERIC_LYRA_READINESS_SCHEMA,
        "trade_date": trade_date,
        "execution_session": trade_date,
        "signal_as_of": (
            None if session_snapshot is None else session_snapshot.get("signal_as_of")
        ),
        "effective_target_date": (
            None if session_snapshot is None else session_snapshot.get("effective_target_date")
        ),
        "evaluated_at": evaluated_at, "status": status,
        "blocker_codes": blockers or ["ALL_FACTUAL_LYRA_V2_INPUTS_GREEN"],
        "session_snapshot_hash": session_hash, "decision_hash": decision_hash,
        "evidence_hashes": sorted(set(evidence_hashes)),
        "next_authorized_action": (
            "BUILD_ADVISORY_EXACT_V4_PLAN" if not blockers else "CAPTURE_MISSING_GOVERNED_EVIDENCE"
        ),
        "schedule_enabled": False, "submission_allowed": False,
        "broker_call_performed": False, "broker_write_performed": False,
        "execution_authority": False, "activation_authority": False,
    }
    body["content_hash"] = _hash(body)
    return validate_generic_lyra_v2_readiness(body)


def validate_generic_lyra_v2_capture_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute a protected capture bundle and its decision byte-for-byte."""

    fields = {
        "schema_version", "trade_date", "execution_session", "signal_as_of",
        "effective_target_date", "captured_at", "status", "market_data_snapshot",
        "target_selection_evidence", "forecast_risk_policy",
        "forecast_risk_policy_proposal", "forecast_risk_policy_owner_decision",
        "live_owner_decision",
        "universe_freeze",
        "universe_members", "prior_target_rows", "session_snapshot",
        "forecast_risk", "liquidity", "capacity", "decision", "readiness",
        "write_enabled", "broker_call_performed", "broker_write_performed",
        "submission_allowed", "execution_authority", "activation_authority",
        "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise GenericLyraV2ProducerError("capture result fields differ")
    if (
        payload.get("schema_version") != GENERIC_LYRA_CAPTURE_RESULT_SCHEMA
        or payload.get("status") != "READY_NO_SUBMIT"
        or any(payload.get(field) is not False for field in (
            "write_enabled", "broker_call_performed", "broker_write_performed",
            "submission_allowed", "execution_authority", "activation_authority",
        ))
    ):
        raise GenericLyraV2ProducerError("capture result semantics differ")
    freeze = validate_governed_universe_freeze(payload.get("universe_freeze"))
    members = payload.get("universe_members")
    if (
        not isinstance(members, list) or len(members) != len(set(members))
        or len(members) != freeze["member_count"]
        or legacy_content_hash(members) != freeze["ordered_members_sha256"]
    ):
        raise GenericLyraV2ProducerError("capture universe membership differs from freeze")
    selection = validate_lyra_target_selection_evidence(
        payload.get("target_selection_evidence")
    )
    market = validate_lyra_market_data_snapshot(payload.get("market_data_snapshot"))
    policy = validate_lyra_forecast_risk_policy(payload.get("forecast_risk_policy"))
    policy_proposal = validate_lyra_forecast_risk_policy_proposal(
        payload.get("forecast_risk_policy_proposal")
    )
    policy_owner_decision = validate_lyra_forecast_risk_policy_owner_decision(
        payload.get("forecast_risk_policy_owner_decision"),
        proposal=policy_proposal,
        as_of=payload.get("captured_at"),
    )
    live_owner = _validate_live_owner_policy_binding(
        live_owner_decision=payload.get("live_owner_decision"),
        policy_proposal=policy_proposal,
        policy_owner_decision=policy_owner_decision,
        policy=policy,
        execution_session=str(payload.get("execution_session") or ""),
        generated_at=str(payload.get("captured_at") or ""),
    )
    session = validate_lyra_governed_session_snapshot(payload.get("session_snapshot"))
    # Revalidate the freeze against the protected prospective session.  This
    # prevents a perfectly self-sealed but retroactively effective freeze from
    # being promoted through the capture bundle.
    freeze = validate_governed_universe_freeze(
        payload.get("universe_freeze"), session_as_of=session["as_of"]
    )
    signal_date = dt.date.fromisoformat(session["signal_as_of"])
    freeze_effective = dt.datetime.fromisoformat(
        freeze["effective_from"].replace("Z", "+00:00")
    ).date()
    freeze_cutoff = dt.date.fromisoformat(freeze["no_retroactive_use_before"])
    if freeze_effective > signal_date or freeze_cutoff > signal_date:
        raise GenericLyraV2ProducerError(
            "Lyra signal predates the governed universe freeze"
        )
    try:
        policy_approved_at = dt.datetime.fromisoformat(
            str(policy["approved_at"]).replace("Z", "+00:00")
        )
        captured_at = dt.datetime.fromisoformat(
            str(session["captured_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:  # validators above normally make this unreachable
        raise GenericLyraV2ProducerError(
            "capture policy/session timestamp differs"
        ) from exc
    if policy_approved_at > captured_at:
        raise GenericLyraV2ProducerError(
            "forecast-risk policy was not approved before capture"
        )
    if (
        payload.get("trade_date") != session["trade_date"]
        or payload.get("execution_session") != session["execution_session"]
        or payload.get("signal_as_of") != session["signal_as_of"]
        or payload.get("effective_target_date") != session["effective_target_date"]
        or payload.get("captured_at") != session["captured_at"]
        or sorted(members) != selection["frozen_universe_symbols"]
        or freeze["content_hash"] != session["universe_freeze_hash"]
        or freeze["source_sha256"] != session["universe_source_hash"]
        or selection["content_hash"] != session["target_selection_evidence_hash"]
        or market["content_hash"] != session["market_data_snapshot_hash"]
        or policy["content_hash"] != session["forecast_risk_policy_hash"]
        or policy_proposal["content_hash"]
        != session["forecast_risk_policy_proposal_hash"]
        or policy_owner_decision["content_hash"]
        != session["forecast_risk_policy_owner_decision_hash"]
    ):
        raise GenericLyraV2ProducerError("capture session/source bundle lineage differs")
    targets = selection["target_rows"]
    prior_targets = normalized_target_rows(payload.get("prior_target_rows"))
    risk = build_lyra_forecast_risk_evidence(
        session_snapshot=session, market_data_snapshot=market,
        target_rows=targets, risk_policy=policy,
        risk_policy_proposal=policy_proposal,
        risk_policy_owner_decision=policy_owner_decision,
        target_selection_evidence=selection,
    )
    liquidity = build_lyra_liquidity_evidence(
        session_snapshot=session, market_data_snapshot=market, target_rows=targets,
        governed_policy=policy,
        governed_policy_proposal=policy_proposal,
        governed_policy_owner_decision=policy_owner_decision,
    )
    capacity = build_lyra_capacity_evidence(liquidity_evidence=liquidity)
    if (
        risk != payload.get("forecast_risk")
        or liquidity != payload.get("liquidity")
        or capacity != payload.get("capacity")
    ):
        raise GenericLyraV2ProducerError("capture factual evidence does not recompute")
    sources = governed_evidence_source_artifacts(
        session_snapshot=session, market_data_snapshot=market,
        forecast_risk=risk, capacity=capacity,
    )
    sources.extend([
        {"artifact_type": "source_session_manifest", "schema_version": "caerus.session_manifest.v1", "content_hash": session["source_session_hash"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "legacy_evaluation_file", "schema_version": "caerus_all_sleeve_evaluation_v1", "content_hash": session["evaluation_file_hash"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "legacy_decision_file", "schema_version": "caerus.sleeve_decision_batch.v1", "content_hash": session["legacy_decision_file_hash"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "legacy_lyra_decision", "schema_version": "caerus.sleeve_decision.v1", "content_hash": session["legacy_lyra_decision_hash"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "current_lyra_shadow_source", "schema_version": "legacy_shadow_snapshot_json", "content_hash": session["lyra_source_hash"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "prior_lyra_shadow_source", "schema_version": "legacy_shadow_snapshot_json", "content_hash": session["prior_lyra_source_hash"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "governed_universe_freeze", "schema_version": freeze["schema_version"], "content_hash": freeze["content_hash"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "governed_universe_bytes", "schema_version": "csv", "content_hash": freeze["source_sha256"], "sleeve_id": LYRA_SLEEVE_ID},
        {"artifact_type": "live_owner_policy_anchor", "schema_version": "caerus.owner_decision.v1", "content_hash": live_owner["content_hash"], "sleeve_id": LYRA_SLEEVE_ID},
    ])
    decision_body = {
        "schema_version": "caerus.sleeve_decision.v2",
        "trade_date": session["trade_date"], "session_id": session["session_id"],
        "session_hash": session["content_hash"], "sleeve_id": LYRA_SLEEVE_ID,
        "outcome": "RECOMMENDATION", "confidence": 1.0,
        "forecast_risk": risk, "capacity": capacity,
        "expected_turnover": _turnover(targets, prior_targets),
        "liquidity_status": liquidity["status"], "source_method": GENERIC_LYRA_SOURCE_METHOD,
        "decision_grade": "READY", "target_rows": targets,
        "reason_codes": sorted({
            "PROSPECTIVE_GOVERNED_EVIDENCE_TRANSITION",
            "LEGACY_EVALUATION_NOT_RELABELED",
            "CONFIDENCE_IS_COMPLETE_GOVERNED_EVIDENCE",
            "FORECAST_RISK_20D_FORMULA_BOUND", "LIQUIDITY_20D_FORMULA_BOUND",
            "CAPACITY_5PCT_ADV_FORMULA_BOUND", "TURNOVER_FULL_L1_FORMULA_BOUND",
            f"RISK_FORMULA:{RISK_FORMULA}", f"LIQUIDITY_FORMULA:{LIQUIDITY_FORMULA}",
            f"CAPACITY_FORMULA:{CAPACITY_FORMULA}", f"TURNOVER_FORMULA:{TURNOVER_FORMULA}",
        }),
        "source_artifacts": sorted(sources, key=canonical_json), "decision_id": "pending",
    }
    identity = legacy_content_hash(decision_body)
    decision_body["decision_id"] = (
        f"sleeve-decision:v2:{session['trade_date']}:caerus_lyra:{identity[:24]}"
    )
    expected_decision = validate_governed_lyra_v2_decision(
        seal_sleeve_decision(decision_body)
    )
    if expected_decision != payload.get("decision"):
        raise GenericLyraV2ProducerError("capture decision does not recompute from evidence")
    expected_readiness = build_generic_lyra_v2_readiness(
        trade_date=session["trade_date"], evaluated_at=session["captured_at"],
        session_snapshot=session, decision=expected_decision,
        evidence_hashes=[
            risk["content_hash"], liquidity["content_hash"], capacity["content_hash"],
            market["content_hash"], selection["content_hash"], policy["content_hash"],
            policy_proposal["content_hash"], policy_owner_decision["content_hash"],
            live_owner["content_hash"],
        ],
    )
    if expected_readiness != payload.get("readiness"):
        raise GenericLyraV2ProducerError("capture readiness does not recompute")
    if payload.get("content_hash") != _hash(payload):
        raise GenericLyraV2ProducerError("capture result content_hash mismatch")
    return copy.deepcopy(dict(payload))


def validate_generic_lyra_v2_readiness(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version", "trade_date", "execution_session", "signal_as_of",
        "effective_target_date", "evaluated_at", "status", "blocker_codes",
        "session_snapshot_hash", "decision_hash", "evidence_hashes",
        "next_authorized_action", "schedule_enabled", "submission_allowed",
        "broker_call_performed", "broker_write_performed",
        "execution_authority", "activation_authority", "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise GenericLyraV2ProducerError("readiness fields differ")
    if payload.get("schema_version") != GENERIC_LYRA_READINESS_SCHEMA:
        raise GenericLyraV2ProducerError("readiness schema differs")
    trade_date = str(payload.get("trade_date") or "")
    try:
        trade = dt.date.fromisoformat(trade_date)
    except ValueError as exc:
        raise GenericLyraV2ProducerError("readiness trade_date is invalid") from exc
    if payload.get("execution_session") != trade_date:
        raise GenericLyraV2ProducerError("readiness execution session differs")
    _timestamp(payload.get("evaluated_at"), label="readiness evaluated_at")
    blockers = payload.get("blocker_codes")
    hashes = payload.get("evidence_hashes")
    if (
        not isinstance(blockers, list) or blockers != sorted(set(blockers)) or not blockers
        or not isinstance(hashes, list) or hashes != sorted(set(hashes))
    ):
        raise GenericLyraV2ProducerError("readiness blockers/evidence hashes differ")
    for value in hashes:
        _sha(value, label="readiness evidence hash")
    green = payload.get("status") == "READY_FOR_EXACT_PLAN_NO_SUBMIT"
    if green:
        signal_as_of = dt.date.fromisoformat(str(payload.get("signal_as_of") or ""))
        effective = dt.date.fromisoformat(str(payload.get("effective_target_date") or ""))
        if signal_as_of != effective or signal_as_of >= trade:
            raise GenericLyraV2ProducerError("readiness signal/execution chronology differs")
        if blockers != ["ALL_FACTUAL_LYRA_V2_INPUTS_GREEN"] or not hashes:
            raise GenericLyraV2ProducerError("green readiness evidence differs")
        _sha(payload.get("session_snapshot_hash"), label="session_snapshot_hash")
        _sha(payload.get("decision_hash"), label="decision_hash")
        if payload.get("next_authorized_action") != "BUILD_ADVISORY_EXACT_V4_PLAN":
            raise GenericLyraV2ProducerError("green readiness next action differs")
    elif payload.get("status") == "BLOCKED_NO_SUBMIT":
        if blockers == ["ALL_FACTUAL_LYRA_V2_INPUTS_GREEN"]:
            raise GenericLyraV2ProducerError("blocked readiness requires blockers")
        if payload.get("next_authorized_action") != "CAPTURE_MISSING_GOVERNED_EVIDENCE":
            raise GenericLyraV2ProducerError("blocked readiness next action differs")
    else:
        raise GenericLyraV2ProducerError("readiness status differs")
    for field in (
        "schedule_enabled", "submission_allowed", "broker_call_performed",
        "broker_write_performed", "execution_authority", "activation_authority",
    ):
        if payload.get(field) is not False:
            raise GenericLyraV2ProducerError(f"readiness safety flag differs: {field}")
    if payload.get("content_hash") != _hash(payload):
        raise GenericLyraV2ProducerError("readiness content_hash mismatch")
    return copy.deepcopy(dict(payload))


def generic_lyra_v2_readiness_path(
    *, output_root: Path | str, readiness: Mapping[str, Any],
) -> Path:
    checked = validate_generic_lyra_v2_readiness(readiness)
    identity = checked["content_hash"]
    trade_date = checked["trade_date"]
    return Path(output_root) / trade_date / f"readiness-{identity}.json"


__all__ = [
    "GENERIC_LYRA_CAPTURE_RESULT_SCHEMA", "GENERIC_LYRA_READINESS_SCHEMA",
    "GENERIC_LYRA_SOURCE_METHOD", "GenericLyraV2ProducerError",
    "build_generic_lyra_v2_decision_batch", "build_generic_lyra_v2_readiness",
    "generic_lyra_v2_readiness_path", "validate_generic_lyra_v2_readiness",
    "validate_generic_lyra_v2_capture_result", "validate_governed_lyra_v2_decision",
]
