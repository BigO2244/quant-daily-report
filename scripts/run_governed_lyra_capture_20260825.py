#!/usr/bin/env python3
"""One-date advisory Lyra capture boundary for 2026-08-25.

Disabled mode exits successfully without reading inputs or writing files.
Enabled mode accepts only explicit literal paths, invokes the governed capture
builder, and can write only immutable advisory artifacts.  It has no broker,
credential, activation, submission, or deployment imports.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.capture_generic_lyra_v2 import (  # noqa: E402
    capture_from_explicit_paths,
    load_price_panel_rows,
    read_strict_json,
)


EXECUTION_SESSION = "2026-08-25"
SIGNAL_AS_OF = "2026-08-24"
EARLIEST_CAPTURE_TIME_ET = dt.time(8, 15)
CAPTURE_TIMEZONE = ZoneInfo("America/New_York")
CAPTURE_BOUNDARY_SCHEMA = "caerus.governed_lyra_capture_boundary.v1"

_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_VALUE = re.compile(r"^[A-Za-z0-9_./:+-]+$")

_PATH_KEYS = {
    "CAERUS_LYRA_CAPTURE_SOURCE_SESSION_MANIFEST",
    "CAERUS_LYRA_CAPTURE_EVALUATION_BATCH",
    "CAERUS_LYRA_CAPTURE_LEGACY_DECISION_BATCH",
    "CAERUS_LYRA_CAPTURE_CURRENT_SOURCE",
    "CAERUS_LYRA_CAPTURE_PRIOR_SOURCE",
    "CAERUS_LYRA_CAPTURE_UNIVERSE_FREEZE",
    "CAERUS_LYRA_CAPTURE_UNIVERSE",
    "CAERUS_LYRA_CAPTURE_RISK_POLICY",
    "CAERUS_LYRA_CAPTURE_RISK_POLICY_PROPOSAL",
    "CAERUS_LYRA_CAPTURE_RISK_POLICY_OWNER_DECISION",
    "CAERUS_LYRA_CAPTURE_LIVE_OWNER_DECISION",
    "CAERUS_LYRA_CAPTURE_PRICE_PANEL",
    "CAERUS_LYRA_CAPTURE_OUTPUT_ROOT",
}
_ALLOWED_KEYS = {"CAERUS_GOVERNED_LYRA_CAPTURE_ENABLED", *_PATH_KEYS}


class GovernedLyraCaptureBoundaryError(ValueError):
    """Raised when the inert capture boundary cannot run safely."""


def read_literal_config(path: Path | str) -> dict[str, str]:
    """Read command-free literal assignments without sourcing a shell file."""

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise GovernedLyraCaptureBoundaryError("capture config is unreadable") from exc
    values: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line or line.startswith("#"):
            continue
        if line != line.strip() or line.count("=") != 1:
            raise GovernedLyraCaptureBoundaryError(
                f"capture config line {line_number} is not a literal assignment"
            )
        key, value = line.split("=", 1)
        if (
            not _KEY.fullmatch(key)
            or key not in _ALLOWED_KEYS
            or not value
            or not _VALUE.fullmatch(value)
        ):
            raise GovernedLyraCaptureBoundaryError(
                f"capture config line {line_number} is not allowed"
            )
        if key in values:
            raise GovernedLyraCaptureBoundaryError(
                f"capture config contains duplicate key: {key}"
            )
        values[key] = value
    if values.get("CAERUS_GOVERNED_LYRA_CAPTURE_ENABLED") not in {"0", "1"}:
        raise GovernedLyraCaptureBoundaryError(
            "capture enabled flag must be the literal 0 or 1"
        )
    return values


def _enabled_paths(config: Mapping[str, str]) -> dict[str, Path]:
    if set(config) != _ALLOWED_KEYS:
        missing = sorted(_ALLOWED_KEYS - set(config))
        unknown = sorted(set(config) - _ALLOWED_KEYS)
        raise GovernedLyraCaptureBoundaryError(
            "enabled capture config keys differ; missing="
            + ",".join(missing)
            + "; unknown="
            + ",".join(unknown)
        )
    paths: dict[str, Path] = {}
    for key in sorted(_PATH_KEYS):
        raw = config[key]
        if "REPLACE_WITH_" in raw:
            raise GovernedLyraCaptureBoundaryError(
                "enabled capture config contains an unresolved template token"
            )
        path = Path(raw)
        if not path.is_absolute():
            raise GovernedLyraCaptureBoundaryError(
                "enabled capture paths must be absolute"
            )
        paths[key] = path
    return paths


def run_governed_lyra_capture_boundary(
    *, config: Mapping[str, str], now: dt.datetime | None = None,
    price_row_loader: Callable[..., list[dict[str, Any]]] = load_price_panel_rows,
) -> dict[str, Any]:
    """Run the date-bound boundary; disabled is a clean no-read/no-write exit."""

    if config.get("CAERUS_GOVERNED_LYRA_CAPTURE_ENABLED") == "0":
        return {
            "schema_version": CAPTURE_BOUNDARY_SCHEMA,
            "status": "DISABLED_NO_WRITE",
            "execution_session": EXECUTION_SESSION,
            "signal_as_of": SIGNAL_AS_OF,
            "input_read_performed": False,
            "write_performed": False,
            "broker_call_performed": False,
            "broker_write_performed": False,
            "submission_allowed": False,
            "activation_authority": False,
            "execution_authority": False,
        }
    if config.get("CAERUS_GOVERNED_LYRA_CAPTURE_ENABLED") != "1":
        raise GovernedLyraCaptureBoundaryError(
            "capture enabled flag must be the literal 0 or 1"
        )
    observed = now or dt.datetime.now(dt.timezone.utc)
    if observed.tzinfo is None:
        raise GovernedLyraCaptureBoundaryError("capture boundary time needs timezone")
    observed_et = observed.astimezone(CAPTURE_TIMEZONE)
    if (
        observed_et.date().isoformat() != EXECUTION_SESSION
        or observed_et.time().replace(tzinfo=None) < EARLIEST_CAPTURE_TIME_ET
    ):
        raise GovernedLyraCaptureBoundaryError(
            "capture is allowed only on 2026-08-25 at or after 08:15 ET"
        )
    paths = _enabled_paths(config)
    session_manifest = read_strict_json(
        paths["CAERUS_LYRA_CAPTURE_SOURCE_SESSION_MANIFEST"]
    )
    if session_manifest.get("trade_date") != EXECUTION_SESSION:
        raise GovernedLyraCaptureBoundaryError(
            "capture session manifest is not the exact 2026-08-25 session"
        )
    session_as_of = str(session_manifest.get("as_of") or "")
    captured_at = observed.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    result = capture_from_explicit_paths(
        execution_session=EXECUTION_SESSION,
        signal_as_of=SIGNAL_AS_OF,
        session_as_of=session_as_of,
        captured_at=captured_at,
        source_session_manifest_path=paths[
            "CAERUS_LYRA_CAPTURE_SOURCE_SESSION_MANIFEST"
        ],
        evaluation_batch_path=paths["CAERUS_LYRA_CAPTURE_EVALUATION_BATCH"],
        legacy_decision_batch_path=paths[
            "CAERUS_LYRA_CAPTURE_LEGACY_DECISION_BATCH"
        ],
        lyra_source_path=paths["CAERUS_LYRA_CAPTURE_CURRENT_SOURCE"],
        prior_lyra_source_path=paths["CAERUS_LYRA_CAPTURE_PRIOR_SOURCE"],
        universe_freeze_path=paths["CAERUS_LYRA_CAPTURE_UNIVERSE_FREEZE"],
        universe_path=paths["CAERUS_LYRA_CAPTURE_UNIVERSE"],
        forecast_risk_policy_path=paths["CAERUS_LYRA_CAPTURE_RISK_POLICY"],
        forecast_risk_policy_proposal_path=paths[
            "CAERUS_LYRA_CAPTURE_RISK_POLICY_PROPOSAL"
        ],
        forecast_risk_policy_owner_decision_path=paths[
            "CAERUS_LYRA_CAPTURE_RISK_POLICY_OWNER_DECISION"
        ],
        live_owner_decision_path=paths[
            "CAERUS_LYRA_CAPTURE_LIVE_OWNER_DECISION"
        ],
        price_panel_path=paths["CAERUS_LYRA_CAPTURE_PRICE_PANEL"],
        output_root=paths["CAERUS_LYRA_CAPTURE_OUTPUT_ROOT"],
        write_advisory_artifacts=True,
        price_row_loader=price_row_loader,
    )
    capture = result["capture_result"]
    return {
        "schema_version": CAPTURE_BOUNDARY_SCHEMA,
        "status": "CAPTURED_IMMUTABLE_ADVISORY_NO_SUBMIT",
        "execution_session": EXECUTION_SESSION,
        "signal_as_of": SIGNAL_AS_OF,
        "captured_at": captured_at,
        "capture_hash": capture["content_hash"],
        "readiness_hash": capture["readiness"]["content_hash"],
        "persisted_paths": result["persisted_paths"],
        "input_read_performed": True,
        "write_performed": True,
        "broker_call_performed": False,
        "broker_write_performed": False,
        "submission_allowed": False,
        "activation_authority": False,
        "execution_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_governed_lyra_capture_boundary(
            config=read_literal_config(args.config)
        )
    except Exception:
        print(json.dumps({
            "schema_version": CAPTURE_BOUNDARY_SCHEMA,
            "status": "BLOCKED_NO_WRITE_NO_SUBMIT",
            "broker_call_performed": False,
            "broker_write_performed": False,
            "submission_allowed": False,
            "activation_authority": False,
            "execution_authority": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
