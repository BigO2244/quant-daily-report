from __future__ import annotations

import hashlib
import logging
import math
import os
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from core.trading_mode import canonical_trading_mode

logger = logging.getLogger(__name__)


LIVE_PILOT_MODE = "live_pilot"
LIVE_PREFLIGHT_MODE = "live_preflight"
PAPER_MODE = "paper"

LIVE_PILOT_APPROVED_ENV = "CAERUS_LIVE_PILOT_APPROVED"
LIVE_PILOT_CAPITAL_CAP_ENV = "CAERUS_LIVE_PILOT_CAPITAL_CAP"
LIVE_PILOT_CAP_PCT_ENV = "CAERUS_LIVE_PILOT_CAP_PCT"
LIVE_PILOT_SLEEVE_ID_ENV = "CAERUS_LIVE_PILOT_SLEEVE_ID"
LIVE_PILOT_ACCOUNT_ID_ENV = "CAERUS_LIVE_PILOT_ACCOUNT_ID"
LIVE_PILOT_ACCOUNT_ID_HASH_ENV = "CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH"
LIVE_PILOT_MAX_ORDERS_ENV = "CAERUS_LIVE_PILOT_MAX_ORDERS"
LIVE_PILOT_KILL_SWITCH_ENV = "CAERUS_LIVE_PILOT_KILL_SWITCH"
LIVE_PILOT_SUBMIT_APPROVED_ENV = "CAERUS_LIVE_PILOT_SUBMIT_APPROVED"
LIVE_PILOT_DRY_RUN_ENV = "CAERUS_LIVE_PILOT_DRY_RUN"
LIVE_PILOT_CRON_APPROVED_ENV = "CAERUS_LIVE_PILOT_CRON_APPROVED"
LIVE_PILOT_SELL_WHITELIST_ENV = "CAERUS_LIVE_PILOT_SELL_WHITELIST"
# Master enable for live sells, fail-closed. Production execution additionally
# proves every SELL against the immutable pretrade broker inventory. The legacy
# per-symbol whitelist is retained as configuration metadata, but never authorizes
# a production sell on its own.
LIVE_PILOT_SELLS_ENABLED_ENV = "CAERUS_LIVE_PILOT_SELLS_ENABLED"
LIVE_PILOT_ALLOW_FRACTIONAL_ENV = "CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL"
LIVE_PILOT_MAX_SLIPPAGE_BPS_ENV = "CAERUS_LIVE_PILOT_MAX_SLIPPAGE_BPS"

LIVE_PILOT_APPROVED_MAX_CAP_USD = 500.0
LIVE_PILOT_MAX_CAP_USD = LIVE_PILOT_APPROVED_MAX_CAP_USD
LIVE_ALLOWED_VALUES = frozenset({"1", "true", "yes", "y", "on", "approve_live_pilot"})
FALSE_VALUES = frozenset({"0", "false", "no", "n", "off"})
EQUITY_ASSET_CLASSES = frozenset({"us_equity", "equity", "assetclass.us_equity"})


