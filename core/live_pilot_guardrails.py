from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Iterable, Mapping

from core.trading_mode import canonical_trading_mode


LIVE_PILOT_MODE = "live_pilot"
LIVE_PREFLIGHT_MODE = "live_preflight"
PAPER_MODE = "paper"

LIVE_PILOT_APPROVED_ENV = "CAERUS_LIVE_PILOT_APPROVED"
LIVE_PILOT_CAPITAL_CAP_ENV = "CAERUS_LIVE_PILOT_CAPITAL_CAP"
LIVE_PILOT_SLEEVE_ID_ENV = "CAERUS_LIVE_PILOT_SLEEVE_ID"
LIVE_PILOT_ACCOUNT_ID_ENV = "CAERUS_LIVE_PILOT_ACCOUNT_ID"
LIVE_PILOT_ACCOUNT_ID_HASH_ENV = "CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH"
LIVE_PILOT_MAX_ORDERS_ENV = "CAERUS_LIVE_PILOT_MAX_ORDERS"
LIVE_PILOT_KILL_SWITCH_ENV = "CAERUS_LIVE_PILOT_KILL_SWITCH"
LIVE_PILOT_DRY_RUN_ENV = "CAERUS_LIVE_PILOT_DRY_RUN"
LIVE_PILOT_CRON_APPROVED_ENV = "CAERUS_LIVE_PILOT_CRON_APPROVED"
LIVE_PILOT_SELL_WHITELIST_ENV = "CAERUS_LIVE_PILOT_SELL_WHITELIST"
LIVE_PILOT_ALLOW_FRACTIONAL_ENV = "CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL"

LIVE_PILOT_MAX_CAP_USD = 100.0
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

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "limit_price": self.limit_price,
            "notional": self.notional,
            "client_order_id": self.client_order_id,
            "order_type": self.order_type,
        }


@dataclass(frozen=True)
class LivePilotPlanValidation:
    status: str
    reason_codes: list[str]
    orders: list[LivePilotOrder]
    total_notional: float
    operator_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "orders": [order.to_dict() for order in self.orders],
            "total_notional": self.total_notional,
            "operator_action": self.operator_action,
        }


