from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


LINEAGE_SCHEMA = "caerus.orion_decision_lineage.v1"
READINESS_SCHEMA = "caerus.orion_decision_readiness.v1"


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_frame_records(frame: pd.DataFrame, *, columns: list[str]) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    available = [column for column in columns if column in frame.columns]
    normalized = frame[available].copy()
    if "date" in normalized.columns:
        normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    sort_columns = [column for column in ("date", "ticker", "momentum_rank") if column in normalized.columns]
    if sort_columns:
        normalized = normalized.sort_values(sort_columns, kind="mergesort")
    return [
        {column: _json_value(value) for column, value in row.items()}
        for row in normalized.to_dict(orient="records")
    ]


def build_decision_lineage(
    *,
    panel: pd.DataFrame,
    signals: pd.DataFrame,
    snapshot: Mapping[str, Any],
    model_version: str,
    source_variant: str,
    generated_at_utc: str,
    coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    effective_date = str(snapshot.get("effective_trade_date") or "")
    panel_columns = ["date", "ticker", "open", "high", "low", "close", "volume", "sector"]
    feature_columns = [
        "date", "ticker", "r1", "r3", "r6_1", "r12_1", "momentum_score",
        "momentum_rank", "momentum_rank_pct", "signal_ready", "r3_rank_pct",
        "mean_reversion_signal", "large_up_1d", "post_move_drift_signal", "spy_above_200dma",
    ]
    normalized_panel = canonical_frame_records(panel, columns=panel_columns)
    current_market = [row for row in normalized_panel if row.get("date") == effective_date]
    feature_records = canonical_frame_records(signals, columns=feature_columns)
    full_rank_records = canonical_frame_records(
        signals[signals.get("signal_ready", False).astype(bool)] if "signal_ready" in signals else pd.DataFrame(),
        columns=["date", "ticker", "momentum_score", "momentum_rank"],
    )
    rank_table = snapshot.get("rank_table")
    current_rank_records = canonical_frame_records(
        rank_table if isinstance(rank_table, pd.DataFrame) else pd.DataFrame(rank_table or []),
        columns=["ticker", "momentum_score", "momentum_rank", "is_selected"],
    )
    weights = snapshot.get("weights")
    if isinstance(weights, pd.Series):
        target_weights = {str(key): round(float(value), 6) for key, value in weights[weights > 0].items()}
    else:
        target_weights = {
            str(key): round(float(value), 6)
            for key, value in dict(weights or {}).items()
            if float(value) > 0
        }

    market_data_hash = canonical_hash(current_market)
    normalized_panel_hash = canonical_hash(normalized_panel)
    feature_hash = canonical_hash(feature_records)
    full_rank_hash = canonical_hash(full_rank_records)
    rank_table_hash = canonical_hash(current_rank_records)
    target_weights_hash = canonical_hash(dict(sorted(target_weights.items())))
    max_market_timestamp = max(
        (str(row.get("date")) for row in normalized_panel if row.get("date")),
        default=None,
    )
    stage_diagnostics = {
        "market_data": _stage_diagnostic(
            stage="market_data",
            source_identity="completed_session_ohlcv_rows",
            records=current_market,
            max_market_timestamp=max_market_timestamp,
        ),
        "normalized_panel": _stage_diagnostic(
            stage="normalized_panel",
            source_identity="research.flow_detection.data.standardize_panel",
            records=normalized_panel,
            max_market_timestamp=max_market_timestamp,
        ),
        "features": _stage_diagnostic(
            stage="features",
            source_identity="research.alpha_lab_v1.signals.build_alpha_lab_signal_frame",
            records=feature_records,
            max_market_timestamp=max_market_timestamp,
        ),
        "full_rank_history": _stage_diagnostic(
            stage="full_rank_history",
            source_identity="alpha_lab_v1.momentum_rank",
            records=full_rank_records,
            max_market_timestamp=max_market_timestamp,
        ),
        "current_rank_table": _stage_diagnostic(
            stage="current_rank_table",
            source_identity="research.alpha_lab_v2.engine.build_target_snapshot",
            records=current_rank_records,
            max_market_timestamp=max_market_timestamp,
        ),
        "target_weights": {
            "stage": "target_weights",
            "source_identity": "research.alpha_lab_v2.engine.build_target_snapshot",
            "row_count": len(target_weights),
            "symbol_count": len(target_weights),
            "max_market_timestamp": max_market_timestamp,
        },
    }
    return {
        "schema_version": LINEAGE_SCHEMA,
        "trade_date": str(snapshot.get("trade_date") or effective_date),
        "market_data_asof": effective_date,
        "market_data_hash": market_data_hash,
        "normalized_panel_hash": normalized_panel_hash,
        "feature_hash": feature_hash,
        "rank_table_hash": rank_table_hash,
        "target_weights_hash": target_weights_hash,
        "generated_at_utc": generated_at_utc,
        "model_version": str(model_version),
        "source_variant": str(source_variant),
        "effective_trade_date": effective_date,
        "parent_artifact_hashes": {
            "normalized_panel": market_data_hash,
            "features": normalized_panel_hash,
            "full_rank_history": feature_hash,
            "current_rank_table": full_rank_hash,
            "target_weights": rank_table_hash,
        },
        "coverage": _json_value(dict(coverage or {})),
        "selection_trace": _json_value(list(snapshot.get("selection_trace") or [])),
        "full_rank_history_hash": full_rank_hash,
        "stage_diagnostics": stage_diagnostics,
    }


def build_readiness_payload(
    *,
    trade_date: str,
    source_artifact_path: Path,
    decision_lineage: Mapping[str, Any],
    hydration_status_path: Path,
    generated_at_utc: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    effective_date = str(decision_lineage.get("effective_trade_date") or "")
    deployed_git_sha = require_clean_git_sha(repo_root)
    assert repo_root is not None
    source_path = _canonical_repo_relative_path(source_artifact_path, repo_root)
    hydration_path = _canonical_repo_relative_path(hydration_status_path, repo_root)
    return {
        "schema_version": READINESS_SCHEMA,
        "status": "READY",
        "trade_date": str(trade_date),
        "effective_trade_date": effective_date,
        "generated_at_utc": generated_at_utc or utc_now_iso(),
        "source_artifact": {"path": source_path, "sha256": file_sha256(source_artifact_path)},
        "decision_lineage": _json_value(dict(decision_lineage)),
        "decision_lineage_hash": canonical_hash(dict(decision_lineage)),
        "hydration_status": {"path": hydration_path, "sha256": file_sha256(hydration_status_path)},
        "deployed_git_sha": deployed_git_sha,
    }


def require_clean_git_sha(repo_root: Path | None) -> str:
    if repo_root is None:
        raise ValueError("repo_root is required for Git deployment attestation")
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Git deployment attestation unavailable") from exc
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise ValueError("Git HEAD is not a valid full SHA")
    if dirty:
        raise ValueError("Git runtime state is dirty; refusing READY attestation")
    return sha


def _canonical_repo_relative_path(path: Path, repo_root: Path) -> str:
    root = Path(repo_root).resolve()
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"readiness artifact is outside repository root: {resolved}") from exc
    return relative.as_posix()


def _stage_diagnostic(
    *,
    stage: str,
    source_identity: str,
    records: list[dict[str, Any]],
    max_market_timestamp: str | None,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "source_identity": source_identity,
        "row_count": len(records),
        "symbol_count": len({str(row["ticker"]) for row in records if row.get("ticker")}),
        "max_market_timestamp": max_market_timestamp,
    }


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            return timestamp.isoformat().replace("+00:00", "Z")
        return timestamp.isoformat()
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)