@dataclass(frozen=True)
class LivePilotGateResult:
    status: str
    reason_code: str
    requested_mode: str
    broker_paper: bool
    base_url: str
    approved: bool
    capital_cap_usd: float | None
    sleeve_id: str | None
    expected_account_id_present: bool
    expected_account_id_hash_present: bool
    max_orders: int | None
    dry_run: bool
    kill_switch: bool
    cron_context: bool
    cron_approved: bool
    live_orders_allowed: bool
    operator_action: str
    account_id_match: bool | None = None
    account_match_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "requested_mode": self.requested_mode,
            "broker_paper": self.broker_paper,
            "base_url": self.base_url,
            "approved_env": LIVE_PILOT_APPROVED_ENV,
            "approved": self.approved,
            "capital_cap_env": LIVE_PILOT_CAPITAL_CAP_ENV,
            "capital_cap_usd": self.capital_cap_usd,
            "max_cap_usd": LIVE_PILOT_MAX_CAP_USD,
            "sleeve_id_env": LIVE_PILOT_SLEEVE_ID_ENV,
            "sleeve_id": self.sleeve_id,
            "expected_account_id_env": LIVE_PILOT_ACCOUNT_ID_ENV,
            "expected_account_id_present": self.expected_account_id_present,
            "expected_account_id_hash_env": LIVE_PILOT_ACCOUNT_ID_HASH_ENV,
            "expected_account_id_hash_present": self.expected_account_id_hash_present,
            "max_orders_env": LIVE_PILOT_MAX_ORDERS_ENV,
            "max_orders": self.max_orders,
            "dry_run_env": LIVE_PILOT_DRY_RUN_ENV,
            "dry_run": self.dry_run,
            "kill_switch_env": LIVE_PILOT_KILL_SWITCH_ENV,
            "kill_switch": self.kill_switch,
            "cron_approved_env": LIVE_PILOT_CRON_APPROVED_ENV,
            "cron_context": self.cron_context,
            "cron_approved": self.cron_approved,
            "live_orders_allowed": self.live_orders_allowed,
            "operator_action": self.operator_action,
            "account_id_match": self.account_id_match,
            "account_match_reason": self.account_match_reason,
        }


@dataclass(frozen=True)
class LivePilotOrder:
    symbol: str
    side: str
    qty: float
    limit_price: float
    notional: float
    client_order_id: str
    order_type: str = "limit"
    expected_price: float | None = None
    original_limit_price: float | None = None
    normalized_limit_price: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "limit_price": self.limit_price,
            "original_limit_price": self.original_limit_price,
            "normalized_limit_price": self.normalized_limit_price,
            "expected_price": self.expected_price,
            "cap_enforcement_price": self.normalized_limit_price,
            "notional": self.notional,
            "client_order_id": self.client_order_id,
            "order_type": self.order_type,
        }


@dataclass(frozen=True)
class LivePilotDroppedOrder:
    """A per-order record of an order that failed validation and was DROPPED.

    Per-order partitioning (BLOCKER 3, PRE_ARM_SWEEP_2026-07-13 §d): an invalid or
    unresolvable order is dropped and flagged here with a per-order reason_code;
    it never blocks the rest of the batch. A dropped SELL is marked
    ``severity="critical"`` (it means we are failing to exit a held position — a
    buy-side problem must never carry the same weight) so it is impossible to
    miss in the artifacts/summary even though it does not halt the run.
    """

    symbol: str
    side: str
    reason_code: str
    severity: str
    stage: str = "plan_validation"

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "reason_code": self.reason_code,
            "severity": self.severity,
            "stage": self.stage,
        }


@dataclass(frozen=True)
class LivePilotPlanValidation:
    status: str
    reason_codes: list[str]
    orders: list[LivePilotOrder]
    total_notional: float
    operator_action: str
    dropped_orders: list[LivePilotDroppedOrder] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "orders": [order.to_dict() for order in self.orders],
            "dropped_orders": [dropped.to_dict() for dropped in self.dropped_orders],
            "total_notional": self.total_notional,
            "operator_action": self.operator_action,
        }


