from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from authority.contracts import build_decision_package, build_evidence_package
from authority.pipeline import decision_package_from_dict, evidence_package_from_dict
from core.strategy_identity import strategy_identity_metadata, validate_lane_strategy_identity
from core.strategy_registry import load_strategy_registry_for_repo
from core.target_attainment_policy import validate_target_attainment_policy
from core.portfolio_operating_model import (
    ALLOCATION_SCHEMA,
    AUDIT_MANIFEST_SCHEMA,
    SESSION_SCHEMA,
    SLEEVE_DECISION_BATCH_SCHEMA,
    allocate_portfolio,
    build_audit_manifest,
    build_session_manifest,
    build_sleeve_decision_batch,
    validate_operating_model_lineage,
)
from core.sleeve_control_plane import load_sleeve_control_registry


PAPER_TARGET_SCHEMA = "caerus.paper_target_package.v2"
LEGACY_PAPER_TARGET_SCHEMA = "caerus.paper_target_package.v1"
PAPER_SIGNALS_SCHEMA = "caerus.paper_target_signals.v1"
PAPER_HANDOFF_SCHEMA = "caerus.paper_precompute_handoff.v1"
SEALED_PRECOMPUTE_SCHEMA_VERSION = 3
LEGACY_SEALED_PRECOMPUTE_SCHEMA_VERSION = 2


class PaperTargetAuthorityError(RuntimeError):
    """Raised when the sole PAPER Decision target cannot be sealed or verified."""


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _pretty_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PaperTargetAuthorityError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PaperTargetAuthorityError(f"{path} must contain a JSON object")
    return payload


def _resolve_repo_path(repo_root: Path, raw_path: object) -> Path:
    path = Path(str(raw_path or "").strip())
    if not str(path):
        raise PaperTargetAuthorityError("source artifact path is blank")
    return path if path.is_absolute() else repo_root / path


def _display_path(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _sole_orion_envelope(sleeve_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = [
        row
        for row in sleeve_payload.get("envelopes") or []
        if isinstance(row, Mapping) and row.get("sleeve_id") == "caerus_orion"
    ]
    if len(rows) != 1:
        raise PaperTargetAuthorityError("sleeve evaluations lack exactly one Orion envelope")
    envelope = rows[0]
    if (
        (envelope.get("evaluation") or {}).get("status") != "OK"
        or (envelope.get("eligibility") or {}).get("evaluation_usable_for_capital") is not True
        or (envelope.get("opportunity") or {}).get("decision_eligible") is not True
    ):
        raise PaperTargetAuthorityError("Orion is not Decision-eligible for PAPER capital")
    source_rows = (envelope.get("provenance") or {}).get("source_artifacts") or []
    existing = [
        row
        for row in source_rows
        if isinstance(row, Mapping) and row.get("exists") is True
    ]
    if len(existing) != 1:
        raise PaperTargetAuthorityError("Orion must bind exactly one existing source artifact")
    return envelope


def _normalized_target_rows(
    *,
    source_payload: Mapping[str, Any],
    target_cash_weight: float,
) -> list[dict[str, Any]]:
    weights = source_payload.get("target_weights")
    if not isinstance(weights, Mapping):
        raise PaperTargetAuthorityError("Orion source target_weights is missing or malformed")
    positive: dict[str, float] = {}
    for raw_symbol, raw_weight in weights.items():
        symbol = str(raw_symbol or "").strip().upper()
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError) as exc:
            raise PaperTargetAuthorityError(f"Orion target weight is nonnumeric: {symbol}") from exc
        if not symbol or symbol == "CASH" or not math.isfinite(weight) or weight <= 0.0:
            raise PaperTargetAuthorityError(f"Orion target weight is invalid: {symbol or '<blank>'}")
        if symbol in positive:
            raise PaperTargetAuthorityError(f"Orion target symbol is duplicated: {symbol}")
        positive[symbol] = weight
    gross = sum(positive.values())
    if gross <= 0.0:
        raise PaperTargetAuthorityError("Orion source has no positive target weights")
    investable = 1.0 - float(target_cash_weight)
    return [
        {
            "symbol": symbol,
            "ticker": symbol,
            "sleeve": "sleeve_trend",
            "target_weight": round(weight / gross * investable, 12),
            "source_target_weight": weight,
        }
        for symbol, weight in sorted(positive.items())
    ]


def _target_projection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "symbol": str(row["symbol"]),
            "ticker": str(row["ticker"]),
            "sleeve": str(row["sleeve"]),
            "target_weight": float(row["target_weight"]),
        }
        if isinstance(row.get("sleeve_contributions"), list):
            item["sleeve_contributions"] = [
                dict(contribution)
                for contribution in row["sleeve_contributions"]
                if isinstance(contribution, Mapping)
            ]
        projected.append(item)
    return projected


