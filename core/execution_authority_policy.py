"""Choice-2 capital-mutation policy.

This module deliberately contains no environment-variable escape hatch.  The
legacy engines remain importable so their deterministic planning and reporting
logic can be tested, but a real Alpaca broker may only be mutated by the exact
execution-plan lane.  Live equity and every options lane are disabled by owner
policy until a future code change (with its own review) replaces these
constants.

Mutation-path inventory covered by this policy:

* ``daily_quant_report -> paper.paper_broker.run_paper_day``
* ``paper/run_paper.py -> paper.paper_broker.run_paper_day``
* ``scripts/run_precomputed_alpaca_execution.py -> run_paper_day``
* ``scripts/execute_alpaca_orders.py -> AlpacaBroker``
* ``core.options_execution`` and ``core.options_smoke_session``

The exact executor is intentionally not named here: it owns the sole approved
PAPER mutation path and enforces its immutable contract separately.
"""

from __future__ import annotations

from typing import Any


EXACT_PAPER_EXECUTION_AUTHORITY = "caerus_orchestrator_exact_plan_only"
LEGACY_EQUITY_EXECUTION_AUTHORITY = "disabled_choice2_exact_plan_required"
LIVE_CAPITAL_EXECUTION_AUTHORITY = "disabled_by_owner_policy"
OPTIONS_CAPITAL_EXECUTION_AUTHORITY = "disabled_by_owner_policy"

LEGACY_EQUITY_BROKER_MUTATION_ENABLED = False
LIVE_CAPITAL_MUTATION_ENABLED = False
OPTIONS_CAPITAL_MUTATION_ENABLED = False

LEGACY_MUTATION_REASON = "legacy_executor_disabled_choice2_exact_plan_required"
LIVE_MUTATION_REASON = "live_capital_disabled_by_owner_policy"
OPTIONS_MUTATION_REASON = "options_capital_disabled_by_owner_policy"


class ExecutionAuthorityViolation(PermissionError, RuntimeError):
    """Raised before a code path without capital authority can mutate a broker."""


def is_production_alpaca_broker(broker: Any) -> bool:
    """Identify the real adapter without trusting mutable environment state.

    Test doubles intentionally do not match.  That lets unit/integration tests
    exercise legacy order-planning mechanics without any route to a broker.
    Subclasses of the production adapter remain covered through their MRO.
    """

    if broker is None:
        return False
    return any(
        getattr(base, "__module__", "") == "brokers.alpaca_broker"
        and getattr(base, "__name__", "") == "AlpacaBroker"
        for base in type(broker).__mro__
    )


def require_legacy_equity_test_double(*, broker: Any, mutation_path: str) -> None:
    """Block a legacy equity route when it reaches a real broker adapter."""

    if LEGACY_EQUITY_BROKER_MUTATION_ENABLED:
        raise AssertionError("Choice-2 legacy mutation constant must remain false")
    if is_production_alpaca_broker(broker):
        raise ExecutionAuthorityViolation(f"{LEGACY_MUTATION_REASON}:{mutation_path}")


def require_legacy_cli_planning_only(*, plan_only: bool, mutation_path: str) -> None:
    """Allow the legacy paper CLI only when explicitly used as a planner."""

    if not bool(plan_only):
        raise ExecutionAuthorityViolation(f"{LEGACY_MUTATION_REASON}:{mutation_path}")


def require_options_capital_disabled(*, mutation_path: str) -> None:
    """Unconditionally reject any options submission request."""

    if OPTIONS_CAPITAL_MUTATION_ENABLED:
        raise AssertionError("Choice-2 options mutation constant must remain false")
    raise ExecutionAuthorityViolation(f"{OPTIONS_MUTATION_REASON}:{mutation_path}")


def require_live_capital_disabled(*, mutation_path: str) -> None:
    """Unconditionally reject any live-equity submission request."""

    if LIVE_CAPITAL_MUTATION_ENABLED:
        raise AssertionError("Choice-2 live mutation constant must remain false")
    raise ExecutionAuthorityViolation(f"{LIVE_MUTATION_REASON}:{mutation_path}")


__all__ = [
    "EXACT_PAPER_EXECUTION_AUTHORITY",
    "LEGACY_EQUITY_EXECUTION_AUTHORITY",
    "LIVE_CAPITAL_EXECUTION_AUTHORITY",
    "OPTIONS_CAPITAL_EXECUTION_AUTHORITY",
    "LEGACY_EQUITY_BROKER_MUTATION_ENABLED",
    "LIVE_CAPITAL_MUTATION_ENABLED",
    "OPTIONS_CAPITAL_MUTATION_ENABLED",
    "LEGACY_MUTATION_REASON",
    "LIVE_MUTATION_REASON",
    "OPTIONS_MUTATION_REASON",
    "ExecutionAuthorityViolation",
    "is_production_alpaca_broker",
    "require_legacy_equity_test_double",
    "require_legacy_cli_planning_only",
    "require_options_capital_disabled",
    "require_live_capital_disabled",
]
