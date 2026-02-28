"""Spec parsing and validation."""

from __future__ import annotations

import re
from pathlib import Path

from .util import VALID_MODES

REQUIRED_PARSE_HEADERS = ("MODE", "OBJECTIVE")
REQUIRED_VERIFY_HEADERS = ("MODE", "PROJECT_TYPE", "RISK_TIER", "OBJECTIVE")
_HEADER_PATTERN = re.compile(r"^([A-Z_]+):\s*(.+?)\s*$")


class SpecValidationError(Exception):
    """Raised when a spec is missing required fields or has invalid values."""


def parse_headers(spec_path: Path) -> dict[str, str]:
    """Extract KEY: value headers from a markdown spec file."""

    if not spec_path.exists() or not spec_path.is_file():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    headers: dict[str, str] = {}
    for line in spec_path.read_text(encoding="utf-8").splitlines():
        match = _HEADER_PATTERN.match(line.strip())
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key not in headers:
            headers[key] = value
    return headers


def validate_headers(headers: dict[str, str], required: tuple[str, ...]) -> None:
    """Validate required header presence and mode value."""

    missing = [key for key in required if not headers.get(key)]
    if missing:
        raise SpecValidationError(f"Missing required headers: {', '.join(missing)}")

    mode = headers.get("MODE", "")
    if mode and mode not in VALID_MODES:
        allowed = ", ".join(VALID_MODES)
        raise SpecValidationError(f"Invalid MODE '{mode}'. Allowed values: {allowed}")
