"""Immutable prospective static-universe freezes."""

from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Mapping


GOVERNED_UNIVERSE_FREEZE_SCHEMA = "caerus.governed_universe_freeze.v1"


class GovernedUniverseFreezeError(ValueError):
    pass


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(_canonical(body).encode()).hexdigest()


def _symbols(source: bytes) -> list[str]:
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GovernedUniverseFreezeError("universe source must be UTF-8") from exc
    lines = [line for line in text.splitlines() if line.strip()]
    rows = list(csv.DictReader(io.StringIO("\n".join(lines))))
    symbols = [str(row.get("ticker") or "").strip().upper() for row in rows]
    if not symbols or any(not symbol for symbol in symbols) or len(symbols) != len(set(symbols)):
        raise GovernedUniverseFreezeError("universe contains blank or duplicate tickers")
    return symbols


def read_universe_symbols(universe_path: Path | str) -> list[str]:
    """Read exact ordered membership with the canonical freeze parser."""
    return _symbols(Path(universe_path).read_bytes())


def build_governed_universe_freeze(
    *, universe_path: Path | str, generated_at: str, effective_from: str,
    source_revision: str, no_retroactive_use_before: str,
    freeze_namespace: str = "lyra-live-v1",
) -> dict[str, Any]:
    path = Path(universe_path)
    source = path.read_bytes()
    symbols = _symbols(source)
    generated = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    effective = dt.datetime.fromisoformat(effective_from.replace("Z", "+00:00"))
    cutoff = dt.date.fromisoformat(no_retroactive_use_before)
    if generated.tzinfo is None or effective.tzinfo is None:
        raise GovernedUniverseFreezeError("freeze timestamps require timezones")
    if effective.date() != cutoff:
        raise GovernedUniverseFreezeError("effective date must equal no-retroactive cutoff")
    namespace = str(freeze_namespace).strip()
    if not namespace or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in namespace
    ):
        raise GovernedUniverseFreezeError("freeze namespace is invalid")
    body = {
        "schema_version": GOVERNED_UNIVERSE_FREEZE_SCHEMA,
        "freeze_id": "pending",
        "generated_at": generated_at,
        "effective_from": effective_from,
        "no_retroactive_use_before": no_retroactive_use_before,
        "source_path": "data/universe.csv",
        "source_revision": source_revision,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "member_count": len(symbols),
        "ordered_members_sha256": hashlib.sha256(_canonical(symbols).encode()).hexdigest(),
        "membership_economics_changed": False,
        "prospective_only": True,
        "execution_authority": False,
    }
    seed = _hash(body)
    body["freeze_id"] = f"governed-universe:{namespace}:{no_retroactive_use_before}:{seed[:24]}"
    body["content_hash"] = _hash(body)
    return validate_governed_universe_freeze(body, universe_path=path)


def validate_governed_universe_freeze(
    payload: Mapping[str, Any], *, universe_path: Path | str | None = None,
    session_as_of: str | None = None,
) -> dict[str, Any]:
    fields = {
        "schema_version", "freeze_id", "generated_at", "effective_from",
        "no_retroactive_use_before", "source_path", "source_revision",
        "source_sha256", "member_count", "ordered_members_sha256",
        "membership_economics_changed", "prospective_only", "execution_authority",
        "content_hash",
    }
    if not isinstance(payload, Mapping) or set(payload) != fields:
        raise GovernedUniverseFreezeError("universe freeze fields are invalid")
    if payload.get("schema_version") != GOVERNED_UNIVERSE_FREEZE_SCHEMA:
        raise GovernedUniverseFreezeError("unsupported universe freeze schema")
    if payload.get("source_path") != "data/universe.csv":
        raise GovernedUniverseFreezeError("universe freeze source path differs")
    if payload.get("membership_economics_changed") is not False or payload.get("prospective_only") is not True or payload.get("execution_authority") is not False:
        raise GovernedUniverseFreezeError("universe freeze governance flags are invalid")
    if payload.get("content_hash") != _hash(payload):
        raise GovernedUniverseFreezeError("universe freeze content_hash mismatch")
    effective = dt.datetime.fromisoformat(str(payload["effective_from"]).replace("Z", "+00:00"))
    generated = dt.datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00"))
    if effective.tzinfo is None or generated.tzinfo is None:
        raise GovernedUniverseFreezeError("universe freeze timestamps require timezones")
    if generated > effective:
        raise GovernedUniverseFreezeError("universe freeze was generated after its effective time")
    cutoff = dt.date.fromisoformat(str(payload["no_retroactive_use_before"]))
    if effective.date() != cutoff:
        raise GovernedUniverseFreezeError("universe freeze cutoff mismatch")
    if universe_path is not None:
        source = Path(universe_path).read_bytes()
        symbols = _symbols(source)
        if hashlib.sha256(source).hexdigest() != payload["source_sha256"]:
            raise GovernedUniverseFreezeError("universe bytes differ from frozen hash")
        if len(symbols) != payload["member_count"]:
            raise GovernedUniverseFreezeError("universe member count differs")
        if hashlib.sha256(_canonical(symbols).encode()).hexdigest() != payload["ordered_members_sha256"]:
            raise GovernedUniverseFreezeError("universe ordered membership differs")
    if session_as_of is not None:
        as_of = dt.datetime.fromisoformat(session_as_of.replace("Z", "+00:00"))
        if as_of.tzinfo is None or generated > as_of or as_of < effective or as_of.date() < cutoff:
            raise GovernedUniverseFreezeError("session predates governed universe freeze")
    return copy.deepcopy(dict(payload))


def read_governed_universe_symbols(
    *, freeze: Mapping[str, Any], universe_path: Path | str, session_as_of: str,
) -> list[str]:
    validate_governed_universe_freeze(
        freeze, universe_path=universe_path, session_as_of=session_as_of
    )
    return _symbols(Path(universe_path).read_bytes())


__all__ = [
    "GOVERNED_UNIVERSE_FREEZE_SCHEMA", "GovernedUniverseFreezeError",
    "build_governed_universe_freeze", "validate_governed_universe_freeze",
    "read_governed_universe_symbols", "read_universe_symbols",
]
