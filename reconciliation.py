from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from core.trading_mode import canonical_trading_mode

logger = logging.getLogger(__name__)

# ============================================================
# Canonical Model Snapshot Helpers
# ============================================================

def _canonical_model_snapshot_path() -> Path:
    """Return path to canonical model snapshot JSON."""
    snapshot_dir = Path("outputs/paper_state")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return snapshot_dir / "canonical_positions.json"


def _legacy_canonical_model_snapshot_path() -> Path:
    """Return path to legacy canonical snapshot location (read-only fallback)."""
    return Path("canonical-model-snapshot") / "canonical_positions.json"


def _write_canonical_model_snapshot(
    positions: dict[str, float],
    cash: float | None = None,
    equity: float | None = None,
    reason: str = "runtime_update",
) -> Path:
    """Write canonical model snapshot to disk."""
    path = _canonical_model_snapshot_path()
    payload = {
        "positions": positions,
        "position_count": len(positions),
        "cash": cash,
        "equity": equity,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "reason": reason,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.info("[RECON] Wrote canonical model snapshot: %s reason=%s", path, reason)
    return path


def _load_canonical_model_snapshot(allow_empty: bool = False) -> dict[str, Any]:
    """Load canonical model snapshot or return dict with parse_error."""
    path = _canonical_model_snapshot_path()
    if not path.exists():
        legacy_path = _legacy_canonical_model_snapshot_path()
        if legacy_path.exists():
            try:
                legacy_payload = json.loads(legacy_path.read_text(encoding="utf-8"))
                legacy_positions = _normalize_positions(legacy_payload.get("positions") or {})
                legacy_cash = _coerce_float(legacy_payload.get("cash"))
                legacy_equity = _coerce_float(legacy_payload.get("equity"))
                legacy_reason = legacy_payload.get("reason") if isinstance(legacy_payload, dict) else None
                if legacy_positions or allow_empty:
                    write_reason = (
                        f"recovered_from_legacy_snapshot:{legacy_reason}"
                        if legacy_reason
                        else "recovered_from_legacy_snapshot"
                    )
                    _write_canonical_model_snapshot(
                        positions=legacy_positions,
                        cash=legacy_cash,
                        equity=legacy_equity,
                        reason=write_reason,
                    )
                    return {
                        "positions": legacy_positions,
                        "position_count": len(legacy_positions),
                        "cash": legacy_cash,
                        "equity": legacy_equity,
                        "timestamp_utc": legacy_payload.get("timestamp_utc") if isinstance(legacy_payload, dict) else None,
                        "reason": write_reason,
                        "path": str(path),
                        "path_exists": True,
                        "parser": "canonical_json_recovered_from_legacy",
                        "parse_error": None,
                        "source_state_notes": [
                            f"Recovered preferred canonical snapshot from legacy source: {legacy_path}",
                        ],
                    }
            except Exception as e:
                logger.warning("[RECON] Failed to recover canonical snapshot from legacy path: %s", e)
        return {
            "positions": {},
            "position_count": 0,
            "cash": None,
            "equity": None,
            "path": str(path),
            "path_exists": False,
            "parser": "canonical_json",
            "parse_error": "canonical_snapshot_missing",
            "source_state_notes": [
                f"Preferred canonical snapshot missing at {path}",
                f"Legacy snapshot unavailable or unusable at {_legacy_canonical_model_snapshot_path()}",
            ],
        }
    
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        positions = _normalize_positions(payload.get("positions") or {})
        cash = _coerce_float(payload.get("cash"))
        equity = _coerce_float(payload.get("equity"))
        
        parse_error = None
        if not positions and not allow_empty:
            # A snapshot with a 'reason' field was deliberately written (e.g.
            # bootstrap_from_broker on a freshly-reset flat account).  Treat
            # 0 positions as valid in that case so reconciliation can compare
            # a flat model against a flat broker and correctly return PASS.
            reason = payload.get("reason") if isinstance(payload, dict) else None
            if not reason:
                parse_error = "no_positions_in_canonical_snapshot"
        
        return {
            "positions": positions,
            "position_count": len(positions),
            "cash": cash,
            "equity": equity,
            "timestamp_utc": payload.get("timestamp_utc") if isinstance(payload, dict) else None,
            "reason": payload.get("reason") if isinstance(payload, dict) else None,
            "path": str(path),
            "path_exists": True,
            "parser": "canonical_json",
            "parse_error": parse_error,
            "source_state_notes": [],
        }
    except Exception as e:
        logger.warning("[RECON] Failed to load canonical snapshot: %s", e)
        return {
            "positions": {},
            "position_count": 0,
            "cash": None,
            "equity": None,
            "path": str(path),
            "path_exists": True,
            "parser": "canonical_json",
            "parse_error": f"canonical_snapshot_parse_error: {e}",
            "source_state_notes": [f"Preferred canonical snapshot parse failed at {path}"],
        }



def _is_truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(str(raw).strip())
    except Exception:
        return float(default)


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        text = str(value).strip()
        if text == "":
            return None
        return float(text)
    except Exception:
        return None


def _coerce_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        if text == "":
            return None
        return Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _extract_qty(value: object) -> float | None:
    if isinstance(value, dict):
        for key in ("qty", "shares", "quantity", "position_qty"):
            if key in value:
                qty = _coerce_float(value.get(key))
                if qty is not None:
                    return qty
        return None
    return _coerce_float(value)


def _normalize_positions(raw_positions: object) -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(raw_positions, dict):
        for raw_sym, raw_qty in raw_positions.items():
            sym = _normalize_symbol(raw_sym)
            if not sym:
                continue
            qty = _extract_qty(raw_qty)
            if qty is None:
                continue
            out[sym] = out.get(sym, 0.0) + float(qty)
        return out

    if isinstance(raw_positions, list):
        for item in raw_positions:
            if not isinstance(item, dict):
                continue
            sym = _normalize_symbol(
                item.get("symbol")
                or item.get("ticker")
                or item.get("asset")
                or item.get("instrument")
            )
            if not sym:
                continue
            qty = _extract_qty(item)
            if qty is None:
                continue
            out[sym] = out.get(sym, 0.0) + float(qty)
    return out


def compare_positions(
    broker_positions: dict[str, float],
    model_positions: dict[str, float],
    max_qty_diff: float,
) -> dict[str, Any]:
    broker = {str(k): float(v) for k, v in (broker_positions or {}).items()}
    model = {str(k): float(v) for k, v in (model_positions or {}).items()}
    missing_in_model: list[str] = []
    missing_in_broker: list[str] = []
    qty_mismatches: list[dict[str, Any]] = []
    max_abs_qty_diff = 0.0

    for sym in sorted(set(broker) | set(model)):
        if sym not in model:
            missing_in_model.append(sym)
            continue
        if sym not in broker:
            missing_in_broker.append(sym)
            continue
        diff = abs(float(broker[sym]) - float(model[sym]))
        if diff > max_abs_qty_diff:
            max_abs_qty_diff = float(diff)
        if diff > float(max_qty_diff):
            qty_mismatches.append(
                {
                    "symbol": sym,
                    "broker_qty": float(broker[sym]),
                    "model_qty": float(model[sym]),
                    "abs_diff": float(diff),
                }
            )

    return {
        "missing_in_model": missing_in_model,
        "missing_in_broker": missing_in_broker,
        "qty_mismatches": qty_mismatches,
        "max_abs_qty_diff": float(max_abs_qty_diff),
        "errors": [],
    }


def verdict_from_diffs(
    diffs: dict[str, Any],
    *,
    cash_delta: float | None = None,
    equity_delta: float | None = None,
    equity_base: float | None = None,
    cash_tol: float = 5.0,
    equity_tol_abs: float = 10.0,
    equity_tol_pct: float = 0.001,
    strict: bool = False,
    hard_fail_on_equity_drift: bool = False,
) -> str:
    """Return PASS / WARN / FAIL for a reconciliation diff result.

    Position-state integrity issues (missing symbols, qty mismatches, errors)
    always produce FAIL regardless of any tolerance or flag.

    Cash/equity drift alone produces WARN by default so that normal
    mark-to-market movement between snapshot time and execution time does not
    block order submission.  Set hard_fail_on_equity_drift=True (via
    RECON_EQUITY_HARD_FAIL=1 env var) to restore the old strict-fail
    behaviour for equity drift.

    The ``strict`` parameter is kept for backward compatibility but no longer
    controls the equity/cash escalation path; use hard_fail_on_equity_drift
    for that purpose.
    """
    missing_model = list((diffs or {}).get("missing_in_model") or [])
    missing_broker = list((diffs or {}).get("missing_in_broker") or [])
    qty_mismatches = list((diffs or {}).get("qty_mismatches") or [])
    errors = list((diffs or {}).get("errors") or [])
    # Position-state integrity: always hard-fail, no flags can soften this.
    if missing_model or missing_broker or qty_mismatches or errors:
        return "FAIL"

    cash_breach = (
        cash_delta is not None and abs(float(cash_delta)) > float(cash_tol)
    )
    equity_tol = float(equity_tol_abs)
    if equity_base is not None:
        equity_tol = max(
            float(equity_tol_abs),
            abs(float(equity_base)) * float(equity_tol_pct),
        )
    equity_breach = (
        equity_delta is not None and abs(float(equity_delta)) > float(equity_tol)
    )
    if not cash_breach and not equity_breach:
        return "PASS"
    # Equity/cash drift alone: WARN by default; FAIL only with explicit opt-in.
    if hard_fail_on_equity_drift:
        return "FAIL"
    return "WARN"


def _classify_block_reason(
    diffs: dict[str, Any],
    *,
    cash_breach: bool,
    equity_breach: bool,
    model_parse_error: str | None = None,
) -> str:
    """Return a short token classifying why reconciliation produced a non-PASS verdict."""
    errors = list((diffs or {}).get("errors") or [])
    missing_model = list((diffs or {}).get("missing_in_model") or [])
    missing_broker = list((diffs or {}).get("missing_in_broker") or [])
    qty_mismatches = list((diffs or {}).get("qty_mismatches") or [])

    if errors or model_parse_error:
        joined = " ".join(str(e) for e in errors) + " " + str(model_parse_error or "")
        if any(kw in joined for kw in ("missing", "stale", "parse_error", "snapshot")):
            return "stale_state"
        if "broker_read" in joined or "broker" in joined.lower():
            return "broker_read_failure"
        return "stale_state"
    if missing_model or missing_broker:
        return "positions_mismatch"
    if qty_mismatches:
        return "quantity_mismatch"
    if equity_breach and cash_breach:
        return "equity_cash_drift"
    if equity_breach:
        return "equity_only_drift"
    if cash_breach:
        return "cash_only_drift"
    return "none"


def _load_broker_snapshot(trading_mode: str) -> dict[str, Any]:
    mode = canonical_trading_mode(trading_mode, field_name="trading_mode")
    broker_snapshot_path = str(os.getenv("RECON_BROKER_SNAPSHOT_JSON", "")).strip()
    if broker_snapshot_path:
        path = Path(broker_snapshot_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        positions = _normalize_positions(payload.get("positions"))
        account = payload.get("account") if isinstance(payload, dict) else {}
        if not isinstance(account, dict):
            account = {}
        cash = _coerce_float(account.get("cash"))
        equity = _coerce_float(account.get("equity") or account.get("portfolio_value"))
        return {
            "source": "snapshot_file",
            "positions": positions,
            "position_count": int(len(positions)),
            "cash": cash,
            "equity": equity,
            "raw_account_fields_found": sorted(account.keys()),
        }

    if mode != "paper":
        return {
            "source": "mode_not_paper",
            "positions": {},
            "position_count": 0,
            "cash": None,
            "equity": None,
            "raw_account_fields_found": [],
        }

    from brokers.alpaca_broker import AlpacaBroker

    broker = AlpacaBroker.from_env()
    account = broker.get_account() or {}
    positions_raw = broker.get_positions() or []
    positions = _normalize_positions(positions_raw)
    return {
        "source": "alpaca_adapter",
        "positions": positions,
        "position_count": int(len(positions)),
        "cash": _coerce_float(account.get("cash")),
        "equity": _coerce_float(account.get("equity") or account.get("portfolio_value")),
        "raw_account_fields_found": sorted(account.keys()) if isinstance(account, dict) else [],
    }


def _load_model_snapshot(ledger_path: str, allow_empty: bool = False) -> dict[str, Any]:
    path = Path(str(ledger_path))
    if not path.exists():
        return {
            "positions": {},
            "position_count": 0,
            "cash": None,
            "equity": None,
            "path": str(path),
            "path_exists": False,
            "parser": "missing",
            "fields_found": [],
            "parse_error": "ledger_path_missing",
        }

    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        positions_raw = payload.get("positions") if isinstance(payload, dict) else payload
        positions = _normalize_positions(positions_raw)
        fields_found: list[str] = []
        if isinstance(payload, dict):
            for k in ("positions", "cash", "equity", "total_equity", "portfolio_value"):
                if k in payload:
                    fields_found.append(k)
        cash = _coerce_float(payload.get("cash")) if isinstance(payload, dict) else None
        equity = (
            _coerce_float(payload.get("equity") or payload.get("total_equity") or payload.get("portfolio_value"))
            if isinstance(payload, dict)
            else None
        )
        parse_error = None
        if not positions and not allow_empty:
            parse_error = "no_positions_found_in_json"
        return {
            "positions": positions,
            "position_count": int(len(positions)),
            "cash": cash,
            "equity": equity,
            "path": str(path),
            "path_exists": True,
            "parser": "json",
            "fields_found": fields_found,
            "parse_error": parse_error,
        }

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    fields = {str(f).strip().lower(): str(f) for f in fieldnames if f}
    sym_col = fields.get("ticker") or fields.get("symbol")
    qty_col = fields.get("shares") or fields.get("qty") or fields.get("quantity")
    date_col = fields.get("date")
    cash_col = fields.get("cash")
    equity_col = fields.get("total_equity") or fields.get("equity") or fields.get("portfolio_value")

    working_rows = rows
    latest_date = None
    if date_col:
        dates = [str(r.get(date_col) or "").strip() for r in rows if str(r.get(date_col) or "").strip()]
        if dates:
            latest_date = max(dates)
            working_rows = [r for r in rows if str(r.get(date_col) or "").strip() == latest_date]

    positions: dict[str, float] = {}
    if sym_col and qty_col:
        for row in working_rows:
            sym = _normalize_symbol(row.get(sym_col))
            qty = _coerce_float(row.get(qty_col))
            if not sym or qty is None:
                continue
            positions[sym] = positions.get(sym, 0.0) + float(qty)

    cash = None
    equity = None
    for row in reversed(working_rows):
        if cash is None and cash_col:
            cash = _coerce_float(row.get(cash_col))
        if equity is None and equity_col:
            equity = _coerce_float(row.get(equity_col))
        if cash is not None and equity is not None:
            break

    parse_error = None
    if not positions and not allow_empty:
        parse_error = "no_positions_found_in_csv"

    fields_found = [c for c in (sym_col, qty_col, date_col, cash_col, equity_col) if c]
    return {
        "positions": positions,
        "position_count": int(len(positions)),
        "cash": cash,
        "equity": equity,
        "path": str(path),
        "path_exists": True,
        "parser": "csv",
        "latest_date": latest_date,
        "fields_found": fields_found,
        "parse_error": parse_error,
    }


def _sent_ledger_meta(sent_ledger_path: str) -> dict[str, Any]:
    path = Path(str(sent_ledger_path))
    if not path.exists():
        return {"path": str(path), "path_exists": False, "row_count": 0, "headers": []}
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            headers = list(reader.fieldnames or [])
    except Exception:
        return {"path": str(path), "path_exists": True, "row_count": None, "headers": []}
    return {
        "path": str(path),
        "path_exists": True,
        "row_count": int(len(rows)),
        "headers": headers,
    }


def _recon_out_path(run_date: str, phase: str) -> Path:
    base_dir = Path(str(os.getenv("RUN_OUTPUT_ROOT", "")).strip() or "outputs")
    broker_dir = base_dir / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)
    return broker_dir / f"recon_{phase}_{run_date}.json"


def _write_report(phase: str, run_date: str, payload: dict[str, Any]) -> str:
    out_path = _recon_out_path(run_date=run_date, phase=phase)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(out_path)


def _snapshot_timestamp(snapshot: dict[str, Any]) -> str | None:
    for key in ("timestamp_utc", "captured_at", "as_of"):
        value = str((snapshot or {}).get(key) or "").strip()
        if value:
            return value
    return None


def _canonical_snapshot_is_stale(snapshot: dict[str, Any], run_date: str) -> bool:
    timestamp = _snapshot_timestamp(snapshot)
    if not timestamp:
        return False
    try:
        normalized = timestamp.replace("Z", "+00:00")
        snapshot_dt = dt.datetime.fromisoformat(normalized)
        return snapshot_dt.date().isoformat() < str(run_date)
    except Exception:
        return False


def _normalize_account_status(value: object) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _load_broker_snapshot_v2(trading_mode: str) -> dict[str, Any]:
    mode = canonical_trading_mode(trading_mode, field_name="trading_mode")
    if mode != "paper":
        return {
            "source": "mode_not_paper",
            "positions": {},
            "position_count": 0,
            "cash": None,
            "equity": None,
            "account_status": None,
            "raw_account_fields_found": [],
            "account_error": None,
            "positions_error": None,
            "errors": [],
        }

    from brokers.alpaca_broker import AlpacaBroker

    payload: dict[str, Any] = {
        "source": "alpaca_adapter",
        "positions": {},
        "position_count": 0,
        "cash": None,
        "equity": None,
        "account_status": None,
        "raw_account_fields_found": [],
        "account_error": None,
        "positions_error": None,
        "errors": [],
    }
    try:
        broker = AlpacaBroker.from_env()
    except Exception as exc:
        payload["account_error"] = f"{type(exc).__name__}: {exc}"
        payload["positions_error"] = f"{type(exc).__name__}: {exc}"
        payload["errors"].append("broker_auth_failure")
        return payload

    try:
        account = broker.get_account() or {}
        if not isinstance(account, dict):
            payload["account_error"] = "corrupt_broker_account_payload"
            payload["errors"].append("corrupt_broker_account_payload")
            account = {}
        payload["cash"] = _coerce_float(account.get("cash"))
        payload["equity"] = _coerce_float(account.get("equity") or account.get("portfolio_value"))
        payload["account_status"] = account.get("status")
        payload["raw_account_fields_found"] = sorted(account.keys())
    except Exception as exc:
        payload["account_error"] = f"{type(exc).__name__}: {exc}"
        payload["errors"].append("broker_account_fetch_failure")

    try:
        positions_raw = broker.get_positions() or []
        if not isinstance(positions_raw, (list, dict)):
            payload["positions_error"] = "corrupt_broker_positions_payload"
            payload["errors"].append("corrupt_broker_positions_payload")
            positions_raw = []
        payload["positions"] = _normalize_positions(positions_raw)
        payload["position_count"] = int(len(payload["positions"]))
    except Exception as exc:
        payload["positions_error"] = f"{type(exc).__name__}: {exc}"
        payload["errors"].append("broker_positions_fetch_failure")

    return payload


def classify_drift(
    *,
    run_date: str,
    broker_snapshot: dict[str, Any],
    model_snapshot: dict[str, Any],
    diffs: dict[str, Any],
    cash_delta: float | None,
    equity_delta: float | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    self_heals: list[str] = []
    hard_blocks: list[str] = []

    account_status = _normalize_account_status((broker_snapshot or {}).get("account_status"))
    if (broker_snapshot or {}).get("account_error"):
        hard_blocks.append("broker_account_fetch_failure")
    if (broker_snapshot or {}).get("positions_error"):
        hard_blocks.append("broker_positions_fetch_failure")
    hard_blocks.extend(str(item) for item in ((broker_snapshot or {}).get("errors") or []))
    if account_status and account_status != "ACTIVE":
        hard_blocks.append(f"account_status_not_active:{account_status}")

    parse_error = str((model_snapshot or {}).get("parse_error") or "").strip()
    if parse_error:
        if "missing" in parse_error or "stale" in parse_error or "parse_error" in parse_error:
            self_heals.append(parse_error)
        else:
            self_heals.append(f"canonical_snapshot_issue:{parse_error}")
    if _canonical_snapshot_is_stale(model_snapshot, run_date):
        self_heals.append("canonical_snapshot_stale")

    missing_in_model = list((diffs or {}).get("missing_in_model") or [])
    missing_in_broker = list((diffs or {}).get("missing_in_broker") or [])
    qty_mismatches = list((diffs or {}).get("qty_mismatches") or [])
    if missing_in_model or missing_in_broker:
        self_heals.append("symbol_set_drift")
    if qty_mismatches:
        self_heals.append("quantity_mismatch")

    equity_base_decimal = _coerce_decimal((model_snapshot or {}).get("equity"))
    if equity_base_decimal is None:
        equity_base_decimal = _coerce_decimal((broker_snapshot or {}).get("equity"))
    cash_delta_decimal = _coerce_decimal(cash_delta)
    equity_delta_decimal = _coerce_decimal(equity_delta)
    if equity_base_decimal not in (None, Decimal("0")):
        if cash_delta_decimal is not None and abs(cash_delta_decimal) > Decimal("0"):
            cash_ratio = abs(cash_delta_decimal) / abs(equity_base_decimal)
            if cash_ratio < Decimal("0.01"):
                warnings.append("cash_drift_lt_1pct_equity")
            else:
                warnings.append("cash_drift_gte_1pct_equity")
        if equity_delta_decimal is not None and abs(equity_delta_decimal) > Decimal("0"):
            equity_ratio = abs(equity_delta_decimal) / abs(equity_base_decimal)
            if equity_ratio < Decimal("0.01"):
                warnings.append("equity_drift_lt_1pct_equity")
            else:
                warnings.append("equity_drift_gte_1pct_equity")

    if hard_blocks:
        decision = "BLOCK"
    elif self_heals:
        decision = "SELF_HEAL"
    elif warnings:
        decision = "WARN"
    else:
        decision = "PASS"

    return {
        "reconciliation_decision": decision,
        "warnings": sorted(set(warnings)),
        "self_heals": sorted(set(self_heals)),
        "hard_blocks": sorted(set(hard_blocks)),
        "missing_in_broker": missing_in_broker,
        "missing_in_model": missing_in_model,
        "qty_mismatches": qty_mismatches,
        "block_reason": sorted(set(hard_blocks))[0] if hard_blocks else "none",
    }


def _refresh_canonical_from_snapshot(
    *,
    broker_snapshot: dict[str, Any],
    reason: str,
) -> Path:
    return _write_canonical_model_snapshot(
        positions=(broker_snapshot or {}).get("positions") or {},
        cash=_coerce_float((broker_snapshot or {}).get("cash")),
        equity=_coerce_float((broker_snapshot or {}).get("equity")),
        reason=reason,
    )


def refresh_canonical_snapshot_from_posttrade_snapshot(
    *,
    positions_snapshot: dict[str, Any],
    account_snapshot: dict[str, Any] | None = None,
    run_date: str | None = None,
) -> bool:
    try:
        positions = _normalize_positions((positions_snapshot or {}).get("positions") or [])
        account = (account_snapshot or {}).get("account") if isinstance(account_snapshot, dict) else {}
        if not isinstance(account, dict):
            account = {}
        cash = _coerce_float((account_snapshot or {}).get("cash"))
        if cash is None:
            cash = _coerce_float(account.get("cash"))
        equity = _coerce_float((account_snapshot or {}).get("equity"))
        if equity is None:
            equity = _coerce_float(account.get("equity") or account.get("portfolio_value"))
        _write_canonical_model_snapshot(
            positions=positions,
            cash=cash,
            equity=equity,
            reason="posttrade_refresh_from_snapshot",
        )
        logger.info(
            "[POSTTRADE] Refreshed canonical snapshot from posttrade artifact: positions=%d run_date=%s",
            len(positions),
            run_date or "n/a",
        )
        return True
    except Exception as exc:
        logger.warning("[POSTTRADE] Failed to refresh canonical snapshot from posttrade artifact: %s", exc)
        return False


def write_post_execution_drift_report(
    *,
    run_date: str,
    expected_positions: dict[str, float],
    actual_positions: dict[str, float],
    expected_cash: float | None = None,
    expected_equity: float | None = None,
    actual_cash: float | None = None,
    actual_equity: float | None = None,
) -> dict[str, Any]:
    expected = _normalize_positions(expected_positions or {})
    actual = _normalize_positions(actual_positions or {})
    missing_in_actual = sorted([sym for sym in expected if sym not in actual])
    missing_in_expected = sorted([sym for sym in actual if sym not in expected])
    qty_mismatches: list[dict[str, Any]] = []
    matching_positions: list[str] = []
    share_deltas: list[dict[str, Any]] = []
    unexpected_short_positions: list[dict[str, Any]] = []
    repair_suggestions: list[str] = []
    duplicate_fill_suspicions: list[str] = []

    for sym in sorted(set(expected) | set(actual)):
        expected_qty = float(expected.get(sym, 0.0))
        actual_qty = float(actual.get(sym, 0.0))
        delta_qty = float(actual_qty - expected_qty)
        classification = "MATCH"
        if sym in expected and sym in actual and abs(delta_qty) <= 1e-9:
            matching_positions.append(sym)
        elif sym in expected and sym not in actual:
            classification = "MISSING_BROKER_POSITION"
        elif sym not in expected and sym in actual:
            classification = "UNEXPECTED_BROKER_POSITION"
        else:
            classification = "QTY_MISMATCH"
            qty_mismatches.append(
                {
                    "symbol": sym,
                    "expected_qty": expected_qty,
                    "actual_qty": actual_qty,
                    "abs_diff": abs(delta_qty),
                }
            )

        if sym in expected or sym in actual:
            share_deltas.append(
                {
                    "symbol": sym,
                    "expected_qty": expected_qty,
                    "broker_qty": actual_qty,
                    "delta_qty": delta_qty,
                    "classification": classification,
                }
            )

        if actual_qty < -1e-9:
            repair_qty = abs(actual_qty)
            unexpected_short_positions.append(
                {
                    "symbol": sym,
                    "expected_qty": expected_qty,
                    "broker_qty": actual_qty,
                    "delta_qty": delta_qty,
                    "repair_suggestion": f"BUY {int(repair_qty) if float(repair_qty).is_integer() else repair_qty} {sym}",
                }
            )
            repair_suggestions.append(
                f"BUY {int(repair_qty) if float(repair_qty).is_integer() else repair_qty} {sym}"
            )
            if expected_qty >= -1e-9:
                duplicate_fill_suspicions.append(sym)

    cash_delta = (
        float(actual_cash) - float(expected_cash)
        if actual_cash is not None and expected_cash is not None
        else None
    )
    equity_delta = (
        float(actual_equity) - float(expected_equity)
        if actual_equity is not None and expected_equity is not None
        else None
    )

    input_issues: list[str] = []
    if not isinstance(expected_positions, dict):
        input_issues.append("expected_positions_unavailable")
    if not isinstance(actual_positions, dict):
        input_issues.append("actual_positions_unavailable")

    if input_issues:
        drift_status = "NOT_COMPARABLE"
    elif unexpected_short_positions:
        drift_status = "UNEXPECTED_SHORT"
    elif missing_in_actual or missing_in_expected or qty_mismatches:
        drift_status = "DRIFT_DETECTED"
    elif (cash_delta is not None and abs(float(cash_delta)) > 1e-9) or (
        equity_delta is not None and abs(float(equity_delta)) > 1e-9
    ):
        drift_status = "DRIFT_DETECTED"
    else:
        drift_status = "OK_RECONCILED"

    if drift_status == "OK_RECONCILED":
        operator_message = "Broker positions match expected post-execution state."
    elif drift_status == "UNEXPECTED_SHORT":
        operator_message = (
            "Unexpected short position(s) detected after execution; inspect broker orders and repair before next run."
        )
    elif drift_status == "DRIFT_DETECTED":
        operator_message = (
            "Post-execution broker drift detected; expected and actual positions differ."
        )
    elif drift_status == "NOT_COMPARABLE":
        operator_message = (
            "Post-execution drift check could not be completed from local artifacts; manual review required."
        )
    else:
        operator_message = (
            "Post-execution drift check could not be completed from local artifacts; manual review required."
        )

    payload = {
        "trade_date": str(run_date),
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "verdict": "PASS" if drift_status == "OK_RECONCILED" else "WARN",
        "drift_status": drift_status,
        "operator_message": operator_message,
        "manual_intervention_required": drift_status in {"UNEXPECTED_SHORT", "NOT_COMPARABLE"},
        "comparison_status": drift_status,
        "not_comparable_reasons": list(input_issues),
        "expected_positions": expected,
        "actual_positions": actual,
        "matching_positions": matching_positions,
        "missing_in_actual": missing_in_actual,
        "missing_in_expected": missing_in_expected,
        "qty_mismatches": qty_mismatches,
        "share_deltas": share_deltas,
        "unexpected_short_positions": unexpected_short_positions,
        "repair_suggestions": repair_suggestions,
        "duplicate_fill_suspicions": duplicate_fill_suspicions,
        "duplicate_fill_suspicions_count": int(len(duplicate_fill_suspicions)),
        "affected_symbols": sorted(
            set(
                list(missing_in_actual)
                + list(missing_in_expected)
                + [str(item.get("symbol") or "") for item in qty_mismatches]
                + [str(item.get("symbol") or "") for item in unexpected_short_positions]
            )
            - {""}
        ),
        "expected_cash": expected_cash,
        "broker_cash": actual_cash,
        "cash_delta": cash_delta,
        "expected_equity": expected_equity,
        "broker_equity": actual_equity,
        "equity_delta": equity_delta,
        "input_issues": input_issues,
    }
    report_path = _write_report(phase="posttrade", run_date=run_date, payload=payload)
    payload["report_path"] = report_path
    logger.info(
        "[POSTTRADE_DRIFT] status=%s shorts=%d qty_mismatches=%d extra=%d missing=%d report=%s",
        drift_status,
        len(unexpected_short_positions),
        len(qty_mismatches),
        len(missing_in_expected),
        len(missing_in_actual),
        report_path,
    )
    return payload


def pre_trade_reconcile_and_classify(
    *,
    run_date: str,
    trading_mode: str,
    ledger_path: str,
    sent_ledger_path: str,
    allow_empty: bool = False,
) -> dict[str, Any]:
    del ledger_path, allow_empty

    broker_snapshot = _load_broker_snapshot_v2(trading_mode)
    model_snapshot = _load_canonical_model_snapshot(allow_empty=True)
    diffs = compare_positions(
        broker_positions=broker_snapshot.get("positions") or {},
        model_positions=model_snapshot.get("positions") or {},
        max_qty_diff=_env_float("RECON_MAX_QTY_DIFF", 0.0),
    )

    broker_cash = _coerce_float(broker_snapshot.get("cash"))
    model_cash = _coerce_float(model_snapshot.get("cash"))
    broker_equity = _coerce_float(broker_snapshot.get("equity"))
    model_equity = _coerce_float(model_snapshot.get("equity"))
    cash_delta = (
        float(broker_cash) - float(model_cash)
        if broker_cash is not None and model_cash is not None
        else None
    )
    equity_delta = (
        float(broker_equity) - float(model_equity)
        if broker_equity is not None and model_equity is not None
        else None
    )

    classification = classify_drift(
        run_date=run_date,
        broker_snapshot=broker_snapshot,
        model_snapshot=model_snapshot,
        diffs=diffs,
        cash_delta=cash_delta,
        equity_delta=equity_delta,
    )
    decision = str(classification.get("reconciliation_decision") or "PASS")
    repair_actions: list[str] = []
    refreshed_canonical_path: str | None = None

    if decision == "SELF_HEAL":
        try:
            refreshed_path = _refresh_canonical_from_snapshot(
                broker_snapshot=broker_snapshot,
                reason="pretrade_self_heal_from_broker",
            )
            refreshed_canonical_path = str(refreshed_path)
            repair_actions.append(f"canonical_refreshed_from_broker:{refreshed_path}")
        except Exception as exc:
            classification["hard_blocks"] = sorted(
                set(list(classification.get("hard_blocks") or []) + ["canonical_self_heal_write_failed"])
            )
            classification["reconciliation_decision"] = "BLOCK"
            classification["block_reason"] = "canonical_self_heal_write_failed"
            decision = "BLOCK"
            repair_actions.append(f"canonical_refresh_failed:{type(exc).__name__}: {exc}")

    payload = {
        "phase": "pretrade",
        "stage": "pretrade",
        "run_date": str(run_date),
        "trading_mode": str(trading_mode),
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "reconciliation_decision": classification.get("reconciliation_decision"),
        "warnings": list(classification.get("warnings") or []),
        "self_heals": list(classification.get("self_heals") or []),
        "hard_blocks": list(classification.get("hard_blocks") or []),
        "missing_in_broker": list(classification.get("missing_in_broker") or []),
        "missing_in_model": list(classification.get("missing_in_model") or []),
        "qty_mismatches": list(classification.get("qty_mismatches") or []),
        "block_reason": classification.get("block_reason"),
        "allowed_to_execute": str(classification.get("reconciliation_decision") or "PASS") != "BLOCK",
        "repair_actions": repair_actions,
        "refreshed_canonical_path": refreshed_canonical_path,
        "broker_snapshot": broker_snapshot,
        "model_snapshot": model_snapshot,
        "cash_delta": cash_delta,
        "equity_delta": equity_delta,
        "diffs": diffs,
        "sent_ledger": _sent_ledger_meta(sent_ledger_path),
    }
    report_path = _write_report(phase="pretrade", run_date=run_date, payload=payload)
    payload["report_path"] = report_path

    logger.info(
        "[RECON][PRETRADE][V2] decision=%s block_reason=%s broker_positions=%d model_positions=%d report=%s",
        payload["reconciliation_decision"],
        payload["block_reason"],
        int(len(broker_snapshot.get("positions") or {})),
        int(len(model_snapshot.get("positions") or {})),
        report_path,
    )

    if payload["reconciliation_decision"] == "BLOCK":
        source_state_notes = list(model_snapshot.get("source_state_notes") or [])
        _write_recon_blocked_artifact(
            run_date=run_date,
            block_reason=str(payload["block_reason"]),
            recon_report_path=report_path,
        )
        _write_preflight_failure_artifact(
            run_date=run_date,
            halt_stage="pretrade_reconciliation",
            halt_reason=f"pretrade_reconcile_failed:{payload['block_reason']}",
            block_reason=str(payload["block_reason"]),
            recon_report_path=report_path,
            source_state_notes=source_state_notes,
        )

    return payload


def _reconcile(
    *,
    stage: str,
    run_date: str,
    trading_mode: str,
    ledger_path: str,
    sent_ledger_path: str,
    allow_empty: bool,
    strict: bool,
) -> tuple[str, str, str]:
    """Run one reconcile pass and return (verdict, report_path, block_reason)."""
    max_qty_diff = _env_float("RECON_MAX_QTY_DIFF", 0.0)
    cash_tol = _env_float("RECON_CASH_TOL", 5.0)
    equity_tol_abs = _env_float("RECON_EQUITY_TOL_ABS", 10.0)
    equity_tol_pct = _env_float("RECON_EQUITY_TOL_PCT", 0.001)
    equity_hard_fail = _is_truthy(os.getenv("RECON_EQUITY_HARD_FAIL"), default=False)

    broker_snapshot = _load_broker_snapshot(trading_mode)
    # Try canonical snapshot first, fall back to legacy ledger
    canonical_snapshot = _load_canonical_model_snapshot(allow_empty=allow_empty)
    if canonical_snapshot.get("parse_error"):
        # Canonical not available; fall back to ledger
        model_snapshot = _load_model_snapshot(ledger_path, allow_empty=allow_empty)
        canonical_notes = list(canonical_snapshot.get("source_state_notes") or [])
        if canonical_notes:
            merged_notes = list(model_snapshot.get("source_state_notes") or [])
            merged_notes.extend(canonical_notes)
            model_snapshot["source_state_notes"] = merged_notes
        if model_snapshot.get("parse_error") and len(broker_snapshot.get("positions") or {}) > 0:
            # Bootstrap scenario: model is empty but broker has positions
            model_snapshot["bootstrap_hint"] = (
                "Model snapshot empty but broker has positions. "
                "Run with --bootstrap-model-ledger-from-broker to bootstrap from broker, or reset broker positions."
            )
    else:
        model_snapshot = canonical_snapshot
    diffs = compare_positions(
        broker_positions=broker_snapshot.get("positions") or {},
        model_positions=model_snapshot.get("positions") or {},
        max_qty_diff=max_qty_diff,
    )
    if model_snapshot.get("parse_error"):
        diffs.setdefault("errors", []).append(str(model_snapshot.get("parse_error")))
    if model_snapshot.get("bootstrap_hint"):
        diffs.setdefault("errors", []).append(str(model_snapshot.get("bootstrap_hint")))

    broker_cash = _coerce_float(broker_snapshot.get("cash"))
    model_cash = _coerce_float(model_snapshot.get("cash"))
    broker_equity = _coerce_float(broker_snapshot.get("equity"))
    model_equity = _coerce_float(model_snapshot.get("equity"))
    cash_delta = (
        float(broker_cash) - float(model_cash)
        if broker_cash is not None and model_cash is not None
        else None
    )
    equity_delta = (
        float(broker_equity) - float(model_equity)
        if broker_equity is not None and model_equity is not None
        else None
    )
    equity_base = model_equity if model_equity is not None else broker_equity

    # Compute breach flags locally so _classify_block_reason has them.
    effective_equity_tol = float(equity_tol_abs)
    if equity_base is not None:
        effective_equity_tol = max(float(equity_tol_abs), abs(float(equity_base)) * float(equity_tol_pct))
    cash_breach = cash_delta is not None and abs(float(cash_delta)) > float(cash_tol)
    equity_breach = equity_delta is not None and abs(float(equity_delta)) > effective_equity_tol

    verdict = verdict_from_diffs(
        diffs,
        cash_delta=cash_delta,
        equity_delta=equity_delta,
        equity_base=equity_base,
        cash_tol=cash_tol,
        equity_tol_abs=equity_tol_abs,
        equity_tol_pct=equity_tol_pct,
        strict=strict,
        hard_fail_on_equity_drift=equity_hard_fail,
    )
    block_reason = _classify_block_reason(
        diffs,
        cash_breach=cash_breach,
        equity_breach=equity_breach,
        model_parse_error=model_snapshot.get("parse_error"),
    ) if verdict != "PASS" else "none"

    positions_ok = not (
        diffs.get("missing_in_model")
        or diffs.get("missing_in_broker")
        or diffs.get("qty_mismatches")
        or diffs.get("errors")
    )
    payload = {
        "phase": stage,
        "stage": stage,
        "run_date": str(run_date),
        "trading_mode": str(trading_mode),
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "verdict": verdict,
        "block_reason": block_reason,
        "strict": bool(strict),
        "allow_empty": bool(allow_empty),
        "recon_summary": {
            "positions_ok": bool(positions_ok),
            "equity_drift_only": bool(equity_breach and positions_ok and not cash_breach),
            "equity_breach": bool(equity_breach),
            "cash_breach": bool(cash_breach),
            "equity_hard_fail_enabled": bool(equity_hard_fail),
        },
        "config": {
            "max_qty_diff": max_qty_diff,
            "cash_tol": cash_tol,
            "equity_tol_abs": equity_tol_abs,
            "equity_tol_pct": equity_tol_pct,
            "equity_hard_fail": bool(equity_hard_fail),
        },
        "broker_snapshot": broker_snapshot,
        "model_snapshot": model_snapshot,
        "sent_ledger": _sent_ledger_meta(sent_ledger_path),
        "cash_delta": cash_delta,
        "equity_delta": equity_delta,
        "diffs": diffs,
    }
    report_path = _write_report(phase=stage, run_date=run_date, payload=payload)
    logger.info(
        "[RECON][%s] verdict=%s block_reason=%s positions_ok=%s "
        "broker_equity=%s model_equity=%s equity_delta=%s "
        "broker_positions=%d model_positions=%d report=%s",
        stage.upper(),
        verdict,
        block_reason,
        positions_ok,
        broker_equity,
        model_equity,
        equity_delta,
        int(len(broker_snapshot.get("positions") or {})),
        int(len(model_snapshot.get("positions") or {})),
        report_path,
    )
    if verdict == "PASS":
        logger.info("[RECON][%s] PASS report=%s", stage.upper(), report_path)
    elif verdict == "WARN":
        logger.warning(
            "[RECON][%s] WARN block_reason=%s — proceeding to execution (equity drift only)",
            stage.upper(),
            block_reason,
        )
    else:
        logger.warning("[RECON][%s] FAIL block_reason=%s report=%s", stage.upper(), block_reason, report_path)
    return verdict, report_path, block_reason


def _write_recon_blocked_artifact(
    run_date: str,
    block_reason: str,
    recon_report_path: str,
) -> str:
    """Write a structured artifact recording that pre-trade reconcile blocked execution.

    This file lets operators distinguish "strategy decided not to trade" from
    "strategy wanted to trade but reconcile blocked it."  It is written before
    SystemExit(2) is raised so it survives the process exit.
    """
    base_dir = Path(str(os.getenv("RUN_OUTPUT_ROOT", "")).strip() or "outputs")
    broker_dir = base_dir / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)
    out_path = broker_dir / f"recon_execution_blocked_{run_date}.json"
    payload = {
        "status": "blocked_by_recon",
        "run_date": str(run_date),
        "block_reason": block_reason,
        "recon_report": recon_report_path,
        # Strategy did not execute so we cannot know intended order count.
        "orders_intended_count": None,
        "note": (
            "Pre-trade reconcile gate blocked order submission. "
            "Strategy signal was not evaluated; intended orders are unknown. "
            "See recon_report for full details."
        ),
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.warning(
        "[RECON] Execution blocked artifact written: %s  block_reason=%s",
        out_path,
        block_reason,
    )
    return str(out_path)


def _recommended_action_for_block_reason(block_reason: str) -> str:
    reason = str(block_reason or "").strip().lower()
    if reason == "stale_state":
        return (
            "Run bootstrap to refresh canonical snapshot from broker positions, then rerun pre-trade checks. "
            "If auto-bootstrap is desired, set AUTO_BOOTSTRAP_ON_RECON_FAIL=1."
        )
    if reason in {"positions_mismatch", "quantity_mismatch"}:
        return (
            "Review model vs broker positions and resolve drift before submitting orders. "
            "Use reconciliation report details to identify offending symbols."
        )
    if reason in {"broker_read_failure"}:
        return "Check broker connectivity/credentials and retry once broker reads are healthy."
    if reason in {"equity_only_drift", "cash_only_drift", "equity_cash_drift"}:
        return "Review cash/equity tolerances and snapshot freshness; reconcile drift policy if needed."
    return "Review reconciliation report details and resolve pre-trade gate failure before rerun."


def _write_preflight_failure_artifact(
    *,
    run_date: str,
    halt_stage: str,
    halt_reason: str,
    block_reason: str,
    recon_report_path: str,
    source_state_notes: list[str] | None = None,
) -> str:
    """Write a structured preflight halt artifact under run logs.

    This artifact is consumed by downstream reporting/dashboard tooling to
    distinguish an early gate halt from an execution-stage failure.
    """
    base_dir = Path(str(os.getenv("RUN_OUTPUT_ROOT", "")).strip() or "outputs")
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    out_path = logs_dir / "preflight_failure.json"
    payload = {
        "run_date": str(run_date),
        "halt_stage": str(halt_stage),
        "halt_reason": str(halt_reason),
        "block_reason": str(block_reason),
        "recon_report": str(recon_report_path),
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "recommended_action": _recommended_action_for_block_reason(block_reason),
        "source_state_notes": list(source_state_notes or []),
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.warning("[RECON] Preflight failure artifact written: %s", out_path)
    return str(out_path)


def _auto_bootstrap_enabled() -> bool:
    return _is_truthy(os.getenv("AUTO_BOOTSTRAP_ON_RECON_FAIL"), default=False)


def _attempt_auto_bootstrap_recovery(
    *,
    run_date: str,
    trading_mode: str,
    ledger_path: str,
    sent_ledger_path: str,
    allow_empty: bool,
    strict_pre: bool,
) -> tuple[bool, str, str, str]:
    """Try to recover from stale-state pre-trade fail by bootstrapping and retrying once.

    Returns (recovered, verdict, report_path, block_reason).
    """
    if not _auto_bootstrap_enabled():
        return False, "FAIL", "", "stale_state"
    if canonical_trading_mode(trading_mode, field_name="trading_mode") != "paper":
        return False, "FAIL", "", "stale_state"

    logger.warning("[RECON][AUTO_BOOTSTRAP] Attempting recovery from stale_state pre-trade failure")
    force_flat = _is_truthy(os.getenv("AUTO_BOOTSTRAP_FORCE_FLAT"), default=False)
    ok = bootstrap_model_ledger_from_broker(
        trading_mode=trading_mode,
        ledger_path=ledger_path,
        sent_ledger_path=sent_ledger_path,
        run_date=run_date,
        force=force_flat,
    )
    if not ok:
        logger.warning("[RECON][AUTO_BOOTSTRAP] Bootstrap attempt failed")
        return False, "FAIL", "", "stale_state"

    retry_verdict, retry_report_path, retry_block_reason = _reconcile(
        stage="pretrade",
        run_date=run_date,
        trading_mode=trading_mode,
        ledger_path=ledger_path,
        sent_ledger_path=sent_ledger_path,
        allow_empty=allow_empty,
        strict=strict_pre,
    )
    recovered = retry_verdict != "FAIL"
    if recovered:
        logger.warning(
            "[RECON][AUTO_BOOTSTRAP] Recovery succeeded verdict=%s report=%s",
            retry_verdict,
            retry_report_path,
        )
    else:
        logger.warning(
            "[RECON][AUTO_BOOTSTRAP] Recovery retry still failing block_reason=%s report=%s",
            retry_block_reason,
            retry_report_path,
        )
    return recovered, retry_verdict, retry_report_path, retry_block_reason


def pre_trade_reconcile_or_exit(
    run_date: str,
    trading_mode: str,
    ledger_path: str,
    sent_ledger_path: str,
    allow_empty: bool = False,
) -> None:
    """Run pre-trade reconcile; raise SystemExit(2) only on hard FAIL.

    A WARN verdict (equity/cash drift with matching positions) does NOT block
    execution — that is normal mark-to-market movement between snapshot time and
    run time.  Only FAIL (position mismatch, parse error, stale state, or
    explicit RECON_EQUITY_HARD_FAIL=1) raises SystemExit(2).
    """
    if not _is_truthy(os.getenv("RECON_ENABLE"), default=True):
        return
    strict_pre = _is_truthy(os.getenv("RECON_STRICT_PRE"), default=True)
    verdict, report_path, block_reason = _reconcile(
        stage="pretrade",
        run_date=run_date,
        trading_mode=trading_mode,
        ledger_path=ledger_path,
        sent_ledger_path=sent_ledger_path,
        allow_empty=allow_empty,
        strict=strict_pre,
    )
    if verdict == "FAIL" and block_reason == "stale_state":
        recovered, retry_verdict, retry_report_path, retry_block_reason = _attempt_auto_bootstrap_recovery(
            run_date=run_date,
            trading_mode=trading_mode,
            ledger_path=ledger_path,
            sent_ledger_path=sent_ledger_path,
            allow_empty=allow_empty,
            strict_pre=strict_pre,
        )
        if recovered:
            return
        if retry_report_path:
            report_path = retry_report_path
            verdict = retry_verdict
            block_reason = retry_block_reason

    if verdict == "FAIL":
        source_state_notes = []
        try:
            report_payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
            model_snapshot = report_payload.get("model_snapshot") if isinstance(report_payload, dict) else None
            if isinstance(model_snapshot, dict):
                source_state_notes = list(model_snapshot.get("source_state_notes") or [])
        except Exception:
            source_state_notes = []

        _write_recon_blocked_artifact(
            run_date=run_date,
            block_reason=block_reason,
            recon_report_path=report_path,
        )
        _write_preflight_failure_artifact(
            run_date=run_date,
            halt_stage="pretrade_reconciliation",
            halt_reason=f"pretrade_reconcile_failed:{block_reason}",
            block_reason=block_reason,
            recon_report_path=report_path,
            source_state_notes=source_state_notes,
        )
        raise SystemExit(2)


def post_trade_validate(
    run_date: str,
    trading_mode: str,
    ledger_path: str,
    sent_ledger_path: str,
) -> None:
    if not _is_truthy(os.getenv("RECON_ENABLE"), default=True):
        return
    strict_post = _is_truthy(os.getenv("RECON_STRICT_POST"), default=False)
    existing_report = _recon_out_path(run_date=run_date, phase="posttrade")
    if existing_report.exists():
        try:
            payload = json.loads(existing_report.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and "expected_positions" in payload and "actual_positions" in payload:
                verdict = str(payload.get("verdict") or "UNKNOWN").upper()
                logger.info("[RECON][POSTTRADE] using existing broker-authoritative report=%s verdict=%s", existing_report, verdict)
                if strict_post and verdict != "PASS":
                    raise RuntimeError(
                        f"Post-trade reconciliation failed strict mode verdict={verdict} report={existing_report}"
                    )
                return
        except Exception:
            pass
    verdict, report_path, _block_reason = _reconcile(
        stage="posttrade",
        run_date=run_date,
        trading_mode=trading_mode,
        ledger_path=ledger_path,
        sent_ledger_path=sent_ledger_path,
        allow_empty=False,
        strict=strict_post,
    )
    if strict_post and verdict != "PASS":
        raise RuntimeError(
            f"Post-trade reconciliation failed strict mode verdict={verdict} report={report_path}"
        )

# ============================================================
# Bootstrap and Ledger Initialization
# ============================================================

def ensure_sent_ledger_exists(sent_ledger_path: str) -> None:
    """Ensure sent ledger exists with headers (idempotent)."""
    path = Path(str(sent_ledger_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if (not path.exists()) or path.stat().st_size == 0:
        # Write header row only
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "run_id", "order_id", "ticker", "side"])
            writer.writeheader()
        logger.info("[RECON] Created sent ledger with headers: %s", path)


def bootstrap_model_ledger_from_broker(
    trading_mode: str,
    ledger_path: str,
    sent_ledger_path: str,
    run_date: str,
    force: bool = False,
) -> bool:
    """
    Bootstrap model ledger from current broker positions (paper-broker mode only).
    Should only be called once to initialize model snapshot from broker.

    Args:
        force: When True, bypass the empty-position safety guard and write a flat
               canonical snapshot even when the account has equity > $100 but 0
               positions (e.g. a freshly-reset paper account that is 100% cash).
               Use this flag only when you have manually verified the account is
               genuinely flat.  Without force=True the guard protects against
               silently overwriting the snapshot with an empty one caused by a
               transient API error.

    Returns True if bootstrap succeeded, False otherwise.
    """
    if canonical_trading_mode(trading_mode, field_name="trading_mode") != "paper":
        logger.error("[BOOTSTRAP] Bootstrap only supported in paper mode, got %s", trading_mode)
        return False

    try:
        broker_snapshot = _load_broker_snapshot(trading_mode)
        positions = broker_snapshot.get("positions") or {}
        cash = broker_snapshot.get("cash")
        equity = broker_snapshot.get("equity")

        if not positions:
            # Distinguish between a legitimately flat account and a silent API failure.
            # If equity > $100 but the position list is empty, the broker almost certainly
            # returned an error or a partial response.  Writing an empty canonical snapshot
            # in that scenario would cause the next run to start ordering from a blank slate
            # against an account that still holds real positions.
            if equity is not None and float(equity) > 100.0:
                if not force:
                    logger.error(
                        "[BOOTSTRAP] Broker reports 0 positions but equity=%.2f — "
                        "possible API error or partial response; refusing to write empty snapshot. "
                        "If the account is genuinely flat (e.g. freshly reset), re-run with "
                        "--force-bootstrap-flat to bypass this guard.",
                        float(equity),
                    )
                    return False
                logger.warning(
                    "[BOOTSTRAP] Broker reports 0 positions but equity=%.2f — "
                    "force=True; writing flat canonical snapshot as requested.",
                    float(equity),
                )
            else:
                logger.warning(
                    "[BOOTSTRAP] Broker has no positions and equity=%s; "
                    "bootstrapping an empty (flat-account) snapshot.",
                    equity,
                )

        # Write canonical model snapshot
        _write_canonical_model_snapshot(
            positions=positions,
            cash=cash,
            equity=equity,
            reason="bootstrap_from_broker",
        )
        
        # Ensure sent ledgers exist
        ensure_sent_ledger_exists(sent_ledger_path)
        ensure_sent_ledger_exists("outputs/orders_sent/orders_sent.csv")
        
        logger.info(
            "[BOOTSTRAP] Bootstrap complete: positions=%d cash=%s equity=%s",
            len(positions),
            cash,
            equity,
        )
        return True
    except Exception as e:
        logger.error("[BOOTSTRAP] Bootstrap failed: %s", e)
        return False


def refresh_canonical_snapshot_from_broker(
    trading_mode: str,
    run_date: str | None = None,
) -> bool:
    """
    Refresh canonical model snapshot from current broker positions.
    Should be called after successful order execution to align model state with broker reality.
    
    Args:
        trading_mode: The trading mode (e.g., "paper")
        run_date: Optional run date for logging
    
    Returns:
        True if refresh succeeded, False otherwise.
    """
    if canonical_trading_mode(trading_mode, field_name="trading_mode") != "paper":
        logger.warning(
            "[POSTTRADE] Canonical refresh only supported in paper mode, got %s",
            trading_mode,
        )
        return False
    
    try:
        broker_snapshot = _load_broker_snapshot(trading_mode)
        positions = broker_snapshot.get("positions") or {}
        cash = broker_snapshot.get("cash")
        equity = broker_snapshot.get("equity")
        
        # Write canonical model snapshot
        _write_canonical_model_snapshot(
            positions=positions,
            cash=cash,
            equity=equity,
            reason="posttrade_refresh_from_broker",
        )
        
        logger.info(
            "[POSTTRADE] Refreshed canonical snapshot from broker: positions=%d cash=%s equity=%s run_date=%s",
            len(positions),
            cash,
            equity,
            run_date or "n/a",
        )
        return True
    except Exception as e:
        logger.warning("[POSTTRADE] Failed to refresh canonical snapshot: %s", e)
        return False
