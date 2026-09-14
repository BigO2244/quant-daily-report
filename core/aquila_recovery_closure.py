"""Verify a completed governed Aquila recovery closes only its bound parents.

This is a read-only consumer of the recovery chain embedded in a successful
exact plan.  It deliberately does not turn failed attempts into successes:
it merely proves that their economic transition was closed by the one
hash-bound successor chain already approved for that session.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from authority.exact_plan import compute_starting_state_hash
from core.portfolio_operating_model import content_hash


class AquilaRecoveryClosureError(ValueError):
    """A claimed successor closure is incomplete, ambiguous, or altered."""


@dataclass(frozen=True)
class AquilaRecoveryClosure:
    """The two failed plan hashes and immutable evidence a successor closes."""

    parent_plan_hashes: frozenset[str]
    evidence_paths: tuple[Path, ...]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise AquilaRecoveryClosureError(reason)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AquilaRecoveryClosureError(f"invalid recovery artifact: {path}") from exc
    _require(isinstance(value, dict), f"recovery artifact is not an object: {path}")
    return value


def _content_hash(value: Mapping[str, Any], label: str) -> str:
    body = dict(value)
    digest = str(body.pop("content_hash", ""))
    _require(bool(digest) and digest == content_hash(body), f"{label} content hash mismatch")
    return digest


def _root_path(root: Path, raw: object) -> Path:
    path = Path(str(raw or ""))
    _require(bool(str(raw or "")) and not path.is_absolute(), "recovery path must be repo-relative")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AquilaRecoveryClosureError("recovery path escapes repo root") from exc
    return resolved


def _verify_refs(
    root: Path,
    refs: list[Mapping[str, Any]],
    *,
    label: str,
    expected_client_ids: set[str],
) -> tuple[Path, ...]:
    _require(bool(refs), f"{label} WAL references missing")
    clients: set[str] = set()
    evidence: list[Path] = []
    for ref in refs:
        path = _root_path(root, ref.get("path") or ref.get("intent_path"))
        expected = str(ref.get("sha256") or ref.get("intent_sha256") or "")
        _require(path.is_file() and _hash(path) == expected, f"{label} artifact hash mismatch")
        intent = _object(path)
        client_id = str(intent.get("client_order_id") or "")
        _require(client_id and client_id not in clients, f"{label} WAL client identity invalid")
        clients.add(client_id)
        evidence.append(path)
        expected_resolutions: set[Path] = set()
        for resolution in ref.get("resolutions") or []:
            resolution_path = _root_path(root, resolution.get("path"))
            _require(
                resolution_path.is_file()
                and _hash(resolution_path) == str(resolution.get("sha256") or ""),
                f"{label} resolution hash mismatch",
            )
            expected_resolutions.add(resolution_path)
            evidence.append(resolution_path)
        _require(bool(expected_resolutions), f"{label} WAL resolution missing")
        _require(
            {item.resolve() for item in resolution_path.parent.glob("*.json")} == expected_resolutions,
            f"{label} WAL has unbound resolution evidence",
        )
    _require(clients == expected_client_ids, f"{label} WAL client set mismatch")
    intent_dirs = {_root_path(root, ref.get("path") or ref.get("intent_path")).parent for ref in refs}
    for directory in intent_dirs:
        expected_paths = {
            _root_path(root, ref.get("path") or ref.get("intent_path"))
            for ref in refs
            if _root_path(root, ref.get("path") or ref.get("intent_path")).parent == directory
        }
        _require(
            {item.resolve() for item in directory.glob("*.json")} == expected_paths,
            f"{label} WAL has unbound intent evidence",
        )
    return tuple(dict.fromkeys(evidence))


def _state_hash(positions: object, cash: object, *, label: str) -> str:
    try:
        return compute_starting_state_hash(positions, cash)
    except (TypeError, ValueError, KeyError) as exc:
        raise AquilaRecoveryClosureError(f"{label} state is invalid") from exc


def verified_recovery_closure(
    *, repo_root: Path, successful_plan: Mapping[str, Any], trade_date: str
) -> AquilaRecoveryClosure | None:
    """Return exactly the failed parent hashes closed by a successful successor.

    ``successful_plan`` must already have passed exact-plan parsing and success
    receipt validation in the caller.  The function verifies the static
    recovery-chain evidence that was embedded in that exact plan.
    """

    root = Path(repo_root).resolve()
    constraints = successful_plan.get("constraints")
    _require(isinstance(constraints, Mapping), "successful plan constraints missing")
    authority = constraints.get("aquila_quantity_authority")
    _require(isinstance(authority, Mapping), "successful plan Aquila authority missing")
    bridge = authority.get("recovery_ownership_bridge")
    if bridge is None:
        return None
    _require(isinstance(bridge, Mapping), "recovery bridge is not an object")
    _content_hash(bridge, "recovery bridge")
    _require(bool(trade_date), "successful plan trade date missing")
    epoch = str(constraints.get("paper_drill_epoch") or "")
    _require(
        bridge.get("schema_version") == "caerus.aquila_recovery_chain.v1"
        and bridge.get("trade_date") == trade_date
        and bridge.get("epoch") == epoch
        and constraints.get("paper_drill_live_eligible") is False
        and bridge.get("original_plans_incomplete") is True,
        "successor recovery-chain identity invalid",
    )
    _require(
        bridge.get("account_id_hash") == successful_plan.get("account_id_hash"),
        "successor recovery-chain account mismatch",
    )

    policy_path = root / "config" / f"paper_intraday_drill_policy_{trade_date}.json"
    policy = _object(policy_path)
    _require(
        policy.get("schema_version") == "caerus.paper_intraday_drill_policy.v1"
        and policy.get("paper_only") is True
        and policy.get("live_eligible") is False
        and policy.get("trade_date") == trade_date
        and epoch in (policy.get("allowed_epochs") or []),
        "recovery policy identity invalid",
    )
    safety = policy.get("safety_contract")
    required_safety = (
        "account_date_mutex_remains_global",
        "epoch_reuse_is_idempotent_only",
        "unresolved_prior_epoch_blocks_submission",
        "wal_and_claim_records_are_append_only",
        "normal_hours_market_orders_after_close_prohibited",
    )
    _require(isinstance(safety, Mapping) and all(safety.get(key) is True for key in required_safety), "recovery policy safety invalid")
    configured = policy.get("ownership_bridge_chain")
    _require(isinstance(configured, Mapping), "recovery policy chain missing")
    parents = bridge.get("parents")
    _require(
        isinstance(parents, list)
        and len(parents) == 2
        and parents == configured.get("parents")
        and bridge.get("approved_target_hash") == configured.get("approved_target_hash"),
        "recovery chain differs from governed policy",
    )

    parent_hashes: list[str] = []
    parent_plans: list[Mapping[str, Any]] = []
    evidence_paths: list[Path] = [policy_path]
    for index, parent in enumerate(parents):
        _require(isinstance(parent, Mapping), "recovery parent invalid")
        expected_namespace = "canonical" if index == 0 else str(parent.get("wal_namespace") or "")
        _require(
            (index == 0 and expected_namespace == "canonical")
            or (index == 1 and expected_namespace and expected_namespace < epoch),
            "recovery parent namespace invalid",
        )
        envelope_path = _root_path(root, parent.get("exact_plan_path"))
        _require(
            envelope_path.is_file()
            and _hash(envelope_path) == str(parent.get("exact_plan_file_sha256") or ""),
            "recovery parent envelope hash mismatch",
        )
        envelope = _object(envelope_path)
        evidence_paths.append(envelope_path)
        exact = envelope.get("exact_execution_plan")
        _require(isinstance(exact, Mapping), "recovery parent exact plan missing")
        _require(
            envelope.get("trade_date") == trade_date
            and exact.get("account_id_hash") == successful_plan.get("account_id_hash")
            and exact.get("plan_id") == parent.get("plan_id")
            and exact.get("content_hash") == parent.get("plan_hash")
            and envelope.get("exact_execution_plan_hash") == parent.get("plan_hash"),
            "recovery parent plan identity mismatch",
        )
        parent_hashes.append(str(parent.get("plan_hash")))
        parent_plans.append(exact)
    _require(len(set(parent_hashes)) == 2, "recovery parent hashes overlap")

    p0_bridge = bridge.get("p0_bridge")
    _require(isinstance(p0_bridge, Mapping), "recovery P0 bridge missing")
    _content_hash(p0_bridge, "recovery P0 bridge")
    _require(
        p0_bridge.get("original_plan_hash") == parent_hashes[0]
        and p0_bridge.get("original_plan_id") == parents[0].get("plan_id")
        and p0_bridge.get("trade_date") == trade_date
        and p0_bridge.get("account_id_hash") == successful_plan.get("account_id_hash"),
        "recovery P0 bridge identity mismatch",
    )
    p0_proof = p0_bridge.get("economic_proof")
    _require(isinstance(p0_proof, Mapping), "recovery P0 proof missing")
    _require(
        content_hash(dict(p0_proof)) == p0_bridge.get("economic_proof_hash"),
        "recovery P0 proof hash mismatch",
    )
    _require(
        p0_proof.get("reconciliation_status") == "TERMINAL_FAILURE_STATE_RECONCILED"
        and p0_proof.get("plan_hash") == parent_hashes[0]
        and p0_proof.get("plan_id") == parents[0].get("plan_id")
        and p0_proof.get("starting_state_hash") == parent_plans[0].get("starting_state_hash")
        and p0_proof.get("final_state_hash") == parent_plans[1].get("starting_state_hash")
        and p0_proof.get("final_state_hash") == p0_bridge.get("current_state_hash")
        and _state_hash(
            p0_proof.get("final_positions"), p0_proof.get("final_cash"), label="recovery P0 proof"
        ) == p0_proof.get("final_state_hash"),
        "recovery P0 proof continuity invalid",
    )
    evidence_paths.extend(
        _verify_refs(
            root,
            list(p0_bridge.get("wal_files") or []),
            label="recovery P0",
            expected_client_ids=set(parents[0].get("expected_client_order_ids") or []),
        )
    )

    p1_proof = bridge.get("p1_economic_proof")
    _require(isinstance(p1_proof, Mapping), "recovery P1 proof missing")
    _require(
        content_hash(dict(p1_proof)) == bridge.get("p1_economic_proof_hash"),
        "recovery P1 proof hash mismatch",
    )
    _require(
        p1_proof.get("plan_hash") == parent_hashes[1]
        and p1_proof.get("plan_id") == parents[1].get("plan_id")
        and p1_proof.get("paper_drill_epoch") == parents[1].get("wal_namespace"),
        "recovery P1 proof identity mismatch",
    )
    _require(
        p1_proof.get("reconciliation_status") == "TERMINAL_FAILURE_STATE_RECONCILED"
        and p1_proof.get("starting_state_hash") == parent_plans[1].get("starting_state_hash")
        and p1_proof.get("final_state_hash") == bridge.get("current_state_hash")
        and p1_proof.get("final_state_hash") == successful_plan.get("starting_state_hash")
        and _state_hash(
            p1_proof.get("final_positions"), p1_proof.get("final_cash"), label="recovery P1 proof"
        ) == p1_proof.get("final_state_hash")
        and _state_hash(
            bridge.get("current_positions"), bridge.get("current_cash"), label="recovery successor"
        ) == bridge.get("current_state_hash"),
        "recovery P1 proof continuity invalid",
    )
    evidence_paths.extend(
        _verify_refs(
            root,
            list(bridge.get("p1_wal_files") or []),
            label="recovery P1",
            expected_client_ids=set(parents[1].get("expected_client_order_ids") or []),
        )
    )
    p1_clients = {str(row.get("client_order_id") or "") for row in p1_proof.get("broker_fills") or []}
    _require(
        p1_clients == set(parents[1].get("expected_client_order_ids") or []),
        "recovery P1 proof client set mismatch",
    )
    _require(
        bridge.get("p1_applied_fills") == p1_proof.get("broker_fills"),
        "recovery P1 applied fills differ from proof",
    )
    quantity_contract = authority.get("quantity_contract")
    _require(isinstance(quantity_contract, Mapping), "successor quantity contract missing")
    _require(
        bridge.get("quantity_contract_hash") == _content_hash(quantity_contract, "successor quantity contract")
        and bridge.get("frozen_ownership_hash") == quantity_contract.get("ownership_snapshot_hash")
        and bridge.get("frozen_ownership_sha256") == quantity_contract.get("ownership_snapshot_sha256")
        and authority.get("ownership_snapshot_sha256") == bridge.get("frozen_ownership_sha256")
        and p0_bridge.get("frozen_ownership_hash") == bridge.get("frozen_ownership_hash")
        and p0_bridge.get("frozen_ownership_sha256") == bridge.get("frozen_ownership_sha256"),
        "recovery frozen ownership bindings invalid",
    )
    return AquilaRecoveryClosure(
        parent_plan_hashes=frozenset(parent_hashes),
        evidence_paths=tuple(dict.fromkeys(evidence_paths)),
    )


def covered_parent_plan_hashes(
    *, repo_root: Path, successful_plan: Mapping[str, Any], trade_date: str
) -> frozenset[str]:
    """Compatibility view of a verified recovery closure's bound parents."""

    closure = verified_recovery_closure(
        repo_root=repo_root,
        successful_plan=successful_plan,
        trade_date=trade_date,
    )
    return closure.parent_plan_hashes if closure is not None else frozenset()
