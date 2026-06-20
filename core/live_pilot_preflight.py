from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from core.live_pilot_guardrails import (
    LIVE_PILOT_MODE,
    build_live_pilot_gate_result,
    validate_live_pilot_submission_guardrails,
)
from core.trading_mode import canonical_trading_mode, raw_trading_mode


LIVE_PREFLIGHT_MODE = "live_preflight"
LIVE_MODE = "live"
PAPER_MODE = "paper"

LIVE_TRADING_FLAG_ENV = "CAERUS_ALLOW_LIVE_TRADING"
LIVE_CAPITAL_CAP_ENV = "CAERUS_LIVE_CAPITAL_CAP_USD"
LIVE_PILOT_SLEEVE_ENV = "CAERUS_APPROVED_PILOT_SLEEVE_ID"

LIVE_ALLOWED_VALUES = frozenset({"1", "true", "yes", "y", "on", "approve_live_pilot"})


@dataclass(frozen=True)
class LivePilotPreflightResult:
    status: str
    reason_code: str
    requested_mode: str
    broker_paper: bool
    base_url: str
    live_trading_flag_set: bool
    capital_cap_usd: float | None
    approved_pilot_sleeve_id: str | None
    live_orders_allowed: bool
    operator_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "requested_mode": self.requested_mode,
            "broker_paper": self.broker_paper,
            "base_url": self.base_url,
            "live_trading_flag_env": LIVE_TRADING_FLAG_ENV,
            "live_trading_flag_set": self.live_trading_flag_set,
            "capital_cap_env": LIVE_CAPITAL_CAP_ENV,
            "capital_cap_usd": self.capital_cap_usd,
            "approved_pilot_sleeve_env": LIVE_PILOT_SLEEVE_ENV,
            "approved_pilot_sleeve_id": self.approved_pilot_sleeve_id,
            "live_orders_allowed": self.live_orders_allowed,
            "operator_action": self.operator_action,
        }


def _truthy_live_flag(value: object) -> bool:
    return str(value or "").strip().lower() in LIVE_ALLOWED_VALUES