def seal_paper_target_bundle(
    *,
    bundle_dir: Path,
    trade_date: str,
    repo_root: Path,
    sealed_at: str | None = None,
) -> dict[str, Any]:
    """Replace research planner projections with one immutable Orion Decision target."""

    bundle_dir = Path(bundle_dir)
    repo_root = Path(repo_root).resolve()
    contract_path = bundle_dir / "contract.json"
    snapshot_path = bundle_dir / "daily_snapshot.json"
    signals_path = bundle_dir / "signals.json"
    handoff_path = bundle_dir / "planned_execution_payload.json"
    sleeve_path = bundle_dir / "sleeve_evaluations.json"
    for path in (contract_path, snapshot_path, signals_path, handoff_path, sleeve_path):
        if not path.is_file():
            raise PaperTargetAuthorityError(f"required precompute artifact is missing: {path}")

    existing_contract = _read_object(contract_path)
    if int(existing_contract.get("schema_version") or 0) in {
        LEGACY_SEALED_PRECOMPUTE_SCHEMA_VERSION,
        SEALED_PRECOMPUTE_SCHEMA_VERSION,
    }:
        failures = validate_sealed_paper_target_bundle(
            bundle_dir=bundle_dir,
            trade_date=trade_date,
            repo_root=repo_root,
        )
        if failures:
            raise PaperTargetAuthorityError(
                "existing sealed PAPER target is invalid: " + ",".join(failures[:5])
            )
        return _read_object(bundle_dir / "paper_target_package.json")

    daily_snapshot = _read_object(snapshot_path)
    legacy_signals = _read_object(signals_path)
    legacy_handoff = _read_object(handoff_path)
    sleeve_payload = _read_object(sleeve_path)
    if str(sleeve_payload.get("trade_date") or "") != str(trade_date):
        raise PaperTargetAuthorityError("sleeve evaluation trade_date mismatch")

    control_registry = load_sleeve_control_registry()
    allocation_policy = dict(control_registry.paper_allocation_policy)
    target_cash_weight = float(allocation_policy.get("target_cash_weight") or 0.0)
    policy = validate_target_attainment_policy(
        allocation_policy.get("account_target_attainment_policy"),
        expected_target_cash_weight=target_cash_weight,
    )
    registry = load_strategy_registry_for_repo(repo_root)
    from paper.trading_calendar import is_trading_day, prev_trading_day

    if not is_trading_day(trade_date):
        raise PaperTargetAuthorityError("PAPER decision date is not an XNYS session")
    prior_date = prev_trading_day(trade_date)
    capital_ids = sorted((allocation_policy.get("sleeve_risk_budgets") or {}).keys())
    envelopes = {
        str(row.get("sleeve_id") or ""): row
        for row in sleeve_payload.get("envelopes") or []
        if isinstance(row, Mapping)
    }
    capital_sources: list[dict[str, Any]] = []
    for sleeve_id in capital_ids:
        envelope = envelopes.get(sleeve_id)
        if not isinstance(envelope, Mapping):
            raise PaperTargetAuthorityError(
                f"capital sleeve evaluation is missing: {sleeve_id}"
            )
        if (
            (envelope.get("evaluation") or {}).get("status") != "OK"
            or (envelope.get("eligibility") or {}).get(
                "evaluation_usable_for_capital"
            )
            is not True
            or (envelope.get("opportunity") or {}).get("decision_eligible")
            is not True
        ):
            raise PaperTargetAuthorityError(
                f"capital sleeve is not Decision-eligible: {sleeve_id}"
            )
        existing_sources = [
            row
            for row in (envelope.get("provenance") or {}).get("source_artifacts")
            or []
            if isinstance(row, Mapping) and row.get("exists") is True
        ]
        if len(existing_sources) != 1:
            raise PaperTargetAuthorityError(
                f"capital sleeve must bind exactly one source: {sleeve_id}"
            )
        source_ref = existing_sources[0]
        source_path = _resolve_repo_path(repo_root, source_ref.get("path"))
        source_hash = _file_hash(source_path) if source_path.is_file() else ""
        if not source_hash or source_hash != str(source_ref.get("sha256") or ""):
            raise PaperTargetAuthorityError(
                f"capital sleeve source hash mismatch: {sleeve_id}"
            )
        source_payload = _read_object(source_path)
        entry = registry.require(sleeve_id)
        execution_config = dict((entry.raw or {}).get("paper_execution") or {})
        if (
            not bool(execution_config.get("enabled"))
            or str(execution_config.get("approval_scope") or "").upper()
            != "PAPER_ONLY"
            or bool(execution_config.get("live_enabled"))
        ):
            raise PaperTargetAuthorityError(
                f"capital sleeve PAPER execution governance is invalid: {sleeve_id}"
            )
        expected_variant = str(
            execution_config.get("source_variant")
            or (entry.shadow_tracking or {}).get("source_variant")
            or ""
        ).strip()
        source_variant = str(source_payload.get("source_variant") or "").strip()
        if expected_variant and source_variant != expected_variant:
            raise PaperTargetAuthorityError(
                f"capital sleeve source variant mismatch: {sleeve_id}"
            )
        if str(source_payload.get("strategy_slug") or "") != sleeve_id:
            raise PaperTargetAuthorityError(
                f"capital sleeve source identity mismatch: {sleeve_id}"
            )
        effective_date = str(
            (envelope.get("opportunity") or {}).get("effective_trade_date")
            or source_payload.get("effective_trade_date")
            or source_payload.get("trade_date")
            or ""
        )
        if effective_date not in {trade_date, prior_date}:
            raise PaperTargetAuthorityError(
                f"capital sleeve source is outside freshness policy: {sleeve_id}"
            )
        if str(source_payload.get("trade_date") or "") != effective_date or str(
            source_payload.get("effective_trade_date") or effective_date
        ) != effective_date:
            raise PaperTargetAuthorityError(
                f"capital sleeve source date mismatch: {sleeve_id}"
            )
        capital_sources.append(
            {
                "sleeve_id": sleeve_id,
                "path": source_path,
                "sha256": source_hash,
                "payload": source_payload,
                "effective_trade_date": effective_date,
                "source_trading_session_lag": (
                    0 if effective_date == trade_date else 1
                ),
                "source_variant": source_variant,
            }
        )
    primary_id = control_registry.paper_capital_authority
    primary_source = next(
        row for row in capital_sources if row["sleeve_id"] == primary_id
    )
    source_path = primary_source["path"]
    source_hash = primary_source["sha256"]
    source_payload = primary_source["payload"]
    effective_date = primary_source["effective_trade_date"]
    lag = primary_source["source_trading_session_lag"]
    source_variant = primary_source["source_variant"]
    source_session_policy = "SAME_OR_PREVIOUS_TRADING_SESSION"

    sealed_timestamp = sealed_at or dt.datetime.now(dt.timezone.utc).isoformat()
    research_pair_hash = _payload_hash(
        {
            "daily_snapshot": daily_snapshot,
            "signals": legacy_signals,
            "planned_execution_payload": legacy_handoff,
        }
    )
    research_dir = (
        bundle_dir / "research" / "growth_engine_v4" / f"precompute-{research_pair_hash}"
    )
    research_dir.mkdir(parents=True, exist_ok=True)
    research_snapshot_path = research_dir / "daily_snapshot.json"
    research_signals_path = research_dir / "signals.json"
    research_handoff_path = research_dir / "planned_execution_payload.json"
    research_snapshot_path.write_text(_pretty_json(daily_snapshot), encoding="utf-8")
    research_signals_path.write_text(_pretty_json(legacy_signals), encoding="utf-8")
    research_handoff_path.write_text(_pretty_json(legacy_handoff), encoding="utf-8")

    session_inputs: list[dict[str, Any]] = [
        {
            "name": "market_state_snapshot",
            "path": research_snapshot_path,
            "required": True,
            "as_of": str(daily_snapshot.get("asof") or effective_date),
            "freshness_status": "FRESH",
        },
        {
            "name": "sleeve_evaluations",
            "path": sleeve_path,
            "required": True,
            "as_of": effective_date,
            "freshness_status": "FRESH",
        },
        {
            "name": "strategy_registry",
            "path": control_registry.registry_path,
            "required": True,
            "freshness_status": "GOVERNED",
        },
        {
            "name": "sleeve_manifest",
            "path": control_registry.manifest_path,
            "required": True,
            "freshness_status": "GOVERNED",
        },
    ]
    observed_sources: set[str] = set()
    for candidate_envelope in sleeve_payload.get("envelopes") or []:
        if not isinstance(candidate_envelope, Mapping):
            continue
        sleeve_id = str(candidate_envelope.get("sleeve_id") or "unknown")
        for index, candidate_source in enumerate(
            (candidate_envelope.get("provenance") or {}).get("source_artifacts") or []
        ):
            if not isinstance(candidate_source, Mapping) or not candidate_source.get("exists"):
                continue
            raw_path = str(candidate_source.get("path") or "").strip()
            if not raw_path or raw_path in observed_sources:
                continue
            observed_sources.add(raw_path)
            session_inputs.append(
                {
                    "name": f"sleeve_source:{sleeve_id}:{index}",
                    "path": raw_path,
                    "sha256": candidate_source.get("sha256"),
                    "required": bool(
                        (candidate_envelope.get("eligibility") or {}).get(
                            "capital_eligible"
                        )
                    ),
                    "as_of": (candidate_envelope.get("opportunity") or {}).get(
                        "effective_trade_date"
                    ),
                    "freshness_status": "FRESH",
                }
            )
    run_id = str(
        legacy_handoff.get("run_id")
        or existing_contract.get("source_run_id")
        or f"{trade_date}:precompute"
    )
    session_manifest = build_session_manifest(
        trade_date=trade_date,
        run_id=run_id,
        as_of=sealed_timestamp,
        repo_root=repo_root,
        inputs=session_inputs,
        created_at=sealed_timestamp,
    )
    session_path = bundle_dir / "session_manifest.json"
    session_path.write_text(_pretty_json(session_manifest), encoding="utf-8")
    sleeve_decisions = build_sleeve_decision_batch(
        evaluation_batch=sleeve_payload,
        session_manifest=session_manifest,
        repo_root=repo_root,
        generated_at=sealed_timestamp,
    )
    sleeve_decisions_path = bundle_dir / "sleeve_decisions.json"
    sleeve_decisions_path.write_text(_pretty_json(sleeve_decisions), encoding="utf-8")
    allocation = allocate_portfolio(
        decision_batch=sleeve_decisions,
        allocation_policy=allocation_policy,
        allocated_at=sealed_timestamp,
    )
    lineage_failures = validate_operating_model_lineage(
        session_manifest=session_manifest,
        decision_batch=sleeve_decisions,
        allocation=allocation,
    )
    if lineage_failures:
        raise PaperTargetAuthorityError(
            "portfolio operating-model lineage failed: "
            + ",".join(lineage_failures[:5])
        )
    allocation_path = bundle_dir / "portfolio_allocation.json"
    allocation_path.write_text(_pretty_json(allocation), encoding="utf-8")
    target_projection = _target_projection(list(allocation["targets"]))
    sleeve_hash = _file_hash(sleeve_path)
    session_hash = _file_hash(session_path)
    sleeve_decisions_hash = _file_hash(sleeve_decisions_path)
    allocation_hash = _file_hash(allocation_path)
    source_refs = tuple(
        value
        for row in capital_sources
        for value in (
            _display_path(repo_root, row["path"]),
            f"sha256:{row['sha256']}",
        )
    ) + (
        _display_path(repo_root, sleeve_path),
        f"sha256:{sleeve_hash}",
        _display_path(repo_root, session_path),
        f"sha256:{session_hash}",
        _display_path(repo_root, sleeve_decisions_path),
        f"sha256:{sleeve_decisions_hash}",
        _display_path(repo_root, allocation_path),
        f"sha256:{allocation_hash}",
    )
    authority_stem = f"{trade_date}:paper:caerus_paper_allocator"
    evidence = build_evidence_package(
        package_id=f"evidence:{authority_stem}",
        trade_date=trade_date,
        source_refs=source_refs,
        observations=target_projection,
    )
    decision = build_decision_package(
        package_id=f"decision:{authority_stem}",
        trade_date=trade_date,
        evidence=evidence,
        target_rows=target_projection,
        target_cash_weight=target_cash_weight,
        source_refs=source_refs,
    )
    approved_sleeve = (
        primary_id if len(capital_ids) == 1 else "caerus_paper_portfolio"
    )
    identity = strategy_identity_metadata(trade_date)
    identity.update(
        {
            "execution_target_strategy_id": approved_sleeve,
            "paper_governed_strategy_id": approved_sleeve,
            "paper_mapping_status": (
                "DIRECT_APPROVED_PACKAGE"
                if len(capital_ids) == 1
                else "DIRECT_ALLOCATOR_PACKAGE"
            ),
            "execution_target_source": _display_path(
                repo_root,
                source_path if len(capital_ids) == 1 else allocation_path,
            ),
            "paper_capital_sleeves": capital_ids,
            "shadow_baseline_source": _display_path(repo_root, source_path),
            "shadow_baseline_source_sha256": source_hash,
            "shadow_baseline_source_trade_date": effective_date,
        }
    )
    identity_check = validate_lane_strategy_identity(
        identity=identity,
        approved_strategy=approved_sleeve,
        lane="paper",
    )
    if identity_check.get("status") != "PASS":
        raise PaperTargetAuthorityError("sealed target strategy identity is invalid")

    target_package = {
        "schema_version": PAPER_TARGET_SCHEMA,
        "trade_date": trade_date,
        "sealed_at": sealed_timestamp,
        "authority": "DECISION",
        "allocator_authority": str(allocation.get("allocator_id") or ""),
        "approved_sleeve": approved_sleeve,
        "capital_sleeves": [
            row["sleeve_id"] for row in allocation["sleeve_allocations"]
        ],
        "approved_target_hash": decision.content_hash,
        "session_id": session_manifest["session_id"],
        "session_content_hash": session_manifest["content_hash"],
        "sleeve_decision_batch_hash": sleeve_decisions["content_hash"],
        "allocation_id": allocation["allocation_id"],
        "allocation_content_hash": allocation["content_hash"],
        "effective_trade_date": effective_date,
        "source_trading_session_lag": lag,
        "source_session_policy": source_session_policy,
        "source_effective_trade_dates": {
            row["sleeve_id"]: row["effective_trade_date"] for row in capital_sources
        },
        "source_variant": source_variant,
        "target_cash_weight": target_cash_weight,
        "target_attainment_policy": policy,
        "target_rows": target_projection,
        "strategy_identity": identity,
        "source_strategy_artifact": {
            "path": _display_path(repo_root, source_path),
            "sha256": source_hash,
            "source_trade_date": str(source_payload.get("trade_date") or effective_date),
            "source_effective_trade_date": effective_date,
            "decision_trade_date": trade_date,
            "source_trading_session_lag": lag,
            "source_session_policy": source_session_policy,
        },
        "source_strategy_artifacts": [
            {
                "sleeve_id": row["sleeve_id"],
                "path": _display_path(repo_root, row["path"]),
                "sha256": row["sha256"],
                "source_trade_date": str(
                    row["payload"].get("trade_date")
                    or row["effective_trade_date"]
                ),
                "source_effective_trade_date": row["effective_trade_date"],
                "decision_trade_date": trade_date,
                "source_trading_session_lag": row[
                    "source_trading_session_lag"
                ],
                "source_session_policy": source_session_policy,
            }
            for row in capital_sources
        ],
        "source_sleeve_evaluations": {
            "path": _display_path(repo_root, sleeve_path),
            "sha256": sleeve_hash,
        },
        "source_session_manifest": {
            "path": _display_path(repo_root, session_path),
            "sha256": session_hash,
        },
        "source_sleeve_decisions": {
            "path": _display_path(repo_root, sleeve_decisions_path),
            "sha256": sleeve_decisions_hash,
        },
        "source_portfolio_allocation": {
            "path": _display_path(repo_root, allocation_path),
            "sha256": allocation_hash,
        },
        "evidence_package": evidence.to_dict(),
        "decision_package": decision.to_dict(),
    }

    target_path = bundle_dir / "paper_target_package.json"
    target_path.write_text(_pretty_json(target_package), encoding="utf-8")
    target_package_hash = _file_hash(target_path)
    signals_payload = {
        "schema_version": PAPER_SIGNALS_SCHEMA,
        "trade_date": trade_date,
        "snapshot_date": effective_date,
        "cash_target_weight": target_cash_weight,
        "signals": target_projection,
        "approved_target_hash": decision.content_hash,
        "session_id": session_manifest["session_id"],
        "allocation_id": allocation["allocation_id"],
        "allocation_content_hash": allocation["content_hash"],
        "paper_target_package_path": _display_path(repo_root, target_path),
        "paper_target_package_sha256": target_package_hash,
        "strategy_identity": identity,
        "meta": {
            "asof_date": effective_date,
            "authority": "DECISION",
            "exact_orders_deferred_to": "09:35 America/New_York",
        },
    }
    handoff_payload = {
        "schema_version": PAPER_HANDOFF_SCHEMA,
        "trade_date": trade_date,
        "run_id": str(legacy_handoff.get("run_id") or existing_contract.get("source_run_id") or ""),
        "mode": "PAPER",
        "execution_status": "TARGET_SEALED",
        "market_status": str(legacy_handoff.get("market_status") or "PREOPEN"),
        "trades": [],
        "planned_trade_count": 0,
        "execution_eligible_trades_count": 0,
        "target_portfolio": target_projection,
        "cash_target_weight": target_cash_weight,
        "approved_target_hash": decision.content_hash,
        "session_id": session_manifest["session_id"],
        "session_manifest_path": _display_path(repo_root, session_path),
        "session_manifest_sha256": session_hash,
        "sleeve_decisions_path": _display_path(repo_root, sleeve_decisions_path),
        "sleeve_decisions_sha256": sleeve_decisions_hash,
        "portfolio_allocation_path": _display_path(repo_root, allocation_path),
        "portfolio_allocation_sha256": allocation_hash,
        "allocation_id": allocation["allocation_id"],
        "allocation_content_hash": allocation["content_hash"],
        "signals_path": _display_path(repo_root, signals_path),
        "paper_target_package_path": _display_path(repo_root, target_path),
        "paper_target_package_sha256": target_package_hash,
        "source_sleeve_evaluations": _display_path(repo_root, sleeve_path),
        "source_sleeve_evaluations_sha256": sleeve_hash,
        "strategy_identity": identity,
        "target_attainment_policy": policy,
        "precompute_execution_authority": False,
        "exact_orders_deferred_to_0935": True,
        "research_precompute": {
            "execution_authority": False,
            "pair_hash": research_pair_hash,
            "daily_snapshot_path": _display_path(repo_root, research_snapshot_path),
            "signals_path": _display_path(repo_root, research_signals_path),
            "planned_execution_payload_path": _display_path(repo_root, research_handoff_path),
        },
    }
    daily_snapshot["strategy_identity"] = identity
    daily_snapshot["signals_snapshot_path"] = _display_path(repo_root, signals_path)
    daily_snapshot["approved_target_hash"] = decision.content_hash
    daily_snapshot["session_id"] = session_manifest["session_id"]
    daily_snapshot["session_manifest_path"] = _display_path(repo_root, session_path)
    daily_snapshot["allocation_id"] = allocation["allocation_id"]
    daily_snapshot["portfolio_allocation_path"] = _display_path(repo_root, allocation_path)
    daily_snapshot["paper_target_package_path"] = _display_path(repo_root, target_path)
    daily_snapshot["market_state_execution_authority"] = False
    if isinstance(daily_snapshot.get("proposed_trades"), list):
        daily_snapshot["research_proposed_trades"] = daily_snapshot.get("proposed_trades")
        daily_snapshot["proposed_trades"] = []

    snapshot_path.write_text(_pretty_json(daily_snapshot), encoding="utf-8")
    signals_path.write_text(_pretty_json(signals_payload), encoding="utf-8")
    handoff_path.write_text(_pretty_json(handoff_payload), encoding="utf-8")
    audit_manifest = build_audit_manifest(
        trade_date=trade_date,
        session_id=session_manifest["session_id"],
        approved_target_hash=decision.content_hash,
        repo_root=repo_root,
        artifacts=(
            {"name": "session_manifest", "path": session_path},
            {"name": "sleeve_evaluations", "path": sleeve_path},
            {"name": "sleeve_decisions", "path": sleeve_decisions_path},
            {"name": "portfolio_allocation", "path": allocation_path},
            {"name": "paper_target_package", "path": target_path},
            {"name": "daily_snapshot", "path": snapshot_path},
            {"name": "signals", "path": signals_path},
            {"name": "planned_execution_payload", "path": handoff_path},
        ),
        generated_at=sealed_timestamp,
    )
    audit_manifest_path = bundle_dir / "audit_manifest.json"
    audit_manifest_path.write_text(_pretty_json(audit_manifest), encoding="utf-8")
    contract = dict(existing_contract)
    contract.update(
        {
            "schema_version": SEALED_PRECOMPUTE_SCHEMA_VERSION,
            "status": "complete",
            "validated_for_execution": True,
            "validation_reason": None,
            "validation_failures": [],
            "authority_model": "registry_allocator_sealed_target_v1",
            "approved_target_hash": decision.content_hash,
            "session_id": session_manifest["session_id"],
            "session_content_hash": session_manifest["content_hash"],
            "allocation_id": allocation["allocation_id"],
            "allocation_content_hash": allocation["content_hash"],
            "precompute_execution_authority": False,
            "files": {
                "daily_snapshot": "daily_snapshot.json",
                "signals": "signals.json",
                "planned_execution_payload": "planned_execution_payload.json",
                "sleeve_evaluations": "sleeve_evaluations.json",
                "session_manifest": "session_manifest.json",
                "sleeve_decisions": "sleeve_decisions.json",
                "portfolio_allocation": "portfolio_allocation.json",
                "paper_target_package": "paper_target_package.json",
                "audit_manifest": "audit_manifest.json",
            },
            "summary": {
                "execution_status": "TARGET_SEALED",
                "target_name_count": len(target_projection),
                "target_cash_weight": target_cash_weight,
                "exact_order_count": None,
                "exact_orders_deferred_to_0935": True,
            },
        }
    )
    contract["file_sha256"] = {
        name: _file_hash(bundle_dir / filename)
        for name, filename in contract["files"].items()
    }
    contract_path.write_text(_pretty_json(contract), encoding="utf-8")

    failures = validate_sealed_paper_target_bundle(
        bundle_dir=bundle_dir,
        trade_date=trade_date,
        repo_root=repo_root,
    )
    if failures:
        raise PaperTargetAuthorityError(
            "sealed PAPER target failed validation: " + ",".join(failures[:5])
        )
    return target_package


