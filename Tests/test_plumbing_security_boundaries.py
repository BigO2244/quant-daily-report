from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

MUTATION_CALL_NAMES = {
    "submit_market_order",
    "submit_limit_order",
    "submit_option_market_order",
    "submit_option_limit_order",
    "submit_order",
    "cancel_order",
    "cancel_orders",
    "replace_order",
    "close_position",
    "close_all_positions",
}

KNOWN_BROKER_MUTATION_MODULES = {
    Path("brokers/alpaca_broker.py"),
    Path("core/options_execution.py"),
    Path("core/options_smoke_session.py"),
    Path("paper/paper_broker.py"),
    Path("scripts/execute_alpaca_orders.py"),
    Path("scripts/execute_options_overlay.py"),
    Path("scripts/live_pilot_execute.py"),
    Path("scripts/options_smoke_session.py"),
}

READ_ONLY_BROKER_SURFACES = {
    Path("brokers/alpaca_snapshot.py"),
    Path("scripts/export_alpaca_broker_snapshot.py"),
    Path("scripts/send_trading_confirmation_email.py"),
}


def _call_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


def test_read_only_broker_surfaces_do_not_call_mutation_methods() -> None:
    offenders: dict[str, list[str]] = {}
    for relative_path in sorted(READ_ONLY_BROKER_SURFACES):
        path = REPO_ROOT / relative_path
        forbidden = sorted(_call_names(path) & MUTATION_CALL_NAMES)
        if forbidden:
            offenders[relative_path.as_posix()] = forbidden

    assert offenders == {}


def test_broker_mutation_calls_remain_confined_to_labeled_modules() -> None:
    offenders: dict[str, list[str]] = {}
    paths = [
        file_path
        for root in ("brokers", "core", "paper", "scripts")
        for file_path in (REPO_ROOT / root).rglob("*.py")
    ]
    for file_path in sorted(paths):
        relative_path = file_path.relative_to(REPO_ROOT)
        if relative_path in KNOWN_BROKER_MUTATION_MODULES:
            continue
        forbidden = sorted(_call_names(file_path) & MUTATION_CALL_NAMES)
        if forbidden:
            offenders[relative_path.as_posix()] = forbidden

    assert offenders == {}


def test_broker_snapshot_rest_fallback_uses_get_only() -> None:
    path = REPO_ROOT / "scripts" / "export_alpaca_broker_snapshot.py"
    text = path.read_text(encoding="utf-8")

    assert "urllib.request.Request(url, headers=headers)" in text
    assert "method=" not in text
    assert "urllib.request.urlopen" in text
