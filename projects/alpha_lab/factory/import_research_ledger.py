"""Audit and migrate existing Alpha Lab evidence into the global ledger.

Dry-run is the default. Canonical writes require both the exact GCP repository
root and an owner-ratified migration manifest. The importer never treats a
data-gate attempt, cost scenario, validation window, or regime cell as a
statistical trial.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .canonical import (
    canonical_hash,
    canonical_json,
    format_datetime,
    parse_datetime,
    require_sha256,
)
from .errors import ContractValidationError, ResearchBoundaryError
from .research_identity import (
    GENESIS_LEDGER_HEAD,
    IdentityActivationEvidence,
    IdentityRegistry,
    IdentityRegistryHistory,
    IdentityRole,
    IdentityTrustAnchor,
    ResearchAttestation,
    typed_event_payload_hash,
)
from .research_ledger import (
    ExpectedDirection,
    GlobalResearchLedger,
    HypothesisFamily,
    InferenceTrack,
    MultipleTestingMethod,
    ResearchPhase,
    ResearchExperiment,
    ResearchRun,
    ResearchRunClass,
    ResearchWave,
    TrialOutcome,
    TrialResult,
    deterministic_attempt_id,
    deterministic_trial_id,
)
from .store import AppendOnlyJSONLEventStore, EventRecord


AUTHORITATIVE_REPO_ROOT = Path("/mnt/disks/alpha-lab/alpha-lab-project")
AUTHORITATIVE_DATA_ROOT = AUTHORITATIVE_REPO_ROOT / "outputs/research/alpha_lab"
LEDGER_RELATIVE_PATH = Path("outputs/research/alpha_lab/ledger/research_events.v1.jsonl")
PUBLICATION_MODE = "CREATE_ONLY_ATOMIC_HARD_LINK"
PUBLICATION_AUTHORIZATION_RULE = (
    "The migration-plan signature ratifies legacy semantics but never authorizes "
    "publication. A distinct Brett-signed QS-003 artifact must bind the signed-plan "
    "hash, exact canonical path, expected ledger bytes and head, fresh receipt-set "
    "hash, create-only mode, authorization timestamp, active registry and external "
    "pin, and prior GENESIS before one publication."
)
PUBLICATION_AUTHORIZATION_SCHEMA = (
    "caerus_alpha_lab_publication_authorization_v1"
)
SIGNED_PUBLICATION_AUTHORIZATION_SCHEMA = (
    "caerus_alpha_lab_signed_publication_authorization_v1"
)
LEGACY_WAVE_ID = "WAVE-2026-001"
LEGACY_CHALLENGE_EPOCH_ID = "CHALLENGE-2026-001"
_DECLARED_SPEC_HASH = re.compile(r"Spec hash: `sha256:([0-9a-f]{64})`")
EXPECTED_CANONICAL_GATE_COUNT = 66
EXPECTED_CANONICAL_GATE_STATUSES = {
    "BLOCKED_DATA": 60,
    "READY_FOR_FROZEN_EVALUATOR": 6,
}
EXPECTED_CANONICAL_VARIANTS_BY_HYPOTHESIS = {
    "HYP-2026-006": 3,
    "HYP-2026-007": 2,
    "HYP-2026-008": 3,
}
EXPECTED_CANONICAL_ROBUSTNESS_COUNT = 8
EXPECTED_CANONICAL_CHALLENGE_READ_COUNT = 0
EXPECTED_CANONICAL_EXPERIMENT_HYPOTHESES = frozenset(
    "HYP-2026-{:03d}".format(index) for index in range(1, 14)
)


EXACT_FAMILY_MAPPING = {
    "HYP-2026-{:03d}".format(index): "FAM-2026-{:03d}".format(index)
    for index in range(1, 14)
}

# These are deterministic owner-normalization candidates, not new frozen
# evidence.  Every value is a normalized rendering of the cited frozen HYP
# source; where the source did not decide a semantic, the corresponding legacy
# blocker is retained so a migration can never convert that gap into evidence.
OWNER_NORMALIZATION_NOTICE = "OWNER_NORMALIZATION_NOT_FROZEN_FACT"
UNRESOLVED_HYP_001_PRIMARY = "UNRESOLVED_LEGACY_ABLATION_REFERENCE_PRIMARY_VARIANT"
UNRESOLVED_HYP_009_PRIMARY = "UNRESOLVED_LEGACY_PRIMARY_VARIANT"
UNRESOLVED_HYP_010_BENCHMARK = "UNRESOLVED_LEGACY_PRIMARY_BENCHMARK"


def _family_definition(
    *,
    name: str,
    economic_mechanism: str,
    primary_metric: str,
    benchmark: str,
    primary_variant_id: str,
    maximum_trial_units: int,
    within_family_method: str,
    family_alpha: float,
    legacy_ambiguity_blockers: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """Return the complete owner-normalization for one immutable HYP source."""

    return {
        "name": name,
        "economic_mechanism": economic_mechanism,
        "primary_metric": primary_metric,
        "benchmark": benchmark,
        "expected_direction": "GREATER_THAN",
        "null_value": 0.0,
        "economic_hurdle": 0.0,
        "primary_variant_id": primary_variant_id,
        "maximum_trial_units": maximum_trial_units,
        # The frozen HYPs permit no outcome-driven selection.  This is a
        # migration accounting normalization, rather than a frozen prose fact.
        "selection_trial_budget": 0,
        "within_family_method": within_family_method,
        "family_alpha": family_alpha,
        "legacy_ambiguity_blockers": list(legacy_ambiguity_blockers),
    }


OWNER_NORMALIZED_FAMILY_DEFINITIONS = {
    "FAM-2026-001": _family_definition(
        name="Current Caerus Decomposition",
        economic_mechanism="caerus_component_selection_attribution_and_ablation",
        primary_metric="worst_case_cost_adjusted_incremental_information_ratio",
        benchmark="eligible_universe_equal_weight_and_unchanged_full_caerus_rule",
        primary_variant_id=UNRESOLVED_HYP_001_PRIMARY,
        maximum_trial_units=6,
        within_family_method="HOLM_BONFERRONI",
        family_alpha=0.05,
        legacy_ambiguity_blockers=(
            "LEGACY_ABLATION_AND_REFERENCE_SEMANTICS_AMBIGUITY",
        ),
    ),
    "FAM-2026-002": _family_definition(
        name="Earnings-Revision Drift",
        economic_mechanism="analyst_revision_information_diffusion",
        primary_metric="annualized_factor_adjusted_cost_net_daily_return_difference_candidate_minus_primary_investable_baseline_intercept",
        benchmark="eligible_universe_equal_weight",
        primary_variant_id="sector_neutral_eps_revenue_revision_composite_top_decile",
        maximum_trial_units=6,
        within_family_method="ROMANO_WOLF",
        family_alpha=0.10,
        legacy_ambiguity_blockers=(
            "LEGACY_DECLARED_VARIANT_COUNT_AND_LISTED_ALTERNATIVES_CONFLICT",
        ),
    ),
    "FAM-2026-003": _family_definition(
        name="Insider-Conviction Clusters",
        economic_mechanism="independent_insider_purchase_cluster_information",
        primary_metric="sixty_trading_day_factor_adjusted_net_calendar_time_portfolio_alpha_clusters_minus_single_insider_purchases",
        benchmark="otherwise_eligible_single_insider_code_p_purchases",
        primary_variant_id="independent_insider_cluster_composite_top_decile",
        maximum_trial_units=5,
        within_family_method="ROMANO_WOLF",
        family_alpha=0.10,
        legacy_ambiguity_blockers=(
            "LEGACY_DECLARED_VARIANT_COUNT_AND_LISTED_ALTERNATIVES_CONFLICT",
            "LEGACY_INSIDER_IDENTITY_AND_AMENDMENT_AMBIGUITY",
        ),
    ),
    "FAM-2026-004": _family_definition(
        name="Options-Information Lead",
        economic_mechanism="informed_options_flow_leads_equity_price_discovery",
        primary_metric="annualized_factor_adjusted_cost_net_candidate_minus_eligible_universe_equity_return_intercept",
        benchmark="eligible_optionable_equity_universe_equal_weight",
        primary_variant_id="concordant_options_information_composite_top_decile",
        maximum_trial_units=6,
        within_family_method="ROMANO_WOLF",
        family_alpha=0.10,
        legacy_ambiguity_blockers=(
            "LEGACY_DECLARED_VARIANT_COUNT_AND_LISTED_ALTERNATIVES_CONFLICT",
            "LEGACY_OPTION_TRADE_AND_DELIVERABLE_AMBIGUITY",
        ),
    ),
    "FAM-2026-005": _family_definition(
        name="Supply-Chain Shock Diffusion",
        economic_mechanism="customer_shock_supplier_demand_diffusion",
        primary_metric="annualized_factor_adjusted_cost_net_candidate_minus_primary_supplier_baseline_calendar_time_return_intercept",
        benchmark="eligible_suppliers_of_same_shocked_customers_equal_weight",
        primary_variant_id="dependency_weighted_customer_shock_supplier_score_top_decile",
        maximum_trial_units=4,
        within_family_method="ROMANO_WOLF",
        family_alpha=0.10,
        legacy_ambiguity_blockers=(
            "LEGACY_DECLARED_VARIANT_COUNT_AND_LISTED_ALTERNATIVES_CONFLICT",
            "LEGACY_RELATIONSHIP_DISCOVERY_TIME_AMBIGUITY",
        ),
    ),
    "FAM-2026-006": _family_definition(
        name="Residual Momentum",
        economic_mechanism="residual_intermediate_horizon_momentum",
        primary_metric="worst_case_annualized_excess_return_after_costs",
        benchmark="eligible_universe_equal_weight",
        primary_variant_id="twelve_minus_one_month_beta_residual_momentum_top_quintile",
        maximum_trial_units=3,
        within_family_method="ROMANO_WOLF",
        family_alpha=0.05,
        legacy_ambiguity_blockers=(
            "LEGACY_WITHIN_FAMILY_MAX_STAT_METHOD_UNSPECIFIED",
        ),
    ),
    "FAM-2026-007": _family_definition(
        name="Stock-Specific Return Seasonality",
        economic_mechanism="issuer_specific_calendar_month_return_seasonality",
        primary_metric="worst_case_annualized_excess_return_after_costs",
        benchmark="eligible_universe_equal_weight",
        primary_variant_id="five_year_same_calendar_month_mean_cross_sectionally_demeaned_top_quintile",
        maximum_trial_units=2,
        within_family_method="HOLM_BONFERRONI",
        family_alpha=0.05,
        legacy_ambiguity_blockers=(
            "LEGACY_WITHIN_FAMILY_PRIMARY_VERSUS_PLACEBO_METHOD_UNSPECIFIED",
        ),
    ),
    "FAM-2026-008": _family_definition(
        name="Short-Horizon Reversal",
        economic_mechanism="temporary_liquidity_pressure_and_order_imbalance_reversal",
        primary_metric="worst_case_annualized_excess_return_after_costs",
        benchmark="eligible_universe_equal_weight",
        primary_variant_id="weekly_negative_five_session_market_residual_return_top_quintile",
        maximum_trial_units=3,
        within_family_method="ROMANO_WOLF",
        family_alpha=0.05,
        legacy_ambiguity_blockers=(
            "LEGACY_WITHIN_FAMILY_MAX_STAT_METHOD_UNSPECIFIED",
        ),
    ),
    "FAM-2026-009": _family_definition(
        name="Cross-Asset Trend",
        economic_mechanism="slow_macro_adjustment_and_institutional_derisking_trend",
        primary_metric="validation_delta_portfolio_information_ratio",
        benchmark="cash_proxy__equal_weight_long_only_proxy_basket__unchanged_caerus_return_stream",
        primary_variant_id=UNRESOLVED_HYP_009_PRIMARY,
        maximum_trial_units=4,
        within_family_method="HOLM_BONFERRONI",
        family_alpha=0.05,
        legacy_ambiguity_blockers=(
            "LEGACY_PRIMARY_VARIANT_UNRESOLVED",
            "LEGACY_MULTIPLE_BASELINES_REQUIRE_OWNER_PRIMARY_COMPARATOR_NORMALIZATION",
        ),
    ),
    "FAM-2026-010": _family_definition(
        name="Executive and Managerial Tone Surprise",
        economic_mechanism="executive_language_change_reveals_private_information",
        primary_metric="validation_60d_factor_residual_car_after_costs",
        benchmark=UNRESOLVED_HYP_010_BENCHMARK,
        primary_variant_id="within_executive_tone_change_residual",
        maximum_trial_units=3,
        within_family_method="HOLM_BONFERRONI",
        family_alpha=0.05,
        legacy_ambiguity_blockers=(
            "LEGACY_PRIMARY_BENCHMARK_UNRESOLVED",
        ),
    ),
    "FAM-2026-011": _family_definition(
        name="Net Payout and Share Issuance",
        economic_mechanism="managerial_market_timing_and_agency_investment",
        primary_metric="worst_case_annualized_excess_return_after_costs",
        benchmark="eligible_universe_equal_weight",
        primary_variant_id="net_payout_yield_top_quintile",
        maximum_trial_units=2,
        within_family_method="HOLM_BONFERRONI",
        family_alpha=0.05,
        legacy_ambiguity_blockers=(
            "LEGACY_WITHIN_FAMILY_METHOD_UNSPECIFIED",
        ),
    ),
    "FAM-2026-012": _family_definition(
        name="Asset Growth and Investment",
        economic_mechanism="overinvestment_and_financing_frictions",
        primary_metric="worst_case_annualized_excess_return_after_costs",
        benchmark="eligible_universe_equal_weight",
        primary_variant_id="negative_one_year_total_asset_growth_top_quintile",
        maximum_trial_units=2,
        within_family_method="HOLM_BONFERRONI",
        family_alpha=0.05,
        legacy_ambiguity_blockers=(
            "LEGACY_WITHIN_FAMILY_METHOD_UNSPECIFIED",
        ),
    ),
    "FAM-2026-013": _family_definition(
        name="AI Power/Grid Commitment Events",
        economic_mechanism="delayed_incorporation_of_public_ai_data_center_grid_commitments",
        primary_metric="annualized_factor_adjusted_cost_net_candidate_minus_primary_baseline_calendar_time_return_intercept",
        benchmark="pit_eligible_exposure_stratum_issuers_excluding_named_event_issuer",
        primary_variant_id="equal_weight_qualifying_direct_exposure_events_preceding_five_trading_days",
        maximum_trial_units=3,
        within_family_method="ROMANO_WOLF",
        family_alpha=0.10,
    ),
}

REQUIRED_LEGACY_DEFINITION_BLOCKERS = {
    hypothesis_id: tuple(
        OWNER_NORMALIZED_FAMILY_DEFINITIONS[family_id]["legacy_ambiguity_blockers"]
    )
    for hypothesis_id, family_id in EXACT_FAMILY_MAPPING.items()
}

FROZEN_TRIAL_BUDGETS = {
    "HYP-2026-001": 6,
    "HYP-2026-002": 6,
    "HYP-2026-003": 5,
    "HYP-2026-004": 6,
    "HYP-2026-005": 4,
    "HYP-2026-006": 3,
    "HYP-2026-007": 2,
    "HYP-2026-008": 3,
    "HYP-2026-009": 4,
    "HYP-2026-010": 3,
    "HYP-2026-011": 2,
    "HYP-2026-012": 2,
    "HYP-2026-013": 3,
}

KNOWN_PRIMARY_METRICS = {
    hypothesis_id: str(OWNER_NORMALIZED_FAMILY_DEFINITIONS[family_id]["primary_metric"])
    for hypothesis_id, family_id in EXACT_FAMILY_MAPPING.items()
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    def reject_duplicates(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ContractValidationError(
                    "{} contains a duplicate JSON key: {}".format(path, key)
                )
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise ContractValidationError(
            "{} contains a non-finite JSON number: {}".format(path, value)
        )

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ContractValidationError("{} is not strict JSON".format(path)) from exc
    if not isinstance(value, Mapping):
        raise ContractValidationError("{} must contain a JSON object".format(path))
    canonical_json(value)
    return value


def _receipt(paths: Iterable[Path], root: Path) -> str:
    mapping = {
        str(path.relative_to(root)): _sha256_file(path)
        for path in sorted(set(paths))
    }
    return canonical_hash(mapping)


def _family_id(hypothesis_id: str) -> str:
    return "FAM-{}".format(hypothesis_id.removeprefix("HYP-"))


def _experiment_id(manifest: Mapping[str, Any]) -> str:
    value = str(manifest.get("experiment_id", ""))
    if not value:
        raise ContractValidationError("run manifest is missing experiment_id")
    return value


def _hypothesis_source(repo_root: Path, hypothesis_id: str) -> Optional[Path]:
    matches = sorted((repo_root / "projects/alpha_lab/hypotheses").glob(hypothesis_id + "*"))
    if len(matches) > 1:
        raise ContractValidationError(
            "multiple frozen hypothesis sources found for {}".format(hypothesis_id)
        )
    return matches[0] if matches else None


def _frozen_hypothesis_source(
    repo_root: Path, hypothesis_id: str
) -> Tuple[Path, str]:
    path = _hypothesis_source(repo_root, hypothesis_id)
    if path is None:
        raise ContractValidationError(
            "frozen hypothesis source is missing for {}".format(hypothesis_id)
        )
    text = path.read_text(encoding="utf-8")
    marker = "## Freeze record\n"
    match = _DECLARED_SPEC_HASH.search(text)
    if marker not in text or match is None:
        raise ContractValidationError(
            "frozen hypothesis contract is incomplete for {}".format(hypothesis_id)
        )
    frozen_hash = hashlib.sha256(text.split(marker, 1)[0].encode("utf-8")).hexdigest()
    if frozen_hash != match.group(1):
        raise ContractValidationError(
            "frozen hypothesis body hash mismatch for {}".format(hypothesis_id)
        )
    return path, frozen_hash


def audit_existing(*, repo_root: Path, data_root: Path) -> Dict[str, Any]:
    """Return a read-only, hash-verified inventory of legacy evidence."""

    repo_root = repo_root.resolve()
    data_root = data_root.resolve()
    manifests = sorted(data_root.glob("HYP-2026-[0-9][0-9][0-9]/*/run_manifest.json"))
    gate_rows: List[Dict[str, Any]] = []
    event_paths: List[Path] = []
    result_paths: List[Path] = []
    provider_paths: List[Path] = []
    evaluator_input_paths: List[Path] = []
    manifest_paths: List[Path] = []
    evaluator_manifest_paths: List[Path] = []
    hypothesis_sources = {
        hypothesis_id: _frozen_hypothesis_source(repo_root, hypothesis_id)
        for hypothesis_id in sorted(FROZEN_TRIAL_BUDGETS)
    }
    for manifest_path in manifests:
        bundle = manifest_path.parent
        result_path = bundle / "result.json"
        event_path = bundle / "events.jsonl"
        provider_path = bundle / "provider_gate.json"
        evaluator_input_path = bundle / "evaluator_input.json"
        if not all(path.is_file() for path in (result_path, event_path, provider_path)):
            raise ContractValidationError("incomplete data-gate bundle: {}".format(bundle))
        manifest = _load_json(manifest_path)
        result = _load_json(result_path)
        provider = _load_json(provider_path)
        if result.get("run_manifest_hash") != canonical_hash(manifest):
            raise ContractValidationError("run manifest hash mismatch: {}".format(bundle))
        hypothesis_id = str(manifest.get("hypothesis_id", ""))
        source = hypothesis_sources.get(hypothesis_id)
        if source is None or manifest.get("hypothesis_hash") != source[1]:
            raise ContractValidationError(
                "run manifest hypothesis hash mismatch: {}".format(bundle)
            )
        if manifest.get("provider_gate_hash") != canonical_hash(provider):
            raise ContractValidationError("provider gate hash mismatch: {}".format(bundle))
        assets = provider.get("assets")
        if not isinstance(assets, list):
            raise ContractValidationError("provider gate assets are missing: {}".format(bundle))
        data_snapshot = [
            {"asset_id": item["asset_id"], "files": item["files"]}
            for item in assets
        ]
        if manifest.get("data_snapshot_hash") != canonical_hash(data_snapshot):
            raise ContractValidationError("data snapshot hash mismatch: {}".format(bundle))
        store = AppendOnlyJSONLEventStore(event_path, research_root=data_root)
        events = store.read_all()
        if len(events) != 2 or [event.event_type for event in events] != [
            "data_gate_started",
            "data_gate_review",
        ]:
            raise ContractValidationError("unexpected data-gate event chain: {}".format(bundle))
        if canonical_hash(events[-1].payload) != canonical_hash(result):
            raise ContractValidationError("terminal event differs from result: {}".format(bundle))
        gate_rows.append(
            {
                "hypothesis_id": manifest["hypothesis_id"],
                "hypothesis_hash": manifest["hypothesis_hash"],
                "experiment_id": _experiment_id(manifest),
                "run_id": manifest["run_id"],
                "occurred_at": manifest["created_at"],
                "source_artifact": str(manifest_path),
                "source_sha256": _sha256_file(manifest_path),
                "terminal_event_hash": events[-1].event_hash,
                "data_gate_status": result.get("outcome", result.get("data_gate_status")),
                "returns_accessed": bool(result.get("returns_accessed", False)),
                "holdout_accessed": bool(result.get("holdout_accessed", False)),
                "evaluator_input_hash": (
                    canonical_hash(_load_json(evaluator_input_path))
                    if evaluator_input_path.is_file()
                    else None
                ),
            }
        )
        if gate_rows[-1]["returns_accessed"] or gate_rows[-1]["holdout_accessed"]:
            raise ContractValidationError("data-gate attempt accessed outcome data")
        manifest_paths.append(manifest_path)
        result_paths.append(result_path)
        event_paths.append(event_path)
        provider_paths.append(provider_path)
        if evaluator_input_path.is_file():
            evaluator_input_paths.append(evaluator_input_path)

    evaluator_paths = sorted(
        data_root.glob("control_plane/evaluator_runs/HYP-2026-[0-9][0-9][0-9]/*/*/result.json")
    )
    evaluator_rows: List[Dict[str, Any]] = []
    for path in evaluator_paths:
        envelope = _load_json(path)
        bundle_manifest_path = path.parent / "manifest.json"
        bundle_manifest = _load_json(bundle_manifest_path)
        evaluator_manifest_paths.append(bundle_manifest_path)
        result_record = next(
            (
                item
                for item in bundle_manifest.get("files", [])
                if item.get("name") == "result.json"
            ),
            None,
        )
        if (
            result_record is None
            or int(result_record.get("bytes", -1)) != path.stat().st_size
            or result_record.get("sha256") != _sha256_file(path)
        ):
            raise ContractValidationError("evaluator bundle manifest mismatch: {}".format(path))
        if canonical_hash({key: value for key, value in envelope.items() if key != "result_hash"}) != envelope.get(
            "result_hash"
        ):
            raise ContractValidationError("evaluator result hash mismatch: {}".format(path))
        result = envelope.get("result")
        if not isinstance(result, Mapping):
            raise ContractValidationError("evaluator result payload is missing")
        variants = result.get("variants")
        if not isinstance(variants, list) or len(variants) != result.get("variant_count"):
            raise ContractValidationError("evaluator variant census mismatch: {}".format(path))
        if envelope.get("phase") == ResearchPhase.CHALLENGE.value or result.get(
            "challenge_period_accessed"
        ):
            raise ContractValidationError("legacy inventory unexpectedly accessed challenge data")
        for variant in variants:
            phases = variant.get("phases")
            if not isinstance(phases, Mapping) or set(phases) != {
                "DISCOVERY",
                "VALIDATION",
            }:
                raise ContractValidationError("legacy robustness windows are incomplete")
            grid_cells = 0
            for phase_payload in phases.values():
                costs = phase_payload.get("cost_scenarios")
                if not isinstance(costs, Mapping) or set(costs) != {"base", "stress"}:
                    raise ContractValidationError("legacy robustness cost grid is incomplete")
                for cost_payload in costs.values():
                    if not isinstance(cost_payload, Mapping) or not {
                        "pessimistic",
                        "zero_incremental",
                    }.issubset(cost_payload):
                        raise ContractValidationError(
                            "legacy robustness terminal grid is incomplete"
                        )
                    grid_cells += 2
            if grid_cells != 8:
                raise ContractValidationError("legacy robustness grid must contain eight cells")
        hypothesis_id = str(envelope["hypothesis_id"])
        matching_gates = [
            row
            for row in gate_rows
            if row["hypothesis_id"] == hypothesis_id
            and row["evaluator_input_hash"] == envelope["input_packet_hash"]
        ]
        if len(matching_gates) != 1:
            raise ContractValidationError(
                "evaluator input must join exactly one authoritative data gate"
            )
        spec_path = (
            repo_root
            / "projects/alpha_lab/experiments/evaluator_specs"
            / "{}.json".format(hypothesis_id)
        )
        spec = _load_json(spec_path)
        unsigned_spec = {key: value for key, value in spec.items() if key != "spec_hash"}
        if canonical_hash(unsigned_spec) != spec.get("spec_hash") or envelope.get(
            "spec_hash"
        ) != spec.get("spec_hash"):
            raise ContractValidationError("evaluator spec hash mismatch: {}".format(path))
        if int(result["variant_count"]) != int(spec["maximum_variants"]):
            raise ContractValidationError(
                "legacy evaluator variant count differs from the frozen ceiling"
            )
        evaluator_module_path = repo_root / Path(
            str(spec["module"]).replace(".", "/") + ".py"
        )
        boundary = envelope.get("boundary_attestation")
        if (
            not isinstance(boundary, Mapping)
            or not evaluator_module_path.is_file()
            or boundary.get("source_sha256") != _sha256_file(evaluator_module_path)
        ):
            raise ContractValidationError(
                "evaluator code hash does not match the frozen source module"
            )
        evaluator_rows.append(
            {
                "hypothesis_id": hypothesis_id,
                "experiment_id": matching_gates[0]["experiment_id"],
                "source_artifact": str(path),
                "source_sha256": _sha256_file(path),
                "result_hash": envelope["result_hash"],
                "input_packet_hash": envelope["input_packet_hash"],
                "primary_metric": result["primary_metric_name"],
                "primary_metric_value": result.get("primary_metric_value"),
                "retrieved_at": bundle_manifest["retrieved_at"],
                "evaluator_spec_sha256": spec["spec_hash"],
                "evaluator_code_sha256": boundary["source_sha256"],
                "effective_sample_floor": int(spec.get("effective_sample_floor", 1)),
                "variant_ids": [str(item["variant_id"]) for item in variants],
                "variants": variants,
                "phase": envelope["phase"],
            }
        )

    variant_count = sum(len(item["variant_ids"]) for item in evaluator_rows)
    hypothesis_source_paths = [item[0] for item in hypothesis_sources.values()]
    for hypothesis_id in sorted(FROZEN_TRIAL_BUDGETS):
        experiment_ids = {
            row["experiment_id"]
            for row in gate_rows
            if row["hypothesis_id"] == hypothesis_id
        }
        if len(experiment_ids) > 1:
            raise ContractValidationError(
                "hypothesis maps to multiple legacy experiments: {}".format(
                    hypothesis_id
                )
            )
    status_counts: Dict[str, int] = {}
    for row in gate_rows:
        key = str(row["data_gate_status"])
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "schema_version": "caerus_alpha_lab_legacy_inventory_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "repo_root": str(repo_root),
        "data_root": str(data_root),
        "source_receipts": {
            "run_manifests": _receipt(manifest_paths, data_root),
            "gate_results": _receipt(result_paths, data_root),
            "event_chains": _receipt(event_paths, data_root),
            "provider_gates": _receipt(provider_paths, data_root),
            "evaluator_inputs": _receipt(evaluator_input_paths, data_root),
            "evaluator_results": _receipt(evaluator_paths, data_root),
            "evaluator_manifests": _receipt(evaluator_manifest_paths, data_root),
            "hypothesis_sources": _receipt(hypothesis_source_paths, repo_root),
        },
        "hypothesis_sources": {
            hypothesis_id: {
                "artifact": str(source[0]),
                "frozen_body_sha256": source[1],
                "file_sha256": _sha256_file(source[0]),
            }
            for hypothesis_id, source in sorted(hypothesis_sources.items())
        },
        "data_gate_attempt_count": len(gate_rows),
        "data_gate_status_counts": status_counts,
        "model_trial_count": variant_count,
        "robustness_record_count": variant_count,
        "challenge_read_count": 0,
        "statistical_trial_count": variant_count,
        "gate_attempts": gate_rows,
        "evaluator_batches": evaluator_rows,
        "proposed_family_mapping": {
            hypothesis_id: _family_id(hypothesis_id)
            for hypothesis_id in sorted(FROZEN_TRIAL_BUDGETS)
        },
        "family_mapping_owner_ratified": False,
        "migration_blockers": [
            "OWNER_RATIFICATION_REQUIRED_FOR_FAMILY_MAPPING",
            "LEGACY_CORRECTED_SIGNIFICANCE_NOT_IMPLEMENTED",
            "NO_CHALLENGE_ACCESS_OR_CONFIRMATION",
        ],
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }


def _legacy_ratification_validator_removed(
    ratification: Mapping[str, Any],
    inventory: Mapping[str, Any],
    *,
    ratification_path: Path,
    identity_history: Optional[IdentityRegistryHistory] = None,
) -> Mapping[str, str]:
    raise ContractValidationError(
        "legacy mutable-wrapper ratification is disabled; build and sign the exact event plan"
    )
    if ratification.get("decision") != "RATIFY_GLOBAL_RESEARCH_LEDGER_MIGRATION":
        raise ContractValidationError("owner ratification decision is missing")
    if ratification.get("owner") != "Brett Olson":
        raise ContractValidationError("migration ratification must name Brett Olson")
    if ratification.get("source_receipts") != inventory["source_receipts"]:
        raise ContractValidationError("ratification does not bind the audited source snapshot")
    declared_artifact = Path(str(ratification.get("artifact", ""))).expanduser().resolve()
    if declared_artifact != ratification_path.expanduser().resolve():
        raise ContractValidationError("ratification artifact path does not match the reviewed file")
    if not declared_artifact.is_file():
        raise ContractValidationError("ratification artifact is unavailable")
    if canonical_hash(_load_json(declared_artifact)) != canonical_hash(ratification):
        raise ContractValidationError(
            "ratification payload differs from the reviewed artifact"
        )
    unsigned_ratification = {
        key: value for key, value in ratification.items() if key != "artifact_sha256"
    }
    if canonical_hash(unsigned_ratification) != ratification.get("artifact_sha256"):
        raise ContractValidationError("ratification artifact hash mismatch")
    mappings = ratification.get("family_mappings")
    if not isinstance(mappings, Mapping):
        raise ContractValidationError("ratification requires explicit family_mappings")
    expected = set(FROZEN_TRIAL_BUDGETS)
    if set(mappings) != expected:
        raise ContractValidationError("ratification must resolve all 13 registered hypotheses")
    grouped: Dict[str, List[str]] = {}
    for hypothesis_id, family_id in mappings.items():
        grouped.setdefault(str(family_id), []).append(str(hypothesis_id))
    definitions = ratification.get("family_definitions", {})
    if not isinstance(definitions, Mapping):
        raise ContractValidationError("family_definitions must be a mapping")
    for family_id, hypothesis_ids in grouped.items():
        if family_id not in definitions:
            raise ContractValidationError(
                "an explicit family definition is required for every owner-ratified family"
            )
        required = {
            "name",
            "economic_mechanism",
            "primary_metric",
            "benchmark",
            "expected_direction",
            "null_value",
            "economic_hurdle",
            "primary_variant_id",
            "maximum_trial_units",
            "selection_trial_budget",
            "within_family_method",
            "family_alpha",
        }
        if not isinstance(definitions[family_id], Mapping) or not required.issubset(
            definitions[family_id]
        ):
            raise ContractValidationError(
                "family definition must freeze all substantive fields"
            )
    if set(definitions) != set(grouped):
        raise ContractValidationError("migration plan has extra or missing family definitions")
    for hypothesis_id, family_id in mappings.items():
        definition = definitions[str(family_id)]
        expected_metric = KNOWN_PRIMARY_METRICS[str(hypothesis_id)]
        if str(definition["primary_metric"]) != expected_metric:
            raise ContractValidationError("family definition primary metric conflicts with frozen evidence")
        if int(definition["maximum_trial_units"]) != FROZEN_TRIAL_BUDGETS[str(hypothesis_id)]:
            raise ContractValidationError("family definition trial budget conflicts with frozen evidence")
    if identity_history is not None:
        expected_plan = migration_plan_payload(inventory=inventory, ratification=ratification)
        if canonical_hash(ratification.get("migration_plan")) != canonical_hash(expected_plan):
            raise ContractValidationError("signed migration plan differs from fresh receipts or definitions")
        if ratification.get("migration_plan_sha256") != canonical_hash(expected_plan):
            raise ContractValidationError("signed migration plan hash mismatch")
        try:
            attestation = ResearchAttestation.from_dict(ratification["owner_attestation"])
            identity_history.verify(
                attestation,
                expected_role=IdentityRole.OWNER_RATIFIER,
                artifact_sha256=canonical_hash(expected_plan),
                ledger_head_hash=GENESIS_LEDGER_HEAD,
                recorded_at=parse_datetime(str(ratification["ratified_at"])),
            )
        except (KeyError, ContractValidationError) as exc:
            raise ContractValidationError("migration plan lacks a valid anchored owner attestation") from exc
    return {str(key): str(value) for key, value in mappings.items()}


def _source_for_family(repo_root: Path, hypothesis_id: str) -> Tuple[str, str]:
    path, frozen_hash = _frozen_hypothesis_source(repo_root, hypothesis_id)
    return str(path), frozen_hash


def _validate_canonical_census(inventory: Mapping[str, Any]) -> None:
    variant_counts: Dict[str, int] = {}
    for batch in inventory["evaluator_batches"]:
        hypothesis_id = str(batch["hypothesis_id"])
        variant_counts[hypothesis_id] = variant_counts.get(hypothesis_id, 0) + len(
            batch["variant_ids"]
        )
    experiment_hypotheses = set()
    for hypothesis_id in EXPECTED_CANONICAL_EXPERIMENT_HYPOTHESES:
        experiment_ids = {
            row["experiment_id"]
            for row in inventory["gate_attempts"]
            if row["hypothesis_id"] == hypothesis_id
        }
        if len(experiment_ids) == 1:
            experiment_hypotheses.add(hypothesis_id)
    actual = {
        "data_gate_attempt_count": inventory["data_gate_attempt_count"],
        "data_gate_status_counts": dict(inventory["data_gate_status_counts"]),
        "data_gate_hypotheses": sorted(
            {str(row["hypothesis_id"]) for row in inventory["gate_attempts"]}
        ),
        "variants_by_hypothesis": variant_counts,
        "robustness_record_count": inventory["robustness_record_count"],
        "challenge_read_count": inventory["challenge_read_count"],
        "experiment_hypotheses": sorted(experiment_hypotheses),
    }
    expected = {
        "data_gate_attempt_count": EXPECTED_CANONICAL_GATE_COUNT,
        "data_gate_status_counts": EXPECTED_CANONICAL_GATE_STATUSES,
        "data_gate_hypotheses": sorted(
            EXPECTED_CANONICAL_EXPERIMENT_HYPOTHESES
        ),
        "variants_by_hypothesis": EXPECTED_CANONICAL_VARIANTS_BY_HYPOTHESIS,
        "robustness_record_count": EXPECTED_CANONICAL_ROBUSTNESS_COUNT,
        "challenge_read_count": EXPECTED_CANONICAL_CHALLENGE_READ_COUNT,
        "experiment_hypotheses": sorted(EXPECTED_CANONICAL_EXPERIMENT_HYPOTHESES),
    }
    if canonical_hash(actual) != canonical_hash(expected):
        raise ContractValidationError(
            "canonical migration census differs from the one-time reviewed baseline"
        )


def _legacy_bootstrap_removed(
    *,
    repo_root: Path,
    data_root: Path,
    inventory: Mapping[str, Any],
    ratification: Mapping[str, Any],
    ratification_path: Path,
    recorded_at: datetime,
    identity_history: Optional[IdentityRegistryHistory] = None,
    _ledger_path_override: Optional[Path] = None,
    _preflight_complete: bool = False,
) -> Dict[str, Any]:
    """Append deterministic migration events after explicit owner ratification."""

    raise ContractValidationError(
        "unsigned legacy bootstrap is disabled; use publish_signed_migration_plan"
    )

    repo_root = repo_root.resolve()
    data_root = data_root.resolve()
    if repo_root != AUTHORITATIVE_REPO_ROOT or data_root != AUTHORITATIVE_DATA_ROOT:
        raise ResearchBoundaryError("global ledger writes are permitted only on canonical GCP")
    fresh_inventory = audit_existing(repo_root=repo_root, data_root=data_root)
    if canonical_hash(fresh_inventory) != canonical_hash(inventory):
        raise ContractValidationError("supplied inventory differs from a fresh canonical audit")
    inventory = fresh_inventory
    _validate_canonical_census(inventory)
    mappings = _validate_ratification(
        ratification,
        inventory,
        ratification_path=ratification_path,
        identity_history=identity_history,
    )
    canonical_ledger_path = repo_root / LEDGER_RELATIVE_PATH
    if not _preflight_complete:
        with tempfile.TemporaryDirectory(
            prefix=".ledger-preflight-", dir=str(data_root)
        ) as temporary_directory:
            scratch_ledger_path = Path(temporary_directory) / "research_events.v1.jsonl"
            canonical_existed = canonical_ledger_path.exists()
            if canonical_existed:
                if canonical_ledger_path.is_symlink() or not canonical_ledger_path.is_file():
                    raise ResearchBoundaryError(
                        "canonical ledger path must be a regular non-symlink file"
                    )
                shutil.copyfile(canonical_ledger_path, scratch_ledger_path)
            preflight_report = bootstrap_inventory(
                repo_root=repo_root,
                data_root=data_root,
                inventory=inventory,
                ratification=ratification,
                ratification_path=ratification_path,
                recorded_at=recorded_at,
                identity_history=identity_history,
                _ledger_path_override=scratch_ledger_path,
                _preflight_complete=True,
            )
            canonical_ledger_path.parent.mkdir(parents=False, exist_ok=True)
            if canonical_existed:
                if canonical_ledger_path.read_bytes() != scratch_ledger_path.read_bytes():
                    raise ContractValidationError(
                        "existing canonical ledger is incomplete or changed; automatic repair is forbidden"
                    )
                verified_report = dict(preflight_report)
                verified_report["appended_event_count"] = 0
                verified_report["appended_event_ids"] = []
                return verified_report
            try:
                os.link(scratch_ledger_path, canonical_ledger_path)
            except FileExistsError as exc:
                raise ContractValidationError(
                    "canonical ledger appeared during atomic publication"
                ) from exc
            directory_descriptor = os.open(
                str(canonical_ledger_path.parent), os.O_RDONLY
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            return preflight_report
    ledger_path = _ledger_path_override or canonical_ledger_path
    ledger_path.parent.mkdir(parents=False, exist_ok=True)
    ledger = GlobalResearchLedger(ledger_path, research_root=data_root)
    existing_by_id = {item.event_id: item for item in ledger.store.read_all()}
    appended: List[str] = []

    def append_or_verify(
        event_id: str, expected_payload: Mapping[str, Any], append_event: Any
    ) -> None:
        existing = existing_by_id.get(event_id)
        if existing is not None:
            if canonical_hash(existing.payload) != canonical_hash(expected_payload):
                raise ContractValidationError(
                    "idempotent import found a conflicting existing event: {}".format(
                        event_id
                    )
                )
            return
        event = append_event()
        appended.append(event.event_id)
        existing_by_id[event.event_id] = event

    family_ids = tuple(dict.fromkeys(mappings[item] for item in sorted(mappings)))
    wave = ResearchWave(
        wave_id=str(ratification.get("wave_id", LEGACY_WAVE_ID)),
        track=InferenceTrack.EXPLORATORY,
        family_ids=family_ids,
        method=MultipleTestingMethod(
            ratification.get("wave_method", MultipleTestingMethod.HOLM_BONFERRONI.value)
        ),
        alpha_or_q=float(ratification.get("wave_alpha_or_q", 0.05)),
        registered_at=parse_datetime(str(ratification["ratified_at"])),
        policy_artifact=str(ratification["artifact"]),
        policy_sha256=str(ratification["artifact_sha256"]),
        owner_ratified=True,
        dependence_contract_sha256=ratification.get("dependence_contract_sha256"),
        legacy_policy=True,
    )
    append_or_verify(
        "wave:{}".format(wave.wave_id),
        wave.to_dict(),
        lambda: ledger.register_wave(wave, recorded_at=recorded_at),
    )

    metrics_by_hypothesis = dict(KNOWN_PRIMARY_METRICS)
    for batch in inventory["evaluator_batches"]:
        metrics_by_hypothesis[batch["hypothesis_id"]] = batch["primary_metric"]
    family_groups: Dict[str, List[str]] = {}
    for hypothesis_id, family_id in mappings.items():
        family_groups.setdefault(family_id, []).append(hypothesis_id)
    definitions = ratification.get("family_definitions", {})
    for family_id, hypothesis_ids in sorted(family_groups.items()):
        hypothesis_ids = sorted(hypothesis_ids)
        representative = hypothesis_ids[0]
        source_artifact = str(ratification["artifact"])
        source_sha = str(ratification["artifact_sha256"])
        definition = definitions.get(family_id, {})
        if not isinstance(definition, Mapping):
            raise ContractValidationError("family definition must be an object")
        metric_candidates = {
            metrics_by_hypothesis[item]
            for item in hypothesis_ids
        }
        if len(metric_candidates) > 1 and "primary_metric" not in definition:
            raise ContractValidationError(
                "merged family requires an explicit frozen primary_metric"
            )
        family = HypothesisFamily(
            family_id=family_id,
            wave_id=wave.wave_id,
            challenge_epoch_id=str(
                ratification.get("challenge_epoch_id", LEGACY_CHALLENGE_EPOCH_ID)
            ),
            name=str(definition["name"]),
            economic_mechanism=str(definition["economic_mechanism"]),
            family_scope_hash=canonical_hash(
                {
                    "hypothesis_ids": hypothesis_ids,
                    "family_id": family_id,
                    "ratification_sha256": ratification["artifact_sha256"],
                }
            ),
            primary_metric=str(definition["primary_metric"]),
            benchmark=str(definition["benchmark"]),
            expected_direction=ExpectedDirection(definition["expected_direction"]),
            null_value=float(definition["null_value"]),
            economic_hurdle=float(definition["economic_hurdle"]),
            primary_variant_id=str(definition["primary_variant_id"]),
            maximum_trial_units=int(definition["maximum_trial_units"]),
            selection_trial_budget=int(definition["selection_trial_budget"]),
            within_family_method=MultipleTestingMethod(
                definition["within_family_method"]
            ),
            family_alpha=float(definition["family_alpha"]),
            registered_at=parse_datetime(str(ratification["ratified_at"])),
            source_artifact=source_artifact,
            source_sha256=source_sha,
            owner_ratified=True,
        )
        append_or_verify(
            "family:{}".format(family_id),
            family.to_dict(),
            lambda family=family: ledger.register_family(
                family, recorded_at=recorded_at
            ),
        )

    experiment_by_hypothesis = {
        row["hypothesis_id"]: row["experiment_id"]
        for row in inventory["gate_attempts"]
    }
    for hypothesis_id, experiment_id in sorted(experiment_by_hypothesis.items()):
        family_id = mappings[hypothesis_id]
        family_metric = str(definitions[family_id]["primary_metric"])
        source_artifact, source_sha = _source_for_family(repo_root, hypothesis_id)
        experiment = ResearchExperiment(
            experiment_id=experiment_id,
            family_id=family_id,
            hypothesis_id=hypothesis_id,
            parent_experiment_ids=(),
            generated_after_results=False,
            generation_reason="LEGACY_IMPORT",
            frozen_primary_metric=family_metric,
            registered_at=parse_datetime(str(ratification["ratified_at"])),
            source_artifact=source_artifact,
            source_sha256=source_sha,
            owner_ratified=True,
        )
        append_or_verify(
            "experiment:{}".format(experiment_id),
            experiment.to_dict(),
            lambda experiment=experiment: ledger.register_experiment(
                experiment, recorded_at=recorded_at
            ),
        )
    for row in inventory["gate_attempts"]:
        semantic_sha = canonical_hash(
            {"source_sha256": row["source_sha256"], "semantic": "DATA_GATE"}
        )
        run = ResearchRun(
            attempt_id=deterministic_attempt_id(semantic_sha),
            family_id=mappings[row["hypothesis_id"]],
            hypothesis_id=row["hypothesis_id"],
            experiment_id=row["experiment_id"],
            run_id=row["run_id"],
            run_class=ResearchRunClass.DATA_GATE,
            phase=ResearchPhase.DATA,
            occurred_at=parse_datetime(row["occurred_at"]),
            source_artifact=row["source_artifact"],
            source_sha256=row["source_sha256"],
            outcome_data_accessed=False,
            challenge_accessed=False,
            legacy_accounting_quality="SOURCE_NATIVE",
            source_chain_head_hash=row["terminal_event_hash"],
            attempt_outcome=row["data_gate_status"],
        )
        append_or_verify(
            "attempt:{}".format(run.attempt_id),
            run.to_dict(),
            lambda run=run: ledger.register_run(run, recorded_at=recorded_at),
        )

    next_ordinal_by_family: Dict[str, int] = {}
    for batch in inventory["evaluator_batches"]:
        hypothesis_id = batch["hypothesis_id"]
        family_id = mappings[hypothesis_id]
        for variant_id, variant in zip(batch["variant_ids"], batch["variants"]):
            ordinal = next_ordinal_by_family.get(family_id, 0) + 1
            next_ordinal_by_family[family_id] = ordinal
            trial_id = deterministic_trial_id(family_id, ordinal)
            trial_semantic_sha = canonical_hash(
                {
                    "result_hash": batch["result_hash"],
                    "variant_id": variant_id,
                    "semantic": "MODEL_TRIAL",
                }
            )
            run = ResearchRun(
                attempt_id=deterministic_attempt_id(trial_semantic_sha),
                family_id=family_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_by_hypothesis[hypothesis_id],
                run_id="{}:{}".format(batch["result_hash"][:16], variant_id),
                run_class=ResearchRunClass.MODEL_TRIAL,
                phase=ResearchPhase.DISCOVERY,
                occurred_at=parse_datetime(batch["retrieved_at"]),
                source_artifact=batch["source_artifact"],
                source_sha256=batch["source_sha256"],
                statistical_trial_id=trial_id,
                primary_metric=batch["primary_metric"],
                variant_id=variant_id,
                variant_definition_hash=canonical_hash(variant),
                consumes_trial_budget=True,
                preregistered=False,
                outcome_data_accessed=True,
                challenge_accessed=False,
                legacy_accounting_quality="AGGREGATE_ONLY",
                source_chain_head_hash=batch["result_hash"],
                code_sha256=batch["evaluator_code_sha256"],
                data_snapshot_sha256=batch["input_packet_hash"],
                evaluator_spec_sha256=batch["evaluator_spec_sha256"],
                effective_sample_floor=batch["effective_sample_floor"],
            )
            append_or_verify(
                "attempt:{}".format(run.attempt_id),
                run.to_dict(),
                lambda run=run: ledger.register_run(run, recorded_at=recorded_at),
            )
            result = TrialResult(
                statistical_trial_id=trial_id,
                outcome=TrialOutcome.NEGATIVE,
                recorded_at=parse_datetime(batch["retrieved_at"]),
                primary_metric=batch["primary_metric"],
                primary_metric_value=variant[
                    "worst_case_validation_annualized_excess_return_after_costs"
                ],
                p_value=None,
                inference_eligible=False,
                ineligibility_reasons=("CORRECTED_SIGNIFICANCE_NOT_IMPLEMENTED",),
                stress_scenario_pass=False,
                capacity_and_concentration_pass=False,
                effective_sample_size=0,
                minimum_effective_sample=batch["effective_sample_floor"],
                source_artifact=batch["source_artifact"],
                source_sha256=batch["source_sha256"],
            )
            append_or_verify(
                "result:{}".format(trial_id),
                result.to_dict(),
                lambda result=result: ledger.record_result(
                    result, recorded_at=recorded_at
                ),
            )
            robustness_sha = canonical_hash(
                {
                    "result_hash": batch["result_hash"],
                    "variant_id": variant_id,
                    "semantic": "ROBUSTNESS_GRID",
                }
            )
            robustness = ResearchRun(
                attempt_id=deterministic_attempt_id(robustness_sha),
                family_id=family_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_by_hypothesis[hypothesis_id],
                run_id="{}:{}:robustness".format(batch["result_hash"][:16], variant_id),
                run_class=ResearchRunClass.ROBUSTNESS,
                phase=ResearchPhase.DISCOVERY,
                occurred_at=parse_datetime(batch["retrieved_at"]),
                source_artifact=batch["source_artifact"],
                source_sha256=batch["source_sha256"],
                parent_trial_id=trial_id,
                primary_metric=batch["primary_metric"],
                outcome_data_accessed=True,
                challenge_accessed=False,
                legacy_accounting_quality="SOURCE_NATIVE_8_CELL_GRID",
                source_chain_head_hash=batch["result_hash"],
                code_sha256=batch["evaluator_code_sha256"],
                data_snapshot_sha256=batch["input_packet_hash"],
                evaluator_spec_sha256=batch["evaluator_spec_sha256"],
                prespecified_non_selective=True,
            )
            append_or_verify(
                "attempt:{}".format(robustness.attempt_id),
                robustness.to_dict(),
                lambda robustness=robustness: ledger.register_run(
                    robustness, recorded_at=recorded_at
                ),
            )

    projection = ledger.project()
    return {
        "schema_version": "caerus_alpha_lab_legacy_bootstrap_report_v1",
        "appended_event_count": len(appended),
        "appended_event_ids": appended,
        "projection": projection,
        "legacy_import_identity_status": "LEGACY_IMPORTED_UNAUTHENTICATED",
        "identity_activation_head_hash": projection["event_chain_head"],
        "challenge_events_imported": 0,
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }


_DEFINITION_FIELDS = frozenset(
    {
        "name",
        "economic_mechanism",
        "primary_metric",
        "benchmark",
        "expected_direction",
        "null_value",
        "economic_hurdle",
        "primary_variant_id",
        "maximum_trial_units",
        "selection_trial_budget",
        "within_family_method",
        "family_alpha",
        "legacy_ambiguity_blockers",
    }
)


def _canonical_census(inventory: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        name: inventory[name]
        for name in (
            "data_gate_attempt_count",
            "data_gate_status_counts",
            "model_trial_count",
            "robustness_record_count",
            "challenge_read_count",
            "statistical_trial_count",
        )
    }


def _validate_migration_definition(
    definition: Mapping[str, Any], inventory: Mapping[str, Any]
) -> Dict[str, Any]:
    """Validate the complete, merge-free owner decision material."""

    if definition.get("decision") != "RATIFY_GLOBAL_RESEARCH_LEDGER_MIGRATION":
        raise ContractValidationError("migration decision is missing")
    if definition.get("owner") != "Brett Olson":
        raise ContractValidationError("migration decision must name Brett Olson")
    mappings = definition.get("family_mappings")
    if not isinstance(mappings, Mapping) or dict(mappings) != EXACT_FAMILY_MAPPING:
        raise ContractValidationError(
            "migration requires the exact one-family-per-HYP mapping for all 13 hypotheses"
        )
    if len(set(str(value) for value in mappings.values())) != 13:
        raise ContractValidationError("family merges are forbidden")
    definitions = definition.get("family_definitions")
    if not isinstance(definitions, Mapping) or set(definitions) != set(
        EXACT_FAMILY_MAPPING.values()
    ):
        raise ContractValidationError(
            "migration requires exactly one complete definition for each of 13 families"
        )
    for hypothesis_id, family_id in EXACT_FAMILY_MAPPING.items():
        raw = definitions[family_id]
        if not isinstance(raw, Mapping) or set(raw) != _DEFINITION_FIELDS:
            raise ContractValidationError(
                "family definition {} must contain every frozen field and no implicit fields".format(
                    family_id
                )
            )
        expected_normalization = OWNER_NORMALIZED_FAMILY_DEFINITIONS[family_id]
        if canonical_hash(raw) != canonical_hash(expected_normalization):
            raise ContractValidationError(
                "family definition differs from the exact owner normalization for its frozen HYP source"
            )
        if raw["maximum_trial_units"] != FROZEN_TRIAL_BUDGETS[hypothesis_id]:
            raise ContractValidationError(
                "family definition trial budget conflicts with frozen evidence"
            )
        expected_blockers = REQUIRED_LEGACY_DEFINITION_BLOCKERS.get(
            hypothesis_id, ()
        )
        if tuple(raw["legacy_ambiguity_blockers"]) != expected_blockers:
            raise ContractValidationError(
                "family definition does not retain the exact legacy ambiguity blockers"
            )
        if (
            hypothesis_id == "HYP-2026-009"
            and raw["primary_variant_id"] != UNRESOLVED_HYP_009_PRIMARY
        ):
            raise ContractValidationError(
                "HYP-2026-009 primary variant must remain explicitly unresolved"
            )
        if (
            hypothesis_id == "HYP-2026-010"
            and raw["benchmark"] != UNRESOLVED_HYP_010_BENCHMARK
        ):
            raise ContractValidationError(
                "HYP-2026-010 benchmark must remain explicitly unresolved"
            )
        # Exercise the full typed contract now, before any event plan exists.
        HypothesisFamily(
            family_id=family_id,
            wave_id=str(definition.get("wave_id", LEGACY_WAVE_ID)),
            challenge_epoch_id=str(
                definition.get("challenge_epoch_id", LEGACY_CHALLENGE_EPOCH_ID)
            ),
            name=str(raw["name"]),
            economic_mechanism=str(raw["economic_mechanism"]),
            family_scope_hash="0" * 64,
            primary_metric=str(raw["primary_metric"]),
            benchmark=str(raw["benchmark"]),
            expected_direction=ExpectedDirection(raw["expected_direction"]),
            null_value=raw["null_value"],
            economic_hurdle=raw["economic_hurdle"],
            primary_variant_id=str(raw["primary_variant_id"]),
            maximum_trial_units=raw["maximum_trial_units"],
            selection_trial_budget=raw["selection_trial_budget"],
            within_family_method=MultipleTestingMethod(raw["within_family_method"]),
            family_alpha=raw["family_alpha"],
            registered_at=parse_datetime(str(definition["recorded_at"])),
            source_artifact="migration-plan:pending",
            source_sha256="0" * 64,
            owner_ratified=True,
            legacy_definition_blockers=expected_blockers,
        )
    method = MultipleTestingMethod(
        definition.get("wave_method", MultipleTestingMethod.HOLM_BONFERRONI.value)
    )
    dependence = definition.get("dependence_contract")
    if not isinstance(dependence, Mapping) or set(dependence) != {
        "assumption",
        "artifact_sha256",
    }:
        raise ContractValidationError(
            "migration must freeze the complete wave dependence contract"
        )
    if method is MultipleTestingMethod.BENJAMINI_HOCHBERG:
        if dependence["artifact_sha256"] is None:
            raise ContractValidationError("BH requires a frozen dependence artifact")
        require_sha256(str(dependence["artifact_sha256"]), "dependence artifact")
    elif dependence != {
        "assumption": "NO_POSITIVE_DEPENDENCE_CLAIM",
        "artifact_sha256": None,
    }:
        raise ContractValidationError(
            "legacy non-BH migration must disclaim a positive-dependence claim"
        )
    if inventory.get("hypothesis_sources") is None:
        raise ContractValidationError("inventory lacks per-hypothesis provenance")
    return json.loads(canonical_json(definition))


def _planned_record(
    *,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    recorded_at: datetime,
    payload: Mapping[str, Any],
    previous_event_hash: Optional[str],
) -> EventRecord:
    payload_hash = canonical_hash(payload)
    unsigned = {
        "schema_version": "caerus_alpha_lab_event_v1",
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": format_datetime(occurred_at),
        "recorded_at": format_datetime(recorded_at),
        "payload": payload,
        "payload_hash": payload_hash,
        "previous_event_hash": previous_event_hash,
    }
    return EventRecord(
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        recorded_at=recorded_at,
        payload=payload,
        payload_hash=payload_hash,
        previous_event_hash=previous_event_hash,
        event_hash=canonical_hash(unsigned),
    )


def _ledger_bytes(records: Sequence[EventRecord]) -> bytes:
    return "".join(canonical_json(item.to_dict()) + "\n" for item in records).encode(
        "utf-8"
    )


def _event_descriptor(record: EventRecord) -> Dict[str, Any]:
    return {
        "event_id": record.event_id,
        "event_type": record.event_type,
        "typed_payload_sha256": typed_event_payload_hash(
            record.event_type, record.payload
        ),
        "record_payload_sha256": record.payload_hash,
        "previous_event_hash": record.previous_event_hash,
        "event_hash": record.event_hash,
        "recorded_at": format_datetime(record.recorded_at),
    }


def _build_migration_event_plan_and_records(
    *,
    inventory: Mapping[str, Any],
    migration_definition: Mapping[str, Any],
    active_registry_hash: str,
    externally_pinned_registry_hash: str,
) -> Tuple[Dict[str, Any], Tuple[EventRecord, ...]]:
    """Purely build the exact canonical legacy event plan an owner may sign."""

    require_sha256(active_registry_hash, "active_registry_hash")
    require_sha256(externally_pinned_registry_hash, "externally_pinned_registry_hash")
    if active_registry_hash != externally_pinned_registry_hash:
        raise ContractValidationError(
            "migration plan requires the externally pinned active registry"
        )
    definition = _validate_migration_definition(migration_definition, inventory)
    recorded_at = parse_datetime(str(definition["recorded_at"]))
    census = _canonical_census(inventory)
    identity_material = {
        "schema_version": "caerus_alpha_lab_migration_plan_identity_v1",
        "decision": definition["decision"],
        "owner": definition["owner"],
        "recorded_at": format_datetime(recorded_at),
        "source_receipts": inventory["source_receipts"],
        "hypothesis_sources": inventory["hypothesis_sources"],
        "census": census,
        "family_mappings": definition["family_mappings"],
        "family_definitions": definition["family_definitions"],
        "wave_id": definition.get("wave_id", LEGACY_WAVE_ID),
        "wave_method": definition.get("wave_method", "HOLM_BONFERRONI"),
        "wave_alpha_or_q": float(definition.get("wave_alpha_or_q", 0.05)),
        "dependence_contract": definition["dependence_contract"],
        "challenge_epoch_id": definition.get(
            "challenge_epoch_id", LEGACY_CHALLENGE_EPOCH_ID
        ),
        "active_registry_hash": active_registry_hash,
        "externally_pinned_registry_hash": externally_pinned_registry_hash,
        "publication_contract": {
            "location": "CANONICAL_GCP_ONLY",
            "mode": PUBLICATION_MODE,
            "owner_signature_authorizes_one_publication": False,
            "separate_publication_authorization_required": True,
            "overwrite_or_repair": False,
        },
    }
    plan_identity = canonical_hash(identity_material)
    plan_uri = "migration-plan:{}".format(plan_identity)
    records: List[EventRecord] = []

    def add(event_id: str, event_type: str, occurred_at: datetime, payload: Mapping[str, Any]) -> None:
        records.append(
            _planned_record(
                event_id=event_id,
                event_type=event_type,
                occurred_at=occurred_at,
                recorded_at=recorded_at,
                payload=payload,
                previous_event_hash=records[-1].event_hash if records else None,
            )
        )

    family_ids = tuple(EXACT_FAMILY_MAPPING[hyp] for hyp in sorted(EXACT_FAMILY_MAPPING))
    wave = ResearchWave(
        wave_id=str(identity_material["wave_id"]),
        track=InferenceTrack.EXPLORATORY,
        family_ids=family_ids,
        method=MultipleTestingMethod(str(identity_material["wave_method"])),
        alpha_or_q=float(identity_material["wave_alpha_or_q"]),
        registered_at=recorded_at,
        policy_artifact=plan_uri,
        policy_sha256=plan_identity,
        owner_ratified=True,
        dependence_contract_sha256=identity_material["dependence_contract"][
            "artifact_sha256"
        ],
        legacy_policy=True,
    )
    add("wave:{}".format(wave.wave_id), GlobalResearchLedger.WAVE_EVENT, wave.registered_at, wave.to_dict())

    for hypothesis_id, family_id in sorted(EXACT_FAMILY_MAPPING.items()):
        raw = identity_material["family_definitions"][family_id]
        family = HypothesisFamily(
            family_id=family_id,
            wave_id=wave.wave_id,
            challenge_epoch_id=str(identity_material["challenge_epoch_id"]),
            name=str(raw["name"]),
            economic_mechanism=str(raw["economic_mechanism"]),
            family_scope_hash=canonical_hash(
                {
                    "hypothesis_id": hypothesis_id,
                    "family_id": family_id,
                    "plan_identity_sha256": plan_identity,
                }
            ),
            primary_metric=str(raw["primary_metric"]),
            benchmark=str(raw["benchmark"]),
            expected_direction=ExpectedDirection(raw["expected_direction"]),
            null_value=float(raw["null_value"]),
            economic_hurdle=float(raw["economic_hurdle"]),
            primary_variant_id=str(raw["primary_variant_id"]),
            maximum_trial_units=int(raw["maximum_trial_units"]),
            selection_trial_budget=int(raw["selection_trial_budget"]),
            within_family_method=MultipleTestingMethod(raw["within_family_method"]),
            family_alpha=float(raw["family_alpha"]),
            registered_at=recorded_at,
            source_artifact=plan_uri,
            source_sha256=plan_identity,
            owner_ratified=True,
            legacy_definition_blockers=tuple(raw["legacy_ambiguity_blockers"]),
        )
        add("family:{}".format(family_id), GlobalResearchLedger.FAMILY_EVENT, family.registered_at, family.to_dict())

    experiment_by_hypothesis = {
        row["hypothesis_id"]: row["experiment_id"]
        for row in inventory["gate_attempts"]
    }
    if set(experiment_by_hypothesis) != set(
        EXPECTED_CANONICAL_EXPERIMENT_HYPOTHESES
    ):
        raise ContractValidationError("inventory does not bind exactly one experiment to every HYP")
    for hypothesis_id, experiment_id in sorted(experiment_by_hypothesis.items()):
        provenance = inventory["hypothesis_sources"][hypothesis_id]
        experiment = ResearchExperiment(
            experiment_id=experiment_id,
            family_id=EXACT_FAMILY_MAPPING[hypothesis_id],
            hypothesis_id=hypothesis_id,
            parent_experiment_ids=(),
            generated_after_results=False,
            generation_reason="LEGACY_IMPORT",
            frozen_primary_metric=identity_material["family_definitions"][
                EXACT_FAMILY_MAPPING[hypothesis_id]
            ]["primary_metric"],
            registered_at=recorded_at,
            source_artifact=str(provenance["artifact"]),
            source_sha256=str(provenance["frozen_body_sha256"]),
            owner_ratified=True,
        )
        add("experiment:{}".format(experiment_id), GlobalResearchLedger.EXPERIMENT_EVENT, experiment.registered_at, experiment.to_dict())

    for row in inventory["gate_attempts"]:
        semantic_sha = canonical_hash(
            {"source_sha256": row["source_sha256"], "semantic": "DATA_GATE"}
        )
        run = ResearchRun(
            attempt_id=deterministic_attempt_id(semantic_sha),
            family_id=EXACT_FAMILY_MAPPING[row["hypothesis_id"]],
            hypothesis_id=row["hypothesis_id"],
            experiment_id=row["experiment_id"],
            run_id=row["run_id"],
            run_class=ResearchRunClass.DATA_GATE,
            phase=ResearchPhase.DATA,
            occurred_at=parse_datetime(row["occurred_at"]),
            source_artifact=row["source_artifact"],
            source_sha256=row["source_sha256"],
            outcome_data_accessed=False,
            challenge_accessed=False,
            legacy_accounting_quality="SOURCE_NATIVE",
            source_chain_head_hash=row["terminal_event_hash"],
            attempt_outcome=row["data_gate_status"],
        )
        add("attempt:{}".format(run.attempt_id), GlobalResearchLedger.RUN_EVENT, run.occurred_at, run.to_dict())

    next_ordinal = {family_id: 0 for family_id in family_ids}
    for batch in inventory["evaluator_batches"]:
        hypothesis_id = batch["hypothesis_id"]
        family_id = EXACT_FAMILY_MAPPING[hypothesis_id]
        for variant_id, variant in zip(batch["variant_ids"], batch["variants"]):
            next_ordinal[family_id] += 1
            trial_id = deterministic_trial_id(family_id, next_ordinal[family_id])
            trial_sha = canonical_hash(
                {
                    "result_hash": batch["result_hash"],
                    "variant_id": variant_id,
                    "semantic": "MODEL_TRIAL",
                }
            )
            trial = ResearchRun(
                attempt_id=deterministic_attempt_id(trial_sha),
                family_id=family_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_by_hypothesis[hypothesis_id],
                run_id="{}:{}".format(batch["result_hash"][:16], variant_id),
                run_class=ResearchRunClass.MODEL_TRIAL,
                phase=ResearchPhase.DISCOVERY,
                occurred_at=parse_datetime(batch["retrieved_at"]),
                source_artifact=batch["source_artifact"],
                source_sha256=batch["source_sha256"],
                statistical_trial_id=trial_id,
                primary_metric=batch["primary_metric"],
                variant_id=variant_id,
                variant_definition_hash=canonical_hash(variant),
                consumes_trial_budget=True,
                preregistered=False,
                outcome_data_accessed=True,
                challenge_accessed=False,
                legacy_accounting_quality="AGGREGATE_ONLY",
                source_chain_head_hash=batch["result_hash"],
                code_sha256=batch["evaluator_code_sha256"],
                data_snapshot_sha256=batch["input_packet_hash"],
                evaluator_spec_sha256=batch["evaluator_spec_sha256"],
                effective_sample_floor=batch["effective_sample_floor"],
            )
            add("attempt:{}".format(trial.attempt_id), GlobalResearchLedger.RUN_EVENT, trial.occurred_at, trial.to_dict())
            result = TrialResult(
                statistical_trial_id=trial_id,
                outcome=TrialOutcome.NEGATIVE,
                recorded_at=parse_datetime(batch["retrieved_at"]),
                primary_metric=batch["primary_metric"],
                primary_metric_value=variant[
                    "worst_case_validation_annualized_excess_return_after_costs"
                ],
                p_value=None,
                inference_eligible=False,
                ineligibility_reasons=("CORRECTED_SIGNIFICANCE_NOT_IMPLEMENTED",),
                stress_scenario_pass=False,
                capacity_and_concentration_pass=False,
                effective_sample_size=0,
                minimum_effective_sample=batch["effective_sample_floor"],
                source_artifact=batch["source_artifact"],
                source_sha256=batch["source_sha256"],
            )
            add("result:{}".format(trial_id), GlobalResearchLedger.RESULT_EVENT, result.recorded_at, result.to_dict())
            robustness_sha = canonical_hash(
                {
                    "result_hash": batch["result_hash"],
                    "variant_id": variant_id,
                    "semantic": "ROBUSTNESS_GRID",
                }
            )
            robustness = ResearchRun(
                attempt_id=deterministic_attempt_id(robustness_sha),
                family_id=family_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_by_hypothesis[hypothesis_id],
                run_id="{}:{}:robustness".format(batch["result_hash"][:16], variant_id),
                run_class=ResearchRunClass.ROBUSTNESS,
                phase=ResearchPhase.DISCOVERY,
                occurred_at=parse_datetime(batch["retrieved_at"]),
                source_artifact=batch["source_artifact"],
                source_sha256=batch["source_sha256"],
                parent_trial_id=trial_id,
                primary_metric=batch["primary_metric"],
                outcome_data_accessed=True,
                challenge_accessed=False,
                legacy_accounting_quality="SOURCE_NATIVE_8_CELL_GRID",
                source_chain_head_hash=batch["result_hash"],
                code_sha256=batch["evaluator_code_sha256"],
                data_snapshot_sha256=batch["input_packet_hash"],
                evaluator_spec_sha256=batch["evaluator_spec_sha256"],
                prespecified_non_selective=True,
            )
            add("attempt:{}".format(robustness.attempt_id), GlobalResearchLedger.RUN_EVENT, robustness.occurred_at, robustness.to_dict())

    ledger_bytes = _ledger_bytes(records)
    terminal_head = records[-1].event_hash
    plan = {
        **identity_material,
        # The plan wrapper has its own schema. The identity sub-hash above
        # deliberately retains ca..._migration_plan_identity_v1.
        "schema_version": "caerus_alpha_lab_migration_event_plan_v2",
        "plan_identity_sha256": plan_identity,
        "ordered_events": [_event_descriptor(item) for item in records],
        "expected_event_count": len(records),
        "expected_terminal_head": terminal_head,
        "identity_activation_head_hash": terminal_head,
        "expected_ledger_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
    }
    return plan, tuple(records)


def build_migration_event_plan(
    *,
    inventory: Mapping[str, Any],
    migration_definition: Mapping[str, Any],
    active_registry_hash: str,
    externally_pinned_registry_hash: str,
) -> Dict[str, Any]:
    """Purely build the exact canonical legacy event plan an owner may sign."""

    plan, _ = _build_migration_event_plan_and_records(
        inventory=inventory,
        migration_definition=migration_definition,
        active_registry_hash=active_registry_hash,
        externally_pinned_registry_hash=externally_pinned_registry_hash,
    )
    return plan


def _records_from_plan_and_inventory(
    *, plan: Mapping[str, Any], inventory: Mapping[str, Any]
) -> Tuple[List[EventRecord], bytes]:
    definition = {
        key: plan[key]
        for key in (
            "decision",
            "owner",
            "recorded_at",
            "family_mappings",
            "family_definitions",
            "wave_id",
            "wave_method",
            "wave_alpha_or_q",
            "dependence_contract",
            "challenge_epoch_id",
        )
    }
    rebuilt, records = _build_migration_event_plan_and_records(
        inventory=inventory,
        migration_definition=definition,
        active_registry_hash=str(plan["active_registry_hash"]),
        externally_pinned_registry_hash=str(plan["externally_pinned_registry_hash"]),
    )
    if canonical_hash(rebuilt) != canonical_hash(plan):
        raise ContractValidationError(
            "signed migration plan differs from a fresh deterministic event plan"
        )
    return list(records), _ledger_bytes(records)


def migration_receipt_set_hash(inventory: Mapping[str, Any]) -> str:
    """Hash every immutable source receipt and the exact canonical census."""

    try:
        material = {
            "schema_version": "caerus_alpha_lab_migration_receipt_set_v1",
            "source_receipts": inventory["source_receipts"],
            "hypothesis_sources": inventory["hypothesis_sources"],
            "census": _canonical_census(inventory),
        }
    except KeyError as exc:
        raise ContractValidationError("migration inventory receipt set is incomplete") from exc
    return canonical_hash(material)


def publication_authorization_attestation_context_hash(
    authorization: Mapping[str, Any],
) -> str:
    """Bind QS-003 to one plan, receipt set, path, and GENESIS creation."""

    expected_fields = {
        "schema_version",
        "decision",
        "owner",
        "authorized_at",
        "signed_migration_plan_sha256",
        "migration_plan_sha256",
        "plan_identity_sha256",
        "canonical_ledger_path",
        "expected_ledger_sha256",
        "expected_event_count",
        "expected_terminal_head",
        "identity_activation_head_hash",
        "fresh_receipt_set_sha256",
        "publication_mode",
        "overwrite_or_repair_allowed",
        "active_registry_hash",
        "externally_pinned_registry_hash",
        "prior_ledger_head",
        "trading_behavior_changed",
        "promotion_performed",
    }
    if set(authorization) != expected_fields:
        raise ContractValidationError("publication authorization schema is incomplete or mutable")
    if authorization.get("schema_version") != PUBLICATION_AUTHORIZATION_SCHEMA:
        raise ContractValidationError("publication authorization schema_version is invalid")
    if authorization.get("decision") != "AUTHORIZE_CREATE_ONLY_GLOBAL_RESEARCH_LEDGER_PUBLICATION":
        raise ContractValidationError("publication authorization decision is invalid")
    if authorization.get("owner") != "Brett Olson":
        raise ContractValidationError("publication authorization must name Brett Olson")
    parse_datetime(str(authorization["authorized_at"]))
    for field in (
        "signed_migration_plan_sha256",
        "migration_plan_sha256",
        "plan_identity_sha256",
        "expected_ledger_sha256",
        "expected_terminal_head",
        "identity_activation_head_hash",
        "fresh_receipt_set_sha256",
        "active_registry_hash",
        "externally_pinned_registry_hash",
    ):
        require_sha256(str(authorization[field]), field)
    if (
        authorization["publication_mode"] != PUBLICATION_MODE
        or authorization["overwrite_or_repair_allowed"] is not False
        or authorization["prior_ledger_head"] != GENESIS_LEDGER_HEAD
        or authorization["trading_behavior_changed"] is not False
        or authorization["promotion_performed"] is not False
    ):
        raise ContractValidationError("publication authorization weakens create-only boundaries")
    if authorization["active_registry_hash"] != authorization["externally_pinned_registry_hash"]:
        raise ContractValidationError("publication authorization does not bind the active external pin")
    if authorization["expected_terminal_head"] != authorization["identity_activation_head_hash"]:
        raise ContractValidationError("publication authorization activation head is inconsistent")
    if not isinstance(authorization["expected_event_count"], int) or authorization["expected_event_count"] < 1:
        raise ContractValidationError("publication authorization event count is invalid")
    path = Path(str(authorization["canonical_ledger_path"]))
    if not path.is_absolute() or ".." in path.parts:
        raise ContractValidationError("publication authorization path must be exact and absolute")
    return canonical_hash(
        {
            "schema_version": "caerus_alpha_lab_publication_authorization_attestation_context_v1",
            "authorization": dict(authorization),
        }
    )


def build_publication_authorization(
    *,
    repo_root: Path,
    inventory: Mapping[str, Any],
    signed_plan: Mapping[str, Any],
    identity_history: IdentityRegistryHistory,
    authorized_at: datetime,
) -> Dict[str, Any]:
    """Build the exact QS-003 payload; this function performs no signing."""

    if not isinstance(identity_history, IdentityRegistryHistory):
        raise ContractValidationError("publication authorization requires pinned public history")
    activation = IdentityActivationEvidence.from_signed_plan(
        signed_plan, identity_history=identity_history
    )
    plan = activation.plan
    _validate_canonical_census(inventory)
    records, expected_bytes = _records_from_plan_and_inventory(
        plan=plan, inventory=inventory
    )
    if hashlib.sha256(expected_bytes).hexdigest() != plan["expected_ledger_sha256"]:
        raise ContractValidationError("publication authorization rebuilt bytes differ from plan")
    if authorized_at.tzinfo is None or authorized_at.utcoffset() is None:
        raise ContractValidationError("publication authorization timestamp must be timezone-aware")
    if authorized_at < parse_datetime(str(plan["recorded_at"])):
        raise ContractValidationError("publication authorization predates its migration plan")
    canonical_path = repo_root.expanduser().resolve() / LEDGER_RELATIVE_PATH
    result = {
        "schema_version": PUBLICATION_AUTHORIZATION_SCHEMA,
        "decision": "AUTHORIZE_CREATE_ONLY_GLOBAL_RESEARCH_LEDGER_PUBLICATION",
        "owner": "Brett Olson",
        "authorized_at": format_datetime(authorized_at),
        "signed_migration_plan_sha256": canonical_hash(signed_plan),
        "migration_plan_sha256": canonical_hash(plan),
        "plan_identity_sha256": plan["plan_identity_sha256"],
        "canonical_ledger_path": str(canonical_path),
        "expected_ledger_sha256": plan["expected_ledger_sha256"],
        "expected_event_count": len(records),
        "expected_terminal_head": plan["expected_terminal_head"],
        "identity_activation_head_hash": plan["identity_activation_head_hash"],
        "fresh_receipt_set_sha256": migration_receipt_set_hash(inventory),
        "publication_mode": PUBLICATION_MODE,
        "overwrite_or_repair_allowed": False,
        "active_registry_hash": identity_history.active_registry_hash,
        "externally_pinned_registry_hash": identity_history.externally_pinned_registry_hash,
        "prior_ledger_head": GENESIS_LEDGER_HEAD,
        "trading_behavior_changed": False,
        "promotion_performed": False,
    }
    publication_authorization_attestation_context_hash(result)
    return result


def verify_signed_publication_authorization(
    value: Mapping[str, Any],
    *,
    repo_root: Path,
    inventory: Mapping[str, Any],
    signed_plan: Mapping[str, Any],
    identity_history: IdentityRegistryHistory,
) -> Dict[str, Any]:
    """Verify QS-003 against freshly rebuilt public migration evidence."""

    expected_wrapper = {"schema_version", "authorization", "owner_attestation"}
    if set(value) != expected_wrapper or value.get("schema_version") != SIGNED_PUBLICATION_AUTHORIZATION_SCHEMA:
        raise ContractValidationError("signed publication authorization wrapper is invalid")
    raw = value.get("authorization")
    if not isinstance(raw, Mapping):
        raise ContractValidationError("signed publication authorization lacks its payload")
    authorization = dict(raw)
    authorized_at = parse_datetime(str(authorization.get("authorized_at", "")))
    expected = build_publication_authorization(
        repo_root=repo_root,
        inventory=inventory,
        signed_plan=signed_plan,
        identity_history=identity_history,
        authorized_at=authorized_at,
    )
    if canonical_hash(expected) != canonical_hash(authorization):
        raise ContractValidationError(
            "publication authorization differs from fresh plan, receipts, path, or registry"
        )
    try:
        attestation = ResearchAttestation.from_dict(value["owner_attestation"])
        identity_history.verify(
            attestation,
            expected_role=IdentityRole.OWNER_RATIFIER,
            artifact_sha256=canonical_hash(authorization),
            ledger_head_hash=GENESIS_LEDGER_HEAD,
            context_sha256=publication_authorization_attestation_context_hash(authorization),
            recorded_at=authorized_at,
            for_new_event=True,
        )
    except (KeyError, ContractValidationError) as exc:
        raise ContractValidationError(
            "publication authorization lacks a valid active-registry owner signature"
        ) from exc
    return authorization


def publish_signed_migration_plan(
    *,
    repo_root: Path,
    data_root: Path,
    inventory: Mapping[str, Any],
    signed_plan: Mapping[str, Any],
    publication_authorization: Mapping[str, Any],
    identity_history: IdentityRegistryHistory,
) -> Dict[str, Any]:
    """Create the canonical ledger exactly once from an active-registry signature."""

    repo_root = repo_root.resolve()
    data_root = data_root.resolve()
    if repo_root != AUTHORITATIVE_REPO_ROOT or data_root != AUTHORITATIVE_DATA_ROOT:
        raise ResearchBoundaryError("global ledger writes are permitted only on canonical GCP")
    if not isinstance(identity_history, IdentityRegistryHistory):
        raise ContractValidationError(
            "publication requires externally pinned identity registry history"
        )
    fresh_inventory = audit_existing(repo_root=repo_root, data_root=data_root)
    if canonical_hash(fresh_inventory) != canonical_hash(inventory):
        raise ContractValidationError("supplied inventory differs from a fresh canonical audit")
    _validate_canonical_census(fresh_inventory)
    activation = IdentityActivationEvidence.from_signed_plan(
        signed_plan, identity_history=identity_history
    )
    plan = activation.plan
    if plan["active_registry_hash"] != identity_history.active_registry_hash:
        raise ContractValidationError("migration plan does not use the externally pinned active registry")
    records, expected_bytes = _records_from_plan_and_inventory(
        plan=plan, inventory=fresh_inventory
    )
    if hashlib.sha256(expected_bytes).hexdigest() != plan["expected_ledger_sha256"]:
        raise ContractValidationError("rebuilt ledger bytes differ from the signed plan")
    activation.verify_legacy_records(records)
    verified_authorization = verify_signed_publication_authorization(
        publication_authorization,
        repo_root=repo_root,
        inventory=fresh_inventory,
        signed_plan=signed_plan,
        identity_history=identity_history,
    )
    canonical_path = repo_root / LEDGER_RELATIVE_PATH
    if canonical_path.exists() or canonical_path.is_symlink():
        raise ContractValidationError(
            "canonical ledger already exists; create-only publication forbids repair or overwrite"
        )
    ledger_directory = canonical_path.parent
    if ledger_directory.exists():
        if ledger_directory.is_symlink() or not ledger_directory.is_dir():
            raise ResearchBoundaryError(
                "canonical ledger directory must be a real directory, not a symlink"
            )
    else:
        ledger_directory.mkdir(parents=False, exist_ok=False, mode=0o700)
    try:
        ledger_directory.resolve(strict=True).relative_to(data_root)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise ResearchBoundaryError(
            "canonical ledger directory escapes the authoritative data root"
        ) from exc
    with tempfile.TemporaryDirectory(prefix=".signed-ledger-", dir=str(data_root)) as directory:
        scratch = Path(directory) / "research_events.v1.jsonl"
        descriptor = os.open(str(scratch), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(expected_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            raise
        if scratch.read_bytes() != expected_bytes:
            raise ContractValidationError("scratch ledger bytes do not match the signed plan")
        if _sha256_file(scratch) != plan["expected_ledger_sha256"]:
            raise ContractValidationError("scratch ledger hash does not match the signed plan")
        scratch_store = AppendOnlyJSONLEventStore(scratch, research_root=data_root)
        scratch_records = scratch_store.read_all()
        activation.verify_legacy_records(scratch_records)
        GlobalResearchLedger(scratch, research_root=data_root).project()
        try:
            os.link(scratch, canonical_path)
        except FileExistsError as exc:
            raise ContractValidationError(
                "canonical ledger appeared during create-only publication"
            ) from exc
        directory_descriptor = os.open(str(canonical_path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    return {
        "schema_version": "caerus_alpha_lab_authenticated_migration_publication_v1",
        "plan_identity_sha256": plan["plan_identity_sha256"],
        "migration_plan_sha256": canonical_hash(plan),
        "signed_migration_plan_sha256": canonical_hash(signed_plan),
        "publication_authorization_sha256": canonical_hash(verified_authorization),
        "published_event_count": len(records),
        "identity_activation_head_hash": plan["identity_activation_head_hash"],
        "ledger_sha256": plan["expected_ledger_sha256"],
        "legacy_import_identity_status": "LEGACY_IMPORTED_UNAUTHENTICATED",
        "publication_mode": PUBLICATION_MODE,
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }


def bootstrap_inventory(
    *,
    repo_root: Path,
    data_root: Path,
    inventory: Mapping[str, Any],
    signed_plan: Mapping[str, Any],
    publication_authorization: Mapping[str, Any],
    identity_history: IdentityRegistryHistory,
) -> Dict[str, Any]:
    """Compatibility name for the strict signed create-only publisher."""

    return publish_signed_migration_plan(
        repo_root=repo_root,
        data_root=data_root,
        inventory=inventory,
        signed_plan=signed_plan,
        publication_authorization=publication_authorization,
        identity_history=identity_history,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--emit-migration-plan", action="store_true")
    parser.add_argument("--migration-definition", type=Path)
    parser.add_argument("--signed-migration-plan", type=Path)
    parser.add_argument("--signed-publication-authorization", type=Path)
    parser.add_argument("--identity-registry", type=Path, action="append")
    parser.add_argument("--identity-trust-anchor", type=Path)
    parser.add_argument("--identity-registry-pin")
    return parser


def _load_identity_history(
    *, registry_paths: Sequence[Path], trust_anchor_path: Path, external_pin: str
) -> IdentityRegistryHistory:
    anchor_raw = _load_json(trust_anchor_path)
    anchor = IdentityTrustAnchor(
        anchor_id=str(anchor_raw["anchor_id"]),
        root_key_id=str(anchor_raw["root_key_id"]),
        root_public_key_pem=str(anchor_raw["root_public_key_pem"]),
        expected_registry_id=str(anchor_raw["expected_registry_id"]),
        schema_version=str(
            anchor_raw.get(
                "schema_version", "caerus_alpha_lab_identity_trust_anchor_v1"
            )
        ),
    )
    registries = tuple(
        IdentityRegistry.from_dict(_load_json(path), trust_anchor=anchor)
        for path in registry_paths
    )
    return IdentityRegistryHistory(
        registries=registries,
        active_registry_hash=external_pin,
        externally_pinned_registry_hash=external_pin,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    inventory = audit_existing(repo_root=args.repo_root, data_root=args.data_root)
    if args.emit_migration_plan:
        if args.write:
            raise ContractValidationError(
                "--emit-migration-plan and --write are mutually exclusive"
            )
        if (
            args.migration_definition is None
            or not args.identity_registry
            or args.identity_trust_anchor is None
            or args.identity_registry_pin is None
        ):
            raise ContractValidationError(
                "--emit-migration-plan requires --migration-definition, full --identity-registry history, "
                "--identity-trust-anchor, and the separately supplied --identity-registry-pin"
            )
        history = _load_identity_history(
            registry_paths=args.identity_registry,
            trust_anchor_path=args.identity_trust_anchor,
            external_pin=args.identity_registry_pin,
        )
        plan = build_migration_event_plan(
            inventory=inventory,
            migration_definition=_load_json(args.migration_definition),
            active_registry_hash=history.active_registry_hash,
            externally_pinned_registry_hash=history.externally_pinned_registry_hash,
        )
        print(canonical_json(plan))
        return 0
    if not args.write:
        print(canonical_json(inventory))
        return 0
    if (
        args.signed_migration_plan is None
        or args.signed_publication_authorization is None
        or not args.identity_registry
        or args.identity_trust_anchor is None
        or args.identity_registry_pin is None
    ):
        raise ContractValidationError(
            "--write requires --signed-migration-plan, --signed-publication-authorization, --identity-registry, "
            "--identity-trust-anchor, and --identity-registry-pin"
        )
    history = _load_identity_history(
        registry_paths=args.identity_registry,
        trust_anchor_path=args.identity_trust_anchor,
        external_pin=args.identity_registry_pin,
    )
    report = publish_signed_migration_plan(
        repo_root=args.repo_root,
        data_root=args.data_root,
        inventory=inventory,
        signed_plan=_load_json(args.signed_migration_plan),
        publication_authorization=_load_json(args.signed_publication_authorization),
        identity_history=history,
    )
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