def _env_mapping(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in LIVE_ALLOWED_VALUES


def _dry_run_enabled(value: object) -> bool:
    raw = str(value if value is not None else "1").strip().lower()
    return raw not in FALSE_VALUES


def _positive_float(value: object) -> float | None:
    try:
        numeric = float(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def _positive_int(value: object) -> int | None:
    try:
        numeric = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


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
    kill_switch = _truthy(environ.get(LIVE_PILOT_KILL_SWITCH_ENV))
    cron_context = _cron_context(environ)
    cron_approved = _truthy(environ.get(LIVE_PILOT_CRON_APPROVED_ENV))

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
            f"Set a positive {LIVE_PILOT_CAPITAL_CAP_ENV} at or below {LIVE_PILOT_MAX_CAP_USD:.0f}.",
        )
    if cap > LIVE_PILOT_MAX_CAP_USD:
        return result(
            "live_pilot_capital_cap_exceeds_program_limit",
            f"Set {LIVE_PILOT_CAPITAL_CAP_ENV}<={LIVE_PILOT_MAX_CAP_USD:.0f}.",
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
    if max_orders is None:
        return result(
            "missing_positive_live_pilot_max_orders",
            f"Set a positive {LIVE_PILOT_MAX_ORDERS_ENV}.",
        )
    if kill_switch:
        return result(
            "live_pilot_kill_switch_enabled",
            f"Unset {LIVE_PILOT_KILL_SWITCH_ENV} before any pilot action.",
        )
    if cron_context and not cron_approved:
        return result(
            "live_pilot_cron_context_requires_explicit_approval",
            f"Set {LIVE_PILOT_CRON_APPROVED_ENV}=1 only after scheduled live pilot approval.",
        )
    if dry_run:
        return result(
            "live_pilot_dry_run_enabled",
            "Dry run may write isolated artifacts, but must not submit live orders.",
            status="PASS",
            allowed=False,
        )
    if submission_intent and order_notional is None:
        return result(
            "missing_live_order_notional",
            "Live pilot order submission requires a pre-submit notional estimate.",
        )
    if order_notional is not None and float(order_notional) > float(cap):
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


def _clean_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _safe_float(value: object) -> float | None:
    try:
        numeric = float(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return numeric


def _sell_whitelist(env: Mapping[str, str]) -> set[str]:
    raw = str(env.get(LIVE_PILOT_SELL_WHITELIST_ENV) or "").strip()
    return {_clean_symbol(item) for item in raw.split(",") if _clean_symbol(item)}


def _fractional_allowed(env: Mapping[str, str]) -> bool:
    return _truthy(env.get(LIVE_PILOT_ALLOW_FRACTIONAL_ENV))


def validate_live_pilot_plan(
    trades: Iterable[Mapping[str, object]],
    *,
    env: Mapping[str, str] | None = None,
    capital_cap_usd: float,
    max_orders: int,
    run_id: str,
) -> LivePilotPlanValidation:
    environ = _env_mapping(env)
    errors: list[str] = []
    orders: list[LivePilotOrder] = []
    total = 0.0
    sell_whitelist = _sell_whitelist(environ)
    fractional_allowed = _fractional_allowed(environ)

    for index, raw_trade in enumerate(trades or []):
        symbol = _clean_symbol(raw_trade.get("symbol") or raw_trade.get("ticker"))
        side = str(raw_trade.get("side") or "").strip().upper()
        qty = _safe_float(raw_trade.get("qty") or raw_trade.get("shares"))
        limit_price = _safe_float(raw_trade.get("limit_price") or raw_trade.get("price"))
        order_type = str(raw_trade.get("order_type") or "limit").strip().lower()
        if not symbol:
            errors.append(f"order_{index}:missing_symbol")
            continue
        if not symbol.replace(".", "").isalpha():
            errors.append(f"{symbol}:unsupported_symbol_format")
            continue
        if symbol.endswith("USD") and len(symbol) > 4:
            errors.append(f"{symbol}:unsupported_crypto_symbol")
            continue
        if side not in {"BUY", "SELL"}:
            errors.append(f"{symbol}:unsupported_side:{side or 'missing'}")
            continue
        if side == "SELL" and symbol not in sell_whitelist:
            errors.append(f"{symbol}:sell_not_whitelisted")
            continue
        if qty is None or qty <= 0:
            errors.append(f"{symbol}:non_positive_qty")
            continue
        if not fractional_allowed and abs(qty - round(qty)) > 1e-9:
            errors.append(f"{symbol}:fractional_qty_not_allowed")
            continue
        if order_type != "limit":
            errors.append(f"{symbol}:only_limit_orders_supported")
            continue
        if limit_price is None or limit_price <= 0:
            errors.append(f"{symbol}:missing_positive_limit_price")
            continue
        notional = round(float(qty) * float(limit_price), 6)
        if notional <= 0:
            errors.append(f"{symbol}:non_positive_notional")
            continue
        total += notional
        orders.append(
            LivePilotOrder(
                symbol=symbol,
                side=side,
                qty=float(qty),
                limit_price=float(limit_price),
                notional=notional,
                client_order_id=f"caerus-live-pilot-{run_id}-{index}-{symbol}".lower()[:48],
            )
        )

    if not orders:
        errors.append("no_live_pilot_orders_after_validation")
    if len(orders) > int(max_orders):
        errors.append("live_pilot_order_count_exceeds_max_orders")
    if total > float(capital_cap_usd):
        errors.append("live_pilot_total_notional_exceeds_cap")

    if errors:
        return LivePilotPlanValidation(
            status="BLOCKED",
            reason_codes=errors,
            orders=[],
            total_notional=round(total, 6),
            operator_action="Fix or remove blocked live pilot orders; no live orders may be submitted.",
        )
    return LivePilotPlanValidation(
        status="PASS",
        reason_codes=[],
        orders=orders,
        total_notional=round(total, 6),
        operator_action="Validated live pilot orders may proceed only if all runtime guardrails pass.",
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
