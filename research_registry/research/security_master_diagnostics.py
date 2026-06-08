from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from research_registry.research.model_quality_common import (
    md_join,
    normalize_date,
    read_json,
    symbol,
    write_json,
    write_text,
)

SCHEMA_VERSION = "caerus_security_master_diagnostics_v1"
OUTPUT_ROOT = Path("outputs/research/security_master_diagnostics")
MAX_SECURITY_MASTER_AGE_DAYS = 3

KEY_NAMES = ("ALPACA_API_KEY_ID", "ALPACA_KEY_ID")
SECRET_NAMES = ("ALPACA_API_SECRET_KEY", "ALPACA_SECRET_KEY")
BASE_URL_NAMES = ("ALPACA_BASE_URL", "APCA_API_BASE_URL")
PAPER_NAMES = ("ALPACA_PAPER", "APCA_PAPER")
PLACEHOLDER_TOKENS = ("<<", ">>", "PASTE", "YOUR_", "INSERT", "REPLACE")


def build_security_master_diagnostics(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    check_live: bool = False,
    env: Mapping[str, str] | None = None,
    alpaca_probe: Callable[[], list[dict[str, Any]]] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    target = normalize_date(trade_date)
    repo = Path(repo_root)
    env_map = dict(os.environ if env is None else env)
    credential_diag = _credential_diagnostics(env_map)
    refresh_diag = _refresh_diagnostic(
        check_live=check_live,
        env=env_map,
        credential_diag=credential_diag,
        alpaca_probe=alpaca_probe,
    )
    artifact_diag = _security_master_artifact(repo=repo, target_date=target)
    alias_diag = _alias_governance(repo=repo)

    reason_codes = set()
    reason_codes.update(refresh_diag.get("reason_codes") or [])
    reason_codes.update(artifact_diag.get("reason_codes") or [])
    reason_codes.update(alias_diag.get("reason_codes") or [])
    reason_codes.discard("ok")

    status = "OK"
    if refresh_diag.get("auth_status") in {"MISSING_CREDENTIALS", "UNAUTHORIZED", "NETWORK_UNAVAILABLE", "REFRESH_FAILED"}:
        status = "PARTIAL"
    if artifact_diag.get("status") in {"MISSING", "STALE", "MALFORMED"}:
        status = "PARTIAL"
    if alias_diag.get("bk_bny", {}).get("execution_blocker_if_unresolved"):
        status = "BLOCKED"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": target,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "status": status,
        "check_live_requested": bool(check_live),
        "credential_diagnostics": credential_diag,
        "refresh_diagnostic": refresh_diag,
        "security_master_artifact": artifact_diag,
        "alias_governance": alias_diag,
        "safe_refresh_command": "python3 scripts/update_security_master.py --asof-date "
        f"{target}",
        "reason_codes": sorted(reason_codes) or ["ok"],
    }
    if write:
        out_dir = _out_dir(repo=repo, target=target, output_root=output_root)
        write_json(out_dir / "security_master_diagnostics.json", payload)
        write_text(out_dir / "security_master_diagnostics.md", render_markdown(payload))
    return payload


def _credential_diagnostics(env: Mapping[str, str]) -> dict[str, Any]:
    key_name, key_value = _first_env(env, KEY_NAMES)
    secret_name, secret_value = _first_env(env, SECRET_NAMES)
    base_name, base_value = _first_env(env, BASE_URL_NAMES)
    paper_name, paper_value = _first_env(env, PAPER_NAMES)
    missing = []
    if not key_value:
        missing.append("alpaca_key")
    if not secret_value:
        missing.append("alpaca_secret")
    endpoint_class = _endpoint_class(base_url=base_value, paper_value=paper_value)
    return {
        "alpaca_key": _redacted_meta(name=key_name, value=key_value),
        "alpaca_secret": _redacted_meta(name=secret_name, value=secret_value),
        "base_url": {
            "name": base_name,
            "set": bool(base_value),
            "ends_with_v2": bool(str(base_value or "").rstrip("/").endswith("/v2")),
            "endpoint_class": endpoint_class,
        },
        "paper_flag": {
            "name": paper_name,
            "set": bool(paper_value),
            "truthy": str(paper_value or "").strip().lower() in {"1", "true", "yes", "y", "on"},
        },
        "missing": sorted(missing),
        "reason_codes": ["MISSING_ALPACA_CREDENTIALS"] if missing else ["ok"],
    }


