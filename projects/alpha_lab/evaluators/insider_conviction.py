"""Fail-closed frozen evaluator boundary for HYP-2026-003.

This adapter implements the causal event-construction portion of the frozen
insider-cluster experiment.  It refuses to read a file unless the evaluator
packet binds it by SHA-256 to a pre-challenge extract.  It intentionally emits
no return estimate while the stateful portfolio, full factor/sector inference,
and frozen multiple-testing obligations remain incomplete.

That blocked result is an operational evaluator outcome, not evidence for or
against insider alpha.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from projects.alpha_lab.factory.canonical import canonical_hash
from projects.alpha_lab.factory.errors import ContractValidationError


HYPOTHESIS_ID = "HYP-2026-003"
PRIMARY_METRIC = "annualized_factor_adjusted_net_cluster_minus_single_alpha"
CHALLENGE_START = "2025-01-01"
REQUIRED_ASSETS = (
    "pit_security_master_v1",
    "pit_membership_v1",
    "pit_prices_liquidity_v1",
    "pit_characteristics_v1",
    "factor_panel_v1",
    "sector_returns_v1",
    "cik_identity_input_v1",
    "form4_event_tape_v1",
)
INCOMPLETE_OBLIGATIONS = (
    "certified_sixty_session_issuer_cooldown_and_nonoverlap",
    "stateful_daily_max_ten_equal_weight_portfolio_with_cash_residual",
    "next_open_entry_and_terminal_settlement_return_chain",
    "same_rule_single_purchase_investable_baseline",
    "date_sector_size_value_reversal_matched_event_baseline",
    "expanding_annual_walk_forward_2019_2024",
    "factor_and_sector_adjusted_calendar_time_alpha",
    "issuer_and_event_month_clustered_inference",
    "romano_wolf_max_t_across_the_frozen_variant_family",
    "issuer_event_and_year_contribution_limits",
    "twenty_sixty_day_year_sign_and_challenge_gates",
    "two_x_cost_and_reference_capital_capacity_gates",
)


@dataclass(frozen=True)
class Event:
    event_id: str
    security_id: str
    issuer_cik: str
    accepted_at: datetime
    available_at: datetime
    owner_ids: tuple[str, ...]
    purchase_dollars: float
    average_role_score: float
    kind: str
    score: float = 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ContractValidationError("{} is required".format(field))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError("{} must be an ISO timestamp".format(field)) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractValidationError("{} must be timezone-aware".format(field))
    return parsed.astimezone(timezone.utc)


def _truth(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("{} must be numeric".format(field)) from exc
    if not math.isfinite(result):
        raise ContractValidationError("{} must be finite".format(field))
    return result


def _assert_ready_packet(packet: Mapping[str, Any]) -> None:
    """Validate all provenance before any evaluator input is opened."""

    if packet.get("data_gate_status") != "READY_FOR_FROZEN_EVALUATOR":
        raise ContractValidationError("HYP-2026-003 requires a ready certified data gate")
    if packet.get("hypothesis_id") != HYPOTHESIS_ID:
        raise ContractValidationError("input packet is not for HYP-2026-003")
    root = Path(str(packet.get("repo_root") or "")).expanduser().resolve()
    if not root.is_dir():
        raise ContractValidationError("repo_root must be an existing directory")
    assets = packet.get("assets")
    if not isinstance(assets, Mapping):
        raise ContractValidationError("certified assets are required")
    missing = [asset_id for asset_id in REQUIRED_ASSETS if asset_id not in assets]
    if missing:
        raise ContractValidationError(
            "missing frozen data contracts: {}".format(",".join(missing))
        )
    for asset_id in REQUIRED_ASSETS:
        asset = assets[asset_id]
        if not isinstance(asset, Mapping):
            raise ContractValidationError("invalid certified asset: {}".format(asset_id))
        gate = asset.get("gate")
        if not isinstance(gate, Mapping) or gate.get("ready") is not True:
            raise ContractValidationError("asset is not certified ready: {}".format(asset_id))
        gate_hash = asset.get("gate_hash")
        if gate_hash != canonical_hash(gate):
            raise ContractValidationError("asset gate_hash mismatch: {}".format(asset_id))
        if asset.get("prechallenge_extract") is not True:
            raise ContractValidationError(
                "asset is not a separately certified pre-challenge extract: {}".format(asset_id)
            )
        maximum_date = str(asset.get("maximum_observation_date") or "")
        try:
            parsed_maximum_date = date.fromisoformat(maximum_date)
        except ValueError as exc:
            raise ContractValidationError(
                "asset maximum_observation_date is invalid: {}".format(asset_id)
            ) from exc
        if parsed_maximum_date >= date.fromisoformat(CHALLENGE_START):
            raise ContractValidationError(
                "asset may expose the locked challenge period: {}".format(asset_id)
            )
        files = asset.get("files")
        if not isinstance(files, list) or not files:
            raise ContractValidationError("certified asset has no files: {}".format(asset_id))
        for record in files:
            if not isinstance(record, Mapping):
                raise ContractValidationError("invalid file record: {}".format(asset_id))
            relative = record.get("path")
            expected = record.get("sha256")
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise ContractValidationError("asset file requires path and sha256")
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ContractValidationError("asset path escapes repo_root") from exc
            if not path.is_file() or _sha256(path) != expected:
                raise ContractValidationError(
                    "asset file does not match certified SHA-256: {}".format(relative)
                )
    form4 = assets["form4_event_tape_v1"]
    if form4.get("causal_amendment_lineage_certified") is not True:
        raise ContractValidationError(
            "Form 4 amendment lineage must be causally certified; issuer exclusion "
            "using amendments observed after an event is not point-in-time safe"
        )
    if form4.get("beneficial_owner_independence_certified") is not True:
        raise ContractValidationError(
            "Form 4 beneficial-owner independence must be causally certified; "
            "reporting-owner CIK alone cannot prove independent conviction"
        )


def _asset_paths(packet: Mapping[str, Any], asset_id: str) -> list[Path]:
    root = Path(str(packet["repo_root"])).expanduser().resolve()
    return sorted((root / item["path"]).resolve() for item in packet["assets"][asset_id]["files"])


def _records(packet: Mapping[str, Any], asset_id: str) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    for path in _asset_paths(packet, asset_id):
        name = path.name.lower()
        if name.endswith((".jsonl.gz", ".ndjson.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                records.extend(json.loads(line) for line in stream if line.strip())
        elif name.endswith((".jsonl", ".ndjson")):
            with path.open("r", encoding="utf-8") as stream:
                records.extend(json.loads(line) for line in stream if line.strip())
        elif name.endswith(".csv"):
            with path.open("r", encoding="utf-8", newline="") as stream:
                records.extend(dict(row) for row in csv.DictReader(stream))
        elif name.endswith((".parquet", ".pq")):
            try:
                import pyarrow.parquet as parquet
            except ImportError as exc:
                raise ContractValidationError(
                    "pyarrow is required for certified parquet evaluator inputs"
                ) from exc
            records.extend(parquet.read_table(path).to_pylist())
        else:
            raise ContractValidationError("unsupported evaluator input: {}".format(path.name))
    if not all(isinstance(row, dict) for row in records):
        raise ContractValidationError("asset rows must be objects: {}".format(asset_id))
    return records


def _role_score(row: Mapping[str, Any]) -> float:
    classification = str(row.get("frozen_role_classification") or "")
    scores = {
        "CEO_CFO": 1.0,
        "OTHER_NAMED_EXECUTIVE_OFFICER": 0.75,
        "OTHER_OFFICER": 0.50,
        "DIRECTOR_OR_10_PERCENT_OWNER": 0.25,
    }
    if classification not in scores:
        raise ContractValidationError(
            "frozen_role_classification is required; officer-title heuristics are not permitted"
        )
    return scores[classification]


def _eligible_purchase(row: Mapping[str, Any]) -> bool:
    amendment = str(row.get("amendment_lineage") or "")
    return (
        str(row.get("transaction_code") or "").upper() == "P"
        and str(row.get("acquired_disposed_code") or "").upper() == "A"
        and not _truth(row.get("is_derivative"))
        and _truth(row.get("is_natural_person"))
        and any(
            _truth(row.get(field))
            for field in ("is_director", "is_officer", "is_ten_percent_owner")
        )
        and str(row.get("parse_status") or "").startswith("PASS")
        and amendment in {"ORIGINAL", "ORIGINAL_CAUSALLY_SUPERSESSION_RESOLVED"}
        and _number(row.get("transaction_shares"), "transaction_shares") > 0
        and _number(row.get("transaction_price"), "transaction_price") > 0
        and bool(str(row.get("security_id") or "").strip())
        and bool(str(row.get("issuer_cik") or "").strip())
        and bool(str(row.get("owner_cik") or "").strip())
    )


def build_events(rows: Iterable[Mapping[str, Any]]) -> tuple[list[Event], list[Event]]:
    """Construct causal candidate events without inspecting subsequent filings."""

    grouped: Dict[tuple[str, str, str, datetime], Dict[str, Any]] = {}
    for row in rows:
        if not _eligible_purchase(row):
            continue
        accepted = _utc(row.get("acceptance_datetime_utc"), "acceptance_datetime_utc")
        available = _utc(row.get("available_at"), "available_at")
        if available < accepted:
            raise ContractValidationError("available_at precedes SEC acceptance")
        owner = str(row["owner_cik"]).zfill(10)
        independent_person_id = str(row.get("independent_person_id") or "").strip()
        if not independent_person_id:
            raise ContractValidationError(
                "independent_person_id is required from the certified control-identity map"
            )
        key = (
            str(row["issuer_cik"]),
            independent_person_id,
            str(row.get("accession_number") or ""),
            accepted,
        )
        aggregate = grouped.setdefault(
            key,
            {
                "row": row,
                "accepted": accepted,
                "available": available,
                "dollars": 0.0,
                "reporting_owner_ciks": set(),
            },
        )
        aggregate["dollars"] += _number(row.get("transaction_value"), "transaction_value")
        aggregate["available"] = max(aggregate["available"], available)
        aggregate["reporting_owner_ciks"].add(owner)

    purchases = sorted(
        grouped.items(), key=lambda item: (item[0][0], item[1]["accepted"], item[0][1], item[0][2])
    )
    singles: list[Event] = []
    clusters: list[Event] = []
    prior_by_issuer: Dict[str, list[tuple[tuple[str, str, str, datetime], Dict[str, Any]]]] = {}
    batch_start = 0
    while batch_start < len(purchases):
        first_key, first_item = purchases[batch_start]
        issuer = first_key[0]
        accepted = first_item["accepted"]
        batch_end = batch_start + 1
        while batch_end < len(purchases):
            next_key, next_item = purchases[batch_end]
            if next_key[0] != issuer or next_item["accepted"] != accepted:
                break
            batch_end += 1
        batch = purchases[batch_start:batch_end]
        prior_history = [
            prior
            for prior in prior_by_issuer.get(issuer, [])
            if 0 <= (accepted - prior[1]["accepted"]).days <= 10
        ]
        history = prior_history + batch
        by_owner: Dict[
            str, list[tuple[tuple[str, str, str, datetime], Dict[str, Any]]]
        ] = {}
        for prior in history:
            by_owner.setdefault(prior[0][1], []).append(prior)
        owners = tuple(sorted(by_owner))
        prior_owners = {prior[0][1] for prior in prior_history}
        completes_cluster = len(prior_owners) < 2 and len(owners) >= 2
        if len(owners) < 2:
            owner = owners[0]
            security_ids = {
                str(purchase[1]["row"]["security_id"]) for purchase in batch
            }
            if len(security_ids) != 1:
                raise ContractValidationError(
                    "one issuer single batch resolves to multiple security_ids"
                )
            singles.append(
                Event(
                    event_id="single:{}:{}:{}".format(
                        issuer, accepted.isoformat(), owner
                    ),
                    security_id=next(iter(security_ids)),
                    issuer_cik=issuer,
                    accepted_at=accepted,
                    available_at=max(purchase[1]["available"] for purchase in batch),
                    owner_ids=(owner,),
                    purchase_dollars=sum(purchase[1]["dollars"] for purchase in batch),
                    average_role_score=max(
                        _role_score(purchase[1]["row"]) for purchase in batch
                    ),
                    kind="SINGLE",
                )
            )
        if completes_cluster:
            security_ids = {
                str(purchase[1]["row"]["security_id"]) for purchase in history
            }
            if len(security_ids) != 1:
                raise ContractValidationError(
                    "one issuer cluster resolves to multiple security_ids"
                )
            purchase_dollars = sum(
                purchase[1]["dollars"]
                for item_owner in owners
                for purchase in by_owner[item_owner]
            )
            owner_role_scores = [
                max(_role_score(purchase[1]["row"]) for purchase in by_owner[item_owner])
                for item_owner in owners
            ]
            clusters.append(
                Event(
                    event_id="cluster:{}:{}:{}".format(
                        issuer, accepted.isoformat(), ",".join(owners)
                    ),
                    security_id=next(iter(security_ids)),
                    issuer_cik=issuer,
                    accepted_at=accepted,
                    available_at=max(purchase[1]["available"] for purchase in batch),
                    owner_ids=owners,
                    purchase_dollars=purchase_dollars,
                    average_role_score=sum(owner_role_scores) / len(owner_role_scores),
                    kind="CLUSTER",
                )
            )
        prior_by_issuer.setdefault(issuer, []).extend(batch)
        batch_start = batch_end
    return clusters, singles


def _percentile(value: float, history: Sequence[float]) -> float:
    if not history:
        return 0.5
    below = sum(item < value for item in history)
    equal = sum(item == value for item in history)
    return (below + 0.5 * equal) / len(history)


def score_clusters(events: Sequence[Event], market_caps: Mapping[str, float]) -> list[Event]:
    """Apply expanding PIT ranks so no future cluster enters an earlier score."""

    buyer_history: list[float] = []
    value_history: list[float] = []
    result = []
    for event in sorted(events, key=lambda item: (item.available_at, item.event_id)):
        market_cap = market_caps.get(event.event_id)
        if market_cap is None or market_cap <= 0:
            continue
        buyers = float(min(len(event.owner_ids), 4))
        purchase_ratio = event.purchase_dollars / market_cap
        score = (
            0.50 * _percentile(buyers, buyer_history)
            + 0.30 * _percentile(purchase_ratio, value_history)
            + 0.20 * event.average_role_score
        )
        result.append(Event(**{**event.__dict__, "score": score}))
        buyer_history.append(buyers)
        value_history.append(purchase_ratio)
    return result


def evaluate(packet: Dict[str, Any], *, phase: str = "DISCOVERY") -> Dict[str, Any]:
    """Validate the frozen boundary and report the remaining evaluator blockers."""

    if phase != "DISCOVERY":
        raise ContractValidationError(
            "HYP-2026-003 challenge access is not implemented by this evaluator"
        )
    _assert_ready_packet(packet)
    clusters, singles = build_events(_records(packet, "form4_event_tape_v1"))
    return {
        "primary_metric_name": PRIMARY_METRIC,
        "primary_metric_value": None,
        "variant_count": 1,
        "variants": [
            {
                "variant_id": "primary_two_insiders_ten_calendar_days_sixty_sessions",
                "status": "BLOCKED_EVALUATOR_OBLIGATIONS",
                "causal_cluster_candidates": len(clusters),
                "causal_single_purchase_candidates": len(singles),
                "incomplete_obligations": list(INCOMPLETE_OBLIGATIONS),
            }
        ],
        "evaluation_scope": "PRECHALLENGE_CAUSAL_EVENT_CONSTRUCTION_ONLY",
        "challenge_period_accessed": False,
        "corrected_significance_status": "NOT_RUN",
        "alpha_claim_permitted": False,
        "lifecycle_classification": "UNPROVEN",
        "orders_submitted": False,
    }
