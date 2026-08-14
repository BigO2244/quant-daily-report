from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.live_pilot_guardrails import (
    LIVE_PILOT_APPROVED_MAX_CAP_USD,
    normalize_live_pilot_limit_price,
)
from core.risk_controls import (
    RiskControls,
    load_sector_map,
    peak_equity_path,
    update_peak_equity_state,
)
from paper.paper_broker import fetch_open_prices_yfinance, load_targets
from paper.run_manager import safe_write_text

# The live-pilot capital cap tracks the account's portfolio value (resolved
# dynamically upstream in the cron lane / execution path). This constant is only a
# non-zero placeholder default for ad-hoc CLI invocations; the cron always passes an
# explicit ``--capital-cap`` equal to the resolved portfolio value.
DEFAULT_CAPITAL_CAP = LIVE_PILOT_APPROVED_MAX_CAP_USD
# Full rebalance: the buy blast-radius ceiling. The executor enforces the buy count
# from CAERUS_LIVE_PILOT_MAX_ORDERS; this is the plan-side default/echo.
DEFAULT_MAX_ORDERS = 50
DEFAULT_OUTPUT_DIR = Path("outputs/live_pilot/plans")
DEFAULT_PRECOMPUTE_ROOT = Path("outputs/precompute")
DEFAULT_SHADOW_ROOT = Path("outputs/shadow_candidates")
DEFAULT_STRATEGY_REGISTRY_PATH = Path("config/research/strategy_registry.json")
DECISION_INPUT_SCHEMA_VERSION = "caerus.decision_input.v1"

# Live-pilot peak-equity state is isolated from paper's state dir. Reusing paper's
# peak (built from paper's ~$10k equity) against the live account's small portfolio
# value would compute a spurious drawdown and trip the circuit breaker every run.
LIVE_PILOT_STATE_DIR = Path("outputs/live_pilot/state")

TARGET_PORTFOLIO_SCHEMA = "caerus.transition_target.v2"
PLAN_SCHEMA_VERSION = "live_pilot_plan_from_precompute.v2"

