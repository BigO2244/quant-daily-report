from __future__ import annotations

from typing import Iterable

CANONICAL_TRADING_MODES = frozenset({"paper", "live"})
LEGACY_TRADING_MODE_ALIASES = {
    "alpaca": "paper",
    "shadow": "paper",
}


def raw_trading_mode(value: object, *, default: str = "paper") -> str:
    text = str(value if value is not None else default).strip().lower()
    return text or str(default).strip().lower()


def normalize_trading_mode(value: object, *, default: str = "paper") -> str:
    raw = raw_trading_mode(value, default=default)
    return LEGACY_TRADING_MODE_ALIASES.get(raw, raw)


def canonical_trading_mode(value: object, *, default: str = "paper", field_name: str = "trading_mode") -> str:
    raw = raw_trading_mode(value, default=default)
    normalized = normalize_trading_mode(raw, default=default)
    if normalized not in CANONICAL_TRADING_MODES:
        raise RuntimeError(f"Unsupported {field_name}={raw}")
    return normalized


def canonical_trading_mode_label(value: object, *, default: str = "paper", field_name: str = "trading_mode") -> str:
    return canonical_trading_mode(value, default=default, field_name=field_name).upper()


def legacy_shadow_mode_requested(*values: object) -> bool:
    return any(raw_trading_mode(value, default="") == "shadow" for value in values)


def any_mode_normalizes_to_live(values: Iterable[object], *, default: str = "paper") -> bool:
    return any(normalize_trading_mode(value, default=default) == "live" for value in values)
