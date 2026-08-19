"""Strict proof that protected raw Lyra inputs reproduced a sealed capture.

The proof is deliberately inert.  Its builder lives at the explicit-input
capture boundary; this module only validates the sealed bytes/path manifest so
activation and submission can pin the same proof without importing a CLI.
"""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from core.generic_lyra_v2_producer import validate_generic_lyra_v2_capture_result
from core.sleeve_decision import canonical_json


GENERIC_LYRA_RAW_RECOMPUTE_SCHEMA = (
    "caerus.generic_lyra_v2_raw_source_recompute.v2"
)
GENERIC_LYRA_RAW_SOURCE_NAMES = frozenset(
    {
        "source_session_manifest",
        "evaluation_batch",
        "legacy_decision_batch",
        "lyra_source",
        "prior_lyra_source",
        "universe_freeze",
        "universe_bytes",
        "forecast_risk_policy",
        "forecast_risk_policy_proposal",
        "forecast_risk_policy_owner_decision",
        "price_panel",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GenericLyraV2RawSourceError(ValueError):
    """Raised when a raw-source reproduction proof is malformed."""


def _hash(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def validate_generic_lyra_v2_raw_source_recompute(
    payload: Mapping[str, Any], *, expected_capture: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate exact path/hash coverage and capture reproduction lineage."""

    fields = {
        "schema_version",
        "status",
        "execution_session",
        "expected_capture_hash",
        "recomputed_capture_hash",
        "source_files",
        "write_enabled",
        "broker_call_performed",
        "broker_write_performed",
        "submission_allowed",
        "execution_authority",
        "activation_authority",
        "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise GenericLyraV2RawSourceError("raw-source recompute fields differ")
    capture = validate_generic_lyra_v2_capture_result(expected_capture)
    if (
        payload.get("schema_version") != GENERIC_LYRA_RAW_RECOMPUTE_SCHEMA
        or payload.get("status") != "PASS_NO_WRITE"
        or payload.get("execution_session") != capture["execution_session"]
        or payload.get("expected_capture_hash") != capture["content_hash"]
        or payload.get("recomputed_capture_hash") != capture["content_hash"]
        or any(
            payload.get(field) is not False
            for field in (
                "write_enabled",
                "broker_call_performed",
                "broker_write_performed",
                "submission_allowed",
                "execution_authority",
                "activation_authority",
            )
        )
    ):
        raise GenericLyraV2RawSourceError("raw-source recompute semantics differ")
    files = payload.get("source_files")
    if not isinstance(files, list):
        raise GenericLyraV2RawSourceError("raw-source file manifest is invalid")
    names = {
        row.get("name") for row in files if isinstance(row, Mapping)
    }
    if (
        len(files) != len(GENERIC_LYRA_RAW_SOURCE_NAMES)
        or names != GENERIC_LYRA_RAW_SOURCE_NAMES
        or files != sorted(files, key=lambda row: row["name"])
    ):
        raise GenericLyraV2RawSourceError("raw-source file coverage differs")
    for row in files:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"name", "path", "sha256"}
            or not isinstance(row.get("path"), str)
            or not row["path"]
            or not Path(row["path"]).is_absolute()
            or not _SHA256.fullmatch(str(row.get("sha256") or ""))
        ):
            raise GenericLyraV2RawSourceError(
                "raw-source path/hash descriptor is invalid"
            )
    if len({row["path"] for row in files}) != len(files):
        raise GenericLyraV2RawSourceError("raw-source paths must be unique")
    if not _SHA256.fullmatch(str(payload.get("content_hash") or "")):
        raise GenericLyraV2RawSourceError("raw-source proof hash is invalid")
    if payload["content_hash"] != _hash(payload):
        raise GenericLyraV2RawSourceError("raw-source recompute hash mismatch")
    return copy.deepcopy(dict(payload))


__all__ = [
    "GENERIC_LYRA_RAW_RECOMPUTE_SCHEMA",
    "GENERIC_LYRA_RAW_SOURCE_NAMES",
    "GenericLyraV2RawSourceError",
    "validate_generic_lyra_v2_raw_source_recompute",
]