def validate_sealed_paper_target_bundle(
    *,
    bundle_dir: Path,
    trade_date: str,
    repo_root: Path,
) -> list[str]:
    bundle_dir = Path(bundle_dir)
    repo_root = Path(repo_root).resolve()
    failures: list[str] = []
    try:
        contract = _read_object(bundle_dir / "contract.json")
        contract_schema = int(contract.get("schema_version") or 0)
        if contract_schema not in {
            LEGACY_SEALED_PRECOMPUTE_SCHEMA_VERSION,
            SEALED_PRECOMPUTE_SCHEMA_VERSION,
        }:
            return ["paper_target:unsealed_precompute_contract"]
        expected_authority_model = (
            "registry_allocator_sealed_target_v1"
            if contract_schema == SEALED_PRECOMPUTE_SCHEMA_VERSION
            else "orion_single_sealed_target_v1"
        )
        if contract.get("authority_model") != expected_authority_model:
            failures.append("paper_target:invalid_authority_model")
        files = contract.get("files")
        hashes = contract.get("file_sha256")
        if not isinstance(files, Mapping) or not isinstance(hashes, Mapping):
            return [*failures, "paper_target:contract_file_manifest_missing"]
        legacy_required = {
            "daily_snapshot",
            "signals",
            "planned_execution_payload",
            "sleeve_evaluations",
            "paper_target_package",
        }
        required = (
            legacy_required
            | {
                "session_manifest",
                "sleeve_decisions",
                "portfolio_allocation",
                "audit_manifest",
            }
            if contract_schema == SEALED_PRECOMPUTE_SCHEMA_VERSION
            else legacy_required
        )
        if set(files) != required:
            failures.append("paper_target:contract_file_manifest_mismatch")
        for name in sorted(required):
            path = bundle_dir / str(files.get(name) or "")
            if not path.is_file():
                failures.append(f"paper_target:missing_file:{name}")
            elif _file_hash(path) != str(hashes.get(name) or ""):
                failures.append(f"paper_target:file_hash_mismatch:{name}")
        if failures:
            return failures

        package = _read_object(bundle_dir / str(files["paper_target_package"]))
        signals = _read_object(bundle_dir / str(files["signals"]))
        handoff = _read_object(bundle_dir / str(files["planned_execution_payload"]))
        sleeve_payload = _read_object(bundle_dir / str(files["sleeve_evaluations"]))
        expected_package_schema = (
            PAPER_TARGET_SCHEMA
            if contract_schema == SEALED_PRECOMPUTE_SCHEMA_VERSION
            else LEGACY_PAPER_TARGET_SCHEMA
        )
        if package.get("schema_version") != expected_package_schema:
            failures.append("paper_target:invalid_package_schema")
        if signals.get("schema_version") != PAPER_SIGNALS_SCHEMA:
            failures.append("paper_target:invalid_signals_schema")
        if handoff.get("schema_version") != PAPER_HANDOFF_SCHEMA:
            failures.append("paper_target:invalid_handoff_schema")
        if any(
            str(payload.get("trade_date") or "") != str(trade_date)
            for payload in (contract, package, signals, handoff, sleeve_payload)
        ):
            failures.append("paper_target:trade_date_mismatch")

        evidence_raw = package.get("evidence_package")
        decision_raw = package.get("decision_package")
        if not isinstance(evidence_raw, Mapping) or not isinstance(decision_raw, Mapping):
            failures.append("paper_target:authority_packages_missing")
            return failures
        evidence = evidence_package_from_dict(evidence_raw)
        decision = decision_package_from_dict(decision_raw)
        if (
            decision.evidence_package_id != evidence.package_id
            or decision.evidence_hash != evidence.content_hash
        ):
            failures.append("paper_target:decision_evidence_lineage_mismatch")
        target_hash = str(package.get("approved_target_hash") or "")
        if not target_hash or target_hash != decision.content_hash:
            failures.append("paper_target:approved_target_hash_mismatch")
        if str(contract.get("approved_target_hash") or "") != target_hash:
            failures.append("paper_target:contract_target_hash_mismatch")
        if str(signals.get("approved_target_hash") or "") != target_hash:
            failures.append("paper_target:signals_target_hash_mismatch")
        if str(handoff.get("approved_target_hash") or "") != target_hash:
            failures.append("paper_target:handoff_target_hash_mismatch")
        target_rows = decision.to_dict()["target_rows"]
        package_rows = package.get("target_rows")
        signal_rows = signals.get("signals")
        handoff_rows = handoff.get("target_portfolio")
        if not (
            package_rows == target_rows
            and signal_rows == target_rows
            and handoff_rows == target_rows
        ):
            failures.append("paper_target:target_projection_mismatch")
        target_cash = float(package.get("target_cash_weight"))
        if abs(sum(float(row["target_weight"]) for row in target_rows) + target_cash - 1.0) > 1e-8:
            failures.append("paper_target:target_weights_do_not_sum_to_one")
        if handoff.get("trades") != []:
            failures.append("paper_target:precompute_contains_exact_trades")
        if handoff.get("precompute_execution_authority") is not False:
            failures.append("paper_target:precompute_execution_authority_not_false")
        if handoff.get("exact_orders_deferred_to_0935") is not True:
            failures.append("paper_target:exact_order_deferral_missing")

        if contract_schema == SEALED_PRECOMPUTE_SCHEMA_VERSION:
            session = _read_object(bundle_dir / str(files["session_manifest"]))
            decisions = _read_object(bundle_dir / str(files["sleeve_decisions"]))
            allocation = _read_object(bundle_dir / str(files["portfolio_allocation"]))
            audit_manifest = _read_object(bundle_dir / str(files["audit_manifest"]))
            if session.get("schema_version") != SESSION_SCHEMA:
                failures.append("paper_target:session_manifest_schema")
            if decisions.get("schema_version") != SLEEVE_DECISION_BATCH_SCHEMA:
                failures.append("paper_target:sleeve_decisions_schema")
            if allocation.get("schema_version") != ALLOCATION_SCHEMA:
                failures.append("paper_target:portfolio_allocation_schema")
            if audit_manifest.get("schema_version") != AUDIT_MANIFEST_SCHEMA:
                failures.append("paper_target:audit_manifest_schema")
            failures.extend(
                validate_operating_model_lineage(
                    session_manifest=session,
                    decision_batch=decisions,
                    allocation=allocation,
                )
            )
            if package.get("session_id") != session.get("session_id"):
                failures.append("paper_target:package_session_lineage")
            if package.get("session_content_hash") != session.get("content_hash"):
                failures.append("paper_target:package_session_hash")
            if package.get("allocation_id") != allocation.get("allocation_id"):
                failures.append("paper_target:package_allocation_lineage")
            if package.get("allocation_content_hash") != allocation.get("content_hash"):
                failures.append("paper_target:package_allocation_hash")
            if allocation.get("targets") != target_rows:
                failures.append("paper_target:allocation_target_projection_mismatch")
            manifest_body = dict(audit_manifest)
            declared_manifest_hash = str(manifest_body.pop("content_hash", ""))
            if declared_manifest_hash != _payload_hash(manifest_body):
                failures.append("paper_target:audit_manifest_content_hash")
            for row in audit_manifest.get("artifacts") or []:
                if not isinstance(row, Mapping):
                    failures.append("paper_target:audit_manifest_row_invalid")
                    continue
                artifact_path = _resolve_repo_path(repo_root, row.get("path"))
                if not artifact_path.is_file() or _file_hash(artifact_path) != str(
                    row.get("sha256") or ""
                ):
                    failures.append(
                        f"paper_target:audit_artifact_hash:{row.get('name')}"
                    )

        source_rows = (
            package.get("source_strategy_artifacts")
            if contract_schema == SEALED_PRECOMPUTE_SCHEMA_VERSION
            else [package.get("source_strategy_artifact") or {}]
        )
        if not isinstance(source_rows, list) or not source_rows:
            failures.append("paper_target:source_strategy_lineage_missing")
            source_rows = []
        for source in source_rows:
            if not isinstance(source, Mapping):
                failures.append("paper_target:source_strategy_lineage_invalid")
                continue
            source_path = _resolve_repo_path(repo_root, source.get("path"))
            if not source_path.is_file() or _file_hash(source_path) != str(
                source.get("sha256") or ""
            ):
                failures.append("paper_target:source_strategy_hash_mismatch")
        sleeve_ref = package.get("source_sleeve_evaluations") or {}
        if str(sleeve_ref.get("sha256") or "") != _file_hash(
            bundle_dir / str(files["sleeve_evaluations"])
        ):
            failures.append("paper_target:sleeve_evaluations_hash_mismatch")
        if contract_schema == LEGACY_SEALED_PRECOMPUTE_SCHEMA_VERSION:
            _sole_orion_envelope(sleeve_payload)
        else:
            capital_ids = sorted(package.get("capital_sleeves") or [])
            envelope_by_id = {
                str(row.get("sleeve_id") or ""): row
                for row in sleeve_payload.get("envelopes") or []
                if isinstance(row, Mapping)
            }
            source_ids = sorted(
                str(row.get("sleeve_id") or "")
                for row in source_rows
                if isinstance(row, Mapping)
            )
            if not capital_ids or source_ids != capital_ids:
                failures.append("paper_target:capital_source_set_mismatch")
            for sleeve_id in capital_ids:
                envelope = envelope_by_id.get(sleeve_id) or {}
                if (
                    (envelope.get("evaluation") or {}).get("status") != "OK"
                    or (envelope.get("eligibility") or {}).get(
                        "evaluation_usable_for_capital"
                    )
                    is not True
                    or (envelope.get("opportunity") or {}).get(
                        "decision_eligible"
                    )
                    is not True
                ):
                    failures.append(
                        f"paper_target:capital_sleeve_not_eligible:{sleeve_id}"
                    )
        identity = package.get("strategy_identity")
        identity_check = validate_lane_strategy_identity(
            identity=identity if isinstance(identity, Mapping) else {},
            approved_strategy=str(package.get("approved_sleeve") or ""),
            lane="paper",
        )
        if identity_check.get("status") != "PASS":
            failures.append("paper_target:strategy_identity_mismatch")
    except Exception as exc:
        failures.append(f"paper_target:validation_exception:{type(exc).__name__}:{exc}")
    return failures