def _env_mapping(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in LIVE_ALLOWED_VALUES


def kill_switch_engaged(value: object) -> bool:
    """Return whether the live-pilot kill switch is engaged (blocking), fail-closed.

    The kill switch is treated as ENGAGED unless the value is an explicitly
    recognized "off" token (see ``FALSE_VALUES``). Unset, empty, unrecognized, or
    unparseable values all resolve to engaged, so a typo or missing env var can
    never silently disarm the only safeguard that stands between an approved lane
    and a live broker submission. To arm live, an operator must set an explicit
    recognized off value (e.g. ``0``/``false``/``off``).
    """
    raw = str(value if value is not None else "").strip().lower()
    return raw not in FALSE_VALUES


def _dry_run_enabled(value: object) -> bool:
    raw = str(value if value is not None else "1").strip().lower()
    return raw not in FALSE_VALUES


def _positive_float(value: object) -> float | None:
    try:
        numeric = float(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) and numeric > 0 else None


def _positive_int(value: object) -> int | None:
    try:
        numeric = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def resolve_dynamic_cap(
    portfolio_value: object,
    env: Mapping[str, str] | None = None,
) -> tuple[float | None, str]:
    """Resolve the live-pilot capital ceiling from the broker account, fail-closed.

    The cap is the account's current portfolio value (fully dynamic, no fixed
    program ceiling). ``CAERUS_LIVE_PILOT_CAPITAL_CAP``, if set, is an OPTIONAL
    lower manual ceiling (it can only tighten, never raise, the account-derived
    cap). Returns ``(cap, source)``; ``(None, ...)`` means the cap could not be
    resolved and the caller must block — a run must never size against an unknown
    account value.
    """
    environ = _env_mapping(env)
    pv = _positive_float(portfolio_value)
    env_cap = _positive_float(environ.get(LIVE_PILOT_CAPITAL_CAP_ENV))
    if pv is not None:
        # Optional deploy fraction of portfolio value (default 1.0 = full account).
        # Setting CAERUS_LIVE_PILOT_CAP_PCT=0.95 keeps a 5% cash buffer so a full
        # rebalance never drives the account to ~0 buying power. Clamped to (0, 1];
        # an invalid/unset value falls back to 1.0 (unchanged behavior).
        pct = _positive_float(environ.get(LIVE_PILOT_CAP_PCT_ENV)) or 1.0
        pct = min(pct, 1.0)
        pv_deployable = pv * pct
        pv_source = "portfolio_value" if pct >= 1.0 else "portfolio_value_pct"
        if env_cap is not None:
            return min(pv_deployable, env_cap), pv_source + "_env_capped"
        return pv_deployable, pv_source
    if env_cap is not None:
        return env_cap, "env_fallback"
    return None, "unavailable"


def requested_mode_from_env(env: Mapping[str, str] | None = None) -> str:
    environ = _env_mapping(env)
    mode_raw = str(environ.get("MODE", "") or "").strip()
    trading_mode_raw = str(environ.get("TRADING_MODE", "") or "").strip()
    mode = canonical_trading_mode(
        trading_mode_raw or mode_raw or PAPER_MODE,
        field_name="TRADING_MODE" if trading_mode_raw else "MODE",
    )
    if mode_raw and trading_mode_raw:
        mode_from_mode = canonical_trading_mode(mode_raw, field_name="MODE")
        mode_from_trading_mode = canonical_trading_mode(trading_mode_raw, field_name="TRADING_MODE")
        if mode_from_mode != mode_from_trading_mode:
            raise RuntimeError(
                "Mode ambiguity: MODE and TRADING_MODE resolve to different execution modes "
                f"(MODE={mode_from_mode}, TRADING_MODE={mode_from_trading_mode})"
            )
    return mode


def account_id_hash(account_id: object) -> str:
    return hashlib.sha256(str(account_id or "").strip().encode("utf-8")).hexdigest()


def expected_account_matches(account_id: object, env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    environ = _env_mapping(env)
    actual = str(account_id or "").strip()
    expected = str(environ.get(LIVE_PILOT_ACCOUNT_ID_ENV) or "").strip()
    expected_hash = str(environ.get(LIVE_PILOT_ACCOUNT_ID_HASH_ENV) or "").strip().lower()
    if not actual:
        return False, "missing_actual_account_id"
    if expected and actual == expected:
        return True, "account_id_match"
    if expected_hash and account_id_hash(actual).lower() == expected_hash:
        return True, "account_id_hash_match"
    return False, "account_id_mismatch"


def _is_live_host(base_url: str) -> bool:
    norm = str(base_url or "").strip().lower().rstrip("/")
    return "api.alpaca.markets" in norm and "paper-api" not in norm


def _is_paper_host(base_url: str) -> bool:
    return "paper-api.alpaca.markets" in str(base_url or "").strip().lower().rstrip("/")


def _cron_context(env: Mapping[str, str]) -> bool:
    if _truthy(env.get("CAERUS_LIVE_PILOT_CRON_CONTEXT")):
        return True
    if _truthy(env.get("CAERUS_CRON_CONTEXT")) or _truthy(env.get("RUNNING_UNDER_CRON")):
        return True
    workflow_kind = str(env.get("WORKFLOW_KIND") or "").strip().lower()
    return workflow_kind in {"live", "live_pilot", "cron", "scheduled"}


def build_live_pilot_gate_result(
    *,
    broker_paper: bool,
    base_url: str,
    env: Mapping[str, str] | None = None,
    order_notional: float | None = None,
    submission_intent: bool = False,
    account_id: str | None = None,
    account_id_hash: str = "",
    enforce_account_match: bool = False,
) -> LivePilotGateResult:
    environ = _env_mapping(env)
    requested_mode = requested_mode_from_env(environ)
    base_url_norm = str(base_url or "").strip().rstrip("/")
    approved = _truthy(environ.get(LIVE_PILOT_APPROVED_ENV))
    cap = _positive_float(environ.get(LIVE_PILOT_CAPITAL_CAP_ENV))
    sleeve = str(environ.get(LIVE_PILOT_SLEEVE_ID_ENV) or "").strip() or None
    expected_account = str(environ.get(LIVE_PILOT_ACCOUNT_ID_ENV) or "").strip()
    expected_hash = str(environ.get(LIVE_PILOT_ACCOUNT_ID_HASH_ENV) or "").strip()
    max_orders = _positive_int(environ.get(LIVE_PILOT_MAX_ORDERS_ENV))
    dry_run = _dry_run_enabled(environ.get(LIVE_PILOT_DRY_RUN_ENV))
    kill_switch = kill_switch_engaged(environ.get(LIVE_PILOT_KILL_SWITCH_ENV))
    cron_context = _cron_context(environ)
    cron_approved = _truthy(environ.get(LIVE_PILOT_CRON_APPROVED_ENV))
    account_id_match: bool | None = None
    account_match_reason: str | None = None

    def result(reason: str, action: str, *, status: str = "BLOCKED", allowed: bool = False) -> LivePilotGateResult:
        return LivePilotGateResult(
            status=status,
            reason_code=reason,
            requested_mode=requested_mode,
            broker_paper=bool(broker_paper),
            base_url=base_url_norm,
            approved=approved,
            capital_cap_usd=cap,
            sleeve_id=sleeve,
            expected_account_id_present=bool(expected_account),
            expected_account_id_hash_present=bool(expected_hash),
            max_orders=max_orders,
            dry_run=dry_run,
            kill_switch=kill_switch,
            cron_context=cron_context,
            cron_approved=cron_approved,
            live_orders_allowed=allowed,
            operator_action=action,
            account_id_match=account_id_match,
            account_match_reason=account_match_reason,
        )

    if requested_mode == LIVE_PREFLIGHT_MODE:
        return result(
            "live_preflight_never_submits_orders",
            "Review preflight evidence only; do not submit live orders in LIVE_PREFLIGHT mode.",
        )
    if bool(broker_paper):
        if requested_mode == LIVE_PILOT_MODE:
            return result(
                "live_pilot_requires_live_alpaca_endpoint",
                "Set ALPACA_PAPER=0 and use the live Alpaca host only after approval.",
            )
        if not _is_paper_host(base_url_norm):
            return result(
                "paper_broker_non_paper_endpoint",
                "Paper mode must use the Alpaca paper endpoint.",
            )
        return result(
            "paper_submission_endpoint_confirmed",
            "Paper submission may proceed under existing paper controls.",
            status="PASS",
            allowed=False,
        )
    if requested_mode != LIVE_PILOT_MODE:
        return result(
            "live_broker_requires_live_pilot_mode",
            "Set TRADING_MODE=live_pilot only for an approved pilot path; legacy live mode is blocked.",
        )
    if not _is_live_host(base_url_norm):
        return result(
            "live_pilot_requires_live_alpaca_endpoint",
            "LIVE_PILOT requires the live Alpaca endpoint and refuses paper/unknown hosts.",
        )
    if not approved:
        return result(
            "missing_live_pilot_approval",
            f"Set {LIVE_PILOT_APPROVED_ENV}=1 only after manual approval.",
        )
    if cap is None:
        return result(
            "missing_positive_live_pilot_capital_cap",
            f"Set an explicit positive {LIVE_PILOT_CAPITAL_CAP_ENV} no greater than "
            f"${LIVE_PILOT_MAX_CAP_USD:.2f}.",
        )
    if cap > LIVE_PILOT_MAX_CAP_USD:
        return result(
            "live_pilot_capital_cap_exceeds_approved_max",
            f"Reduce {LIVE_PILOT_CAPITAL_CAP_ENV} to no more than "
            f"${LIVE_PILOT_MAX_CAP_USD:.2f}.",
        )
    if sleeve is None:
        return result(
            "missing_live_pilot_sleeve_id",
            f"Set {LIVE_PILOT_SLEEVE_ID_ENV} to the approved sleeve id.",
        )
    if not expected_account and not expected_hash:
        return result(
            "missing_live_pilot_expected_account_id",
            f"Set {LIVE_PILOT_ACCOUNT_ID_ENV} or {LIVE_PILOT_ACCOUNT_ID_HASH_ENV}.",
        )
    if enforce_account_match:
        # Wire the real account-hash match into the orchestrated gate result rather
        # than only checking that an expected id/hash is configured. Mismatch (or a
        # missing broker-reported account id) blocks before any submission.
        matched, match_reason = expected_account_matches(account_id, environ)
        actual_hash = str(account_id_hash or "").strip().lower()
        if not matched and actual_hash:
            env_hash = str(environ.get(LIVE_PILOT_ACCOUNT_ID_HASH_ENV) or "").strip().lower()
            if env_hash and actual_hash == env_hash:
                matched, match_reason = True, "account_id_hash_match"
        account_id_match = matched
        account_match_reason = match_reason
        if not matched:
            return result(
                "live_pilot_account_id_mismatch",
                "Expected live pilot account id/hash does not match the broker account; "
                "block before any submission until the pinned account is restored.",
            )
    if max_orders is None:
        return result(
            "missing_positive_live_pilot_max_orders",
            f"Set a positive {LIVE_PILOT_MAX_ORDERS_ENV}.",
        )
    if cron_context and not cron_approved:
        return result(
            "live_pilot_cron_context_requires_explicit_approval",
            f"Set {LIVE_PILOT_CRON_APPROVED_ENV}=1 only after scheduled live pilot approval.",
        )
    if dry_run:
        return result(
            "live_pilot_dry_run_enabled",
            "Dry run may write isolated artifacts while the kill switch remains engaged, "
            "but must not submit live orders.",
            status="PASS",
            allowed=False,
        )
    if kill_switch:
        return result(
            "live_pilot_kill_switch_enabled",
            f"Unset {LIVE_PILOT_KILL_SWITCH_ENV} before any live submission.",
        )
    if submission_intent and not _truthy(environ.get(LIVE_PILOT_SUBMIT_APPROVED_ENV)):
        return result(
            "live_pilot_submit_not_approved",
            f"Set {LIVE_PILOT_SUBMIT_APPROVED_ENV}=1 only for an owner-approved submission.",
        )
    finite_order_notional = _positive_float(order_notional)
    if submission_intent and order_notional is None:
        return result(
            "missing_live_order_notional",
            "Live pilot order submission requires a finite positive pre-submit notional estimate.",
        )
    if submission_intent and finite_order_notional is None:
        return result(
            "nonfinite_or_nonpositive_live_order_notional",
            "Live pilot order submission requires a finite positive pre-submit notional estimate.",
        )
    if finite_order_notional is not None and finite_order_notional > cap:
        return result(
            "order_notional_exceeds_live_pilot_cap",
            "Reduce or block the order; live pilot order notional exceeds the approved cap.",
        )

    return result(
        "live_pilot_guardrails_satisfied",
        "Live pilot guardrails are satisfied; submit only through the approved isolated live pilot path.",
        status="PASS",
        allowed=True,
    )


def validate_live_pilot_submission_guardrails(
    *,
    broker_paper: bool,
    base_url: str,
    env: Mapping[str, str] | None = None,
    order_notional: float | None = None,
) -> LivePilotGateResult:
    result = build_live_pilot_gate_result(
        broker_paper=broker_paper,
        base_url=base_url,
        env=env,
        order_notional=order_notional,
        submission_intent=True,
    )
    if bool(broker_paper) and result.status == "PASS":
        return result
    if result.live_orders_allowed:
        return result
    raise RuntimeError(
        "Alpaca submission blocked by live pilot guardrails: "
        f"{result.reason_code}; mode={result.requested_mode}; "
        f"broker_paper={result.broker_paper}; base_url={result.base_url!r}; "
        f"action={result.operator_action}"
    )


def _bounded_client_order_id(raw: str) -> str:
    """Return a UNIQUE, broker-safe client_order_id <=48 ASCII chars.

    Mirrors brokers.alpaca_broker.alpaca_client_order_id (kept local to avoid a
    core->brokers import cycle). Plain [:48] truncation is NOT safe: the
    "caerus-live-pilot-{run_id}" prefix alone fills 48 chars, so the unique
    -{index}-{symbol} suffix is chopped and every order in a run collides ->
    broker rejects all but the first as "client_order_id must be unique".
    """
    text = str(raw or "").strip()
    if text and len(text) <= 48 and all(32 <= ord(ch) <= 126 for ch in text):
        return text
    return "qd:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:45]


def _clean_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _safe_float(value: object) -> float | None:
    try:
        numeric = float(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _fractional_allowed(env: Mapping[str, str]) -> bool:
    return _truthy(env.get(LIVE_PILOT_ALLOW_FRACTIONAL_ENV))


def normalize_live_pilot_limit_price(limit_price: float) -> float:
    places = Decimal("0.01") if float(limit_price) >= 1.0 else Decimal("0.0001")
    return float(Decimal(str(limit_price)).quantize(places, rounding=ROUND_HALF_UP))


def validate_live_pilot_plan(
    trades: Iterable[Mapping[str, object]],
    *,
    env: Mapping[str, str] | None = None,
    capital_cap_usd: float,
    max_orders: int,
    run_id: str,
    sell_inventory: Mapping[str, object] | None = None,
    allow_fractional_sells: bool = False,
) -> LivePilotPlanValidation:
    """Validate the day's candidate live-pilot orders with PER-ORDER partitioning.

    BLOCKER 3 fix (PRE_ARM_SWEEP_2026-07-13 §d): previously a single invalid or
    unresolvable order (bad symbol format, unsupported asset, unauthorized sell,
    ...) put ALL of that trade's errors into one list and returned
    ``orders=[]`` the moment ANY error existed — so one bad BUY candidate blocked
    the SELLs that free capital too. Now each order is validated independently:
    a failing order is DROPPED (recorded in ``dropped_orders`` with its own
    reason_code) and every other order proceeds. The whole batch is only
    ``BLOCKED`` when NOT ONE order survives, or when a genuine BATCH-level
    constraint (order-count ceiling, total buy notional over the approved cap)
    is violated by the surviving orders — those two remain batch-scoped because
    there is no principled per-order way to decide which order to keep/drop to
    satisfy them, unlike a per-symbol validity problem.
    """
    environ = _env_mapping(env)
    batch_errors: list[str] = []
    orders: list[LivePilotOrder] = []
    dropped: list[LivePilotDroppedOrder] = []
    total = 0.0
    sells_enabled = _truthy(environ.get(LIVE_PILOT_SELLS_ENABLED_ENV))
    fractional_allowed = _fractional_allowed(environ)
    # Only finite positive long broker positions are eligible, and cumulative
    # SELL quantity is bounded by the pretrade holding so this boundary cannot
    # accidentally synthesize a short position. Missing inventory fails closed.
    normalized_sell_inventory: dict[str, Decimal] | None = None
    if sell_inventory is not None:
        normalized_sell_inventory = {}
        for raw_symbol, raw_qty in sell_inventory.items():
            inventory_symbol = _clean_symbol(raw_symbol)
            inventory_qty = _safe_float(raw_qty)
            if (
                inventory_symbol
                and inventory_qty is not None
                and inventory_qty == inventory_qty
                and inventory_qty not in {float("inf"), float("-inf")}
                and inventory_qty > 0.0
            ):
                normalized_sell_inventory[inventory_symbol] = Decimal(str(inventory_qty))
    planned_sell_qty: dict[str, Decimal] = {}

    def _drop(symbol: str, side: str, reason_code: str) -> None:
        # A dropped SELL means we are failing to exit a HELD position — treat it
        # as critical/loud even though (per the partitioning fix) it does not
        # block the rest of the batch. A dropped BUY is a lower-severity miss.
        severity = "critical" if side == "SELL" else "warning"
        dropped.append(
            LivePilotDroppedOrder(
                symbol=symbol,
                side=side,
                reason_code=reason_code,
                severity=severity,
                stage="plan_validation",
            )
        )
        if severity == "critical":
            logger.error(
                "live_pilot_sell_dropped_at_validation: symbol=%s reason=%s — "
                "an intended SELL of a held position was dropped; it will NOT be "
                "submitted this run. Other orders proceed.",
                symbol,
                reason_code,
            )

    for index, raw_trade in enumerate(trades or []):
        symbol = _clean_symbol(raw_trade.get("symbol") or raw_trade.get("ticker"))
        side = str(raw_trade.get("side") or "").strip().upper()
        qty = _safe_float(raw_trade.get("qty") or raw_trade.get("shares"))
        limit_price = _safe_float(
            raw_trade.get("limit_price")
            or raw_trade.get("price")
            or raw_trade.get("expected_price")
            or raw_trade.get("cap_enforcement_price")
        )
        order_type = str(raw_trade.get("order_type") or "limit").strip().lower()
        if not symbol:
            _drop(f"order_{index}", side, "missing_symbol")
            continue
        if not symbol.replace(".", "").isalpha():
            _drop(symbol, side, f"{symbol}:unsupported_symbol_format")
            continue
        if symbol.endswith("USD") and len(symbol) > 4:
            _drop(symbol, side, f"{symbol}:unsupported_crypto_symbol")
            continue
        if side not in {"BUY", "SELL"}:
            _drop(symbol, side, f"{symbol}:unsupported_side:{side or 'missing'}")
            continue
        if side == "SELL" and not sells_enabled:
            # Master gate (fail-closed): live sells are globally disabled unless
            # CAERUS_LIVE_PILOT_SELLS_ENABLED is explicitly on. Broker inventory
            # remains mandatory even when the master gate is enabled.
            _drop(symbol, side, f"{symbol}:sells_disabled")
            continue
        if qty is None or qty <= 0:
            _drop(symbol, side, f"{symbol}:non_positive_qty")
            continue
        row_fractional_allowed = fractional_allowed or (
            side == "SELL" and bool(allow_fractional_sells)
        )
        if not row_fractional_allowed and abs(qty - round(qty)) > 1e-9:
            _drop(symbol, side, f"{symbol}:fractional_qty_not_allowed")
            continue
        if order_type not in {"limit", "market"}:
            _drop(symbol, side, f"{symbol}:unsupported_order_type:{order_type or 'missing'}")
            continue
        if limit_price is None or limit_price <= 0:
            _drop(symbol, side, f"{symbol}:missing_positive_cap_enforcement_price")
            continue
        original_limit_price = _safe_float(raw_trade.get("original_limit_price")) or float(limit_price)
        normalized_input = _safe_float(raw_trade.get("normalized_limit_price")) or float(limit_price)
        normalized_limit_price = normalize_live_pilot_limit_price(normalized_input)
        notional = round(float(qty) * float(normalized_limit_price), 6)
        if notional <= 0:
            _drop(symbol, side, f"{symbol}:non_positive_notional")
            continue
        if side == "SELL":
            if normalized_sell_inventory is None:
                _drop(symbol, side, f"{symbol}:sell_inventory_unavailable")
                continue
            else:
                held_qty = normalized_sell_inventory.get(symbol)
                if held_qty is None:
                    _drop(symbol, side, f"{symbol}:sell_position_not_held")
                    continue
                cumulative_qty = planned_sell_qty.get(symbol, Decimal("0")) + Decimal(str(qty))
                if cumulative_qty > held_qty:
                    _drop(symbol, side, f"{symbol}:sell_qty_exceeds_held_position")
                    continue
                planned_sell_qty[symbol] = cumulative_qty
        total += notional
        orders.append(
            LivePilotOrder(
                symbol=symbol,
                side=side,
                qty=float(qty),
                limit_price=float(normalized_limit_price),
                notional=notional,
                client_order_id=_bounded_client_order_id(
                    f"caerus-live-pilot-{run_id}-{index}-{symbol}".lower()
                ),
                order_type=order_type,
                expected_price=float(normalized_limit_price),
                original_limit_price=original_limit_price,
                normalized_limit_price=float(normalized_limit_price),
            )
        )

    if not orders:
        batch_errors.append("no_live_pilot_orders_after_validation")
    buy_count = sum(1 for order in orders if order.side == "BUY")
    if buy_count > int(max_orders):
        batch_errors.append("live_pilot_order_count_exceeds_max_orders")
        batch_errors.append("live_pilot_buy_order_count_exceeds_max_orders")
    # The cap bounds deployed BUY notional (the "portfolio size for Live"), not gross
    # turnover. A full rebalance sells over-weight/removed names AND buys under-weight
    # ones; summing both sides would block legitimate high-turnover rebalances whose
    # buys stay within the portfolio value. Sells are exits (they fund buys, gated by
    # the fail-closed sells master flag + broker inventory) and never consume the buying cap.
    buy_notional = sum(order.notional for order in orders if order.side == "BUY")
    if capital_cap_usd is None:
        batch_errors.append("live_pilot_capital_cap_unresolved")
    elif buy_notional > float(capital_cap_usd):
        batch_errors.append("live_pilot_total_notional_exceeds_cap")

    if batch_errors:
        # Batch-level failure (nothing survived, or a genuine batch-scoped sizing
        # constraint was tripped by the surviving orders) — fail closed exactly as
        # before. dropped_orders is still attached for the audit trail.
        return LivePilotPlanValidation(
            status="BLOCKED",
            reason_codes=batch_errors + [d.reason_code for d in dropped],
            orders=[],
            dropped_orders=dropped,
            total_notional=round(total, 6),
            operator_action="Fix or remove blocked live pilot orders; no live orders may be submitted.",
        )
    return LivePilotPlanValidation(
        status="PASS",
        reason_codes=[],
        orders=orders,
        dropped_orders=dropped,
        total_notional=round(total, 6),
        operator_action=(
            "Validated live pilot orders may proceed only if all runtime guardrails pass."
            if not dropped
            else (
                "Some candidate orders were dropped at validation (see dropped_orders); "
                "all remaining valid orders may proceed only if all runtime guardrails pass."
            )
        ),
    )


def validate_live_pilot_asset(asset: Mapping[str, object] | None, symbol: str) -> str | None:
    if not asset:
        return f"{symbol}:asset_lookup_missing"
    tradable = str(asset.get("tradable", "")).strip().lower()
    status = str(asset.get("status", "")).strip().lower().replace("assetstatus.", "")
    asset_class = str(asset.get("asset_class", "")).strip().lower().replace("assetclass.", "")
    if tradable in {"0", "false", "no", "n", "off"}:
        return f"{symbol}:asset_not_tradable"
    if status and status != "active":
        return f"{symbol}:asset_not_active"
    if asset_class and asset_class not in EQUITY_ASSET_CLASSES:
        return f"{symbol}:unsupported_asset_class:{asset_class}"
    return None
