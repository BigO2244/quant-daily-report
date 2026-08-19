"""Prospective full-registry v1→v2 replay with governed Lyra universe lineage.

Every legacy blocker is preserved and only the explicit factual replay formulas
are used. A universe freeze closes prospective point-in-time provenance; it
does not manufacture liquidity, risk, or capacity evidence.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Mapping

from core.factual_legacy_decision_replay import build_factual_legacy_v1_replay_batch
from core.governed_universe_freeze import read_governed_universe_symbols, validate_governed_universe_freeze


class GenericLyraV2ProducerError(ValueError):
    pass


def _lyra_evaluation(batch: Mapping[str, Any], *, trade_date: str) -> Mapping[str, Any]:
    if batch.get("schema_version") != "caerus_all_sleeve_evaluation_v1" or batch.get("all_non_frozen_evaluated") is not True:
        raise GenericLyraV2ProducerError("evaluation batch is incomplete or unsupported")
    expected = batch.get("expected_non_frozen_sleeve_ids")
    envelopes = batch.get("envelopes")
    if not isinstance(expected, list) or not expected or not isinstance(envelopes, list):
        raise GenericLyraV2ProducerError("evaluation registry coverage is absent")
    actual = [str(row.get("sleeve_id") or "") for row in envelopes if isinstance(row, Mapping)]
    if actual != expected or len(actual) != len(set(actual)):
        raise GenericLyraV2ProducerError("evaluation does not exactly cover the registry")
    if batch.get("trade_date") != trade_date:
        raise GenericLyraV2ProducerError("evaluation trade date differs")
    rows = [row for row in envelopes if row.get("sleeve_id") == "caerus_lyra"]
    if len(rows) != 1:
        raise GenericLyraV2ProducerError("evaluation must contain exactly one Lyra envelope")
    return rows[0]


def build_generic_lyra_v2_decision_batch(
    *, legacy_decision_batch: Mapping[str, Any], evaluation_batch: Mapping[str, Any],
    universe_freeze: Mapping[str, Any], universe_path: Path | str,
    session_as_of: str, generated_at: str,
) -> dict[str, Any]:
    freeze = validate_governed_universe_freeze(
        universe_freeze, universe_path=universe_path, session_as_of=session_as_of
    )
    try:
        as_of = dt.datetime.fromisoformat(session_as_of.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GenericLyraV2ProducerError("session_as_of is invalid") from exc
    trade_date = str(legacy_decision_batch.get("trade_date") or "")
    if as_of.tzinfo is None or as_of.date().isoformat() != trade_date:
        raise GenericLyraV2ProducerError("session as-of must be within the decision trade date")
    evaluation = _lyra_evaluation(evaluation_batch, trade_date=trade_date)
    universe = evaluation.get("universe")
    if not isinstance(universe, Mapping) or universe.get("snapshot_hash") != freeze["source_sha256"]:
        raise GenericLyraV2ProducerError("evaluation universe hash differs from freeze")
    members = set(read_governed_universe_symbols(
        freeze=freeze, universe_path=universe_path, session_as_of=session_as_of
    ))
    legacy_rows = legacy_decision_batch.get("decisions")
    if not isinstance(legacy_rows, list):
        raise GenericLyraV2ProducerError("legacy decision rows are absent")
    lyra = [row for row in legacy_rows if isinstance(row, Mapping) and row.get("sleeve_id") == "caerus_lyra"]
    if len(lyra) != 1:
        raise GenericLyraV2ProducerError("legacy decision batch must contain exactly one Lyra row")
    targets = lyra[0].get("target_rows")
    if not isinstance(targets, list) or any(
        not isinstance(row, Mapping) or str(row.get("symbol") or "") not in members
        for row in targets
    ):
        raise GenericLyraV2ProducerError("Lyra target is outside frozen universe membership")
    return build_factual_legacy_v1_replay_batch(
        evaluation_batch=evaluation_batch,
        legacy_decision_batch=legacy_decision_batch,
        expected_sleeve_ids=evaluation_batch["expected_non_frozen_sleeve_ids"],
        generated_at=generated_at,
        additional_source_artifacts_by_sleeve={
            "caerus_lyra": [
                {"artifact_type": "governed_universe_freeze", "schema_version": freeze["schema_version"], "content_hash": freeze["content_hash"], "sleeve_id": "caerus_lyra"},
                {"artifact_type": "frozen_universe_bytes", "schema_version": "csv", "content_hash": freeze["source_sha256"], "sleeve_id": "caerus_lyra"},
            ]
        },
        additional_reason_codes_by_sleeve={
            "caerus_lyra": ["GOVERNED_UNIVERSE_FREEZE_PROSPECTIVE", "HISTORICAL_BLOCKERS_PRESERVED"]
        },
    )


__all__ = ["GenericLyraV2ProducerError", "build_generic_lyra_v2_decision_batch"]
