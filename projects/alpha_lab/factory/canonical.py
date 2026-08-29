"""Canonical JSON and SHA-256 helpers for reproducible research artifacts."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .errors import ContractValidationError


def format_datetime(value: datetime) -> str:
    """Return an aware timestamp in a stable UTC representation."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractValidationError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    """Parse a canonical or ISO-8601 timestamp and require timezone context."""

    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError("timestamp must be a non-empty string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ContractValidationError("invalid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractValidationError("timestamps must be timezone-aware")
    return parsed


def _json_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return _json_value(to_dict())
        return _json_value(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, datetime):
        return format_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (Mapping, MappingProxyType)):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractValidationError("canonical JSON object keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError("canonical JSON rejects NaN and infinity")
        return value
    raise ContractValidationError(
        "unsupported canonical JSON value type: {}".format(type(value).__name__)
    )


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically, rejecting lossy or ambiguous values."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_hash(value: Any) -> str:
    """Return the lowercase SHA-256 digest of canonical JSON bytes."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def is_sha256(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def require_sha256(value: str, field_name: str) -> None:
    if not is_sha256(value):
        raise ContractValidationError(
            "{} must be a lowercase 64-character SHA-256 digest".format(field_name)
        )


def require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError("{} is required".format(field_name))
