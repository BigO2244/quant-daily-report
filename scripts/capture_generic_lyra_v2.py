#!/usr/bin/env python3
"""Capture a prospective governed Lyra v2 decision from explicit files only.

The command is advisory and no-write by default.  It never reads runtime
configuration, credentials, broker state, or a deployment registry, and it
cannot submit an order or enable a schedule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.generic_lyra_v2_producer import (  # noqa: E402
    build_generic_lyra_v2_decision_batch,
    generic_lyra_v2_readiness_path,
    validate_generic_lyra_v2_capture_result,
)
from core.governed_universe_freeze import read_governed_universe_symbols  # noqa: E402
from core.lyra_governed_evidence import (  # noqa: E402
    build_lyra_market_data_snapshot,
    normalized_target_rows,
)
from core.sleeve_decision import canonical_json  # noqa: E402
from core.lyra_target_selection import build_lyra_target_selection_evidence  # noqa: E402
from core.generic_lyra_v2_raw_sources import (  # noqa: E402
    GENERIC_LYRA_RAW_RECOMPUTE_SCHEMA,
    validate_generic_lyra_v2_raw_source_recompute,
)


class GenericLyraV2CaptureCliError(ValueError):
    """Raised when an explicit capture input cannot be safely consumed."""


def _reject_constant(value: str) -> None:
    raise GenericLyraV2CaptureCliError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GenericLyraV2CaptureCliError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def read_strict_json(path: Path | str) -> dict[str, Any]:
    target = Path(path)
    try:
        value = json.loads(
            target.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GenericLyraV2CaptureCliError(f"cannot read strict JSON input: {target}") from exc
    if not isinstance(value, dict):
        raise GenericLyraV2CaptureCliError(f"JSON input must be an object: {target}")
    return value


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_price_panel_rows(
    path: Path | str, *, symbols: Sequence[str], data_as_of: str,
) -> list[dict[str, Any]]:
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - production dependency check
        raise GenericLyraV2CaptureCliError("pandas is required to read the price panel") from exc
    try:
        frame = pd.read_parquet(path, columns=["date", "ticker", "close", "volume"])
    except Exception as exc:  # pragma: no cover - backend errors vary
        raise GenericLyraV2CaptureCliError("price panel cannot be read with required columns") from exc
    required = {str(symbol).upper() for symbol in symbols}
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    frame["date"] = frame["date"].astype(str).str[:10]
    frame = frame[(frame["ticker"].isin(required)) & (frame["date"] <= data_as_of)]
    rows: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        close = float(row["close"])
        volume = float(row["volume"])
        if not math.isfinite(close) or not math.isfinite(volume):
            raise GenericLyraV2CaptureCliError("price panel contains non-finite close/volume")
        rows.append({
            "date": str(row["date"]), "ticker": str(row["ticker"]),
            "close": close, "volume": volume,
        })
    return rows


def _persist_exact(path: Path, payload: Mapping[str, Any]) -> str:
    encoded = (canonical_json(payload) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise GenericLyraV2CaptureCliError(f"immutable artifact already differs: {path}")
        return str(path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return str(path)


def capture_from_explicit_paths(
    *, execution_session: str, signal_as_of: str, session_as_of: str,
    captured_at: str, source_session_manifest_path: Path | str,
    evaluation_batch_path: Path | str, legacy_decision_batch_path: Path | str,
    lyra_source_path: Path | str, prior_lyra_source_path: Path | str,
    universe_freeze_path: Path | str, universe_path: Path | str,
    forecast_risk_policy_path: Path | str,
    forecast_risk_policy_proposal_path: Path | str,
    forecast_risk_policy_owner_decision_path: Path | str,
    price_panel_path: Path | str,
    output_root: Path | str,
    write_advisory_artifacts: bool = False,
    price_row_loader: Callable[..., list[dict[str, Any]]] = load_price_panel_rows,
) -> dict[str, Any]:
    """Build, validate, and optionally persist immutable advisory artifacts."""

    if type(write_advisory_artifacts) is not bool:
        raise GenericLyraV2CaptureCliError("write_advisory_artifacts must be a literal boolean")
    source_session = read_strict_json(source_session_manifest_path)
    evaluation = read_strict_json(evaluation_batch_path)
    legacy_decisions = read_strict_json(legacy_decision_batch_path)
    lyra_source = read_strict_json(lyra_source_path)
    prior_source = read_strict_json(prior_lyra_source_path)
    freeze = read_strict_json(universe_freeze_path)
    risk_policy = read_strict_json(forecast_risk_policy_path)
    risk_policy_proposal = read_strict_json(forecast_risk_policy_proposal_path)
    risk_policy_owner_decision = read_strict_json(
        forecast_risk_policy_owner_decision_path
    )
    if source_session.get("trade_date") != execution_session:
        raise GenericLyraV2CaptureCliError("source session differs from execution_session")
    if lyra_source.get("effective_trade_date") != signal_as_of:
        raise GenericLyraV2CaptureCliError("Lyra source differs from signal_as_of")
    targets = normalized_target_rows([
        {"symbol": symbol, "target_weight": weight}
        for symbol, weight in (lyra_source.get("target_weights") or {}).items()
    ])
    symbols = [row["symbol"] for row in targets]
    universe_symbols = read_governed_universe_symbols(
        freeze=freeze, universe_path=universe_path, session_as_of=session_as_of,
    )
    price_rows = price_row_loader(
        price_panel_path, symbols=universe_symbols, data_as_of=signal_as_of,
    )
    panel_hash = file_sha256(price_panel_path)
    selection = build_lyra_target_selection_evidence(
        execution_session=execution_session, signal_as_of=signal_as_of,
        captured_at=captured_at,
        source_path="outputs/research/flow_detection_v1/price_panel.parquet",
        source_sha256=panel_hash, universe_freeze_hash=freeze["content_hash"],
        universe_source_hash=freeze["source_sha256"],
        frozen_universe_symbols=universe_symbols, price_rows=price_rows,
    )
    market = build_lyra_market_data_snapshot(
        trade_date=execution_session, data_as_of=signal_as_of,
        captured_at=captured_at,
        source_path="outputs/research/flow_detection_v1/price_panel.parquet",
        source_sha256=panel_hash, required_symbols=symbols,
        price_rows=price_rows,
    )
    result = build_generic_lyra_v2_decision_batch(
        source_session_manifest=source_session,
        evaluation_batch=evaluation,
        evaluation_file_hash=file_sha256(evaluation_batch_path),
        legacy_decision_batch=legacy_decisions,
        legacy_decision_file_hash=file_sha256(legacy_decision_batch_path),
        lyra_source=lyra_source, lyra_source_hash=file_sha256(lyra_source_path),
        prior_lyra_source=prior_source,
        prior_lyra_source_hash=file_sha256(prior_lyra_source_path),
        universe_freeze=freeze, universe_path=universe_path,
        market_data_snapshot=market, target_selection_evidence=selection,
        forecast_risk_policy=risk_policy, session_as_of=session_as_of,
        forecast_risk_policy_proposal=risk_policy_proposal,
        forecast_risk_policy_owner_decision=risk_policy_owner_decision,
        generated_at=captured_at,
    )
    persisted_paths: list[str] = []
    if write_advisory_artifacts:
        root = Path(output_root)
        artifacts = {
            "market-data": result["market_data_snapshot"],
            "target-selection": result["target_selection_evidence"],
            "risk-policy": result["forecast_risk_policy"],
            "risk-policy-proposal": result["forecast_risk_policy_proposal"],
            "risk-policy-owner-decision": (
                result["forecast_risk_policy_owner_decision"]
            ),
            "session": result["session_snapshot"],
            "risk": result["forecast_risk"],
            "liquidity": result["liquidity"],
            "capacity": result["capacity"],
            "decision": result["decision"],
            "capture": result,
        }
        for label, artifact in artifacts.items():
            path = root / execution_session / f"{label}-{artifact['content_hash']}.json"
            persisted_paths.append(_persist_exact(path, artifact))
        persisted_paths.append(_persist_exact(
            generic_lyra_v2_readiness_path(
                output_root=root, readiness=result["readiness"]
            ),
            result["readiness"],
        ))
    return {
        "capture_result": result,
        "write_advisory_artifacts": write_advisory_artifacts,
        "persisted_paths": sorted(persisted_paths),
        "broker_call_performed": False,
        "broker_write_performed": False,
        "submission_allowed": False,
        "execution_authority": False,
        "activation_authority": False,
    }


def recompute_capture_from_explicit_paths(
    *, expected_capture: Mapping[str, Any],
    execution_session: str, signal_as_of: str, session_as_of: str,
    captured_at: str, source_session_manifest_path: Path | str,
    evaluation_batch_path: Path | str, legacy_decision_batch_path: Path | str,
    lyra_source_path: Path | str, prior_lyra_source_path: Path | str,
    universe_freeze_path: Path | str, universe_path: Path | str,
    forecast_risk_policy_path: Path | str,
    forecast_risk_policy_proposal_path: Path | str,
    forecast_risk_policy_owner_decision_path: Path | str,
    price_panel_path: Path | str,
    price_row_loader: Callable[..., list[dict[str, Any]]] = load_price_panel_rows,
) -> dict[str, Any]:
    """Rehash raw protected inputs and reproduce the capture byte-for-byte."""

    expected = validate_generic_lyra_v2_capture_result(expected_capture)
    result = capture_from_explicit_paths(
        execution_session=execution_session, signal_as_of=signal_as_of,
        session_as_of=session_as_of, captured_at=captured_at,
        source_session_manifest_path=source_session_manifest_path,
        evaluation_batch_path=evaluation_batch_path,
        legacy_decision_batch_path=legacy_decision_batch_path,
        lyra_source_path=lyra_source_path,
        prior_lyra_source_path=prior_lyra_source_path,
        universe_freeze_path=universe_freeze_path, universe_path=universe_path,
        forecast_risk_policy_path=forecast_risk_policy_path,
        forecast_risk_policy_proposal_path=forecast_risk_policy_proposal_path,
        forecast_risk_policy_owner_decision_path=(
            forecast_risk_policy_owner_decision_path
        ),
        price_panel_path=price_panel_path, output_root=Path("."),
        write_advisory_artifacts=False, price_row_loader=price_row_loader,
    )
    recomputed = result["capture_result"]
    if recomputed != expected:
        raise GenericLyraV2CaptureCliError(
            "raw protected sources do not reproduce the sealed Lyra capture"
        )
    source_paths = {
        "source_session_manifest": source_session_manifest_path,
        "evaluation_batch": evaluation_batch_path,
        "legacy_decision_batch": legacy_decision_batch_path,
        "lyra_source": lyra_source_path,
        "prior_lyra_source": prior_lyra_source_path,
        "universe_freeze": universe_freeze_path,
        "universe_bytes": universe_path,
        "forecast_risk_policy": forecast_risk_policy_path,
        "forecast_risk_policy_proposal": forecast_risk_policy_proposal_path,
        "forecast_risk_policy_owner_decision": (
            forecast_risk_policy_owner_decision_path
        ),
        "price_panel": price_panel_path,
    }
    body = {
        "schema_version": GENERIC_LYRA_RAW_RECOMPUTE_SCHEMA,
        "status": "PASS_NO_WRITE",
        "execution_session": execution_session,
        "expected_capture_hash": expected["content_hash"],
        "recomputed_capture_hash": recomputed["content_hash"],
        "source_files": [
            {
                "name": name,
                "path": str(Path(path).resolve()),
                "sha256": file_sha256(path),
            }
            for name, path in sorted(source_paths.items())
        ],
        "write_enabled": False, "broker_call_performed": False,
        "broker_write_performed": False, "submission_allowed": False,
        "execution_authority": False, "activation_authority": False,
    }
    body["content_hash"] = hashlib.sha256(
        canonical_json(body).encode("utf-8")
    ).hexdigest()
    return validate_generic_lyra_v2_raw_source_recompute(
        body, expected_capture=expected
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-session", required=True)
    parser.add_argument("--signal-as-of", required=True)
    parser.add_argument("--session-as-of", required=True)
    parser.add_argument("--captured-at", required=True)
    parser.add_argument("--source-session-manifest", required=True)
    parser.add_argument("--evaluation-batch", required=True)
    parser.add_argument("--legacy-decision-batch", required=True)
    parser.add_argument("--lyra-source", required=True)
    parser.add_argument("--prior-lyra-source", required=True)
    parser.add_argument("--universe-freeze", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--forecast-risk-policy", required=True)
    parser.add_argument("--forecast-risk-policy-proposal", required=True)
    parser.add_argument("--forecast-risk-policy-owner-decision", required=True)
    parser.add_argument("--price-panel", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--write-advisory-artifacts", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = capture_from_explicit_paths(
            execution_session=args.execution_session,
            signal_as_of=args.signal_as_of,
            session_as_of=args.session_as_of, captured_at=args.captured_at,
            source_session_manifest_path=args.source_session_manifest,
            evaluation_batch_path=args.evaluation_batch,
            legacy_decision_batch_path=args.legacy_decision_batch,
            lyra_source_path=args.lyra_source,
            prior_lyra_source_path=args.prior_lyra_source,
            universe_freeze_path=args.universe_freeze,
            universe_path=args.universe,
            forecast_risk_policy_path=args.forecast_risk_policy,
            forecast_risk_policy_proposal_path=args.forecast_risk_policy_proposal,
            forecast_risk_policy_owner_decision_path=(
                args.forecast_risk_policy_owner_decision
            ),
            price_panel_path=args.price_panel,
            output_root=args.output_root,
            write_advisory_artifacts=args.write_advisory_artifacts,
        )
    except Exception as exc:
        print(json.dumps({
            "status": "BLOCKED_NO_SUBMIT",
            "blocker": type(exc).__name__,
            "submission_allowed": False,
            "broker_write_performed": False,
        }, sort_keys=True))
        return 2
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
