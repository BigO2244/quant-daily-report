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


PAPER_TARGET_SCHEMA = "caerus.paper_target_package.v1"
PAPER_SIGNALS_SCHEMA = "caerus.paper_target_signals.v1"
PAPER_HANDOFF_SCHEMA = "caerus.paper_precompute_handoff.v1"
SEALED_PRECOMPUTE_SCHEMA_VERSION = 2


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
    return [
        {
            "symbol": str(row["symbol"]),
            "ticker": str(row["ticker"]),
            "sleeve": str(row["sleeve"]),
            "target_weight": float(row["target_weight"]),
        }
        for row in rows
    ]


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
    if int(existing_contract.get("schema_version") or 0) == SEALED_PRECOMPUTE_SCHEMA_VERSION:
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

    envelope = _sole_orion_envelope(sleeve_payload)
    source_ref = (envelope.get("provenance") or {}).get("source_artifacts")[0]
    source_path = _resolve_repo_path(repo_root, source_ref.get("path"))
    if not source_path.is_file():
        raise PaperTargetAuthorityError(f"Orion source artifact is missing: {source_path}")
    source_hash = _file_hash(source_path)
    if source_hash != str(source_ref.get("sha256") or ""):
        raise PaperTargetAuthorityError("Orion source artifact hash differs from sleeve evaluation")
    source_payload = _read_object(source_path)

    registry = load_strategy_registry_for_repo(repo_root)
    if registry.paper_execution_strategy_id() != "caerus_orion":
        raise PaperTargetAuthorityError("registry PAPER authority is not solely caerus_orion")
    config = registry.paper_execution_config()
    if str(config.get("approval_scope") or "").upper() != "PAPER_ONLY":
        raise PaperTargetAuthorityError("Orion approval_scope must be PAPER_ONLY")
    if bool(config.get("live_enabled")):
        raise PaperTargetAuthorityError("Orion live execution must remain disabled")
    target_cash_weight = float(config.get("target_cash_weight") or 0.0)
    policy = validate_target_attainment_policy(
        config.get("target_attainment_policy"),
        expected_target_cash_weight=target_cash_weight,
    )
    expected_variant = str(config.get("source_variant") or "").strip()
    source_variant = str(source_payload.get("source_variant") or "").strip()
    if expected_variant and source_variant != expected_variant:
        raise PaperTargetAuthorityError(
            f"Orion source variant mismatch: expected={expected_variant} actual={source_variant}"
        )
    if str(source_payload.get("strategy_slug") or "") != "caerus_orion":
        raise PaperTargetAuthorityError("Orion source strategy_slug mismatch")

    effective_date = str(
        (envelope.get("opportunity") or {}).get("effective_trade_date")
        or source_payload.get("effective_trade_date")
        or source_payload.get("trade_date")
        or ""
    )
    if not effective_date:
        raise PaperTargetAuthorityError("Orion effective trade date is missing")
    from paper.trading_calendar import is_trading_day, prev_trading_day

    if not is_trading_day(trade_date):
        raise PaperTargetAuthorityError("PAPER decision date is not an XNYS session")
    prior_date = prev_trading_day(trade_date)
    if effective_date not in {trade_date, prior_date}:
        raise PaperTargetAuthorityError(
            "Orion source is outside the current/prior XNYS session policy"
        )
    if str(source_payload.get("trade_date") or "") != effective_date:
        raise PaperTargetAuthorityError("Orion source trade_date mismatch")
    if str(source_payload.get("effective_trade_date") or effective_date) != effective_date:
        raise PaperTargetAuthorityError("Orion source effective_trade_date mismatch")
    if str(config.get("source_session_policy") or "").upper() != (
        "SAME_OR_PREVIOUS_TRADING_SESSION"
    ):
        raise PaperTargetAuthorityError("Orion source session policy is not approved")
    if int(config.get("max_source_trading_session_lag") or -1) != 1:
        raise PaperTargetAuthorityError("Orion maximum source-session lag must be one")
    lag = 0 if effective_date == trade_date else 1

    target_rows = _normalized_target_rows(
        source_payload=source_payload,
        target_cash_weight=target_cash_weight,
    )
    target_projection = _target_projection(target_rows)
    sleeve_hash = _file_hash(sleeve_path)
    source_refs = (
        _display_path(repo_root, source_path),
        f"sha256:{source_hash}",
        _display_path(repo_root, sleeve_path),
        f"sha256:{sleeve_hash}",
    )
    authority_stem = f"{trade_date}:paper:caerus_orion"
    evidence = build_evidence_package(
        package_id=f"evidence:{authority_stem}",
        trade_date=trade_date,
        source_refs=source_refs,
        observations=target_rows,
    )
    decision = build_decision_package(
        package_id=f"decision:{authority_stem}",
        trade_date=trade_date,
        evidence=evidence,
        target_rows=target_projection,
        target_cash_weight=target_cash_weight,
        source_refs=source_refs,
    )
    identity = strategy_identity_metadata(trade_date)
    identity.update(
        {
            "execution_target_source": _display_path(repo_root, source_path),
            "shadow_baseline_source": _display_path(repo_root, source_path),
            "shadow_baseline_source_sha256": source_hash,
            "shadow_baseline_source_trade_date": effective_date,
        }
    )
    identity_check = validate_lane_strategy_identity(
        identity=identity,
        approved_strategy="caerus_orion",
        lane="paper",
    )
    if identity_check.get("status") != "PASS":
        raise PaperTargetAuthorityError("sealed target strategy identity is invalid")

    target_package = {
        "schema_version": PAPER_TARGET_SCHEMA,
        "trade_date": trade_date,
        "sealed_at": sealed_at or dt.datetime.now(dt.timezone.utc).isoformat(),
        "authority": "DECISION",
        "approved_sleeve": "caerus_orion",
        "approved_target_hash": decision.content_hash,
        "effective_trade_date": effective_date,
        "source_trading_session_lag": lag,
        "source_session_policy": str(config.get("source_session_policy") or ""),
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
            "source_session_policy": str(config.get("source_session_policy") or ""),
        },
        "source_sleeve_evaluations": {
            "path": _display_path(repo_root, sleeve_path),
            "sha256": sleeve_hash,
        },
        "evidence_package": evidence.to_dict(),
        "decision_package": decision.to_dict(),
    }

    research_pair_hash = _payload_hash(
        {"signals": legacy_signals, "planned_execution_payload": legacy_handoff}
    )
    research_dir = (
        bundle_dir / "research" / "growth_engine_v4" / f"precompute-{research_pair_hash}"
    )
    research_dir.mkdir(parents=True, exist_ok=True)
    research_signals_path = research_dir / "signals.json"
    research_handoff_path = research_dir / "planned_execution_payload.json"
    research_signals_path.write_text(_pretty_json(legacy_signals), encoding="utf-8")
    research_handoff_path.write_text(_pretty_json(legacy_handoff), encoding="utf-8")

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
            "signals_path": _display_path(repo_root, research_signals_path),
            "planned_execution_payload_path": _display_path(repo_root, research_handoff_path),
        },
    }
    daily_snapshot["strategy_identity"] = identity
    daily_snapshot["signals_snapshot_path"] = _display_path(repo_root, signals_path)
    daily_snapshot["approved_target_hash"] = decision.content_hash
    daily_snapshot["paper_target_package_path"] = _display_path(repo_root, target_path)
    daily_snapshot["market_state_execution_authority"] = False
    if isinstance(daily_snapshot.get("proposed_trades"), list):
        daily_snapshot["research_proposed_trades"] = daily_snapshot.get("proposed_trades")
        daily_snapshot["proposed_trades"] = []

    snapshot_path.write_text(_pretty_json(daily_snapshot), encoding="utf-8")
    signals_path.write_text(_pretty_json(signals_payload), encoding="utf-8")
    handoff_path.write_text(_pretty_json(handoff_payload), encoding="utf-8")
    contract = dict(existing_contract)
    contract.update(
        {
            "schema_version": SEALED_PRECOMPUTE_SCHEMA_VERSION,
            "status": "complete",
            "validated_for_execution": True,
            "validation_reason": None,
            "validation_failures": [],
            "authority_model": "orion_single_sealed_target_v1",
            "approved_target_hash": decision.content_hash,
            "precompute_execution_authority": False,
            "files": {
                "daily_snapshot": "daily_snapshot.json",
                "signals": "signals.json",
                "planned_execution_payload": "planned_execution_payload.json",
                "sleeve_evaluations": "sleeve_evaluations.json",
                "paper_target_package": "paper_target_package.json",
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
        if int(contract.get("schema_version") or 0) != SEALED_PRECOMPUTE_SCHEMA_VERSION:
            return ["paper_target:unsealed_precompute_contract"]
        if contract.get("authority_model") != "orion_single_sealed_target_v1":
            failures.append("paper_target:invalid_authority_model")
        files = contract.get("files")
        hashes = contract.get("file_sha256")
        if not isinstance(files, Mapping) or not isinstance(hashes, Mapping):
            return [*failures, "paper_target:contract_file_manifest_missing"]
        required = {
            "daily_snapshot",
            "signals",
            "planned_execution_payload",
            "sleeve_evaluations",
            "paper_target_package",
        }
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
        if package.get("schema_version") != PAPER_TARGET_SCHEMA:
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
        target_rows = list(decision.target_rows)
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

        source = package.get("source_strategy_artifact") or {}
        source_path = _resolve_repo_path(repo_root, source.get("path"))
        if not source_path.is_file() or _file_hash(source_path) != str(source.get("sha256") or ""):
            failures.append("paper_target:source_strategy_hash_mismatch")
        sleeve_ref = package.get("source_sleeve_evaluations") or {}
        if str(sleeve_ref.get("sha256") or "") != _file_hash(
            bundle_dir / str(files["sleeve_evaluations"])
        ):
            failures.append("paper_target:sleeve_evaluations_hash_mismatch")
        _sole_orion_envelope(sleeve_payload)
        identity = package.get("strategy_identity")
        identity_check = validate_lane_strategy_identity(
            identity=identity if isinstance(identity, Mapping) else {},
            approved_strategy="caerus_orion",
            lane="paper",
        )
        if identity_check.get("status") != "PASS":
            failures.append("paper_target:strategy_identity_mismatch")
    except Exception as exc:
        failures.append(f"paper_target:validation_exception:{type(exc).__name__}:{exc}")
    return failures
