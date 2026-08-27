"""Canonical, non-trading sleeve inventory and evaluation dispatcher.

The control plane observes existing named-strategy and functional-sleeve
artifacts.  It does not run signal code, allocate capital, construct orders, or
submit to a broker.  Every non-retired, non-frozen sleeve receives exactly one
terminal evaluation envelope so missing research implementations and data
dependencies cannot disappear silently.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping


REGISTRY_SCHEMA_VERSION = "caerus_sleeve_control_plane_registry_v1"
ENVELOPE_SCHEMA_VERSION = "caerus_sleeve_evaluation_v1"
BATCH_SCHEMA_VERSION = "caerus_all_sleeve_evaluation_v1"
TERMINAL_STATUSES = ("OK", "NO_OPPORTUNITY", "BLOCKED", "FAILED")
ACTIVE_LIFECYCLE_STATUSES = frozenset({"research", "shadow", "paper"})
ORION_DECISION_LINEAGE_SCHEMA = "caerus.orion_decision_lineage.v1"
ORION_DECISION_LINEAGE_HASH_FIELDS = (
    "market_data_hash",
    "normalized_panel_hash",
    "feature_hash",
    "full_rank_history_hash",
    "rank_table_hash",
    "target_weights_hash",
)
ORION_DECISION_LINEAGE_STAGES = (
    "market_data",
    "normalized_panel",
    "features",
    "full_rank_history",
    "current_rank_table",
    "target_weights",
)


class SleeveRegistryIntegrityError(ValueError):
    """Raised when canonical sleeve identity or eligibility is inconsistent."""


class SleeveEvaluationBlocked(RuntimeError):
    """Expected inability to evaluate due to a declared dependency or gate."""


def _canonical_payload_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_orion_decision_lineage(
    source_payload: Mapping[str, Any],
    *,
    effective_trade_date: str,
    previous_source_payload: Mapping[str, Any] | None = None,
) -> list[str]:
    """Validate Orion's causal computation chain for capital use.

    An unchanged target is allowed when fresh feature/rank computation supports
    it. A child stage that is byte-identical after its direct parent changed is
    treated as suspicious and fails closed.
    """

    prefix = "orion_lineage"
    if source_payload.get("decision_eligible") is not True:
        failures = [f"{prefix}:source_decision_not_eligible"]
    else:
        failures = []
    if source_payload.get("observation_status") != "OK":
        failures.append(f"{prefix}:source_observation:not_ok")
    if source_payload.get("data_status") != "OK":
        failures.append(f"{prefix}:source_data:not_ok")
    lineage = source_payload.get("decision_lineage")
    if not isinstance(lineage, Mapping):
        return [*failures, f"{prefix}:missing"]
    if lineage.get("schema_version") != ORION_DECISION_LINEAGE_SCHEMA:
        failures.append(f"{prefix}:invalid_schema")

    for field in ORION_DECISION_LINEAGE_HASH_FIELDS:
        value = str(lineage.get(field) or "")
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            failures.append(f"{prefix}:{field}:invalid")

    for field in (
        "trade_date",
        "effective_trade_date",
        "market_data_asof",
        "generated_at_utc",
        "model_version",
        "source_variant",
    ):
        if not str(lineage.get(field) or "").strip():
            failures.append(f"{prefix}:{field}:missing")
    for field in ("parent_artifact_hashes", "coverage", "stage_diagnostics"):
        if not isinstance(lineage.get(field), Mapping) or not lineage.get(field):
            failures.append(f"{prefix}:{field}:missing")
    if not isinstance(lineage.get("selection_trace"), (list, Mapping)):
        failures.append(f"{prefix}:selection_trace:invalid")
    if str(lineage.get("effective_trade_date") or "") != effective_trade_date:
        failures.append(f"{prefix}:effective_trade_date:mismatch")
    if str(lineage.get("trade_date") or "") != effective_trade_date:
        failures.append(f"{prefix}:trade_date:mismatch")
    if source_payload.get("coverage_status") != "OK":
        failures.append(f"{prefix}:source_coverage:not_ok")
    coverage = lineage.get("coverage")
    if isinstance(coverage, Mapping):
        if coverage.get("status") != "OK":
            failures.append(f"{prefix}:coverage:not_ok")
        if str(coverage.get("current_session") or "") != effective_trade_date:
            failures.append(f"{prefix}:coverage:current_session_mismatch")
        anchors = coverage.get("required_anchor_dates")
        if not isinstance(anchors, (list, tuple)) or not anchors:
            failures.append(f"{prefix}:coverage:required_anchor_dates_missing")
        else:
            for anchor in anchors:
                try:
                    if dt.date.fromisoformat(str(anchor)) >= dt.date.fromisoformat(
                        effective_trade_date
                    ):
                        failures.append(f"{prefix}:coverage:anchor_not_prior")
                except ValueError:
                    failures.append(f"{prefix}:coverage:anchor_invalid")
        if coverage.get("missing_current_session_symbols") != []:
            failures.append(f"{prefix}:coverage:current_session_incomplete")
        missing_anchors = coverage.get("missing_required_anchor_symbols")
        if not isinstance(missing_anchors, Mapping) or any(
            bool(symbols) for symbols in missing_anchors.values()
        ):
            failures.append(f"{prefix}:coverage:anchors_incomplete")

    stage_diagnostics = lineage.get("stage_diagnostics")
    if isinstance(stage_diagnostics, Mapping):
        if set(stage_diagnostics) != set(ORION_DECISION_LINEAGE_STAGES):
            failures.append(f"{prefix}:stage_diagnostics:stage_set_mismatch")
        for stage in ORION_DECISION_LINEAGE_STAGES:
            diagnostic = stage_diagnostics.get(stage)
            stage_prefix = f"{prefix}:stage_diagnostics:{stage}"
            if not isinstance(diagnostic, Mapping):
                failures.append(f"{stage_prefix}:missing")
                continue
            if diagnostic.get("stage") != stage:
                failures.append(f"{stage_prefix}:identity_mismatch")
            if not str(diagnostic.get("source_identity") or "").strip():
                failures.append(f"{stage_prefix}:source_identity_missing")
            for count_field in ("row_count", "symbol_count"):
                value = diagnostic.get(count_field)
                minimum = 0 if stage == "target_weights" else 1
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < minimum
                ):
                    failures.append(f"{stage_prefix}:{count_field}_invalid")
            if str(diagnostic.get("max_market_timestamp") or "") != effective_trade_date:
                failures.append(f"{stage_prefix}:max_market_timestamp_mismatch")

    try:
        market_asof = dt.datetime.fromisoformat(
            str(lineage.get("market_data_asof") or "").replace("Z", "+00:00")
        )
        if market_asof.date().isoformat() != effective_trade_date:
            failures.append(f"{prefix}:market_data_asof:mismatch")
    except ValueError:
        failures.append(f"{prefix}:market_data_asof:invalid")
    try:
        generated_at = dt.datetime.fromisoformat(
            str(lineage.get("generated_at_utc") or "").replace("Z", "+00:00")
        )
        if generated_at.tzinfo is None or generated_at.utcoffset() != dt.timedelta(0):
            failures.append(f"{prefix}:generated_at_utc:not_utc")
    except ValueError:
        failures.append(f"{prefix}:generated_at_utc:invalid")

    source_variant = str(source_payload.get("source_variant") or "").strip()
    model_version = str(lineage.get("model_version") or "").strip()
    if source_variant and model_version != source_variant:
        failures.append(f"{prefix}:model_version:mismatch")
    if source_variant and str(lineage.get("source_variant") or "") != source_variant:
        failures.append(f"{prefix}:source_variant:mismatch")

    parents = lineage.get("parent_artifact_hashes")
    if isinstance(parents, Mapping):
        expected_parents = {
            "normalized_panel": lineage.get("market_data_hash"),
            "features": lineage.get("normalized_panel_hash"),
            "full_rank_history": lineage.get("feature_hash"),
            "current_rank_table": lineage.get("full_rank_history_hash"),
            "target_weights": lineage.get("rank_table_hash"),
        }
        for stage, expected_hash in expected_parents.items():
            if parents.get(stage) != expected_hash:
                failures.append(f"{prefix}:parent_artifact_hashes:{stage}:mismatch")

    weights = source_payload.get("target_weights")
    if isinstance(weights, Mapping):
        try:
            normalized_weights = {
                str(symbol).strip().upper(): round(float(weight), 6)
                for symbol, weight in sorted(weights.items(), key=lambda item: str(item[0]))
            }
            if _canonical_payload_hash(normalized_weights) != lineage.get(
                "target_weights_hash"
            ):
                failures.append(f"{prefix}:target_weights_hash:mismatch")
            target_diagnostic = (
                stage_diagnostics.get("target_weights")
                if isinstance(stage_diagnostics, Mapping)
                else None
            )
            if isinstance(target_diagnostic, Mapping) and (
                target_diagnostic.get("row_count") != len(normalized_weights)
                or target_diagnostic.get("symbol_count") != len(normalized_weights)
            ):
                failures.append(f"{prefix}:target_weights_diagnostics:mismatch")
        except (TypeError, ValueError):
            failures.append(f"{prefix}:target_weights_hash:unverifiable")
    else:
        failures.append(f"{prefix}:target_weights:missing_or_invalid")

    previous = (
        previous_source_payload.get("decision_lineage")
        if isinstance(previous_source_payload, Mapping)
        else None
    )
    if previous_source_payload is None:
        failures.append(f"{prefix}:prior_source_missing")
    elif not isinstance(previous, Mapping):
        failures.append(f"{prefix}:prior_lineage_missing_or_legacy")
    else:
        prior_only_anchor = (
            previous_source_payload.get("decision_eligible") is False
            and previous_source_payload.get("authority_scope")
            == "PRIOR_LINEAGE_TRUST_ANCHOR"
            and str(previous_source_payload.get("valid_as_prior_only_for") or "")
            == effective_trade_date
        )
        try:
            from paper.trading_calendar import prev_trading_day

            expected_prior_date = prev_trading_day(effective_trade_date)
            prior_failures = validate_orion_decision_lineage(
                previous_source_payload,
                effective_trade_date=expected_prior_date,
                previous_source_payload=None,
            )
            prior_failures = [
                item
                for item in prior_failures
                if item != f"{prefix}:prior_source_missing"
                and not (
                    prior_only_anchor
                    and item == f"{prefix}:source_decision_not_eligible"
                )
            ]
            if previous_source_payload.get("decision_eligible") is False and not prior_only_anchor:
                failures.append(f"{prefix}:prior_anchor_scope_invalid")
            failures.extend(
                f"{prefix}:prior_invalid:{item.removeprefix(prefix + ':')}"
                for item in prior_failures
            )
        except (TypeError, ValueError):
            failures.append(f"{prefix}:prior_effective_trade_date_invalid")
        stages = ORION_DECISION_LINEAGE_HASH_FIELDS
        if all(lineage.get(field) == previous.get(field) for field in stages):
            failures.append(f"{prefix}:copied_forward")
        for parent, child in zip(stages, stages[1:]):
            if (
                lineage.get(parent) != previous.get(parent)
                and lineage.get(child) == previous.get(child)
                and child != "target_weights_hash"
            ):
                failures.append(
                    f"{prefix}:stale_child:{child}:changed_parent:{parent}"
                )
    return failures


@dataclass(frozen=True)
class SleeveDefinition:
    sleeve_id: str
    display_name: str
    strategy_type: str
    family: str
    lifecycle_status: str
    role: str | None
    benchmark: str | None
    execution_impact: str
    frozen: bool
    frozen_reason: str | None
    runner: str
    source_artifact: str
    artifact_key: str | None
    universe_family: str
    universe_method: str
    universe_source: str | None
    availability_policy: str
    capital_eligible: bool
    execution_eligible: bool
    evaluation_only: bool
    blocked_reason: str | None
    registry_origin: str

    @property
    def evaluated(self) -> bool:
        return self.lifecycle_status != "retired" and not self.frozen


@dataclass(frozen=True)
class RunnerResult:
    status: str
    reason_codes: tuple[str, ...]
    message: str
    opportunity: dict[str, Any]
    source_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_STATUSES:
            raise ValueError(f"invalid terminal status: {self.status!r}")


class SleeveControlRegistry:
    def __init__(
        self,
        *,
        definitions: list[SleeveDefinition],
        paper_capital_authority: str,
        paper_allocation_policy: Mapping[str, Any],
        registry_path: Path,
        manifest_path: Path,
    ) -> None:
        self.definitions = tuple(definitions)
        self.paper_capital_authority = paper_capital_authority
        self.paper_allocation_policy = dict(paper_allocation_policy)
        self.registry_path = registry_path
        self.manifest_path = manifest_path
        self._by_id = {item.sleeve_id: item for item in self.definitions}
        if len(self._by_id) != len(self.definitions):
            raise SleeveRegistryIntegrityError("duplicate sleeve_id in canonical registry")
        self._validate_authority()

    @classmethod
    def from_path(
        cls,
        registry_path: str | Path,
        *,
        manifest_path: str | Path | None = None,
        enforce_manifest_parity: bool = True,
    ) -> "SleeveControlRegistry":
        path = Path(registry_path)
        payload = _read_json(path)
        control = payload.get("sleeve_control_plane")
        if not isinstance(control, Mapping):
            raise SleeveRegistryIntegrityError("strategy registry missing sleeve_control_plane")
        if control.get("schema_version") != REGISTRY_SCHEMA_VERSION:
            raise SleeveRegistryIntegrityError(
                f"unsupported sleeve control-plane schema: {control.get('schema_version')!r}"
            )
        if tuple(control.get("terminal_statuses") or ()) != TERMINAL_STATUSES:
            raise SleeveRegistryIntegrityError(
                "terminal_statuses must be exactly OK, NO_OPPORTUNITY, BLOCKED, FAILED"
            )

        strategy_rows = payload.get("strategies")
        overrides = control.get("strategy_overrides")
        functional_rows = control.get("functional_sleeves")
        if not isinstance(strategy_rows, list) or not isinstance(overrides, Mapping):
            raise SleeveRegistryIntegrityError(
                "strategy registry must contain strategies and control-plane strategy_overrides"
            )
        if not isinstance(functional_rows, list):
            raise SleeveRegistryIntegrityError("functional_sleeves must be a list")

        strategy_ids = {
            str(row.get("strategy_id") or "").strip()
            for row in strategy_rows
            if isinstance(row, Mapping)
        }
        override_ids = {str(key).strip() for key in overrides}
        if not strategy_ids or "" in strategy_ids:
            raise SleeveRegistryIntegrityError("strategy registry contains a blank strategy_id")
        if strategy_ids != override_ids:
            missing = sorted(strategy_ids - override_ids)
            extra = sorted(override_ids - strategy_ids)
            raise SleeveRegistryIntegrityError(
                f"control-plane strategy override mismatch: missing={missing} extra={extra}"
            )

        definitions: list[SleeveDefinition] = []
        for row in strategy_rows:
            if not isinstance(row, Mapping):
                raise SleeveRegistryIntegrityError("strategy registry row must be an object")
            strategy_id = str(row.get("strategy_id") or "").strip()
            override = overrides[strategy_id]
            if not isinstance(override, Mapping):
                raise SleeveRegistryIntegrityError(
                    f"{strategy_id}: control-plane override must be an object"
                )
            definitions.append(
                _definition_from_payload(
                    {**dict(row), **dict(override), "sleeve_id": strategy_id},
                    registry_origin="named_strategy",
                )
            )
        for row in functional_rows:
            if not isinstance(row, Mapping):
                raise SleeveRegistryIntegrityError("functional sleeve row must be an object")
            definitions.append(
                _definition_from_payload(row, registry_origin="legacy_functional")
            )

        repo_root = path.resolve().parents[2]
        configured_manifest = str(control.get("manifest_path") or "").strip()
        resolved_manifest = (
            Path(manifest_path)
            if manifest_path is not None
            else repo_root / configured_manifest
        )
        registry = cls(
            definitions=definitions,
            paper_capital_authority=str(control.get("paper_capital_authority") or "").strip(),
            paper_allocation_policy=(
                control.get("paper_allocation_policy")
                if isinstance(control.get("paper_allocation_policy"), Mapping)
                else {}
            ),
            registry_path=path,
            manifest_path=resolved_manifest,
        )
        if enforce_manifest_parity:
            registry.validate_manifest_parity()
        return registry

    def get(self, sleeve_id: str) -> SleeveDefinition | None:
        return self._by_id.get(sleeve_id)

    def require(self, sleeve_id: str) -> SleeveDefinition:
        item = self.get(sleeve_id)
        if item is None:
            raise KeyError(f"unknown sleeve_id: {sleeve_id}")
        return item

    def evaluated_definitions(self) -> tuple[SleeveDefinition, ...]:
        return tuple(item for item in self.definitions if item.evaluated)

    def frozen_definitions(self) -> tuple[SleeveDefinition, ...]:
        return tuple(item for item in self.definitions if item.frozen)

    def functional_allocation_keys(self) -> frozenset[str]:
        return frozenset(
            item.artifact_key
            for item in self.definitions
            if item.registry_origin == "legacy_functional" and item.artifact_key
        )

    def validate_allocations_registered(
        self,
        daily_snapshot: Mapping[str, Any],
        *,
        tolerance: float = 1e-10,
    ) -> None:
        allocations = daily_snapshot.get("sleeve_allocations") or {}
        if not isinstance(allocations, Mapping):
            raise SleeveRegistryIntegrityError("daily_snapshot.sleeve_allocations must be an object")
        registered = self.functional_allocation_keys()
        unregistered: list[str] = []
        for key, raw_value in allocations.items():
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                raise SleeveRegistryIntegrityError(
                    f"allocation for {key!r} is not numeric"
                ) from None
            if not math.isfinite(value):
                raise SleeveRegistryIntegrityError(
                    f"allocation for {key!r} is not finite"
                )
            if abs(value) > tolerance and str(key) not in registered:
                unregistered.append(str(key))
        if unregistered:
            raise SleeveRegistryIntegrityError(
                "unregistered allocatable sleeves: " + ", ".join(sorted(unregistered))
            )

    def validate_manifest_parity(self) -> None:
        try:
            manifest = _read_json(self.manifest_path)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise SleeveRegistryIntegrityError(
                f"unable to validate sleeve manifest parity: {exc}"
            ) from exc
        rows = manifest.get("sleeves")
        if not isinstance(rows, list):
            raise SleeveRegistryIntegrityError("sleeve manifest missing sleeves list")
        by_strategy = {
            str(row.get("strategy_id") or "").strip(): row
            for row in rows
            if isinstance(row, Mapping) and row.get("strategy_id")
        }
        named = {
            item.sleeve_id: item
            for item in self.definitions
            if item.registry_origin == "named_strategy"
            and item.strategy_type not in {"benchmark", "reference_portfolio"}
        }
        if set(by_strategy) != set(named):
            raise SleeveRegistryIntegrityError(
                "strategy registry / sleeve manifest identity mismatch: "
                f"registry_only={sorted(set(named) - set(by_strategy))} "
                f"manifest_only={sorted(set(by_strategy) - set(named))}"
            )
        for strategy_id, definition in named.items():
            row = by_strategy[strategy_id]
            if str(row.get("strategy_registry_status") or "") != definition.lifecycle_status:
                raise SleeveRegistryIntegrityError(
                    f"{strategy_id}: manifest strategy_registry_status does not match registry"
                )
            if row.get("control_plane_frozen") is not definition.frozen:
                raise SleeveRegistryIntegrityError(
                    f"{strategy_id}: manifest control_plane_frozen does not match registry"
                )
            expected_manifest_status = (
                "current_paper_authority"
                if definition.lifecycle_status == "paper"
                else "current_shadow_baseline"
                if definition.lifecycle_status == "shadow" and definition.role == "baseline"
                else "current_shadow_challenger"
                if definition.lifecycle_status == "shadow"
                else "research_placeholder"
                if definition.lifecycle_status == "research"
                else None
            )
            if expected_manifest_status and row.get("status") != expected_manifest_status:
                raise SleeveRegistryIntegrityError(
                    f"{strategy_id}: manifest status does not match registry lifecycle"
                )
            expected_stage = (
                "paper_observed"
                if definition.lifecycle_status == "paper"
                else "shadow_observed"
                if definition.lifecycle_status == "shadow"
                else None
            )
            if expected_stage and row.get("lifecycle_stage") != expected_stage:
                raise SleeveRegistryIntegrityError(
                    f"{strategy_id}: manifest lifecycle_stage does not match registry"
                )

    def _validate_authority(self) -> None:
        if not self.paper_capital_authority:
            raise SleeveRegistryIntegrityError("paper_capital_authority is required")
        if self.paper_capital_authority != "caerus_orion":
            raise SleeveRegistryIntegrityError(
                "caerus_orion must remain the current primary PAPER sleeve"
            )
        capital = [item for item in self.definitions if item.capital_eligible]
        execution = [item for item in self.definitions if item.execution_eligible]
        if not capital or self.paper_capital_authority not in {
            item.sleeve_id for item in capital
        }:
            raise SleeveRegistryIntegrityError(
                "the current primary PAPER sleeve must remain capital eligible"
            )
        if {item.sleeve_id for item in execution} != {
            item.sleeve_id for item in capital
        }:
            raise SleeveRegistryIntegrityError(
                "capital and PAPER execution eligibility must match"
            )
        authority = self.require(self.paper_capital_authority)
        if authority.lifecycle_status != "paper" or authority.execution_impact != "PAPER":
            raise SleeveRegistryIntegrityError(
                "paper capital authority must have lifecycle=paper and execution_impact=PAPER"
            )
        if authority.evaluation_only or authority.frozen:
            raise SleeveRegistryIntegrityError(
                "paper capital authority cannot be evaluation-only or frozen"
            )
        for item in capital:
            if item.lifecycle_status != "paper" or item.execution_impact != "PAPER":
                raise SleeveRegistryIntegrityError(
                    f"{item.sleeve_id}: capital sleeves require lifecycle=paper and execution_impact=PAPER"
                )
            if item.evaluation_only or item.frozen:
                raise SleeveRegistryIntegrityError(
                    f"{item.sleeve_id}: capital sleeves cannot be evaluation-only or frozen"
                )
        for item in self.definitions:
            if item.capital_eligible:
                continue
            if not item.evaluation_only or item.execution_impact != "NON_EXECUTIONAL":
                raise SleeveRegistryIntegrityError(
                    f"{item.sleeve_id}: non-capital sleeves must be evaluation-only and NON_EXECUTIONAL"
                )
        from core.portfolio_operating_model import ALLOCATION_POLICY_SCHEMA

        policy = self.paper_allocation_policy
        if policy.get("schema_version") != ALLOCATION_POLICY_SCHEMA:
            raise SleeveRegistryIntegrityError("paper allocation policy schema is invalid")
        if policy.get("method") != "configured_risk_budget":
            raise SleeveRegistryIntegrityError("paper allocator method is not approved")
        if policy.get("unavailable_policy") != "fail_closed":
            raise SleeveRegistryIntegrityError("paper allocator must fail closed")
        if bool((policy.get("governance") or {}).get("automatic_promotion_enabled")):
            raise SleeveRegistryIntegrityError("automatic sleeve promotion must remain disabled")
        if bool((policy.get("governance") or {}).get("live_enabled")):
            raise SleeveRegistryIntegrityError("paper allocation policy cannot enable live")
        if str((policy.get("governance") or {}).get("current_primary_sleeve") or "") != self.paper_capital_authority:
            raise SleeveRegistryIntegrityError(
                "allocation policy primary sleeve must match the registry primary"
            )
        raw_budgets = policy.get("sleeve_risk_budgets")
        if not isinstance(raw_budgets, Mapping):
            raise SleeveRegistryIntegrityError("paper sleeve risk budgets are missing")
        if set(raw_budgets) != {item.sleeve_id for item in capital}:
            raise SleeveRegistryIntegrityError(
                "paper sleeve risk budgets must match capital-eligible sleeves"
            )
        try:
            budget_total = sum(float(value) for value in raw_budgets.values())
            policy_cash = float(policy.get("target_cash_weight"))
        except (TypeError, ValueError) as exc:
            raise SleeveRegistryIntegrityError("paper allocation weights are invalid") from exc
        if abs(budget_total - 1.0) > 1e-10:
            raise SleeveRegistryIntegrityError("paper sleeve risk budgets must sum to one")
        if abs(policy_cash - 0.05) > 1e-12:
            raise SleeveRegistryIntegrityError("paper allocator must retain approved 5% cash")
        freshness = policy.get("source_freshness") or {}
        if (
            freshness.get("calendar") != "XNYS"
            or freshness.get("allowed_sessions")
            != "CURRENT_OR_PREVIOUS_TRADING_SESSION"
            or int(freshness.get("max_trading_session_lag") or -1) != 1
        ):
            raise SleeveRegistryIntegrityError(
                "paper allocator source freshness policy is invalid"
            )
        from core.target_attainment_policy import validate_target_attainment_policy

        validate_target_attainment_policy(
            policy.get("account_target_attainment_policy"),
            expected_target_cash_weight=policy_cash,
        )


def default_registry_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "research" / "strategy_registry.json"


def load_sleeve_control_registry(
    path: str | Path | None = None,
    *,
    manifest_path: str | Path | None = None,
    enforce_manifest_parity: bool = True,
) -> SleeveControlRegistry:
    return SleeveControlRegistry.from_path(
        path or default_registry_path(),
        manifest_path=manifest_path,
        enforce_manifest_parity=enforce_manifest_parity,
    )


def dispatch_all_sleeves(
    *,
    trade_date: str,
    run_id: str,
    daily_snapshot: Mapping[str, Any],
    runtime_root: str | Path = ".",
    registry: SleeveControlRegistry | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    control = registry or load_sleeve_control_registry()
    control.validate_allocations_registered(daily_snapshot)
    root = Path(runtime_root)
    generated_at = (now or dt.datetime.now(dt.timezone.utc)).astimezone(
        dt.timezone.utc
    ).isoformat()
    code_sha = _git_sha(root)
    registry_sha = _sha256(control.registry_path)
    manifest_sha = _sha256(control.manifest_path)

    runners: dict[
        str,
        Callable[[SleeveDefinition, str, Mapping[str, Any], Path], RunnerResult],
    ] = {
        "shadow_snapshot": _run_shadow_snapshot,
        "shadow_benchmark": _run_shadow_benchmark,
        "precompute_snapshot": _run_precompute_snapshot,
        "research_snapshot": _run_research_snapshot,
    }
    envelopes: list[dict[str, Any]] = []
    for definition in control.evaluated_definitions():
        reason_codes: list[str] = []
        if definition.blocked_reason:
            result = RunnerResult(
                status="BLOCKED",
                reason_codes=("GOVERNANCE_BLOCK", definition.blocked_reason.upper()),
                message=definition.blocked_reason,
                opportunity={"available": False},
            )
        elif definition.runner not in runners:
            result = RunnerResult(
                status="BLOCKED",
                reason_codes=("RUNNER_NOT_REGISTERED",),
                message=f"no evaluator registered for {definition.runner}",
                opportunity={"available": False},
            )
        else:
            try:
                result = runners[definition.runner](
                    definition,
                    trade_date,
                    daily_snapshot,
                    root,
                )
            except SleeveEvaluationBlocked as exc:
                result = RunnerResult(
                    status="BLOCKED",
                    reason_codes=("SOURCE_DEPENDENCY_BLOCKED",),
                    message=str(exc),
                    opportunity={"available": False},
                )
            except Exception as exc:
                result = RunnerResult(
                    status="FAILED",
                    reason_codes=("RUNNER_EXCEPTION", type(exc).__name__.upper()),
                    message=str(exc),
                    opportunity={"available": False},
                )
        reason_codes.extend(result.reason_codes)
        if definition.universe_method in {
            "legacy_current_universe",
            "pit_universe_required",
        }:
            reason_codes.append("NON_DECISION_GRADE_UNIVERSE")
        if definition.evaluation_only:
            reason_codes.append("EVALUATION_ONLY")
        source_decision_eligible = result.opportunity.get("decision_eligible") is not False
        if definition.capital_eligible and (
            result.status != "OK" or not source_decision_eligible
        ):
            reason_codes.append("CAPITAL_SOURCE_NOT_DECISION_ELIGIBLE")

        envelopes.append(
            {
                "schema_version": ENVELOPE_SCHEMA_VERSION,
                "trade_date": trade_date,
                "run_id": run_id,
                "sleeve_id": definition.sleeve_id,
                "display_name": definition.display_name,
                "registry_origin": definition.registry_origin,
                "strategy_type": definition.strategy_type,
                "family": definition.family,
                "role": definition.role,
                "benchmark": definition.benchmark,
                "lifecycle": {
                    "status": definition.lifecycle_status,
                    "frozen": definition.frozen,
                    "frozen_reason": definition.frozen_reason,
                },
                "evaluation": {
                    "status": result.status,
                    "runner": definition.runner,
                    "message": result.message,
                    "evaluated_at": generated_at,
                },
                "opportunity": result.opportunity,
                "eligibility": {
                    "evaluation_eligible": True,
                    "evaluation_only": definition.evaluation_only,
                    "capital_eligible": definition.capital_eligible,
                    "paper_execution_eligible": definition.execution_eligible,
                    "live_execution_eligible": False,
                    "evaluation_usable_for_capital": bool(
                        definition.capital_eligible
                        and result.status == "OK"
                        and source_decision_eligible
                    ),
                    "execution_impact": definition.execution_impact,
                },
                "universe": _universe_provenance(definition, root),
                "provenance": {
                    "availability_policy": definition.availability_policy,
                    "configured_source_artifact": definition.source_artifact,
                    "source_artifacts": [
                        _source_provenance(path, root) for path in result.source_paths
                    ],
                    "registry_path": _display_path(control.registry_path, root),
                    "registry_sha256": registry_sha,
                    "manifest_path": _display_path(control.manifest_path, root),
                    "manifest_sha256": manifest_sha,
                    "code_sha": code_sha,
                },
                "reason_codes": sorted(set(reason_codes)),
            }
        )

    expected_ids = [item.sleeve_id for item in control.evaluated_definitions()]
    actual_ids = [item["sleeve_id"] for item in envelopes]
    if actual_ids != expected_ids:
        raise SleeveRegistryIntegrityError(
            "dispatcher did not emit exactly one envelope per non-frozen sleeve"
        )
    status_counts = Counter(item["evaluation"]["status"] for item in envelopes)
    return {
        "schema_version": BATCH_SCHEMA_VERSION,
        "trade_date": trade_date,
        "run_id": run_id,
        "generated_at": generated_at,
        "paper_capital_authority": control.paper_capital_authority,
        "all_non_frozen_evaluated": True,
        "expected_non_frozen_sleeve_ids": expected_ids,
        "frozen_sleeves": [
            {
                "sleeve_id": item.sleeve_id,
                "reason": item.frozen_reason,
            }
            for item in control.frozen_definitions()
        ],
        "retired_sleeve_ids": [
            item.sleeve_id
            for item in control.definitions
            if item.lifecycle_status == "retired"
        ],
        "summary": {
            "expected_count": len(expected_ids),
            "envelope_count": len(envelopes),
            "terminal_status_counts": {
                status: int(status_counts.get(status, 0))
                for status in TERMINAL_STATUSES
            },
            "capital_eligible_sleeve_ids": [
                item.sleeve_id for item in control.definitions if item.capital_eligible
            ],
            "execution_eligible_sleeve_ids": [
                item.sleeve_id for item in control.definitions if item.execution_eligible
            ],
        },
        "registry": {
            "path": _display_path(control.registry_path, root),
            "sha256": registry_sha,
            "manifest_path": _display_path(control.manifest_path, root),
            "manifest_sha256": manifest_sha,
            "code_sha": code_sha,
        },
        "envelopes": envelopes,
    }


def write_all_sleeve_evaluation(
    *,
    output_path: str | Path,
    trade_date: str,
    run_id: str,
    daily_snapshot: Mapping[str, Any],
    runtime_root: str | Path = ".",
    registry: SleeveControlRegistry | None = None,
    now: dt.datetime | None = None,
    allow_overwrite: bool = True,
) -> dict[str, Any]:
    payload = dispatch_all_sleeves(
        trade_date=trade_date,
        run_id=run_id,
        daily_snapshot=daily_snapshot,
        runtime_root=runtime_root,
        registry=registry,
        now=now,
    )
    path = Path(output_path)
    from paper.run_manager import safe_write_text

    safe_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        allow_overwrite=allow_overwrite,
    )
    return payload


def _definition_from_payload(
    payload: Mapping[str, Any],
    *,
    registry_origin: str,
) -> SleeveDefinition:
    sleeve_id = str(payload.get("sleeve_id") or "").strip()
    required_text = {
        "sleeve_id": sleeve_id,
        "display_name": str(payload.get("display_name") or "").strip(),
        "strategy_type": str(payload.get("strategy_type") or "").strip(),
        "family": str(payload.get("family") or "").strip(),
        "status": str(payload.get("status") or "").strip(),
        "execution_impact": str(payload.get("execution_impact") or "").strip(),
        "runner": str(payload.get("runner") or "").strip(),
        "source_artifact": str(payload.get("source_artifact") or "").strip(),
        "universe_family": str(payload.get("universe_family") or "").strip(),
        "universe_method": str(payload.get("universe_method") or "").strip(),
        "availability_policy": str(payload.get("availability_policy") or "").strip(),
    }
    missing = sorted(key for key, value in required_text.items() if not value)
    if missing:
        raise SleeveRegistryIntegrityError(
            f"{sleeve_id or '<blank>'}: missing control-plane fields: {', '.join(missing)}"
        )
    frozen = payload.get("frozen")
    if not isinstance(frozen, bool):
        raise SleeveRegistryIntegrityError(f"{sleeve_id}: frozen must be boolean")
    frozen_reason = str(payload.get("frozen_reason") or "").strip() or None
    if frozen and not frozen_reason:
        raise SleeveRegistryIntegrityError(
            f"{sleeve_id}: frozen sleeves require frozen_reason"
        )
    status = required_text["status"]
    if status not in ACTIVE_LIFECYCLE_STATUSES | {"retired"}:
        raise SleeveRegistryIntegrityError(
            f"{sleeve_id}: unsupported lifecycle status {status!r}"
        )
    for field in ("capital_eligible", "execution_eligible", "evaluation_only"):
        if not isinstance(payload.get(field), bool):
            raise SleeveRegistryIntegrityError(f"{sleeve_id}: {field} must be boolean")
    if required_text["execution_impact"] not in {"NON_EXECUTIONAL", "PAPER", "LIVE"}:
        raise SleeveRegistryIntegrityError(
            f"{sleeve_id}: invalid execution_impact {required_text['execution_impact']!r}"
        )
    artifact_key = str(payload.get("artifact_key") or "").strip() or None
    if registry_origin == "legacy_functional" and not artifact_key:
        raise SleeveRegistryIntegrityError(
            f"{sleeve_id}: legacy functional sleeve requires artifact_key"
        )
    return SleeveDefinition(
        sleeve_id=sleeve_id,
        display_name=required_text["display_name"],
        strategy_type=required_text["strategy_type"],
        family=required_text["family"],
        lifecycle_status=status,
        role=str(payload.get("role") or "").strip() or None,
        benchmark=(
            str(payload.get("benchmark")).strip()
            if payload.get("benchmark") is not None
            else None
        ),
        execution_impact=required_text["execution_impact"],
        frozen=frozen,
        frozen_reason=frozen_reason,
        runner=required_text["runner"],
        source_artifact=required_text["source_artifact"],
        artifact_key=artifact_key,
        universe_family=required_text["universe_family"],
        universe_method=required_text["universe_method"],
        universe_source=(
            str(payload.get("universe_source")).strip()
            if payload.get("universe_source") is not None
            else None
        ),
        availability_policy=required_text["availability_policy"],
        capital_eligible=bool(payload.get("capital_eligible")),
        execution_eligible=bool(payload.get("execution_eligible")),
        evaluation_only=bool(payload.get("evaluation_only")),
        blocked_reason=str(payload.get("blocked_reason") or "").strip() or None,
        registry_origin=registry_origin,
    )


def _run_shadow_snapshot(
    definition: SleeveDefinition,
    trade_date: str,
    daily_snapshot: Mapping[str, Any],
    root: Path,
) -> RunnerResult:
    del daily_snapshot
    if definition.sleeve_id == "caerus_orion":
        from paper.trading_calendar import prev_trading_day

        exact = root / definition.source_artifact.format(trade_date=trade_date)
        source = exact
        payload = _read_json(exact) if exact.is_file() else {}
        observation_status = str(payload.get("observation_status") or "").upper()
        exact_eligible = bool(
            payload
            and payload.get("decision_eligible") is True
            and observation_status == "OK"
        )
        if not exact_eligible:
            prior_trade_date = prev_trading_day(trade_date)
            source = root / definition.source_artifact.format(
                trade_date=prior_trade_date
            )
            payload = _read_json(source) if source.is_file() else {}
            observation_status = str(payload.get("observation_status") or "").upper()
        if (
            not payload
            or payload.get("decision_eligible") is not True
            or observation_status != "OK"
        ):
            raise SleeveEvaluationBlocked(
                "paper authority has no decision-eligible current/prior snapshot"
            )
    else:
        source = _resolve_dated_source(
            definition,
            trade_date,
            root,
            allow_prior=True,
        )
        payload = _read_json(source)
        observation_status = str(payload.get("observation_status") or "OK").upper()
    data_status = str(
        payload.get("data_status") or ("" if definition.capital_eligible else "OK")
    ).upper()
    data_reason = str(payload.get("data_reason") or payload.get("reason_code") or "").upper()
    if data_status != "OK" or data_reason in {
        "PRICE_CACHE_STALE",
        "NO_DATA",
        "SOURCE_STALE",
    }:
        return RunnerResult(
            status="FAILED",
            reason_codes=(data_reason or f"SOURCE_DATA_STATUS_{data_status}",),
            message="shadow snapshot is not fresh enough for a daily decision",
            opportunity={"available": False, "candidate_count": 0},
            source_paths=(source,),
        )
    if definition.sleeve_id == "caerus_orion":
        effective_trade_date = str(
            payload.get("effective_trade_date") or payload.get("trade_date") or ""
        )
        previous_payload: Mapping[str, Any] | None = None
        try:
            from paper.trading_calendar import prev_trading_day

            previous_date = prev_trading_day(effective_trade_date)
            previous_path = root / definition.source_artifact.format(
                trade_date=previous_date
            )
            if previous_path.is_file():
                previous_payload = _read_json(previous_path)
        except (TypeError, ValueError):
            previous_payload = None
        lineage_failures = validate_orion_decision_lineage(
            payload,
            effective_trade_date=effective_trade_date,
            previous_source_payload=previous_payload,
        )
        if lineage_failures:
            return RunnerResult(
                status="BLOCKED",
                reason_codes=("STALE_DECISION_SUSPECTED", *lineage_failures),
                message="Orion decision lineage is incomplete or causally stale",
                opportunity={
                    "available": False,
                    "candidate_count": 0,
                    "decision_eligible": False,
                    "decision_status": "STALE_DECISION_SUSPECTED",
                    "freshness_status": "BLOCKED",
                    "effective_trade_date": effective_trade_date,
                    "lineage_failures": lineage_failures,
                },
                source_paths=(source,),
            )
    weights = payload.get("target_weights")
    if not isinstance(weights, Mapping):
        raise ValueError("shadow snapshot target_weights is missing or invalid")
    positive = {
        str(key): float(value)
        for key, value in weights.items()
        if _finite_positive(value)
    }
    if not positive:
        return RunnerResult(
            status="NO_OPPORTUNITY",
            reason_codes=("EMPTY_TARGET_WEIGHTS",),
            message="shadow snapshot completed with no positive target weights",
            opportunity={
                "available": False,
                "candidate_count": 0,
                "effective_trade_date": payload.get("effective_trade_date"),
            },
            source_paths=(source,),
        )
    reasons = []
    if observation_status == "PENDING_SESSION_CLOSE":
        reasons.append("PENDING_SESSION_CLOSE")
    return RunnerResult(
        status="OK",
        reason_codes=tuple(reasons or ["OPPORTUNITY_AVAILABLE"]),
        message="existing shadow opportunity snapshot observed",
        opportunity={
            "available": True,
            "candidate_count": len(positive),
            "gross_target_weight": round(sum(positive.values()), 10),
            "effective_trade_date": payload.get("effective_trade_date")
            or payload.get("trade_date"),
            "observation_status": observation_status,
            "decision_eligible": payload.get("decision_eligible") is True
            and observation_status == "OK",
            "source_variant": payload.get("source_variant"),
            "decision_lineage": (
                dict(payload.get("decision_lineage") or {})
                if definition.sleeve_id == "caerus_orion"
                else None
            ),
            "freshness_status": (
                "VERIFIED" if definition.capital_eligible else "OBSERVED"
            ),
        },
        source_paths=(source,),
    )


def _run_shadow_benchmark(
    definition: SleeveDefinition,
    trade_date: str,
    daily_snapshot: Mapping[str, Any],
    root: Path,
) -> RunnerResult:
    del daily_snapshot
    source = _resolve_dated_source(definition, trade_date, root, allow_prior=True)
    payload = _read_json(source)
    data_status = str(payload.get("data_status") or "OK").upper()
    data_reason = str(payload.get("data_reason") or payload.get("reason_code") or "").upper()
    if data_status not in {"OK", "COMPLETE"} or data_reason in {
        "PRICE_CACHE_STALE",
        "NO_DATA",
        "SOURCE_STALE",
    }:
        return RunnerResult(
            status="FAILED",
            reason_codes=(data_reason or f"SOURCE_DATA_STATUS_{data_status}",),
            message="shadow benchmark is not fresh enough for a daily observation",
            opportunity={"available": False, "candidate_count": 0},
            source_paths=(source,),
        )
    strategy = (payload.get("strategies") or {}).get(definition.sleeve_id)
    if not isinstance(strategy, Mapping):
        raise ValueError("benchmark entry missing from shadow performance artifact")
    if strategy.get("nav") is None:
        return RunnerResult(
            status="NO_OPPORTUNITY",
            reason_codes=("BENCHMARK_NAV_UNAVAILABLE",),
            message="benchmark observation exists but NAV is unavailable",
            opportunity={"available": False, "candidate_count": 0},
            source_paths=(source,),
        )
    return RunnerResult(
        status="OK",
        reason_codes=("BENCHMARK_OBSERVED",),
        message="benchmark observation available",
        opportunity={
            "available": True,
            "candidate_count": 1,
            "nav": strategy.get("nav"),
            "daily_return": strategy.get("daily_return"),
        },
        source_paths=(source,),
    )


def _run_precompute_snapshot(
    definition: SleeveDefinition,
    trade_date: str,
    daily_snapshot: Mapping[str, Any],
    root: Path,
) -> RunnerResult:
    del trade_date
    key = definition.artifact_key
    if not key:
        raise ValueError("functional sleeve mapping is missing artifact_key")
    allocations = daily_snapshot.get("sleeve_allocations") or {}
    if not isinstance(allocations, Mapping) or key not in allocations:
        raise ValueError(f"mapped functional allocation key is missing: {key}")
    allocation = float(allocations[key])
    if not math.isfinite(allocation) or allocation < 0:
        raise ValueError(f"mapped functional allocation is invalid: {key}={allocation!r}")
    routes = (
        ((daily_snapshot.get("allocation_diagnostics") or {}).get("sleeve_1") or {}).get(
            "cash_routing"
        )
        or []
    )
    matching_routes = [
        item
        for item in routes
        if isinstance(item, Mapping) and str(item.get("sleeve_id") or "") == key
    ]
    source = root / definition.source_artifact.format(
        trade_date=str(daily_snapshot.get("asof") or "")
    )
    if matching_routes:
        reasons = [str(item.get("reason") or "cash_routed") for item in matching_routes]
        return RunnerResult(
            status="FAILED",
            reason_codes=("SLEEVE_INVALID_ROUTED_TO_CASH",),
            message="; ".join(reasons),
            opportunity={
                "available": False,
                "allocation_weight": allocation,
                "cash_routes": [dict(item) for item in matching_routes],
            },
            source_paths=(source,),
        )
    if allocation <= 1e-10:
        return RunnerResult(
            status="NO_OPPORTUNITY",
            reason_codes=("ZERO_FUNCTIONAL_ALLOCATION",),
            message="functional sleeve completed with zero allocation",
            opportunity={
                "available": False,
                "allocation_weight": allocation,
                "candidate_count": 0,
            },
            source_paths=(source,),
        )
    holdings = daily_snapshot.get("holdings") or []
    candidate_count = sum(
        1
        for row in holdings
        if isinstance(row, Mapping)
        and str(row.get("sleeve") or row.get("sleeve_name") or "") == key
    )
    return RunnerResult(
        status="OK",
        reason_codes=("FUNCTIONAL_ALLOCATION_OBSERVED",),
        message="existing functional precompute allocation observed",
        opportunity={
            "available": True,
            "allocation_weight": allocation,
            "candidate_count": candidate_count,
        },
        source_paths=(source,),
    )


def _run_research_snapshot(
    definition: SleeveDefinition,
    trade_date: str,
    daily_snapshot: Mapping[str, Any],
    root: Path,
) -> RunnerResult:
    del daily_snapshot
    source = _resolve_dated_source(definition, trade_date, root, allow_prior=False)
    payload = _read_json(source)
    declared = str(
        payload.get("evaluation_status")
        or payload.get("status")
        or payload.get("decision_state")
        or "OK"
    ).upper()
    if declared in {"BLOCKED", "PARTIAL", "SHELVED"}:
        return RunnerResult(
            status="BLOCKED",
            reason_codes=(f"SOURCE_STATUS_{declared}",),
            message=f"research source declared {declared}",
            opportunity={"available": False},
            source_paths=(source,),
        )
    if declared in {"FAILED", "ERROR"}:
        return RunnerResult(
            status="FAILED",
            reason_codes=(f"SOURCE_STATUS_{declared}",),
            message=f"research source declared {declared}",
            opportunity={"available": False},
            source_paths=(source,),
        )
    weights = payload.get("target_weights")
    holdings = payload.get("holdings")
    candidate_count = (
        len([value for value in weights.values() if _finite_positive(value)])
        if isinstance(weights, Mapping)
        else len(holdings)
        if isinstance(holdings, list)
        else 0
    )
    if declared in {"NO_DATA", "NO_OPPORTUNITY"} or candidate_count == 0:
        return RunnerResult(
            status="NO_OPPORTUNITY",
            reason_codes=("RESEARCH_NO_OPPORTUNITY",),
            message="research evaluation completed without candidates",
            opportunity={"available": False, "candidate_count": 0},
            source_paths=(source,),
        )
    return RunnerResult(
        status="OK",
        reason_codes=("RESEARCH_OPPORTUNITY_AVAILABLE",),
        message="research opportunity artifact observed",
        opportunity={"available": True, "candidate_count": candidate_count},
        source_paths=(source,),
    )


def _resolve_dated_source(
    definition: SleeveDefinition,
    trade_date: str,
    root: Path,
    *,
    allow_prior: bool,
) -> Path:
    exact = root / definition.source_artifact.format(trade_date=trade_date)
    if exact.exists():
        return exact
    if allow_prior:
        from paper.trading_calendar import prev_trading_day

        prior_trade_date = prev_trading_day(trade_date)
        candidate = root / definition.source_artifact.format(
            trade_date=prior_trade_date
        )
        if candidate.exists():
            return candidate
    raise SleeveEvaluationBlocked(
        f"source artifact missing: {definition.source_artifact.format(trade_date=trade_date)}"
    )


def _universe_provenance(definition: SleeveDefinition, root: Path) -> dict[str, Any]:
    source = root / definition.universe_source if definition.universe_source else None
    payload: dict[str, Any] = {
        "family": definition.universe_family,
        "method": definition.universe_method,
        "source": definition.universe_source,
        "snapshot_hash": _sha256(source) if source and source.is_file() else None,
        "source_available": bool(source and source.is_file()),
    }
    if source and source.is_file():
        try:
            payload["member_count"] = max(
                0,
                len(source.read_text(encoding="utf-8").splitlines()) - 1,
            )
        except UnicodeDecodeError:
            payload["member_count"] = None
    return payload


def _source_provenance(path: Path, root: Path) -> dict[str, Any]:
    exists = path.is_file()
    return {
        "path": _display_path(path, root),
        "exists": exists,
        "sha256": _sha256(path) if exists else None,
        "size_bytes": path.stat().st_size if exists else None,
        "modified_at": (
            dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat()
            if exists
            else None
        ),
    }


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _finite_positive(value: Any) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and numeric > 0.0


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload


def _sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha(root: Path) -> str | None:
    env_sha = str(os.getenv("GITHUB_SHA") or os.getenv("DEPLOYED_SHA") or "").strip()
    if env_sha:
        return env_sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None