def _positive_float(value: object) -> float | None:
    try:
        numeric = float(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def _env_mapping(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def requested_mode_from_env(env: Mapping[str, str] | None = None) -> str:
    environ = _env_mapping(env)
    mode_raw = str(environ.get("MODE", "") or "").strip()
    trading_mode_raw = str(environ.get("TRADING_MODE", "") or "").strip()
    source_value = trading_mode_raw or mode_raw or PAPER_MODE
    mode = canonical_trading_mode(
        source_value,
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


def build_live_pilot_preflight_result(
    *,
    broker_paper: bool,
    base_url: str,
    env: Mapping[str, str] | None = None,
    order_notional: float | None = None,
) -> LivePilotPreflightResult:
    environ = _env_mapping(env)
    requested_mode = requested_mode_from_env(environ)
    base_url_norm = str(base_url or "").strip().rstrip("/")
    live_flag = _truthy_live_flag(environ.get(LIVE_TRADING_FLAG_ENV))
    cap = _positive_float(environ.get(LIVE_CAPITAL_CAP_ENV))
    sleeve = str(environ.get(LIVE_PILOT_SLEEVE_ENV) or "").strip() or None
    live_host = "api.alpaca.markets" in base_url_norm.lower() and "paper-api" not in base_url_norm.lower()
    paper_host = "paper-api.alpaca.markets" in base_url_norm.lower()

    if requested_mode == LIVE_PILOT_MODE:
        pilot = build_live_pilot_gate_result(
            broker_paper=broker_paper,
            base_url=base_url_norm,
            env=environ,
            order_notional=order_notional,
            submission_intent=False,
        )
        return LivePilotPreflightResult(
            status=pilot.status,
            reason_code=pilot.reason_code,
            requested_mode=pilot.requested_mode,
            broker_paper=pilot.broker_paper,
            base_url=pilot.base_url,
            live_trading_flag_set=pilot.approved,
            capital_cap_usd=pilot.capital_cap_usd,
            approved_pilot_sleeve_id=pilot.sleeve_id,
            live_orders_allowed=pilot.live_orders_allowed,
            operator_action=pilot.operator_action,
        )

    if requested_mode == LIVE_PREFLIGHT_MODE:
        return LivePilotPreflightResult(
            status="BLOCKED",
            reason_code="live_preflight_never_submits_orders",
            requested_mode=requested_mode,
            broker_paper=bool(broker_paper),
            base_url=base_url_norm,
            live_trading_flag_set=live_flag,
            capital_cap_usd=cap,
            approved_pilot_sleeve_id=sleeve,
            live_orders_allowed=False,
            operator_action="Review preflight evidence only; do not submit live orders in LIVE_PREFLIGHT mode.",
        )

    if requested_mode == LIVE_MODE and not bool(broker_paper):
        return LivePilotPreflightResult(
            status="BLOCKED",
            reason_code="legacy_live_mode_blocked_use_live_pilot",
            requested_mode=requested_mode,
            broker_paper=False,
            base_url=base_url_norm,
            live_trading_flag_set=live_flag,
            capital_cap_usd=cap,
            approved_pilot_sleeve_id=sleeve,
            live_orders_allowed=False,
            operator_action="Use TRADING_MODE=live_pilot with FR-104 guardrails; legacy live mode cannot submit orders.",
        )

    if bool(broker_paper):
        if requested_mode == LIVE_MODE:
            return LivePilotPreflightResult(
                status="BLOCKED",
                reason_code="live_mode_with_paper_broker_endpoint",
                requested_mode=requested_mode,
                broker_paper=True,
                base_url=base_url_norm,
                live_trading_flag_set=live_flag,
                capital_cap_usd=cap,
                approved_pilot_sleeve_id=sleeve,
                live_orders_allowed=False,
                operator_action="Resolve mode ambiguity before any pilot review.",
            )
        if not paper_host:
            return LivePilotPreflightResult(
                status="BLOCKED",
                reason_code="paper_broker_non_paper_endpoint",
                requested_mode=requested_mode,
                broker_paper=True,
                base_url=base_url_norm,
                live_trading_flag_set=live_flag,
                capital_cap_usd=cap,
                approved_pilot_sleeve_id=sleeve,
                live_orders_allowed=False,
                operator_action="Set ALPACA_PAPER=1 with the Alpaca paper API host or refuse submission.",
            )
        return LivePilotPreflightResult(
            status="PASS",
            reason_code="paper_submission_endpoint_confirmed",
            requested_mode=requested_mode,
            broker_paper=True,
            base_url=base_url_norm,
            live_trading_flag_set=live_flag,
            capital_cap_usd=cap,
            approved_pilot_sleeve_id=sleeve,
            live_orders_allowed=False,
            operator_action="Paper submission may proceed under existing paper controls.",
        )

    if requested_mode != LIVE_MODE:
        return LivePilotPreflightResult(
            status="BLOCKED",
            reason_code="live_broker_requires_explicit_live_mode",
            requested_mode=requested_mode,
            broker_paper=False,
            base_url=base_url_norm,
            live_trading_flag_set=live_flag,
            capital_cap_usd=cap,
            approved_pilot_sleeve_id=sleeve,
            live_orders_allowed=False,
            operator_action="Set TRADING_MODE=live only after pilot approval; paper modes must not use live credentials.",
        )
    if not live_host:
        return LivePilotPreflightResult(
            status="BLOCKED",
            reason_code="live_mode_requires_live_alpaca_endpoint",
            requested_mode=requested_mode,
            broker_paper=False,
            base_url=base_url_norm,
            live_trading_flag_set=live_flag,
            capital_cap_usd=cap,
            approved_pilot_sleeve_id=sleeve,
            live_orders_allowed=False,
            operator_action="Use the live Alpaca host only with an approved pilot packet.",
        )
    if not live_flag:
        return LivePilotPreflightResult(
            status="BLOCKED",
            reason_code="missing_explicit_live_trading_flag",
            requested_mode=requested_mode,
            broker_paper=False,
            base_url=base_url_norm,
            live_trading_flag_set=False,
            capital_cap_usd=cap,
            approved_pilot_sleeve_id=sleeve,
            live_orders_allowed=False,
            operator_action=f"Set {LIVE_TRADING_FLAG_ENV}=approve_live_pilot only after manual approval.",
        )
    if cap is None:
        return LivePilotPreflightResult(
            status="BLOCKED",
            reason_code="missing_positive_live_capital_cap",
            requested_mode=requested_mode,
            broker_paper=False,
            base_url=base_url_norm,
            live_trading_flag_set=True,
            capital_cap_usd=None,
            approved_pilot_sleeve_id=sleeve,
            live_orders_allowed=False,
            operator_action=f"Set a positive {LIVE_CAPITAL_CAP_ENV} before live pilot submission.",
        )
    if sleeve is None:
        return LivePilotPreflightResult(
            status="BLOCKED",
            reason_code="missing_approved_pilot_sleeve_id",
            requested_mode=requested_mode,
            broker_paper=False,
            base_url=base_url_norm,
            live_trading_flag_set=True,
            capital_cap_usd=cap,
            approved_pilot_sleeve_id=None,
            live_orders_allowed=False,
            operator_action=f"Set {LIVE_PILOT_SLEEVE_ENV} to the approved sleeve id before live pilot submission.",
        )
    if order_notional is not None and float(order_notional) > cap:
        return LivePilotPreflightResult(
            status="BLOCKED",
            reason_code="order_notional_exceeds_live_capital_cap",
            requested_mode=requested_mode,
            broker_paper=False,
            base_url=base_url_norm,
            live_trading_flag_set=True,
            capital_cap_usd=cap,
            approved_pilot_sleeve_id=sleeve,
            live_orders_allowed=False,
            operator_action="Reduce the live pilot order notional below the approved cap.",
        )

    return LivePilotPreflightResult(
        status="PASS",
        reason_code="live_order_guardrails_satisfied",
        requested_mode=requested_mode,
        broker_paper=False,
        base_url=base_url_norm,
        live_trading_flag_set=True,
        capital_cap_usd=cap,
        approved_pilot_sleeve_id=sleeve,
        live_orders_allowed=True,
        operator_action="Live order guardrails are satisfied; submit only through a separately approved live pilot path.",
    )


def build_live_pilot_preflight_result_from_env(
    env: Mapping[str, str] | None = None,
) -> LivePilotPreflightResult:
    environ = _env_mapping(env)
    paper_raw = str(environ.get("ALPACA_PAPER", "1") or "1").strip().lower()
    broker_paper = paper_raw not in {"0", "false", "no", "n", "off"}
    default_base = (
        "https://paper-api.alpaca.markets"
        if broker_paper
        else "https://api.alpaca.markets"
    )
    base_url = str(environ.get("ALPACA_BASE_URL") or default_base).strip().rstrip("/")
    if base_url.endswith("/v2"):
        base_url = base_url[:-3]
    return build_live_pilot_preflight_result(
        broker_paper=broker_paper,
        base_url=base_url,
        env=environ,
    )


def validate_alpaca_submission_guardrails(
    *,
    broker_paper: bool,
    base_url: str,
    env: Mapping[str, str] | None = None,
    order_notional: float | None = None,
) -> LivePilotPreflightResult:
    result = build_live_pilot_preflight_result(
        broker_paper=broker_paper,
        base_url=base_url,
        env=env,
        order_notional=order_notional,
    )
    if result.requested_mode == LIVE_PILOT_MODE:
        pilot = validate_live_pilot_submission_guardrails(
            broker_paper=broker_paper,
            base_url=base_url,
            env=env,
            order_notional=order_notional,
        )
        return LivePilotPreflightResult(
            status=pilot.status,
            reason_code=pilot.reason_code,
            requested_mode=pilot.requested_mode,
            broker_paper=pilot.broker_paper,
            base_url=pilot.base_url,
            live_trading_flag_set=pilot.approved,
            capital_cap_usd=pilot.capital_cap_usd,
            approved_pilot_sleeve_id=pilot.sleeve_id,
            live_orders_allowed=pilot.live_orders_allowed,
            operator_action=pilot.operator_action,
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


def mode_uses_live_preflight(value: object) -> bool:
    return canonical_trading_mode(
        raw_trading_mode(value, default=PAPER_MODE),
        field_name="trading_mode",
    ) == LIVE_PREFLIGHT_MODE