def _first_env(env: Mapping[str, str], names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for name in names:
        value = env.get(name)
        if value is not None and str(value) != "":
            return name, str(value)
    return None, None


def _redacted_meta(*, name: str | None, value: str | None) -> dict[str, Any]:
    text = "" if value is None else str(value)
    return {
        "name": name,
        "set": bool(text),
        "length": len(text),
        "placeholder_tokens": any(token in text.upper() for token in PLACEHOLDER_TOKENS),
        "leading_space": bool(text and text != text.lstrip()),
        "trailing_space": bool(text and text != text.rstrip()),
    }


def _endpoint_class(*, base_url: str | None, paper_value: str | None) -> str:
    base = str(base_url or "").lower()
    paper = str(paper_value or "").strip().lower()
    if "paper" in base or paper in {"1", "true", "yes", "y", "on"}:
        return "paper"
    if "api.alpaca.markets" in base:
        return "live"
    return "unknown"


def _refresh_diagnostic(
    *,
    check_live: bool,
    env: Mapping[str, str],
    credential_diag: dict[str, Any],
    alpaca_probe: Callable[[], list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    if not check_live:
        return {
            "status": "NOT_CHECKED",
            "auth_status": "NOT_CHECKED",
            "asset_count": None,
            "error_class": None,
            "error_message": None,
            "reason_codes": ["LIVE_REFRESH_NOT_REQUESTED"],
        }
    if credential_diag.get("missing"):
        return {
            "status": "FAILED",
            "auth_status": "MISSING_CREDENTIALS",
            "asset_count": None,
            "error_class": None,
            "error_message": None,
            "reason_codes": ["MISSING_ALPACA_CREDENTIALS"],
        }
    try:
        assets = alpaca_probe() if alpaca_probe is not None else _default_alpaca_probe()
    except Exception as exc:
        classification = _classify_refresh_exception(exc, env=env)
        return {
            "status": "FAILED",
            "auth_status": classification["auth_status"],
            "asset_count": None,
            "error_class": type(exc).__name__,
            "error_message": classification["safe_error_message"],
            "reason_codes": classification["reason_codes"],
        }
    return {
        "status": "SUCCESS",
        "auth_status": "OK",
        "asset_count": len(assets or []),
        "error_class": None,
        "error_message": None,
        "reason_codes": ["ok"],
    }


def _default_alpaca_probe() -> list[dict[str, Any]]:
    from brokers.alpaca_broker import AlpacaBroker

    return AlpacaBroker.from_env().list_assets(status="active", asset_class="us_equity")


def _classify_refresh_exception(exc: Exception, *, env: Mapping[str, str]) -> dict[str, Any]:
    safe_message = _sanitize_message(str(exc), env=env)
    text = safe_message.lower()
    if "401" in text or "unauthorized" in text:
        return {
            "auth_status": "UNAUTHORIZED",
            "safe_error_message": safe_message,
            "reason_codes": ["ALPACA_401_UNAUTHORIZED"],
        }
    if any(token in text for token in ("timed out", "name resolution", "temporary failure", "connection", "network", "dns")):
        return {
            "auth_status": "NETWORK_UNAVAILABLE",
            "safe_error_message": safe_message,
            "reason_codes": ["NETWORK_UNAVAILABLE"],
        }
    if "paper" in text and "live" in text:
        return {
            "auth_status": "PAPER_LIVE_ENDPOINT_MISMATCH_POSSIBLE",
            "safe_error_message": safe_message,
            "reason_codes": ["PAPER_LIVE_ENDPOINT_MISMATCH_POSSIBLE"],
        }
    return {
        "auth_status": "REFRESH_FAILED",
        "safe_error_message": safe_message,
        "reason_codes": ["SECURITY_MASTER_REFRESH_FAILED"],
    }


def _sanitize_message(message: str, *, env: Mapping[str, str]) -> str:
    safe = str(message or "")
    for name in KEY_NAMES + SECRET_NAMES:
        value = env.get(name)
        if value and len(str(value)) >= 4:
            safe = safe.replace(str(value), "[REDACTED]")
    return safe[:400]


def _security_master_artifact(*, repo: Path, target_date: str) -> dict[str, Any]:
    path = _latest_security_master_path(repo)
    if path is None:
        return {
            "status": "MISSING",
            "path": None,
            "asof_date": None,
            "age_days": None,
            "symbol_count": 0,
            "reason_codes": ["SECURITY_MASTER_MISSING"],
        }
    payload = read_json(path)
    if payload is None:
        return {
            "status": "MALFORMED",
            "path": str(path),
            "asof_date": None,
            "age_days": None,
            "symbol_count": 0,
            "reason_codes": ["SECURITY_MASTER_MALFORMED"],
        }
    asof_date = payload.get("asof_date")
    age_days = _age_days(asof_date=asof_date, target_date=target_date)
    reason_codes = []
    if age_days is None:
        reason_codes.append("SECURITY_MASTER_ASOF_MISSING")
    elif age_days > MAX_SECURITY_MASTER_AGE_DAYS:
        reason_codes.append("SECURITY_MASTER_STALE")
    status = "READY"
    if reason_codes:
        status = "STALE" if "SECURITY_MASTER_STALE" in reason_codes else "PARTIAL"
    return {
        "status": status,
        "path": str(path),
        "asof_date": asof_date,
        "age_days": age_days,
        "symbol_count": len(payload.get("symbols") or []),
        "reason_codes": reason_codes or ["ok"],
    }


def _latest_security_master_path(repo: Path) -> Path | None:
    root = repo / "data" / "security_master"
    latest = root / "ticker_universe_latest.json"
    pointer = root / "latest.json"
    if pointer.exists():
        payload = read_json(pointer) or {}
        rel = str(payload.get("ticker_universe_path") or "").strip()
        if rel:
            path = Path(rel)
            if not path.is_absolute():
                path = repo / path
            if path.exists():
                return path
    if latest.exists():
        return latest
    return None


def _age_days(*, asof_date: Any, target_date: str) -> int | None:
    try:
        asof = dt.date.fromisoformat(str(asof_date))
        target = dt.date.fromisoformat(target_date)
    except Exception:
        return None
    return int((target - asof).days)


def _alias_governance(*, repo: Path) -> dict[str, Any]:
    universe_symbols = set(_read_universe_symbols(repo / "data" / "universe.csv"))
    manual_aliases = _read_alias_map(repo / "data" / "security_master" / "manual_aliases.json")
    ticker_exceptions = read_json(repo / "data" / "ticker_exceptions.json") or {}
    provider_aliases = {
        symbol(src): symbol(dst)
        for src, dst in ((ticker_exceptions.get("aliases") or {}).items() if isinstance(ticker_exceptions.get("aliases"), dict) else [])
        if symbol(src) and symbol(dst)
    }
    bk_bny = {
        "stale_symbol": "BK",
        "current_symbol": "BNY",
        "universe_contains_stale_symbol": "BK" in universe_symbols,
        "manual_execution_alias_configured": manual_aliases.get("BK") == "BNY",
        "price_provider_exception_configured": provider_aliases.get("BK") == "BNY",
        "universe_migration_backlog": "BK" in universe_symbols,
        "execution_blocker_if_unresolved": "BK" in universe_symbols and manual_aliases.get("BK") != "BNY",
        "recommended_action": "migrate_universe_symbol_after_security_master_refresh_and_governance_review"
        if "BK" in universe_symbols
        else "no_universe_migration_required",
    }
    reason_codes = []
    if bk_bny["price_provider_exception_configured"]:
        reason_codes.append("BK_BNY_PRICE_EXCEPTION_CONFIGURED")
    if bk_bny["manual_execution_alias_configured"]:
        reason_codes.append("BK_BNY_EXECUTION_ALIAS_CONFIGURED")
    if bk_bny["universe_migration_backlog"]:
        reason_codes.append("BK_BNY_UNIVERSE_MIGRATION_BACKLOG")
    if bk_bny["execution_blocker_if_unresolved"]:
        reason_codes.append("BK_BNY_EXECUTION_BLOCKER_IF_TRADED")
    return {
        "universe_symbol_count": len(universe_symbols),
        "manual_alias_count": len(manual_aliases),
        "price_provider_alias_count": len(provider_aliases),
        "bk_bny": bk_bny,
        "reason_codes": sorted(set(reason_codes)) or ["ok"],
    }


def _read_universe_symbols(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(line for line in handle if line.strip())
            for row in reader:
                ticker = symbol(row.get("ticker") or row.get("symbol"))
                if ticker:
                    out.append(ticker)
    except Exception:
        return []
    return sorted(set(out))


def _read_alias_map(path: Path) -> dict[str, str]:
    payload = read_json(path) or {}
    aliases = payload.get("aliases") or {}
    if not isinstance(aliases, dict):
        return {}
    return {symbol(src): symbol(dst) for src, dst in aliases.items() if symbol(src) and symbol(dst)}


def _out_dir(*, repo: Path, target: str, output_root: Path | str | None) -> Path:
    root = Path(output_root) if output_root is not None else repo / OUTPUT_ROOT
    return root / normalize_date(target)


def render_markdown(payload: dict[str, Any]) -> str:
    refresh = payload.get("refresh_diagnostic") or {}
    artifact = payload.get("security_master_artifact") or {}
    alias = (payload.get("alias_governance") or {}).get("bk_bny") or {}
    lines = [
        f"# Security Master Diagnostics - {payload.get('date')}",
        "",
        f"- Status: {payload.get('status')}",
        f"- Live check requested: {payload.get('check_live_requested')}",
        f"- Auth status: {refresh.get('auth_status')}",
        f"- Security master status: {artifact.get('status')}",
        f"- Security master as-of: {artifact.get('asof_date')}",
        f"- BK -> BNY universe backlog: {alias.get('universe_migration_backlog')}",
        f"- BK -> BNY execution blocker if unresolved: {alias.get('execution_blocker_if_unresolved')}",
        f"- Reason codes: {md_join(payload.get('reason_codes') or [])}",
        f"- Safe refresh command: `{payload.get('safe_refresh_command')}`",
        "",
        "## Alias Governance",
        "",
        "| Check | Value |",
        "|---|---:|",
    ]
    for name, value in sorted(alias.items()):
        lines.append(f"| {name} | {value} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit security-master refresh/auth and alias governance.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--check-live", action="store_true")
    args = parser.parse_args(argv)
    payload = build_security_master_diagnostics(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
        check_live=bool(args.check_live),
    )
    print(
        json.dumps(
            {
                "date": payload["date"],
                "status": payload["status"],
                "auth_status": (payload.get("refresh_diagnostic") or {}).get("auth_status"),
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
