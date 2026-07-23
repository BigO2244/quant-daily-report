"""Manual research pipeline for forward options-proxy observation artifacts."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

from projects.alpha_lab.factory import ContractValidationError, canonical_hash
from projects.alpha_lab.factory.canonical import format_datetime, parse_datetime

from .boundary import build_boundary_attestation
from .config import ProxyConfig
from .evaluation import build_scoreboard, evaluate_signal
from .features import build_feature_rows, build_signal
from .market_calendar import session_for
from .source import SOURCE_LIMITATIONS, YFinanceSource
from .storage import (
    evaluation_artifacts,
    output_root,
    previous_feature_artifact,
    read_json,
    sha256_file,
    write_immutable_json,
)


Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractValidationError("collection timestamp must be timezone-aware")


def _collection_window_status(collected_at: datetime, config: ProxyConfig) -> str:
    local = collected_at.astimezone(ZoneInfo(config.decision_timezone))
    session = session_for(local.date())
    if session.close_at is None:
        return session.status
    not_before = session.decision_not_before
    assert not_before is not None
    return (
        "DECISION_TIME_ELIGIBLE"
        if local >= not_before
        else "COLLECTED_BEFORE_DECISION_TIME"
    )


def collect_snapshot(
    *,
    repo_root: Path,
    config: ProxyConfig,
    source: Optional[Any] = None,
    collected_at: Optional[datetime] = None,
    clock: Clock = _utc_now,
    sleeper: Sleeper = time.sleep,
) -> Dict[str, Any]:
    """Collect one immutable current-chain snapshot; never submit or size an order."""

    started_at = collected_at or clock()
    _require_aware(started_at)
    adapter = source or YFinanceSource()
    as_of_date = started_at.astimezone(ZoneInfo(config.decision_timezone)).date()
    chains = []
    errors = []
    attempts_by_symbol = {}
    for index, symbol in enumerate(config.symbols):
        last_error = None
        for attempt in range(1, config.maximum_symbol_attempts + 1):
            attempts_by_symbol[symbol] = attempt
            try:
                chain = adapter.collect_chain(
                    symbol=symbol,
                    as_of_date=as_of_date,
                    minimum_dte=config.minimum_dte,
                    maximum_dte=config.maximum_dte,
                )
                if not isinstance(chain, dict) or chain.get("symbol") != symbol:
                    raise ValueError("source returned a malformed symbol chain")
                chains.append(chain)
                last_error = None
                break
            except Exception as exc:  # retry transient public-source failures, then fail closed
                last_error = exc
                if attempt < config.maximum_symbol_attempts and config.retry_backoff_seconds:
                    sleeper(config.retry_backoff_seconds)
        if last_error is not None:
            errors.append(
                {
                    "symbol": symbol,
                    "attempts": attempts_by_symbol[symbol],
                    "error_type": type(last_error).__name__,
                    "message": str(last_error),
                }
            )
        if index < len(config.symbols) - 1 and config.symbol_pause_seconds:
            sleeper(config.symbol_pause_seconds)
    available_at = clock()
    _require_aware(available_at)
    if available_at < started_at:
        raise ContractValidationError("available_at cannot precede collection start")
    unsigned = {
        "schema_version": "caerus_options_proxy_snapshot_v1",
        "hypothesis_id": config.hypothesis_id,
        "classification": "PROXY_FORWARD_OBSERVATION_ONLY",
        "evidence_relationship": config.experiment_relationship,
        "alpha_claim_permitted": False,
        "trading_or_order_artifact": False,
        "as_of_date": as_of_date.isoformat(),
        "collection_started_at": format_datetime(started_at),
        "available_at": format_datetime(available_at),
        "collection_window_status": _collection_window_status(started_at, config),
        "source": config.source,
        "source_version": str(getattr(adapter, "source_version", "UNKNOWN")),
        "source_success_count": len(chains),
        "source_error_count": len(errors),
        "attempts_by_symbol": attempts_by_symbol,
        "requested_symbol_count": len(config.symbols),
        "source_errors": errors,
        "source_limitations": SOURCE_LIMITATIONS,
        "config_hash": config.config_hash,
        "chains": sorted(chains, key=lambda row: row["symbol"]),
    }
    snapshot_hash = canonical_hash(unsigned)
    snapshot_id = "{}-{}".format(
        available_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        snapshot_hash[:12],
    )
    snapshot = dict(unsigned)
    snapshot["snapshot_id"] = snapshot_id
    snapshot["snapshot_hash"] = canonical_hash(snapshot)
    root = output_root(repo_root)
    snapshot_dir = root / "snapshots" / as_of_date.isoformat() / snapshot_id
    snapshot_path = write_immutable_json(
        snapshot_dir / "snapshot.json",
        snapshot,
        repo_root=repo_root,
    )
    manifest = {
        "schema_version": "caerus_options_proxy_snapshot_manifest_v1",
        "snapshot_id": snapshot_id,
        "snapshot_path": str(snapshot_path.relative_to(Path(repo_root).resolve())),
        "snapshot_file_sha256": sha256_file(snapshot_path),
        "snapshot_hash": snapshot["snapshot_hash"],
        "config_hash": config.config_hash,
        "immutable": True,
        "alpha_claim_permitted": False,
    }
    manifest["manifest_hash"] = canonical_hash(manifest)
    manifest_path = write_immutable_json(
        snapshot_dir / "manifest.json",
        manifest,
        repo_root=repo_root,
    )
    return {
        "snapshot": snapshot,
        "snapshot_path": snapshot_path,
        "manifest_path": manifest_path,
    }


def _previous_features(repo_root: Path, as_of_date: str) -> Dict[str, Mapping[str, Any]]:
    path = previous_feature_artifact(repo_root=repo_root, as_of_date=as_of_date)
    if path is None:
        return {}
    payload = read_json(path, repo_root=repo_root)
    return {str(row["symbol"]): row for row in payload.get("rows", [])}


def build_from_snapshot(
    *,
    repo_root: Path,
    config: ProxyConfig,
    snapshot_path: Path,
) -> Dict[str, Any]:
    snapshot = read_json(snapshot_path, repo_root=repo_root)
    if snapshot.get("schema_version") != "caerus_options_proxy_snapshot_v1":
        raise ContractValidationError("unsupported options proxy snapshot schema")
    if snapshot.get("config_hash") != config.config_hash:
        raise ContractValidationError("snapshot config hash does not match current config")
    prior = _previous_features(repo_root, str(snapshot["as_of_date"]))
    rows = build_feature_rows(snapshot, config=config, previous_features=prior)
    features = {
        "schema_version": "caerus_options_proxy_features_v1",
        "hypothesis_id": config.hypothesis_id,
        "classification": "PROXY_FORWARD_OBSERVATION_ONLY",
        "alpha_claim_permitted": False,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_hash": snapshot["snapshot_hash"],
        "as_of_date": snapshot["as_of_date"],
        "available_at": snapshot["available_at"],
        "prior_feature_date_available": bool(prior),
        "rows": rows,
        "config_hash": config.config_hash,
    }
    features["features_hash"] = canonical_hash(features)
    root = output_root(repo_root)
    feature_dir = root / "features" / str(snapshot["as_of_date"]) / str(snapshot["snapshot_id"])
    feature_path = write_immutable_json(
        feature_dir / "features.json",
        features,
        repo_root=repo_root,
    )
    signal = build_signal(snapshot=snapshot, feature_rows=rows, config=config)
    signal_dir = root / "signals" / str(snapshot["as_of_date"]) / str(snapshot["snapshot_id"])
    signal_path = write_immutable_json(
        signal_dir / "signal.json",
        signal,
        repo_root=repo_root,
    )
    return {
        "features": features,
        "features_path": feature_path,
        "signal": signal,
        "signal_path": signal_path,
    }


def collect_and_build(
    *,
    repo_root: Path,
    config: ProxyConfig,
    source: Optional[Any] = None,
    collected_at: Optional[datetime] = None,
    clock: Clock = _utc_now,
    sleeper: Sleeper = time.sleep,
) -> Dict[str, Any]:
    collected = collect_snapshot(
        repo_root=repo_root,
        config=config,
        source=source,
        collected_at=collected_at,
        clock=clock,
        sleeper=sleeper,
    )
    built = build_from_snapshot(
        repo_root=repo_root,
        config=config,
        snapshot_path=collected["snapshot_path"],
    )
    result = dict(collected)
    result.update(built)
    return result


def mature_signal(
    *,
    repo_root: Path,
    config: ProxyConfig,
    signal_path: Path,
    through_date: date,
    source: Optional[Any] = None,
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    signal = read_json(signal_path, repo_root=repo_root)
    if signal.get("config_hash") != config.config_hash:
        raise ContractValidationError("signal config hash does not match current config")
    adapter = source or YFinanceSource()
    decision_date = date.fromisoformat(str(signal["as_of_date"]))
    symbols = sorted(
        set(
            [config.benchmark_symbol]
            + [str(value) for value in signal.get("baseline_symbols", [])]
            + [str(row["symbol"]) for row in signal.get("research_targets", [])]
        )
    )
    bars_by_symbol = {
        symbol: adapter.daily_bars(
            symbol=symbol,
            start=decision_date + timedelta(days=1),
            end=through_date + timedelta(days=1),
        )
        for symbol in symbols
    }
    price_payload = {
        "schema_version": "caerus_options_proxy_price_observation_v1",
        "signal_hash": signal["signal_hash"],
        "snapshot_id": signal["snapshot_id"],
        "decision_date": decision_date.isoformat(),
        "through_date": through_date.isoformat(),
        "source": "yfinance_daily_unadjusted",
        "source_version": str(getattr(adapter, "source_version", "UNKNOWN")),
        "bars_by_symbol": bars_by_symbol,
        "used_for_signal": False,
    }
    price_payload["price_snapshot_hash"] = canonical_hash(price_payload)
    root = output_root(repo_root)
    evaluation_dir = (
        root
        / "evaluations"
        / decision_date.isoformat()
        / str(signal["snapshot_id"])
        / through_date.isoformat()
    )
    price_path = write_immutable_json(
        evaluation_dir / "prices.json",
        price_payload,
        repo_root=repo_root,
    )
    evaluation = evaluate_signal(
        signal=signal,
        bars_by_symbol=bars_by_symbol,
        config=config,
    )
    evaluation["price_snapshot_hash"] = price_payload["price_snapshot_hash"]
    evaluation["evaluation_hash"] = canonical_hash(
        {key: value for key, value in evaluation.items() if key != "evaluation_hash"}
    )
    evaluation_path = write_immutable_json(
        evaluation_dir / "evaluation.json",
        evaluation,
        repo_root=repo_root,
    )
    evaluations = [
        read_json(path, repo_root=repo_root) for path in evaluation_artifacts(repo_root)
    ]
    scoreboard = build_scoreboard(evaluations)
    timestamp = generated_at or _utc_now()
    _require_aware(timestamp)
    scoreboard_id = "{}-{}".format(
        timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        scoreboard["scoreboard_hash"][:12],
    )
    scoreboard_path = write_immutable_json(
        root / "scoreboards" / scoreboard_id / "scoreboard.json",
        scoreboard,
        repo_root=repo_root,
    )
    return {
        "price_path": price_path,
        "evaluation_path": evaluation_path,
        "scoreboard_path": scoreboard_path,
        "evaluation": evaluation,
        "scoreboard": scoreboard,
    }


def write_boundary_attestation(*, repo_root: Path) -> Dict[str, Any]:
    package_root = Path(__file__).resolve().parent
    attestation = build_boundary_attestation(package_root)
    root = output_root(repo_root)
    path = write_immutable_json(
        root / "attestations" / attestation["attestation_hash"] / "boundary.json",
        attestation,
        repo_root=repo_root,
    )
    return {"attestation": attestation, "path": path}