PriceFetcher = Callable[[Sequence[str], str], "pd.DataFrame"]


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> None:
    safe_write_text(path, text, allow_overwrite=True)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text(path, json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n")


def _write_immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Exclusive authority publication; an identical retry is idempotent."""

    serialized = json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != serialized:
            raise ValueError(f"immutable authority artifact conflict: {path}")
        return
    safe_write_text(path, serialized, allow_overwrite=False)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _governed_market_state_from_precompute(
    *, payload_path: Path, payload: Mapping[str, Any], trade_date: str
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Bind Risk to the dated regime source bar in the validated bundle.

    The exact-plan authorizer persists regime hysteresis across runs.  Its input
    must therefore identify the immutable precompute observation, rather than a
    mutable outer-plan alias or a synthetic identifier created at authorization
    time.  The daily snapshot is the canonical source of the composite regime.
    """

    snapshot_path = payload_path.with_name("daily_snapshot.json")
    if not snapshot_path.is_file():
        raise ValueError(f"governed daily_snapshot.json is missing: {snapshot_path}")
    snapshot = _read_json(snapshot_path)
    if not isinstance(snapshot, Mapping):
        raise ValueError("governed daily snapshot must be a JSON object")

    snapshot_asof = str(snapshot.get("asof") or "").strip()
    snapshot_date = snapshot_asof[:10]
    if snapshot_date != str(trade_date):
        raise ValueError(
            "governed daily snapshot date does not match execution trade date: "
            f"snapshot={snapshot_date or 'MISSING'} trade_date={trade_date}"
        )
    payload_trade_date = str(payload.get("trade_date") or "").strip()
    if payload_trade_date != str(trade_date):
        raise ValueError("planned execution payload trade_date is inconsistent")
    precompute_run_id = str(payload.get("run_id") or "").strip()
    if not precompute_run_id:
        raise ValueError("planned execution payload lacks stable run_id lineage")

    regime_summary = snapshot.get("regime_summary")
    if not isinstance(regime_summary, Mapping):
        raise ValueError("governed daily snapshot lacks regime_summary")
    observed_state = str(regime_summary.get("composite_regime") or "").strip()
    if observed_state.lower() in {"", "unknown", "n/a", "none"}:
        raise ValueError("governed daily snapshot lacks a usable composite regime")

    snapshot_hash = _file_sha256(snapshot_path)
    source_bar = {
        "schema_version": "caerus.market_state_source_bar.v1",
        "trade_date": str(trade_date),
        "source_asof": snapshot_asof,
        "source_artifact": snapshot_path.name,
        "source_artifact_sha256": snapshot_hash,
        "source_precompute_run_id": precompute_run_id,
        "observed_state": observed_state.upper(),
        "confidence": 1.0,
        "regime_summary": dict(regime_summary),
    }
    source_bar_hash = _canonical_sha256(source_bar)
    source_bar["market_state_id"] = (
        f"market:{trade_date}:daily_snapshot:{source_bar_hash}"
    )
    return source_bar, (str(snapshot_path), f"sha256:{snapshot_hash}")


def _canonical_strategy_id(value: object) -> str:
    raw = str(value or "").strip().lower()
    return {"orion": "caerus_orion", "polaris": "caerus_polaris"}.get(raw, raw)


def _load_sealed_paper_decision(
    *, payload_path: Path, trade_date: str, approved_sleeve: str
) -> tuple[Path, dict[str, Any], Any, Any, dict[str, Any]]:
    """Load the pre-open portfolio allocation; never re-select a sleeve source."""

    from authority.pipeline import decision_package_from_dict, evidence_package_from_dict
    from core.paper_target_authority import validate_sealed_paper_target_bundle

    bundle_dir = payload_path.parent
    repo_root = payload_path.resolve().parents[3]
    failures = validate_sealed_paper_target_bundle(
        bundle_dir=bundle_dir,
        trade_date=trade_date,
        repo_root=repo_root,
    )
    if failures:
        raise ValueError("sealed PAPER target validation failed: " + ",".join(failures[:5]))
    package_path = bundle_dir / "paper_target_package.json"
    package = _read_json(package_path)
    if not isinstance(package, Mapping):
        raise ValueError("sealed PAPER target package must be a JSON object")
    package_sleeve = _canonical_strategy_id(package.get("approved_sleeve"))
    requested_sleeve = _canonical_strategy_id(approved_sleeve)
    if requested_sleeve not in {package_sleeve, "caerus_orion"}:
        raise ValueError(
            "requested PAPER sleeve differs from the sealed allocator authority"
        )
    evidence_raw = package.get("evidence_package")
    decision_raw = package.get("decision_package")
    if not isinstance(evidence_raw, Mapping) or not isinstance(decision_raw, Mapping):
        raise ValueError("sealed PAPER target lacks Evidence and Decision packages")
    evidence = evidence_package_from_dict(evidence_raw)
    decision = decision_package_from_dict(decision_raw)
    source = dict(package.get("source_strategy_artifact") or {})
    source.update(
        {
            "effective_trade_date": trade_date,
            "target_attainment_tolerance": float(
                (package.get("target_attainment_policy") or {}).get(
                    "fixed_drift_tolerance", 0.02
                )
            ),
            "target_attainment_policy": dict(
                package.get("target_attainment_policy") or {}
            ),
            "approved_target_hash": decision.content_hash,
            "paper_target_package_path": str(package_path),
            "paper_target_package_sha256": _file_sha256(package_path),
            "session_id": package.get("session_id"),
            "session_content_hash": package.get("session_content_hash"),
            "allocation_id": package.get("allocation_id"),
            "allocation_content_hash": package.get("allocation_content_hash"),
            "source_session_manifest": dict(
                package.get("source_session_manifest") or {}
            ),
            "source_sleeve_decisions": dict(
                package.get("source_sleeve_decisions") or {}
            ),
            "source_portfolio_allocation": dict(
                package.get("source_portfolio_allocation") or {}
            ),
        }
    )
    return bundle_dir / "signals.json", source, evidence, decision, dict(package)


def _safe_float(value: object) -> float | None:
    try:
        numeric = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _safe_positive_float(value: object) -> float | None:
    numeric = _safe_float(value)
    return numeric if numeric is not None and numeric > 0 else None


def _clean_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _first_nonempty(row: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _is_supported_equity_symbol(symbol: str) -> bool:
    """Reject anything that is not a plain us-equity ticker (fail-closed).

    ``load_targets`` already rejects banned leveraged/inverse ETFs, so target names
    are strategy equities. This is a defensive belt-and-suspenders check so a crypto
    or malformed symbol can never slip into a live target row.
    """
    if not symbol or symbol == "CASH":
        return False
    if not symbol.replace(".", "").isalpha():
        return False
    if symbol.endswith("USD") and len(symbol) > 4:
        return False
    return True


def latest_precompute_payload_path(precompute_root: Path = DEFAULT_PRECOMPUTE_ROOT) -> Path:
    candidates = sorted(precompute_root.glob("*/planned_execution_payload.json"))
    if not candidates:
        raise FileNotFoundError(f"No planned_execution_payload.json found under {precompute_root}")
    return candidates[-1]


def precompute_payload_path_for_date(trade_date: str, precompute_root: Path = DEFAULT_PRECOMPUTE_ROOT) -> Path:
    path = precompute_root / str(trade_date) / "planned_execution_payload.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing planned execution payload: {path}")
    return path


def _resolve_signals_path(payload_path: Path, payload: Mapping[str, Any]) -> Path:
    """Locate the strategy target signals file for the LIVE lane.

    Preference (live-only; paper uses its own loader and always reads signals.json):
      1. ``signals.json`` beside the payload in the precompute bundle dir.
      2. an explicit ``execution_target_source`` pointer in the payload.
    """
    sibling = payload_path.parent / "signals.json"
    if sibling.exists():
        return sibling
    raw = str(payload.get("execution_target_source") or "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = payload_path.parent / candidate.name
        if candidate.exists():
            return candidate
    # Return the (missing) sibling so the caller fails closed with a clear path.
    return sibling


def _entry_prices_from_payload(payload: Mapping[str, Any]) -> dict[str, float]:
    """Reference prices for names that CHANGED in the precompute (delta names).

    The payload ``trades[]`` carry ``entry_price`` (the precompute's reference price)
    only for tickers that changed. Full-target names not present here are priced via
    the yfinance open fetch (the same source paper uses).
    """
    out: dict[str, float] = {}
    trades = payload.get("trades") or payload.get("orders") or payload.get("planned_trades") or []
    if not isinstance(trades, list):
        return out
    for trade in trades:
        if not isinstance(trade, Mapping):
            continue
        symbol = _clean_symbol(trade.get("ticker") or trade.get("symbol"))
        if not symbol:
            continue
        price = _safe_positive_float(
            _first_nonempty(trade, ("entry_price", "price", "limit_price", "normalized_limit_price"))
        )
        if price is not None and symbol not in out:
            out[symbol] = float(price)
    return out


def _yfinance_prices(
    symbols: Sequence[str],
    run_date: str,
    *,
    price_fetcher: PriceFetcher,
) -> dict[str, float]:
    if not symbols:
        return {}
    try:
        frame = price_fetcher(list(symbols), run_date)
    except Exception:
        # Fail closed: any fetch error leaves these names unpriced, which blocks the
        # plan below rather than silently dropping them from the rebalance.
        return {}
    out: dict[str, float] = {}
    if frame is None or getattr(frame, "empty", True):
        return out
    for _, row in frame.iterrows():
        symbol = _clean_symbol(row.get("ticker"))
        price = _safe_positive_float(row.get("open") or row.get("price"))
        if symbol and price is not None:
            out[symbol] = float(price)
    return out


def _hydrate_prices(
    symbols: Sequence[str],
    *,
    payload: Mapping[str, Any],
    run_date: str,
    price_fetcher: PriceFetcher,
) -> tuple[dict[str, float], dict[str, str], list[str]]:
    """Return (price_by_symbol, source_by_symbol, unpriced_symbols), fail-closed.

    Per name: payload ``entry_price`` where present, else the yfinance open for the
    remainder. Any symbol that still lacks a positive price is returned in
    ``unpriced_symbols`` so the caller blocks the plan (mirrors the
    ``live_pilot_capital_cap_unresolved`` fail-closed pattern) and the executor never
    silently drops it.
    """
    entry_prices = _entry_prices_from_payload(payload)
    price_by_symbol: dict[str, float] = {}
    source_by_symbol: dict[str, str] = {}
    need_fetch: list[str] = []
    for symbol in symbols:
        price = entry_prices.get(symbol)
        if price is not None and price > 0:
            price_by_symbol[symbol] = float(price)
            source_by_symbol[symbol] = "payload_entry_price"
        else:
            need_fetch.append(symbol)

    fetched = _yfinance_prices(need_fetch, run_date, price_fetcher=price_fetcher)
    for symbol in need_fetch:
        price = fetched.get(symbol)
        if price is not None and price > 0:
            price_by_symbol[symbol] = float(price)
            source_by_symbol[symbol] = "yfinance_open"

    unpriced = [s for s in symbols if s not in price_by_symbol]
    return price_by_symbol, source_by_symbol, unpriced


def _target_rows_from_weights(
    weights: pd.DataFrame,
    *,
    approved_sleeve: str,
    price_by_symbol: Mapping[str, float],
    source_by_symbol: Mapping[str, str],
    signal_sleeve_by_symbol: Mapping[str, str],
    sleeve_contributions_by_symbol: Mapping[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Full target portfolio rows (schema caerus.transition_target.v2).

    One row per risk-adjusted target name, each carrying a GUARANTEED positive price
    and the approved sleeve (with source-signal sleeve preserved as provenance). The
    executor's Transition Engine consumes these to rebalance the whole live account
    to target weights, sizing against the live snapshot and enforcing caps/max_orders.
    Ranked by target weight desc, then symbol, for stable priority.
    """
    rows: list[dict[str, Any]] = []
    for _, w in weights.iterrows():
        symbol = _clean_symbol(w.get("ticker"))
        price = _safe_positive_float(price_by_symbol.get(symbol))
        target_weight = _safe_float(w.get("target_weight"))
        if not symbol or price is None or target_weight is None:
            continue
        normalized_price = normalize_live_pilot_limit_price(float(price))
        row = {
                "symbol": symbol,
                "ticker": symbol,
                "target_weight": float(target_weight),
                "price": float(normalized_price),
                "reference_price": float(price),
                "price_source": source_by_symbol.get(symbol),
                "order_type": "market",
                "sleeve": approved_sleeve,
                "source_signal_sleeve": signal_sleeve_by_symbol.get(symbol) or None,
                "source_signal_target_weight": float(target_weight),
            }
        contributions = (sleeve_contributions_by_symbol or {}).get(symbol)
        if isinstance(contributions, list) and contributions:
            row["sleeve_contributions"] = [
                dict(item) for item in contributions if isinstance(item, Mapping)
            ]
        rows.append(row)
    rows.sort(key=lambda r: (-(float(r["target_weight"])), str(r.get("symbol") or "")))
    return rows


def build_live_pilot_plan(
    *,
    payload_path: Path,
    approved_sleeve: str,
    capital_cap: float = DEFAULT_CAPITAL_CAP,
    max_orders: int = DEFAULT_MAX_ORDERS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    allow_missing_sleeve: bool = False,
    allow_fractional: bool = False,
    price_fetcher: PriceFetcher | None = None,
    sector_map: Mapping[str, str] | None = None,
    state_dir: Path | None = None,
    lane: str | None = None,
    recovery_policy: str | None = None,
    recovery_policy_config: Path = Path("config/paper_recovery_policy.json"),
    factor_history_fetcher: Callable[[str, str], pd.DataFrame] | None = None,
    shadow_root: Path = DEFAULT_SHADOW_ROOT,
    strategy_registry_path: Path = DEFAULT_STRATEGY_REGISTRY_PATH,
) -> dict[str, Any]:
    """Build a FULL rebalance target plan for the live pilot.

    The plan is the strategy's risk-adjusted target weights over the ENTIRE target
    universe (from ``signals.json``), sized to the live portfolio value. It uses the
    SAME pipeline paper uses -- ``paper.paper_broker.load_targets`` then
    ``core.risk_controls.RiskControls.apply_to_targets`` fed the LIVE equity -- so
    there is one implementation and no drift. The executor rebalances the live
    holdings to this target through the shared core, selling over-weight / removed
    names and buying under-weight ones, honouring the safety gates.
    """
    if not str(approved_sleeve or "").strip():
        raise ValueError("approved_sleeve is required")
    if float(capital_cap) <= 0:
        # No fixed program ceiling: the cap tracks the account's portfolio value
        # (resolved upstream in the cron lane / execution path). Must still be positive.
        raise ValueError("capital_cap must be > 0")
    if int(max_orders) <= 0:
        raise ValueError("max_orders must be > 0")

    price_fetcher = price_fetcher or fetch_open_prices_yfinance

    payload = _read_json(payload_path)
    if not isinstance(payload, Mapping):
        raise ValueError("planned execution payload must be a JSON object")
    trade_date = str(payload.get("trade_date") or payload_path.parent.name)

    signals_path = _resolve_signals_path(payload_path, payload)
    decision_source_artifact: dict[str, Any] | None = None
    sealed_evidence = None
    sealed_decision = None
    sealed_target_package: dict[str, Any] | None = None
    lane_id = str(lane or "").strip().lower()
    governed_market_state: dict[str, Any] | None = None
    market_state_source_refs: tuple[str, ...] = ()
    if lane_id == "paper":
        try:
            governed_market_state, market_state_source_refs = (
                _governed_market_state_from_precompute(
                    payload_path=payload_path,
                    payload=payload,
                    trade_date=trade_date,
                )
            )
        except Exception as exc:
            return _emit_blocked_plan(
                output_dir=output_dir,
                trade_date=trade_date,
                approved_sleeve=approved_sleeve,
                lane=lane,
                capital_cap=float(capital_cap),
                max_orders=int(max_orders),
                allow_fractional=bool(allow_fractional),
                reason_code="paper_governed_market_state_invalid",
                diagnostics={"error": str(exc), "payload_path": str(payload_path)},
            )
    if lane_id == "paper" and recovery_policy:
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            lane=lane,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="paper_downstream_target_substitution_disabled",
            diagnostics={"recovery_policy": recovery_policy},
        )
    if lane_id == "paper":
        try:
            (
                signals_path,
                decision_source_artifact,
                sealed_evidence,
                sealed_decision,
                sealed_target_package,
            ) = _load_sealed_paper_decision(
                payload_path=payload_path,
                trade_date=trade_date,
                approved_sleeve=approved_sleeve,
            )
            approved_sleeve = str(
                sealed_target_package.get("approved_sleeve") or approved_sleeve
            )
        except Exception as exc:
            return _emit_blocked_plan(
                output_dir=output_dir,
                trade_date=trade_date,
                approved_sleeve=approved_sleeve,
                lane=lane,
                capital_cap=float(capital_cap),
                max_orders=int(max_orders),
                allow_fractional=bool(allow_fractional),
                reason_code="paper_sealed_target_invalid",
                diagnostics={"error": str(exc), "bundle_dir": str(payload_path.parent)},
            )
    elif not signals_path.exists():
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            lane=lane,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="live_pilot_signals_source_missing",
            diagnostics={"signals_path": str(signals_path)},
        )

    recovery_policy_meta: dict[str, Any] | None = None
    if recovery_policy:
        if str(lane or "").strip().lower() != "paper":
            return _emit_blocked_plan(
                output_dir=output_dir,
                trade_date=trade_date,
                approved_sleeve=approved_sleeve,
                lane=lane,
                capital_cap=float(capital_cap),
                max_orders=int(max_orders),
                allow_fractional=bool(allow_fractional),
                reason_code="paper_recovery_policy_wrong_lane",
                diagnostics={
                    "lane": lane,
                    "recovery_policy": recovery_policy,
                },
            )
        try:
            from core.paper_recovery_policy import (
                derive_weekly_rotation_guard_payload,
                fetch_factor_closes_yfinance,
                validate_recovery_config,
            )

            config = _read_json(recovery_policy_config)
            config_validation = validate_recovery_config(
                config,
                requested_policy=recovery_policy,
            )
            if config_validation.get("status") != "PASS":
                raise ValueError(
                    "recovery config blocked: "
                    + ",".join(config_validation.get("reason_codes") or [])
                )
            derived_payload, recovery_policy_meta = (
                derive_weekly_rotation_guard_payload(
                    precompute_root=payload_path.parent.parent,
                    trade_date=trade_date,
                    factor_fetcher=(
                        factor_history_fetcher
                        or fetch_factor_closes_yfinance
                    ),
                )
            )
            recovery_policy_meta["config_path"] = str(recovery_policy_config)
            recovery_policy_meta["config_validation"] = config_validation
            recovery_dir = output_dir.parent / "recovery_targets"
            recovery_dir.mkdir(parents=True, exist_ok=True)
            derived_path = (
                recovery_dir / f"paper_recovery_targets_{trade_date}.json"
            )
            _write_json(derived_path, derived_payload)
            signals_path = derived_path
        except Exception as exc:
            return _emit_blocked_plan(
                output_dir=output_dir,
                trade_date=trade_date,
                approved_sleeve=approved_sleeve,
                lane=lane,
                capital_cap=float(capital_cap),
                max_orders=int(max_orders),
                allow_fractional=bool(allow_fractional),
                reason_code="paper_recovery_policy_build_failed",
                diagnostics={
                    "recovery_policy": recovery_policy,
                    "config_path": str(recovery_policy_config),
                    "error": str(exc),
                },
            )

    identity_validation: dict[str, Any] | None = None
    if lane is not None:
        from core.strategy_identity import validate_lane_strategy_identity

        signals_payload = _read_json(signals_path)
        identity = (
            signals_payload.get("strategy_identity")
            if isinstance(signals_payload, Mapping)
            and isinstance(signals_payload.get("strategy_identity"), Mapping)
            else {}
        )
        identity_validation = validate_lane_strategy_identity(
            identity=identity,
            approved_strategy=approved_sleeve,
            lane=lane,
        )
        if identity_validation.get("status") != "PASS":
            return _emit_blocked_plan(
                output_dir=output_dir,
                trade_date=trade_date,
                approved_sleeve=approved_sleeve,
                lane=lane,
                capital_cap=float(capital_cap),
                max_orders=int(max_orders),
                allow_fractional=bool(allow_fractional),
                reason_code="strategy_identity_mismatch",
                diagnostics={"strategy_identity_validation": identity_validation},
            )

    # 1) Strategy target for the FULL universe (same loader paper uses).
    payload_cash_default = _safe_float(payload.get("cash_target_weight")) or 0.0
    targets, cash_target_weight, _snapshot_date, _asof = load_targets(
        str(signals_path),
        cash_target_weight_default=float(payload_cash_default),
    )
    signal_sleeve_by_symbol = {
        _clean_symbol(row.get("ticker")): str(row.get("sleeve") or "").strip()
        for _, row in targets.iterrows()
    }
    sleeve_contributions_by_symbol: dict[str, list[dict[str, Any]]] = {}
    if lane_id == "paper" and isinstance(sealed_target_package, Mapping):
        for row in sealed_target_package.get("target_rows") or []:
            if not isinstance(row, Mapping):
                continue
            symbol = _clean_symbol(row.get("symbol") or row.get("ticker"))
            contributions = row.get("sleeve_contributions")
            if symbol and isinstance(contributions, list):
                sleeve_contributions_by_symbol[symbol] = [
                    dict(item) for item in contributions if isinstance(item, Mapping)
                ]

    # Live remains fail-closed when any target's layer cannot be resolved. This
    # is classification-only; paper/live continue to share the same target and
    # risk-control path, and no per-layer sector-cap behavior is introduced.
    from core.sleeve_layers import unresolved_sleeve_labels

    unresolved_layers: dict[str, list[str]] = {}
    # The sealed PAPER allocator target uses governed strategy sleeve IDs and
    # carries the full per-symbol causal contribution list.  Functional alpha /
    # protection / diversifier labels are a legacy live-pilot classification and
    # are not an execution authority for the portfolio allocator.
    if lane_id != "paper":
        for symbol, label in signal_sleeve_by_symbol.items():
            unresolved = unresolved_sleeve_labels(label)
            if unresolved:
                unresolved_layers[symbol] = unresolved
    if unresolved_layers:
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            lane=lane,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="live_pilot_layer_unresolved",
            diagnostics={"unresolved_layer_labels": unresolved_layers},
        )

    # 2) Risk controls at the LIVE portfolio size. current_equity is the resolved
    #    dynamic cap (== the live portfolio value; an optional operator CAPITAL_CAP
    #    only tightens it -- that IS "the portfolio size for Live"). Peak-equity is
    #    tracked in a live-scoped state dir, isolated from paper's.
    live_equity = float(capital_cap)
    peak_path = peak_equity_path(state_dir=Path(state_dir) if state_dir else LIVE_PILOT_STATE_DIR)
    peak_state = update_peak_equity_state(
        current_equity=live_equity,
        trade_date=trade_date,
        source="live_pilot_dynamic_cap",
        path=peak_path,
    )
    resolved_sector_map = dict(sector_map) if sector_map is not None else load_sector_map()
    controls = RiskControls()
    result = controls.apply_to_targets(
        targets,
        sector_map=resolved_sector_map,
        cash_target_weight=cash_target_weight,
        current_equity=live_equity,
        peak_equity=_safe_float(peak_state.get("peak_equity")),
    )
    adjusted_weights = result.weights

    # 3) Sanity: every target name must be a plain equity (fail-closed).
    bad_symbols = [
        _clean_symbol(w.get("ticker"))
        for _, w in adjusted_weights.iterrows()
        if not _is_supported_equity_symbol(_clean_symbol(w.get("ticker")))
    ]
    if bad_symbols:
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            lane=lane,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="live_pilot_target_unsupported_symbol",
            diagnostics={"unsupported_symbols": sorted(set(bad_symbols))},
            risk_controls=result.to_artifact(),
        )

    target_symbols = [_clean_symbol(w.get("ticker")) for _, w in adjusted_weights.iterrows()]

    # 4) Price EVERY target name; fail closed if any name ends up unpriced.
    price_by_symbol, source_by_symbol, unpriced = _hydrate_prices(
        target_symbols,
        payload=payload,
        run_date=trade_date,
        price_fetcher=price_fetcher,
    )
    if unpriced:
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            lane=lane,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="live_pilot_target_unpriced",
            diagnostics={"unpriced_targets": sorted(set(unpriced))},
            risk_controls=result.to_artifact(),
        )

    target_portfolio = _target_rows_from_weights(
        adjusted_weights,
        approved_sleeve=approved_sleeve,
        price_by_symbol=price_by_symbol,
        source_by_symbol=source_by_symbol,
        signal_sleeve_by_symbol=signal_sleeve_by_symbol,
        sleeve_contributions_by_symbol=sleeve_contributions_by_symbol,
    )
    if not target_portfolio:
        return _emit_blocked_plan(
            output_dir=output_dir,
            trade_date=trade_date,
            approved_sleeve=approved_sleeve,
            lane=lane,
            capital_cap=float(capital_cap),
            max_orders=int(max_orders),
            allow_fractional=bool(allow_fractional),
            reason_code="live_pilot_no_target_names",
            diagnostics={"cash_target_weight": float(result.cash_target_weight)},
            risk_controls=result.to_artifact(),
        )

    # Canonical authority chain. Alpha/precompute supplies evidence; Decision
    # owns the original target; Risk may only reduce it; Trader receives the
    # hash-verified risk-approved target package.
    from authority.contracts import (
        build_decision_package,
        build_evidence_package,
        build_risk_package,
    )
    from authority.pipeline import execution_package_from_risk

    authority_lane_id = str(lane or "manual")
    authority_stem = f"{trade_date}:{authority_lane_id}:{approved_sleeve}"
    sleeve_evaluations_path = payload_path.with_name("sleeve_evaluations.json")
    if lane_id == "paper" and not sleeve_evaluations_path.is_file():
        raise ValueError("governed PAPER sleeve_evaluations.json is missing")
    sleeve_authority_refs = (
        (str(sleeve_evaluations_path), f"sha256:{_file_sha256(sleeve_evaluations_path)}")
        if sleeve_evaluations_path.is_file()
        else ()
    )
    projected_decision_target_rows = [
        {
            "symbol": _clean_symbol(row.get("ticker")),
            "ticker": _clean_symbol(row.get("ticker")),
            "sleeve": str(row.get("sleeve") or ""),
            "target_weight": float(row.get("target_weight") or 0.0),
        }
        for _, row in targets.iterrows()
    ]
    if lane_id == "paper":
        if sealed_evidence is None or sealed_decision is None or sealed_target_package is None:
            raise ValueError("sealed PAPER Evidence and Decision authority is unresolved")
        evidence = sealed_evidence
        decision = sealed_decision
        sealed_decision_rows = decision.to_dict()["target_rows"]
        sealed_projection = [
            {
                "symbol": str(row.get("symbol") or ""),
                "ticker": str(row.get("ticker") or row.get("symbol") or ""),
                "sleeve": str(row.get("sleeve") or ""),
                "target_weight": float(row.get("target_weight") or 0.0),
            }
            for row in sealed_decision_rows
        ]
        if projected_decision_target_rows != sealed_projection:
            raise ValueError("09:35 target projection diverges from sealed PAPER Decision")
        if abs(float(cash_target_weight) - float(decision.target_cash_weight)) > 1e-12:
            raise ValueError("09:35 cash target diverges from sealed PAPER Decision")
    else:
        decision_target_rows = projected_decision_target_rows
        evidence = build_evidence_package(
            package_id=f"evidence:{authority_stem}",
            trade_date=trade_date,
            source_refs=(
                str(signals_path),
                str(payload_path),
                *sleeve_authority_refs,
                *(
                    (f"sha256:{decision_source_artifact['sha256']}",)
                    if decision_source_artifact
                    else ()
                ),
            ),
            observations=decision_target_rows,
        )
        decision = build_decision_package(
            package_id=f"decision:{authority_stem}",
            trade_date=trade_date,
            evidence=evidence,
            target_rows=decision_target_rows,
            target_cash_weight=float(cash_target_weight),
            source_refs=(
                str(signals_path),
                str(payload_path),
                *sleeve_authority_refs,
                *(
                    (f"sha256:{decision_source_artifact['sha256']}",)
                    if decision_source_artifact
                    else ()
                ),
            ),
        )
    risk_constraints = dict(result.to_artifact())
    if lane_id == "paper":
        if not governed_market_state:
            raise ValueError("governed PAPER market state is unresolved")
        risk_constraints["market_state"] = governed_market_state
        target_policy = dict(
            (decision_source_artifact or {}).get("target_attainment_policy") or {}
        )
        if not target_policy:
            raise ValueError("governed PAPER target-attainment policy missing")
        risk_constraints["target_attainment_policy"] = target_policy
    risk = build_risk_package(
        package_id=f"risk:{authority_stem}",
        decision=decision,
        approved_target_rows=target_portfolio,
        approved_cash_weight=float(result.cash_target_weight),
        constraints=risk_constraints,
        source_refs=(f"decision:{decision.package_id}", *market_state_source_refs),
    )
    execution = execution_package_from_risk(risk)
    # Authority packages are decision/content-addressed. A later same-date
    # build may advance the mutable latest plan, but it can never overwrite the
    # Evidence→Decision→Risk→Execution bytes already bound into an exact plan.
    authority_dir = (
        output_dir
        / "authority"
        / trade_date
        / f"execution-{execution.content_hash}"
    )
    authority_paths = {
        "evidence": authority_dir / "evidence_package.json",
        "decision": authority_dir / "decision_package.json",
        "risk": authority_dir / "risk_package.json",
        "execution": authority_dir / "execution_package.json",
    }
    authority_payloads = {
        "evidence": evidence.to_dict(),
        "decision": decision.to_dict(),
        "risk": risk.to_dict(),
        "execution": execution.to_dict(),
    }
    authority_dir.mkdir(parents=True, exist_ok=True)
    for name, path in authority_paths.items():
        _write_immutable_json(path, authority_payloads[name])

    plan = _plan_scaffold(
        trade_date=trade_date,
        approved_sleeve=approved_sleeve,
        lane=lane,
        capital_cap=float(capital_cap),
        max_orders=int(max_orders),
        allow_missing_sleeve=bool(allow_missing_sleeve),
        allow_fractional=bool(allow_fractional),
        output_dir=output_dir,
        payload_path=payload_path,
        signals_path=signals_path,
    )
    plan.update(
        {
            "status": "READY_FOR_MANUAL_APPROVAL",
            "reason_code": "full_rebalance_target_ready",
            "cash_target_weight": float(result.cash_target_weight),
            "target_portfolio_schema": TARGET_PORTFOLIO_SCHEMA,
            "target_portfolio": target_portfolio,
            "target_name_count": len(target_portfolio),
            "risk_controls": {
                key: value
                for key, value in risk_constraints.items()
                if key != "target_attainment_policy"
            },
            "price_sources": {row["symbol"]: row["price_source"] for row in target_portfolio},
            "strategy_identity_validation": identity_validation
            or {
                "status": "UNVERIFIED",
                "reason_code": "lane_not_supplied_to_builder",
            },
            "paper_recovery_policy": recovery_policy_meta,
            "decision_source_artifact": decision_source_artifact,
            "target_attainment_tolerance": float(
                (decision_source_artifact or {}).get("target_attainment_tolerance")
                or 0.02
            ),
            "target_attainment_policy": (
                dict((decision_source_artifact or {}).get("target_attainment_policy") or {})
                if lane_id == "paper"
                else None
            ),
            "approved_execution_package": execution.to_dict(),
            "approved_target_hash": decision.content_hash,
            "source_paper_target_package": (
                str((decision_source_artifact or {}).get("paper_target_package_path") or "")
                if lane_id == "paper"
                else None
            ),
            "source_paper_target_package_sha256": (
                str((decision_source_artifact or {}).get("paper_target_package_sha256") or "")
                if lane_id == "paper"
                else None
            ),
            "source_session_manifest": (
                str(
                    ((decision_source_artifact or {}).get("source_session_manifest") or {}).get(
                        "path"
                    )
                    or ""
                )
                if lane_id == "paper"
                else None
            ),
            "source_session_manifest_sha256": (
                str(
                    ((decision_source_artifact or {}).get("source_session_manifest") or {}).get(
                        "sha256"
                    )
                    or ""
                )
                if lane_id == "paper"
                else None
            ),
            "source_sleeve_decisions": (
                str(
                    ((decision_source_artifact or {}).get("source_sleeve_decisions") or {}).get(
                        "path"
                    )
                    or ""
                )
                if lane_id == "paper"
                else None
            ),
            "source_sleeve_decisions_sha256": (
                str(
                    ((decision_source_artifact or {}).get("source_sleeve_decisions") or {}).get(
                        "sha256"
                    )
                    or ""
                )
                if lane_id == "paper"
                else None
            ),
            "source_portfolio_allocation": (
                str(
                    ((decision_source_artifact or {}).get("source_portfolio_allocation") or {}).get(
                        "path"
                    )
                    or ""
                )
                if lane_id == "paper"
                else None
            ),
            "source_portfolio_allocation_sha256": (
                str(
                    ((decision_source_artifact or {}).get("source_portfolio_allocation") or {}).get(
                        "sha256"
                    )
                    or ""
                )
                if lane_id == "paper"
                else None
            ),
            "session_id": (
                (decision_source_artifact or {}).get("session_id")
                if lane_id == "paper"
                else None
            ),
            "allocation_id": (
                (decision_source_artifact or {}).get("allocation_id")
                if lane_id == "paper"
                else None
            ),
            "source_sleeve_evaluations": str(sleeve_evaluations_path),
            "source_sleeve_evaluations_sha256": (
                _file_sha256(sleeve_evaluations_path)
                if sleeve_evaluations_path.is_file()
                else None
            ),
            "authority_package_paths": {
                name: str(path) for name, path in authority_paths.items()
            },
        }
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / f"live_pilot_plan_{trade_date}.json"
    md_path = output_dir / f"live_pilot_plan_{trade_date}.md"
    _write_json(plan_path, plan)
    _write_text(md_path, render_markdown(plan, json_path=plan_path))
    plan["json_path"] = str(plan_path)
    plan["markdown_path"] = str(md_path)
    return plan


def _plan_scaffold(
    *,
    trade_date: str,
    approved_sleeve: str,
    lane: str | None,
    capital_cap: float,
    max_orders: int,
    allow_missing_sleeve: bool,
    allow_fractional: bool,
    output_dir: Path,
    payload_path: Path,
    signals_path: Path,
) -> dict[str, Any]:
    plan_path = output_dir / f"live_pilot_plan_{trade_date}.json"
    lane_id = str(lane or "live_pilot").strip().lower()
    paper_lane = lane_id == "paper"
    environment_prefix = (
        "MODE=paper TRADING_MODE=paper WORKFLOW_KIND=paper ALPACA_PAPER=1 "
        "ALPACA_BASE_URL=https://paper-api.alpaca.markets "
        if paper_lane
        else "TRADING_MODE=live_pilot ALPACA_PAPER=0 ALPACA_BASE_URL=https://api.alpaca.markets "
    )
    approved_package_gate = (
        "CAERUS_REQUIRE_APPROVED_EXECUTION_PACKAGE=1 " if paper_lane else ""
    )
    dry_run_command = (
        environment_prefix
        + f"CAERUS_LIVE_PILOT_APPROVED=1 CAERUS_LIVE_PILOT_CAPITAL_CAP={float(capital_cap):g} "
        f"CAERUS_LIVE_PILOT_SLEEVE_ID={approved_sleeve} "
        "CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH=<SHA256_ACCOUNT_ID> "
        f"CAERUS_LIVE_PILOT_MAX_ORDERS={int(max_orders)} "
        f"CAERUS_LIVE_PILOT_ALLOW_MISSING_SLEEVE={1 if allow_missing_sleeve else 0} "
        f"CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL={1 if allow_fractional else 0} "
        f"{approved_package_gate}CAERUS_LIVE_PILOT_DRY_RUN=1 "
        f".venv/bin/python3 scripts/live_pilot_execute.py --plan {plan_path.as_posix()}"
    )
    submit_command = dry_run_command.replace(
        "CAERUS_LIVE_PILOT_DRY_RUN=1", "CAERUS_LIVE_PILOT_DRY_RUN=0"
    )
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "execution_lane": lane_id,
        "generated_at": _now_utc(),
        "source_precompute_payload": str(payload_path),
        "source_signals": str(signals_path),
        "trade_date": trade_date,
        "approved_sleeve": approved_sleeve,
        "capital_cap": float(capital_cap),
        "max_orders": int(max_orders),
        "allow_missing_sleeve": bool(allow_missing_sleeve),
        "allow_fractional": bool(allow_fractional),
        "order_policy": {
            "scope": (
                "governed PAPER full rebalance"
                if paper_lane
                else "FR-104 LIVE_PILOT full rebalance"
            ),
            "model": "rebalance_to_risk_adjusted_target_weight",
            "order_type": "market",
            "time_in_force": "day",
            "normal_market_hours_only": True,
            "buys_capped_by": "CAERUS_LIVE_PILOT_MAX_ORDERS",
            "sells_gated_by": "CAERUS_LIVE_PILOT_SELLS_ENABLED + sell whitelist/wildcard",
            "sizing": "live portfolio value via execution core (same engine as paper)",
            "paper_or_production_impact": (
                "PAPER only; live capital prohibited" if paper_lane else "none"
            ),
        },
        "required_dry_run_command": dry_run_command,
        # Retained for v2 schema compatibility. On a PAPER plan this is the
        # PAPER submit command and never references the live broker endpoint.
        "required_live_command": submit_command,
        "required_submit_command": submit_command,
        "operator_confirmation": {
            "approved_sleeve": approved_sleeve,
            "capital_cap": float(capital_cap),
            "max_orders": int(max_orders),
            "allow_missing_sleeve": bool(allow_missing_sleeve),
            "allow_fractional": bool(allow_fractional),
            "required_manual_review": True,
            "orders_submitted": 0,
        },
    }


def _emit_blocked_plan(
    *,
    output_dir: Path,
    trade_date: str,
    approved_sleeve: str,
    lane: str | None = None,
    capital_cap: float,
    max_orders: int,
    allow_fractional: bool,
    reason_code: str,
    diagnostics: Mapping[str, Any],
    risk_controls: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    plan = _plan_scaffold(
        trade_date=trade_date,
        approved_sleeve=approved_sleeve,
        lane=lane,
        capital_cap=float(capital_cap),
        max_orders=int(max_orders),
        allow_missing_sleeve=False,
        allow_fractional=bool(allow_fractional),
        output_dir=output_dir,
        payload_path=Path(""),
        signals_path=Path(""),
    )
    plan.update(
        {
            "status": "BLOCKED",
            "reason_code": reason_code,
            "cash_target_weight": 0.0,
            "target_portfolio_schema": TARGET_PORTFOLIO_SCHEMA,
            "target_portfolio": [],
            "target_name_count": 0,
            "block_diagnostics": dict(diagnostics),
            "risk_controls": dict(risk_controls) if risk_controls is not None else None,
        }
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / f"live_pilot_plan_{trade_date}.json"
    md_path = output_dir / f"live_pilot_plan_{trade_date}.md"
    _write_json(plan_path, plan)
    _write_text(md_path, render_markdown(plan, json_path=plan_path))
    plan["json_path"] = str(plan_path)
    plan["markdown_path"] = str(md_path)
    return plan


def render_markdown(plan: Mapping[str, Any], *, json_path: Path) -> str:
    target_portfolio = plan.get("target_portfolio") or []
    lines = [
        "# LIVE_PILOT Full Rebalance Plan From Precompute",
        "",
        f"Status: `{plan.get('status')}`",
        f"Reason: `{plan.get('reason_code')}`",
        f"Trade Date: `{plan.get('trade_date')}`",
        f"Approved Sleeve: `{plan.get('approved_sleeve')}`",
        f"Capital Cap (portfolio value): `${float(plan.get('capital_cap') or 0.0):.2f}`",
        f"Cash Target Weight: `{float(plan.get('cash_target_weight') or 0.0):.4f}`",
        f"Max Buy Orders: `{plan.get('max_orders')}`",
        f"Target Names: `{plan.get('target_name_count')}`",
        f"JSON Plan: `{json_path.as_posix()}`",
        "",
        "## Target Portfolio (risk-adjusted weights)",
        "",
    ]
    if target_portfolio:
        lines.append("| Symbol | Target Weight | Ref Price | Price Source |")
        lines.append("| --- | ---: | ---: | --- |")
        for row in target_portfolio:
            lines.append(
                f"| `{row.get('symbol')}` | {float(row.get('target_weight') or 0.0):.4f} | "
                f"{float(row.get('price') or 0.0):.2f} | {row.get('price_source')} |"
            )
    else:
        lines.append("No target names (plan blocked).")
        diagnostics = plan.get("block_diagnostics") or {}
        if diagnostics:
            lines.extend(["", "## Block Diagnostics", "", "```json", json.dumps(dict(diagnostics), indent=2, sort_keys=True), "```"])
    lines.extend(
        [
            "",
            "## Required Dry-Run Command",
            "",
            "```bash",
            str(plan.get("required_dry_run_command") or ""),
            "```",
            "",
            "## Required Live Command - Not Executed",
            "",
            "```bash",
            str(plan.get("required_live_command") or ""),
            "```",
            "",
            "## Operator Confirmation",
            "",
            "- Confirm the sleeve, cap (portfolio value), target weights, and dry-run artifact before any live attempt.",
            "- Sells are gated by the fail-closed sells master flag + whitelist/wildcard; buys are capped by max_orders.",
            "- This builder does not submit orders.",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a full-rebalance LIVE_PILOT plan from precompute output")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--payload-path", default=None, help="Explicit planned_execution_payload.json path")
    source.add_argument("--trade-date", default=None, help="Trade date under outputs/precompute/<DATE>")
    parser.add_argument("--precompute-root", default=str(DEFAULT_PRECOMPUTE_ROOT))
    parser.add_argument("--shadow-root", default=str(DEFAULT_SHADOW_ROOT))
    parser.add_argument(
        "--strategy-registry-path",
        default=str(DEFAULT_STRATEGY_REGISTRY_PATH),
    )
    parser.add_argument("--approved-sleeve", required=True)
    parser.add_argument(
        "--lane",
        choices=("paper", "live_pilot"),
        default=None,
        help="Execution lane; production callers must set this for fail-closed strategy identity validation.",
    )
    parser.add_argument(
        "--recovery-policy",
        default=None,
        help="Governed paper-only recovery policy id; rejected on non-paper lanes.",
    )
    parser.add_argument(
        "--recovery-policy-config",
        default="config/paper_recovery_policy.json",
    )
    parser.add_argument("--capital-cap", type=float, default=DEFAULT_CAPITAL_CAP)
    parser.add_argument("--max-orders", type=int, default=DEFAULT_MAX_ORDERS)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--state-dir",
        default=None,
        help=(
            "Peak-equity state dir for this lane. Default (unset) keeps the live-scoped "
            f"{LIVE_PILOT_STATE_DIR} — live behavior unchanged. The unified PAPER lane "
            "passes its own isolated dir (e.g. outputs/paper_lane/state) so paper and "
            "live peak-equity/drawdown state can never cross-contaminate."
        ),
    )
    parser.add_argument(
        "--allow-missing-sleeve",
        action="store_true",
        default=os.getenv("CAERUS_LIVE_PILOT_ALLOW_MISSING_SLEEVE", "").strip().lower() in {"1", "true", "yes", "y", "on"},
        help="Accepted for CLI compatibility; the full-rebalance builder stamps the approved sleeve on all target rows.",
    )
    parser.add_argument(
        "--allow-fractional",
        action="store_true",
        default=os.getenv("CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL", "").strip().lower() in {"1", "true", "yes", "y", "on"},
        help="Echoed into the plan's dry-run/live commands; the executor enforces fractional policy.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    precompute_root = Path(args.precompute_root)
    if args.payload_path:
        payload_path = Path(args.payload_path)
    elif args.trade_date:
        payload_path = precompute_payload_path_for_date(args.trade_date, precompute_root)
    else:
        payload_path = latest_precompute_payload_path(precompute_root)

    plan = build_live_pilot_plan(
        payload_path=payload_path,
        approved_sleeve=str(args.approved_sleeve),
        capital_cap=float(args.capital_cap),
        max_orders=int(args.max_orders),
        output_dir=Path(args.output_dir),
        allow_missing_sleeve=bool(args.allow_missing_sleeve),
        allow_fractional=bool(args.allow_fractional),
        state_dir=Path(args.state_dir) if args.state_dir else None,
        lane=args.lane,
        recovery_policy=args.recovery_policy,
        recovery_policy_config=Path(args.recovery_policy_config),
        shadow_root=Path(args.shadow_root),
        strategy_registry_path=Path(args.strategy_registry_path),
    )
    print(
        json.dumps(
            {
                "status": plan.get("status"),
                "reason_code": plan.get("reason_code"),
                "target_name_count": plan.get("target_name_count"),
                "json_path": plan.get("json_path"),
                "markdown_path": plan.get("markdown_path"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if plan.get("status") == "READY_FOR_MANUAL_APPROVAL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
