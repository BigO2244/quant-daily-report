from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ============================================================
# Canonical Model Snapshot Helpers
# ============================================================

def _canonical_model_snapshot_path() -> Path:
    """Return path to canonical model snapshot JSON."""
    snapshot_dir = Path("outputs/paper_state")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return snapshot_dir / "canonical_positions.json"


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
        return {
            "positions": {},
            "position_count": 0,
            "cash": None,
            "equity": None,
            "path": str(path),
            "path_exists": False,
            "parser": "canonical_json",
            "parse_error": "canonical_snapshot_missing",
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
            "reason": payload.get("reason") if isinstance(payload, dict) else None,
            "path": str(path),
            "path_exists": True,
            "parser": "canonical_json",
            "parse_error": parse_error,
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
) -> str:
    missing_model = list((diffs or {}).get("missing_in_model") or [])
    missing_broker = list((diffs or {}).get("missing_in_broker") or [])
    qty_mismatches = list((diffs or {}).get("qty_mismatches") or [])
    errors = list((diffs or {}).get("errors") or [])
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
    return "FAIL" if strict else "WARN"


def _load_broker_snapshot(trading_mode: str) -> dict[str, Any]:
    mode = str(trading_mode or "").strip().lower()
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

    if mode != "alpaca":
        return {
            "source": "mode_not_alpaca",
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


def _reconcile(
    *,
    stage: str,
    run_date: str,
    trading_mode: str,
    ledger_path: str,
    sent_ledger_path: str,
    allow_empty: bool,
    strict: bool,
) -> tuple[str, str]:
    max_qty_diff = _env_float("RECON_MAX_QTY_DIFF", 0.0)
    cash_tol = _env_float("RECON_CASH_TOL", 5.0)
    equity_tol_abs = _env_float("RECON_EQUITY_TOL_ABS", 10.0)
    equity_tol_pct = _env_float("RECON_EQUITY_TOL_PCT", 0.001)

    broker_snapshot = _load_broker_snapshot(trading_mode)
    # Try canonical snapshot first, fall back to legacy ledger
    canonical_snapshot = _load_canonical_model_snapshot(allow_empty=allow_empty)
    if canonical_snapshot.get("parse_error"):
        # Canonical not available; fall back to ledger
        model_snapshot = _load_model_snapshot(ledger_path, allow_empty=allow_empty)
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
    verdict = verdict_from_diffs(
        diffs,
        cash_delta=cash_delta,
        equity_delta=equity_delta,
        equity_base=equity_base,
        cash_tol=cash_tol,
        equity_tol_abs=equity_tol_abs,
        equity_tol_pct=equity_tol_pct,
        strict=strict,
    )
    payload = {
        "phase": stage,
        "stage": stage,
        "run_date": str(run_date),
        "trading_mode": str(trading_mode),
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "verdict": verdict,
        "strict": bool(strict),
        "allow_empty": bool(allow_empty),
        "config": {
            "max_qty_diff": max_qty_diff,
            "cash_tol": cash_tol,
            "equity_tol_abs": equity_tol_abs,
            "equity_tol_pct": equity_tol_pct,
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
        "[RECON][%s] verdict=%s errors=%d broker_positions=%d model_positions=%d model_path=%s report=%s",
        stage.upper(),
        verdict,
        len((diffs or {}).get("errors") or []),
        int(len(broker_snapshot.get("positions") or {})),
        int(len(model_snapshot.get("positions") or {})),
        str(model_snapshot.get("path") or "n/a"),
        report_path,
    )
    if verdict == "PASS":
        logger.info("[RECON][%s] PASS report=%s", stage.upper(), report_path)
    else:
        logger.warning("[RECON][%s] %s report=%s", stage.upper(), verdict, report_path)
    return verdict, report_path


def pre_trade_reconcile_or_exit(
    run_date: str,
    trading_mode: str,
    ledger_path: str,
    sent_ledger_path: str,
    allow_empty: bool = False,
) -> None:
    if not _is_truthy(os.getenv("RECON_ENABLE"), default=True):
        return
    strict_pre = _is_truthy(os.getenv("RECON_STRICT_PRE"), default=True)
    verdict, _ = _reconcile(
        stage="pretrade",
        run_date=run_date,
        trading_mode=trading_mode,
        ledger_path=ledger_path,
        sent_ledger_path=sent_ledger_path,
        allow_empty=allow_empty,
        strict=strict_pre,
    )
    if strict_pre and verdict != "PASS":
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
    verdict, report_path = _reconcile(
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
    Bootstrap model ledger from current broker positions (ALPACA only).
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
    if str(trading_mode or "").strip().lower() != "alpaca":
        logger.error("[BOOTSTRAP] Bootstrap only supported in alpaca mode, got %s", trading_mode)
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
        ensure_sent_ledger_exists("outputs/shadow_orders/orders_sent.csv")
        
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
        trading_mode: The trading mode (e.g., "alpaca")
        run_date: Optional run date for logging
    
    Returns:
        True if refresh succeeded, False otherwise.
    """
    if str(trading_mode or "").strip().lower() != "alpaca":
        logger.warning(
            "[POSTTRADE] Canonical refresh only supported in alpaca mode, got %s",
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
