"""Fail-closed dependency guard for morning Orion PAPER precompute."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from core.sleeve_control_plane import validate_orion_decision_lineage
from core.orion_decision_lineage import require_clean_git_sha


ORION_READINESS_SCHEMA = "caerus.orion_decision_readiness.v1"


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _repo_path(repo_root: Path, raw_path: object) -> Path:
    path = Path(str(raw_path or "").strip())
    return path if path.is_absolute() else repo_root / path


def latest_completed_xnys_session(report_date: str) -> str:
    from paper.trading_calendar import prev_trading_day

    return prev_trading_day(report_date)


def validate_orion_precompute_dependency(
    *,
    repo_root: Path,
    report_date: str,
) -> dict[str, Any]:
    """Require the successful post-close chain for the last completed session."""

    root = Path(repo_root).resolve()
    effective_date = latest_completed_xnys_session(report_date)
    marker_path = (
        root
        / "outputs"
        / "price_hydration"
        / effective_date
        / "orion_decision_ready.json"
    )
    failures: list[str] = []
    prior_lineage_binding: dict[str, Any] | None = None
    marker: Mapping[str, Any] = {}
    if not marker_path.is_file():
        failures.append("orion_dependency:readiness_marker_missing")
    else:
        try:
            marker = _read_object(marker_path)
        except Exception as exc:
            failures.append(
                f"orion_dependency:readiness_marker_invalid:{type(exc).__name__}"
            )

    if marker:
        if marker.get("schema_version") != ORION_READINESS_SCHEMA:
            failures.append("orion_dependency:readiness_schema_invalid")
        if marker.get("status") != "READY":
            failures.append("orion_dependency:not_ready")
        if str(marker.get("trade_date") or "") != effective_date or str(
            marker.get("effective_trade_date") or ""
        ) != effective_date:
            failures.append("orion_dependency:effective_trade_date_mismatch")
        try:
            generated_at = dt.datetime.fromisoformat(
                str(marker.get("generated_at_utc") or "").replace("Z", "+00:00")
            )
            if generated_at.tzinfo is None or generated_at.utcoffset() != dt.timedelta(0):
                failures.append("orion_dependency:generated_at_not_utc")
        except ValueError:
            failures.append("orion_dependency:generated_at_invalid")

        lineage = marker.get("decision_lineage")
        if not isinstance(lineage, Mapping):
            failures.append("orion_dependency:decision_lineage_missing")
        elif marker.get("decision_lineage_hash") != _canonical_hash(lineage):
            failures.append("orion_dependency:decision_lineage_hash_mismatch")

        source_ref = marker.get("source_artifact")
        source_payload: Mapping[str, Any] | None = None
        source_path: Path | None = None
        if not isinstance(source_ref, Mapping):
            failures.append("orion_dependency:source_artifact_missing")
        else:
            expected_source_raw = (
                f"outputs/shadow_candidates/{effective_date}/caerus_orion.json"
            )
            if str(source_ref.get("path") or "") != expected_source_raw:
                failures.append("orion_dependency:source_artifact_path_not_canonical")
            source_path = _repo_path(root, source_ref.get("path"))
            if not source_path.is_file():
                failures.append("orion_dependency:source_artifact_missing")
            elif _file_hash(source_path) != str(source_ref.get("sha256") or ""):
                failures.append("orion_dependency:source_artifact_hash_mismatch")
            else:
                try:
                    source_payload = _read_object(source_path)
                except Exception:
                    failures.append("orion_dependency:source_artifact_invalid")
        if source_payload is not None:
            if source_payload.get("strategy_slug") != "caerus_orion":
                failures.append("orion_dependency:source_strategy_identity_mismatch")
            if str(source_payload.get("trade_date") or "") != effective_date or str(
                source_payload.get("effective_trade_date") or ""
            ) != effective_date:
                failures.append("orion_dependency:source_trade_date_mismatch")
            if source_payload.get("decision_lineage") != lineage:
                failures.append("orion_dependency:source_lineage_mismatch")
            previous_source_payload = None
            if source_path is not None:
                from paper.trading_calendar import prev_trading_day

                previous_path = (
                    source_path.parent.parent
                    / prev_trading_day(effective_date)
                    / source_path.name
                )
                if previous_path.is_file():
                    try:
                        previous_source_payload = _read_object(previous_path)
                        previous_lineage = previous_source_payload.get(
                            "decision_lineage"
                        )
                        if isinstance(previous_lineage, Mapping):
                            prior_lineage_binding = {
                                "status": "BOUND",
                                "effective_trade_date": prev_trading_day(
                                    effective_date
                                ),
                                "source_path": str(previous_path.relative_to(root)),
                                "source_sha256": _file_hash(previous_path),
                                "decision_lineage_hash": _canonical_hash(
                                    previous_lineage
                                ),
                            }
                    except Exception:
                        failures.append("orion_dependency:previous_source_invalid")
            failures.extend(
                validate_orion_decision_lineage(
                    source_payload,
                    effective_trade_date=effective_date,
                    previous_source_payload=previous_source_payload,
                )
            )

        hydration_ref = marker.get("hydration_status")
        if not isinstance(hydration_ref, Mapping):
            failures.append("orion_dependency:hydration_status_missing")
        else:
            expected_hydration_raw = (
                f"outputs/price_hydration/{effective_date}/status.json"
            )
            if str(hydration_ref.get("path") or "") != expected_hydration_raw:
                failures.append("orion_dependency:hydration_status_path_not_canonical")
            hydration_path = _repo_path(root, hydration_ref.get("path"))
            if not hydration_path.is_file():
                failures.append("orion_dependency:hydration_status_missing")
            elif _file_hash(hydration_path) != str(hydration_ref.get("sha256") or ""):
                failures.append("orion_dependency:hydration_status_hash_mismatch")
            else:
                try:
                    hydration = _read_object(hydration_path)
                    if hydration.get("status") != "OK":
                        failures.append("orion_dependency:hydration_not_ok")
                    if str(hydration.get("as_of_date") or "") != effective_date:
                        failures.append("orion_dependency:hydration_asof_mismatch")
                    if (hydration.get("coverage_validation") or {}).get("status") != "OK":
                        failures.append("orion_dependency:coverage_not_ok")
                    if (hydration.get("shadow_refresh") or {}).get("status") != "OK":
                        failures.append("orion_dependency:shadow_refresh_not_ok")
                except Exception:
                    failures.append("orion_dependency:hydration_status_invalid")

        deployed_sha = str(marker.get("deployed_git_sha") or "")
        if len(deployed_sha) != 40 or any(
            char not in "0123456789abcdef" for char in deployed_sha
        ):
            failures.append("orion_dependency:deployed_git_sha_invalid")
        else:
            try:
                head_sha = require_clean_git_sha(root)
                if deployed_sha != head_sha:
                    failures.append("orion_dependency:repo_head_sha_mismatch")
            except ValueError:
                failures.append("orion_dependency:repo_runtime_not_clean_or_unavailable")
            deploy_state_path = root / "outputs" / "deploy_state.json"
            if deploy_state_path.is_file():
                try:
                    deploy_state_sha = str(
                        _read_object(deploy_state_path).get("deployed_sha") or ""
                    )
                    if deploy_state_sha != deployed_sha:
                        failures.append("orion_dependency:deploy_state_sha_mismatch")
                except Exception:
                    failures.append("orion_dependency:deploy_state_invalid")

    status = "READY" if not failures else "BLOCKED"
    return {
        "schema_version": "caerus.orion_precompute_dependency_guard.v1",
        "status": status,
        "report_date": report_date,
        "required_effective_trade_date": effective_date,
        "readiness_marker_path": str(marker_path),
        "decision_status": (
            "VERIFIED" if not failures else "STALE_DECISION_SUSPECTED"
        ),
        "failures": failures,
        "prior_decision_lineage": prior_lineage_binding,
        "deployed_git_sha": marker.get("deployed_git_sha") if marker else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--report-date", required=True)
    parser.add_argument("--json-output")
    args = parser.parse_args(argv)
    result = validate_orion_precompute_dependency(
        repo_root=Path(args.repo_root), report_date=args.report_date
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
