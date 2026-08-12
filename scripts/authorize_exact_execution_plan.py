"""Publish the one broker-state-bound exact order plan for the PAPER lane.

The precompute and target builder remain evidence/portfolio-construction workers.
This authorizer takes a fresh broker snapshot, applies deterministic risk and
capital constraints once, and seals the resulting orders into v3.  The executor
may not repeat any of this work.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from authority.exact_plan import build_exact_execution_plan, exact_execution_plan_from_dict
from authority.pipeline import validate_persisted_authority_chain
from brokers.alpaca_broker import AlpacaBroker
from core.broker_retry_policy import is_retryable_broker_read_error
from core.live_pilot_guardrails import resolve_dynamic_cap
from core.precompute_bundle_validation import validate_sleeve_evaluation_payload
from core.regime_state_store import (
    RegimeAuthorityEvent,
    RegimePersistenceResult,
    commit_prepared_regime_authority,
    persist_regime_authority,
    prepare_regime_authority,
)
from core.submission_wal import OrderIntent
from execution.core import (
    apply_capital_budget_and_execution_filter,
    compute_transition_trades,
    live_pilot_execution_config,
)
from paper.run_manager import safe_write_text
from scripts.live_pilot_execute import (
    _broker_snapshot,
    _build_core_request,
    _core_rows_from_frame,
    _finite_float,
    _settled_cash_context,
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _regime_state_root(
    *,
    plan_path: Path | None,
    env: Mapping[str, str],
) -> Path:
    configured = str(env.get("CAERUS_REGIME_AUTHORITY_STATE_ROOT") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else REPO_ROOT / path
    if plan_path is not None:
        plan_parent = plan_path.resolve().parent
        if plan_parent.name == "plans":
            return plan_parent.parent / "state" / "regime_authority"
        return plan_parent / "regime_authority_state"
    return REPO_ROOT / "outputs" / "paper_lane" / "state" / "regime_authority"


def _bool_value(value: Any, *, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off", ""}:
        return False
    raise RuntimeError(f"{label} must be boolean")


def _first_present(mappings: list[Mapping[str, Any]], keys: tuple[str, ...]) -> Any:
    for mapping in mappings:
        for key in keys:
            if key in mapping and mapping.get(key) not in (None, ""):
                return mapping.get(key)
    return None


def _governed_regime_inputs(
    *,
    plan: Mapping[str, Any],
    risk_controls: Mapping[str, Any],
    risk_package_id: str,
) -> tuple[str, float, bool, str]:
    """Resolve regime inputs only from the hash-validated Risk authority.

    Risk may carry a nested ``regime_authority``/``market_state`` object or the
    established flat fields.  Mutable outer-plan aliases are accepted only as
    exact redundant copies; outer counters and previous state are forbidden
    because the append-only store owns them.
    """

    regime_authority = (
        dict(risk_controls.get("regime_authority"))
        if isinstance(risk_controls.get("regime_authority"), Mapping)
        else {}
    )
    market_state = (
        dict(risk_controls.get("market_state"))
        if isinstance(risk_controls.get("market_state"), Mapping)
        else {}
    )
    sources = [regime_authority, market_state, risk_controls]
    governed_observation = _first_present(
        sources,
        ("observed_state", "observed_regime", "composite_regime", "regime"),
    )
    observed_state = str(governed_observation or "UNKNOWN").strip().upper()
    governed_confidence = _first_present(
        sources,
        ("confidence", "regime_confidence"),
    )
    confidence = float(
        governed_confidence
        if governed_confidence is not None
        else (1.0 if governed_observation is not None else 0.0)
    )
    metrics = (
        dict(risk_controls.get("metrics"))
        if isinstance(risk_controls.get("metrics"), Mapping)
        else {}
    )
    nested_acute = _first_present(
        [regime_authority, market_state],
        ("acute_risk", "emergency_risk_response", "risk_veto_buys"),
    )
    acute_risk = (
        _bool_value(risk_controls.get("blocked"), label="Risk blocked")
        or _bool_value(
            metrics.get("circuit_breaker_triggered"),
            label="Risk circuit breaker",
        )
        or (
            _bool_value(nested_acute, label="governed acute risk")
            if nested_acute is not None
            else False
        )
    )
    governed_market_state_id = _first_present(
        [regime_authority, market_state, risk_controls],
        ("market_state_id", "state_id"),
    )
    if governed_market_state_id is None:
        raise RuntimeError(
            "persisted Risk authority requires a stable market_state_id source bar"
        )
    market_state_id = str(governed_market_state_id).strip()

    outer_observation = _first_present(
        [plan],
        ("observed_regime", "regime"),
    )
    if outer_observation is not None:
        if governed_observation is None or str(outer_observation).strip().upper() != observed_state:
            raise RuntimeError("mutable outer regime observation diverges from persisted Risk authority")
    if plan.get("regime_confidence") is not None:
        if governed_confidence is None or float(plan["regime_confidence"]) != confidence:
            raise RuntimeError("mutable outer regime confidence diverges from persisted Risk authority")
    if any(
        plan.get(key) is not None
        for key in (
            "previous_regime",
            "regime_bars_in_state",
            "regime_consecutive_observations",
        )
    ):
        raise RuntimeError("mutable outer regime persistence state is forbidden")
    if plan.get("emergency_risk_response") is not None and _bool_value(
        plan.get("emergency_risk_response"),
        label="outer emergency risk response",
    ) is not acute_risk:
        raise RuntimeError("mutable outer acute-risk claim diverges from persisted Risk authority")
    return observed_state, confidence, acute_risk, market_state_id


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _fresh_market_prices(
    *, broker: Any, symbols: list[str], as_of: str, env: Mapping[str, str]
) -> tuple[dict[str, float], dict[str, Any]]:
    getter = getattr(broker, "get_latest_trades", None)
    if not callable(getter):
        raise RuntimeError("broker lacks timestamped latest-trade reads for final Decision")
    unique = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    rows = getter(unique)
    if not isinstance(rows, Mapping):
        raise RuntimeError("latest-trade response is malformed")
    try:
        decision_time = dt.datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if decision_time.tzinfo is None:
            raise ValueError("timestamp lacks timezone")
        max_age_seconds = float(env.get("CAERUS_AUTHORIZATION_QUOTE_MAX_AGE_SECONDS") or 120)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid final-Decision quote freshness policy") from exc
    if max_age_seconds <= 0:
        raise RuntimeError("final-Decision quote freshness policy must be positive")
    prices: dict[str, float] = {}
    evidence_rows: list[dict[str, Any]] = []
    for symbol in unique:
        raw = rows.get(symbol)
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"latest trade missing for {symbol}")
        price = _finite_float(raw.get("price"))
        timestamp_raw = str(raw.get("timestamp") or "").strip()
        try:
            timestamp = dt.datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("timestamp lacks timezone")
        except ValueError as exc:
            raise RuntimeError(f"latest trade timestamp invalid for {symbol}") from exc
        age = (decision_time.astimezone(dt.timezone.utc) - timestamp.astimezone(dt.timezone.utc)).total_seconds()
        if price is None or price <= 0 or age < -5.0 or age > max_age_seconds:
            raise RuntimeError(f"latest trade is missing, invalid, or stale for {symbol}")
        prices[symbol] = float(price)
        evidence_rows.append(
            {
                "symbol": symbol,
                "price": float(price),
                "timestamp": timestamp.isoformat(),
                "age_seconds": age,
                "feed": str(raw.get("feed") or "UNKNOWN"),
            }
        )
    evidence = {
        "schema_version": "caerus.authorization_market_state.v1",
        "captured_at": str(as_of),
        "max_age_seconds": max_age_seconds,
        "quotes": evidence_rows,
    }
    evidence["content_hash"] = _canonical_hash(evidence)
    return prices, evidence


def _recover_existing_authority_for_wal(
    *, latest_pointer: Path, trade_date: str, wal_intents: Path
) -> Path | None:
    """Resolve the immutable authority artifact bound to durable WAL intents.

    Once any broker intent exists, fresh authorization is forbidden even when
    the mutable latest pointer was lost. Recovery is by the immutable plan ID
    and hash carried in the WAL itself.
    """
    intent_paths = sorted(wal_intents.glob("*.json")) if wal_intents.exists() else []
    if not intent_paths:
        return None
    identities: set[tuple[str, str]] = set()
    for path in intent_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        intent = OrderIntent.from_dict(payload)
        if intent.trade_date != trade_date:
            raise RuntimeError("submission WAL trade date does not match authorization date")
        identities.add((intent.plan_id, intent.plan_hash))
    if len(identities) != 1:
        raise RuntimeError("submission WAL contains multiple or mismatched exact plans")
    expected_plan_id, expected_plan_hash = next(iter(identities))

    candidates: set[Path] = set()
    if latest_pointer.exists():
        try:
            pointer = json.loads(latest_pointer.read_text(encoding="utf-8"))
            raw_path = Path(str(pointer.get("json_path") or ""))
            if raw_path and not raw_path.is_absolute():
                raw_path = latest_pointer.parent / raw_path
            if raw_path.is_file():
                candidates.add(raw_path.resolve())
        except (OSError, ValueError, TypeError):
            pass
    authority_dir = latest_pointer.parent / "authority" / trade_date
    if authority_dir.exists():
        candidates.update(path.resolve() for path in authority_dir.glob("*.json"))

    matches: list[Path] = []
    for path in sorted(candidates):
        try:
            handoff = json.loads(path.read_text(encoding="utf-8"))
            exact_payload = handoff.get("exact_execution_plan")
            if not isinstance(exact_payload, Mapping):
                continue
            exact = exact_execution_plan_from_dict(
                exact_payload,
                expected_plan_id=expected_plan_id,
                expected_account_scope="PAPER",
            )
            if exact.content_hash == expected_plan_hash:
                matches.append(path)
        except Exception:
            continue
    if len(matches) != 1:
        raise RuntimeError(
            "stable_wal_original_plan_recovery_unresolved: immutable authority artifact "
            f"matches={len(matches)}"
        )
    return matches[0]


def _quantity_positions(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in snapshot.get("positions") or []:
        if not isinstance(raw, Mapping):
            continue
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").strip().upper()
        quantity = _finite_float(raw.get("qty", raw.get("quantity", raw.get("shares"))))
        if symbol and quantity is not None and quantity > 1e-12:
            rows.append({"symbol": symbol, "quantity": float(quantity)})
    return sorted(rows, key=lambda row: row["symbol"])


def _expected_state(
    *,
    positions: list[dict[str, Any]],
    cash: float,
    orders: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float]:
    quantity_by_symbol = {str(row["symbol"]): float(row["quantity"]) for row in positions}
    expected_cash = float(cash)
    for order in orders:
        symbol = str(order["symbol"])
        quantity = float(order.get("quantity", order.get("shares", order.get("qty"))) or 0.0)
        price = float(order.get("expected_price", order.get("price", order.get("limit_price"))) or 0.0)
        notional = float(order.get("notional") or quantity * price)
        if str(order["side"]).upper() == "SELL":
            quantity_by_symbol[symbol] = max(0.0, quantity_by_symbol.get(symbol, 0.0) - quantity)
            expected_cash += notional
        else:
            quantity_by_symbol[symbol] = quantity_by_symbol.get(symbol, 0.0) + quantity
            expected_cash -= notional
    expected_positions = [
        {"symbol": symbol, "quantity": quantity}
        for symbol, quantity in sorted(quantity_by_symbol.items())
        if quantity > 1e-12
    ]
    return expected_positions, max(0.0, expected_cash)


def _seal_regime_committed_handoff(
    prepared_handoff: Mapping[str, Any],
    committed_regime: RegimePersistenceResult,
    *,
    verify_event: bool,
) -> dict[str, Any]:
    """Re-seal a handoff with a committed view of its prepared event."""

    if not committed_regime.committed or (
        verify_event and not committed_regime.event_path.is_file()
    ):
        raise RuntimeError("regime authority event is not durably committed")
    metadata = prepared_handoff.get("regime_authority_event")
    exact_payload = prepared_handoff.get("exact_execution_plan")
    if not isinstance(metadata, Mapping) or not isinstance(exact_payload, Mapping):
        raise RuntimeError("prepared exact handoff lacks regime commit metadata")
    if (
        str(metadata.get("content_hash") or "")
        != committed_regime.event.content_hash
        or str(metadata.get("observation_id") or "")
        != committed_regime.event.observation_id
    ):
        raise RuntimeError("committed regime event differs from prepared handoff")

    source_hashes = dict(exact_payload.get("source_artifact_hashes") or {})
    finalized = build_exact_execution_plan(
        run_id=str(exact_payload.get("run_id") or ""),
        as_of=str(exact_payload.get("as_of") or ""),
        created_at=str(exact_payload.get("created_at") or ""),
        orchestrator_version=str(exact_payload.get("orchestrator_version") or ""),
        source_precompute_ids=exact_payload.get("source_precompute_ids") or (),
        source_artifact_hashes=source_hashes,
        market_state_id=str(exact_payload.get("market_state_id") or ""),
        market_state=exact_payload.get("market_state") or {},
        regime_state=committed_regime.regime_state(),
        sleeve_allocations=exact_payload.get("sleeve_allocations") or (),
        portfolio_nav=exact_payload.get("portfolio_nav"),
        starting_positions=exact_payload.get("starting_positions") or (),
        starting_cash=exact_payload.get("starting_cash"),
        account_id_hash=str(exact_payload.get("account_id_hash") or ""),
        risk_state=exact_payload.get("risk_state") or {},
        sell_orders=exact_payload.get("sell_orders") or (),
        buy_orders=exact_payload.get("buy_orders") or (),
        expected_posttrade_positions=(
            exact_payload.get("expected_posttrade_positions") or ()
        ),
        expected_posttrade_cash=exact_payload.get("expected_posttrade_cash"),
        constraints=exact_payload.get("constraints") or {},
        authorization_state=exact_payload.get("authorization_state") or {},
        strategy_id=str(exact_payload.get("strategy_id") or ""),
        account_scope=str(exact_payload.get("account_scope") or ""),
    )
    result = dict(prepared_handoff)
    result.update(
        {
            "exact_execution_plan": finalized.to_dict(),
            "exact_execution_plan_id": finalized.plan_id,
            "exact_execution_plan_hash": finalized.content_hash,
            "exact_execution_authority_run_id": finalized.run_id,
            "regime_authority_event": {
                "path": str(committed_regime.event_path),
                "content_hash": committed_regime.event.content_hash,
                "observation_id": committed_regime.event.observation_id,
                "sequence": committed_regime.event.sequence,
                "created": committed_regime.created,
                "committed_at_evaluation": True,
                "commit_required_before_pointer": False,
                "event": committed_regime.event.to_dict(),
            },
        }
    )
    if verify_event:
        exact_execution_plan_from_dict(result["exact_execution_plan"])
    return result


def finalize_regime_committed_handoff(
    prepared_handoff: Mapping[str, Any],
    committed_regime: RegimePersistenceResult,
) -> dict[str, Any]:
    """Re-seal an exact handoff only after its regime event is durable."""

    return _seal_regime_committed_handoff(
        prepared_handoff,
        committed_regime,
        verify_event=True,
    )


def authorize_exact_execution_plan(
    *,
    plan: Mapping[str, Any],
    broker: Any,
    env: Mapping[str, str],
    run_id: str,
    plan_path: Path | None = None,
    created_at: str | None = None,
    regime_state_root: Path | None = None,
) -> dict[str, Any]:
    effective_authorized_at = created_at or _now()
    if str(plan.get("execution_lane") or "").strip().lower() != "paper":
        raise RuntimeError("exact execution authorization is PAPER-lane only")
    if str(plan.get("approved_sleeve") or "").strip().lower() not in {
        "orion",
        "caerus_orion",
    }:
        raise RuntimeError("only Orion is capital-eligible for exact PAPER authorization")
    if not bool(getattr(broker, "paper", False)):
        raise RuntimeError("exact execution authorization requires a paper broker")
    sleeve_path_raw = str(plan.get("source_sleeve_evaluations") or "").strip()
    sleeve_hash = str(plan.get("source_sleeve_evaluations_sha256") or "").strip().lower()
    if not sleeve_path_raw or not sleeve_hash:
        raise RuntimeError("validated sleeve-evaluation authority lineage is required")
    sleeve_path = Path(sleeve_path_raw)
    if not sleeve_path.is_absolute():
        sleeve_path = REPO_ROOT / sleeve_path
    if not sleeve_path.is_file() or _hash_file(sleeve_path) != sleeve_hash:
        raise RuntimeError("sleeve-evaluation authority hash is missing or invalid")
    sleeve_payload = json.loads(sleeve_path.read_text(encoding="utf-8"))
    sleeve_failures = validate_sleeve_evaluation_payload(
        sleeve_payload, trade_date=str(plan.get("trade_date") or "")
    )
    if sleeve_failures:
        raise RuntimeError(
            "sleeve-evaluation authority semantic validation failed: "
            + ",".join(sleeve_failures[:5])
        )
    orion_envelopes = [
        row for row in sleeve_payload.get("envelopes") or []
        if isinstance(row, Mapping) and row.get("sleeve_id") == "caerus_orion"
    ]
    if len(orion_envelopes) != 1:
        raise RuntimeError("sleeve evaluations lack sole Orion authority evidence")
    orion = orion_envelopes[0]
    if (
        (orion.get("evaluation") or {}).get("status") != "OK"
        or (orion.get("eligibility") or {}).get("evaluation_usable_for_capital") is not True
        or (orion.get("opportunity") or {}).get("decision_eligible") is not True
    ):
        raise RuntimeError("Orion sleeve evaluation is not capital-decision eligible")
    embedded_execution = plan.get("approved_execution_package")
    authority_paths = plan.get("authority_package_paths")
    if not isinstance(embedded_execution, Mapping) or not isinstance(authority_paths, Mapping):
        raise RuntimeError("complete governed authority package chain is required")
    _evidence, _decision, _risk, governed_execution = validate_persisted_authority_chain(
        paths={str(key): str(value) for key, value in authority_paths.items()},
        embedded_execution=embedded_execution,
        trade_date=str(plan.get("trade_date") or ""),
        required_source_hash=sleeve_hash,
    )
    governed_risk_controls = _plain(_risk.constraints)
    governed_outer_controls = dict(governed_risk_controls)
    governed_outer_controls.pop("target_attainment_policy", None)
    outer_risk_controls = (
        dict(plan.get("risk_controls"))
        if isinstance(plan.get("risk_controls"), Mapping)
        else {}
    )
    # market_state_id is consumed from the persisted, hash-validated Risk
    # package.  Legacy outer handoffs need not redundantly copy it, but when
    # they do carry it the value must still match exactly.
    comparable_governed = _plain(governed_outer_controls)
    for nested_key in ("regime_authority", "market_state"):
        governed_nested = comparable_governed.get(nested_key)
        outer_nested = outer_risk_controls.get(nested_key)
        if isinstance(governed_nested, dict) and not isinstance(outer_nested, Mapping):
            governed_nested.pop("market_state_id", None)
            governed_nested.pop("state_id", None)
            if not governed_nested:
                comparable_governed.pop(nested_key, None)
        elif isinstance(governed_nested, dict) and isinstance(outer_nested, Mapping):
            for identity_key in ("market_state_id", "state_id"):
                if identity_key not in outer_nested:
                    governed_nested.pop(identity_key, None)
    for identity_key in ("market_state_id", "state_id"):
        if identity_key not in outer_risk_controls:
            comparable_governed.pop(identity_key, None)
    if outer_risk_controls != comparable_governed:
        raise RuntimeError("outer risk_controls diverge from persisted Risk authority")
    snapshot = _broker_snapshot(broker, fail_on_open_order_lookup=True)
    account = snapshot.get("account") if isinstance(snapshot.get("account"), Mapping) else {}
    cash = _finite_float((account or {}).get("cash"))
    nav = _finite_float((account or {}).get("portfolio_value") or (account or {}).get("equity"))
    if cash is None or cash < 0 or nav is None or nav <= 0:
        raise RuntimeError("fresh broker cash/NAV is unavailable at Decision")

    planning_cap = _finite_float(env.get("CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP"))
    request, malformed = _build_core_request(
        pre_snapshot=snapshot,
        plan=plan,
        run_id=run_id,
        planning_equity_cap=planning_cap,
    )
    if request is None or malformed:
        raise RuntimeError(f"fresh broker state cannot support exact planning: {malformed}")
    decision_symbols = [
        str(symbol).strip().upper()
        for symbol in set(request.prices.index.tolist())
        if str(symbol).strip()
    ]
    fresh_prices, market_state_evidence = _fresh_market_prices(
        broker=broker,
        symbols=decision_symbols,
        as_of=effective_authorized_at,
        env=env,
    )
    for symbol, price in fresh_prices.items():
        request.prices.loc[symbol] = float(price)
    request = dataclasses.replace(
        request,
        price_basis="timestamped_alpaca_latest_trade_at_authorization",
    )
    settled, _history, availability = _settled_cash_context(
        broker,
        broker_cash=request.planning_account.get("cash"),
        as_of_date=str(plan.get("trade_date") or ""),
        env=env,
    )
    if settled.fail_closed:
        raise RuntimeError(f"settled cash unavailable at Decision: {availability}")
    request.planning_account["settled_cash"] = float(settled.settled_cash)
    request.planning_account["settled_cash_fail_closed"] = False
    cap, cap_source = resolve_dynamic_cap(float(nav), env)
    if cap is None or cap <= 0:
        raise RuntimeError("dynamic capital cap is unresolved at Decision")
    max_orders = int(float(env.get("CAERUS_LIVE_PILOT_MAX_ORDERS") or 50))
    min_trade = float(env.get("CAERUS_LIVE_PILOT_MIN_TRADE_USD") or 10)
    config = live_pilot_execution_config(
        approved_cap_usd=float(cap),
        allow_fractional=bool(plan.get("allow_fractional", False)),
        allow_fractional_sells=(
            str(env.get("CAERUS_PAPER_FRACTIONAL_EXIT_ENABLED") or "")
            .strip()
            .lower()
            in {"1", "true", "yes", "y", "on"}
        ),
        fractional_sell_min_trade_usd=float(
            env.get("CAERUS_PAPER_FRACTIONAL_EXIT_MIN_NOTIONAL_USD") or 1.0
        ),
        max_orders=max_orders,
        min_trade_usd=min_trade,
        buy_buffer_pct=float(env.get("CAERUS_LIVE_PILOT_BUY_BUFFER_PCT") or 0.98),
        ledger_enabled=False,
    )
    # Whole-share target-attainment is governed PAPER policy. The shared factory
    # defaults to the live-pilot label, so preserve the constraints but carry the
    # actual lane identity before planning (the executor already does this).
    config = dataclasses.replace(config, mode="paper")
    raw, trade_meta = compute_transition_trades(request=request, config=config)
    _capital, capital_budget, executable, filter_stats = apply_capital_budget_and_execution_filter(
        trades=raw,
        planning_account=request.planning_account,
        config=config,
    )
    exact_rows = _core_rows_from_frame(executable, plan=plan)
    if len(exact_rows) > max_orders:
        raise RuntimeError("exact order count exceeds authorized maximum")
    sells = [dict(row) for row in exact_rows if str(row.get("side")).upper() == "SELL"]
    buys = [dict(row) for row in exact_rows if str(row.get("side")).upper() == "BUY"]
    if len(sells) + len(buys) != len(exact_rows):
        raise RuntimeError("exact planning produced unsupported order sides")

    risk_controls = governed_outer_controls
    observed_regime, regime_confidence, acute_risk, market_state_id = (
        _governed_regime_inputs(
            plan=plan,
            risk_controls=risk_controls,
            risk_package_id=_risk.package_id,
        )
    )
    # The broker snapshot intentionally exposes only the deterministic account
    # hash; that stable identity is sufficient to isolate persistent authority
    # without writing the raw broker account identifier to disk.
    broker_account_id_hash = str(
        (account or {}).get("account_id_hash") or ""
    ).strip().lower()
    if not broker_account_id_hash:
        raise RuntimeError("fresh PAPER broker account identity is unavailable")
    resolved_regime_state_root = regime_state_root or _regime_state_root(
        plan_path=plan_path,
        env=env,
    )
    regime_inputs = {
        "account_scope": "PAPER",
        "account_id": broker_account_id_hash,
        "sleeve_id": "caerus_orion",
        "authorization_run_id": run_id,
        "trade_date": str(plan.get("trade_date") or ""),
        "recorded_at": effective_authorized_at,
        "observed_state": observed_regime,
        "confidence": regime_confidence,
        "acute_risk": acute_risk,
        "risk_package_id": _risk.package_id,
        "risk_package_hash": _risk.content_hash,
        "market_state_id": market_state_id,
    }
    prepared_regime = prepare_regime_authority(
        resolved_regime_state_root,
        **regime_inputs,
    )
    # Acute risk is durable immediately, before any possible buy authority.
    # Normal observations remain prepared until main() publishes the immutable
    # exact handoff and commits them before exposing its workflow pointer.
    immediate_risk_authority = (
        acute_risk or prepared_regime.event.to_decision().risk_veto_buys
    )
    regime_record = (
        persist_regime_authority(resolved_regime_state_root, **regime_inputs)
        if immediate_risk_authority
        else prepared_regime
    )
    regime_decision = regime_record.event.to_decision()
    if regime_decision.risk_veto_buys and buys:
        raise RuntimeError("emergency regime risk response vetoes new buy exposure")

    starting_positions = _quantity_positions(snapshot)
    expected_positions, expected_cash = _expected_state(
        positions=starting_positions,
        cash=float(cash),
        orders=[*sells, *buys],
    )
    source_hashes: dict[str, str] = {}
    if plan_path is not None and plan_path.exists():
        source_hashes[str(plan_path)] = _hash_file(plan_path)
    decision_source = plan.get("decision_source_artifact")
    if isinstance(decision_source, Mapping):
        source_path = Path(str(decision_source.get("path") or ""))
        if source_path and not source_path.is_absolute():
            source_path = REPO_ROOT / source_path
        if source_path.exists():
            source_hashes[str(source_path)] = _hash_file(source_path)
    source_hashes["approved_target_package"] = governed_execution.content_hash
    source_hashes[str(sleeve_path)] = sleeve_hash
    source_hashes["authorization_market_state"] = str(
        market_state_evidence["content_hash"]
    )
    source_hashes["regime_authority_event_content"] = (
        regime_record.event.content_hash
    )
    trade_date = str(plan.get("trade_date") or "")
    exact = build_exact_execution_plan(
        run_id=run_id,
        as_of=f"{trade_date}T09:35:00-04:00",
        created_at=effective_authorized_at,
        orchestrator_version=str(env.get("CAERUS_ORCHESTRATOR_VERSION") or "choice2.v1"),
        source_precompute_ids=[
            str(plan.get("source_precompute_payload") or ""),
            str(plan.get("source_signals") or ""),
            str(plan.get("source_sleeve_evaluations") or ""),
        ],
        source_artifact_hashes=source_hashes,
        market_state_id=market_state_id,
        market_state={
            "captured_at": snapshot.get("captured_at"),
            "pricing_basis": "timestamped_alpaca_latest_trade_at_authorization",
            "quote_evidence": market_state_evidence,
            "risk_package_id": _risk.package_id,
            "risk_package_hash": _risk.content_hash,
        },
        regime_state=regime_record.regime_state(),
        sleeve_allocations=[
            {
                "sleeve_id": "caerus_orion",
                "capital_eligible": True,
                "account_scope": "PAPER",
                "allocation_weight": 1.0,
            }
        ],
        portfolio_nav=float(nav),
        starting_positions=starting_positions,
        starting_cash=float(cash),
        account_id_hash=broker_account_id_hash,
        risk_state={
            "target_risk": risk_controls,
            "trade_meta": trade_meta,
            "capital_budget": capital_budget,
            "execution_filter": filter_stats,
            "settled_cash": settled.to_report(),
        },
        sell_orders=sells,
        buy_orders=buys,
        expected_posttrade_positions=expected_positions,
        expected_posttrade_cash=expected_cash,
        constraints={
            "max_orders": max_orders,
            "capital_cap_usd": float(cap),
            "capital_cap_source": cap_source,
            "allow_fractional": bool(plan.get("allow_fractional", False)),
            "sell_first": True,
            "post_sell_rebudgeting": "FORBIDDEN",
            "cash_reconciliation_tolerance_usd": max(
                1.0,
                sum(float(row.get("notional") or 0.0) for row in exact_rows) * 0.01,
            ),
        },
        authorization_state={
            "status": "AUTHORIZED",
            "authority": "CAERUS_ORCHESTRATOR",
            "authorized_at": effective_authorized_at,
            "authorization_reason": (
                "AUTHORIZED_NO_TRADE"
                if not exact_rows
                else "ORION_PAPER_EXACT_ORDERS_AUTHORIZED"
            ),
        },
    )
    result = dict(plan)
    result.update(
        {
            "schema_version": "caerus.authorized_execution_handoff.v1",
            "status": "AUTHORIZED_NO_TRADE" if not exact.orders else "AUTHORIZED_EXACT_PLAN",
            "reason_code": (
                "authorized_no_trade"
                if not exact.orders
                else "fresh_broker_state_exact_plan_authorized"
            ),
            "exact_execution_plan": exact.to_dict(),
            "exact_execution_plan_id": exact.plan_id,
            "exact_execution_plan_hash": exact.content_hash,
            "exact_execution_authority_run_id": exact.run_id,
            "execution_authority": "exact_execution_plan_only",
            "precompute_execution_authority": False,
            "broker_state_at_decision": snapshot,
            "regime_authority_event": {
                "path": str(regime_record.event_path),
                "content_hash": regime_record.event.content_hash,
                "observation_id": regime_record.event.observation_id,
                "sequence": regime_record.event.sequence,
                "created": regime_record.created,
                "committed_at_evaluation": regime_record.committed,
                "commit_required_before_pointer": not regime_record.committed,
                "event": regime_record.event.to_dict(),
            },
        }
    )
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    trade_date = str(plan.get("trade_date") or "")
    latest_pointer = args.output or args.plan.with_name(
        f"exact_execution_plan_{trade_date}.latest.json"
    )
    wal_intents = latest_pointer.parent.parent / "submission_wal" / trade_date / "intents"
    try:
        prior_path = _recover_existing_authority_for_wal(
            latest_pointer=latest_pointer,
            trade_date=trade_date,
            wal_intents=wal_intents,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason_code": "stable_wal_original_plan_recovery_unresolved",
                    "error": str(exc)[:1000],
                    "orders_submitted": 0,
                },
                sort_keys=True,
            )
        )
        return 1
    if prior_path is not None:
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "status": "RECOVER_EXISTING_EXACT_PLAN",
                    "json_path": str(prior_path),
                    "plan_id": prior.get("exact_execution_plan_id"),
                    "reason_code": "stable_wal_requires_original_plan_recovery",
                },
                sort_keys=True,
            )
        )
        return 0
    broker = AlpacaBroker.from_env()
    resolved_regime_state_root = (
        latest_pointer.parent.parent / "state" / "regime_authority"
    )
    try:
        result = authorize_exact_execution_plan(
            plan=plan,
            broker=broker,
            env=os.environ,
            run_id=args.run_id,
            plan_path=args.plan,
            regime_state_root=resolved_regime_state_root,
        )
    except Exception as exc:
        transient = is_retryable_broker_read_error(exc)
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason_code": (
                        "paper_exact_plan_authorization_transient_failed"
                        if transient
                        else "paper_exact_plan_authorization_nonretryable_failed"
                    ),
                    "error": str(exc)[:1000],
                    "orders_submitted": 0,
                },
                sort_keys=True,
            )
        )
        return 2 if transient else 1
    authority_dir = latest_pointer.parent / "authority" / trade_date
    authority_dir.mkdir(parents=True, exist_ok=True)
    regime_metadata = result.get("regime_authority_event")
    if not isinstance(regime_metadata, Mapping):
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason_code": "regime_authority_commit_metadata_missing",
                    "orders_submitted": 0,
                },
                sort_keys=True,
            )
        )
        return 1
    if bool(regime_metadata.get("commit_required_before_pointer")):
        try:
            event_payload = regime_metadata.get("event")
            if not isinstance(event_payload, Mapping):
                raise RuntimeError("prepared regime event payload is missing")
            prepared_event = RegimeAuthorityEvent.from_dict(event_payload)
            prepared_regime = RegimePersistenceResult(
                event=prepared_event,
                event_path=Path(str(regime_metadata.get("path") or "")),
                created=False,
                committed=False,
            )
            committed_view = RegimePersistenceResult(
                event=prepared_event,
                event_path=prepared_regime.event_path,
                created=False,
                committed=True,
            )
            final_result = _seal_regime_committed_handoff(
                result,
                committed_view,
                verify_event=False,
            )
            safe_plan_id = str(final_result["exact_execution_plan_id"]).replace(
                ":", "_"
            )
            output = authority_dir / f"{safe_plan_id}.json"
            staging = authority_dir / f".{safe_plan_id}.staged"
            safe_write_text(
                staging,
                json.dumps(final_result, indent=2, sort_keys=True, default=str) + "\n",
                allow_overwrite=False,
            )
            committed_regime = commit_prepared_regime_authority(
                resolved_regime_state_root,
                prepared_regime,
            )
            if committed_regime.event.content_hash != str(
                regime_metadata.get("content_hash") or ""
            ):
                raise RuntimeError("committed regime event differs from exact handoff")
            exact_execution_plan_from_dict(final_result["exact_execution_plan"])
            if output.exists():
                existing = json.loads(output.read_text(encoding="utf-8"))
                exact_execution_plan_from_dict(existing["exact_execution_plan"])
                result = existing
                staging.unlink(missing_ok=True)
            else:
                os.replace(staging, output)
                directory_fd = os.open(str(authority_dir), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                result = final_result
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "status": "BLOCKED",
                        "reason_code": "regime_authority_commit_failed",
                        "error": str(exc)[:1000],
                        "orders_submitted": 0,
                    },
                    sort_keys=True,
                )
            )
            return 1
    else:
        exact_execution_plan_from_dict(result["exact_execution_plan"])
        safe_plan_id = str(result["exact_execution_plan_id"]).replace(":", "_")
        output = authority_dir / f"{safe_plan_id}.json"
        if output.exists():
            existing = json.loads(output.read_text(encoding="utf-8"))
            exact_execution_plan_from_dict(existing["exact_execution_plan"])
            result = existing
        else:
            safe_write_text(
                output,
                json.dumps(result, indent=2, sort_keys=True, default=str) + "\n",
                allow_overwrite=False,
            )
    pointer_payload = {
        "schema_version": "caerus.exact_execution_plan_pointer.v1",
        "trade_date": trade_date,
        "plan_id": result["exact_execution_plan_id"],
        "plan_hash": result["exact_execution_plan_hash"],
        "json_path": str(output),
    }
    safe_write_text(
        latest_pointer,
        json.dumps(pointer_payload, indent=2, sort_keys=True) + "\n",
        allow_overwrite=True,
    )
    print(json.dumps({"status": result["status"], "json_path": str(output), "plan_id": result["exact_execution_plan_id"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
