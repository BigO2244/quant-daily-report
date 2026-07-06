"""Task 0 (Workstream B): kill switch, gate JSON reads, and account-hash match fail closed.

These tests pin the fail-closed contract added in the safety-stabilization commit:

1. The live-pilot kill switch is ENGAGED for any value that is not an explicit,
   recognized "off" token (unset / empty / garbage / "1" all block). Only an
   explicit off value ("0"/"false"/"off"/...) disarms it.
2. A present-but-corrupt execution artifact surfaces as an explicit critical
   failure (RED) in the reliability report instead of being silently swallowed to
   ``{}`` and classified as a clean no-op. A *missing* artifact stays benign.
3. The account-hash *match* (not merely presence of the expected id/hash) is wired
   into the orchestrated gate result: a mismatch or a missing broker-reported
   account id blocks before any submission.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.live_pilot_guardrails import (
    LIVE_PILOT_ACCOUNT_ID_ENV,
    LIVE_PILOT_ACCOUNT_ID_HASH_ENV,
    LIVE_PILOT_KILL_SWITCH_ENV,
    account_id_hash,
    build_live_pilot_gate_result,
    kill_switch_engaged,
)
from core.operational_invariants import (
    RELIABILITY_RED,
    build_execution_reliability_report,
)


LIVE_HOST = "https://api.alpaca.markets"


def _armed_env(**overrides: str | None) -> dict[str, str]:
    """A fully-armed live-pilot env with an explicitly disarmed kill switch."""
    env = {
        "TRADING_MODE": "live_pilot",
        "CAERUS_LIVE_PILOT_APPROVED": "1",
        "CAERUS_LIVE_PILOT_CAPITAL_CAP": "500",
        "CAERUS_LIVE_PILOT_SLEEVE_ID": "polaris",
        LIVE_PILOT_ACCOUNT_ID_ENV: "acct-123",
        "CAERUS_LIVE_PILOT_MAX_ORDERS": "1",
        "CAERUS_LIVE_PILOT_DRY_RUN": "0",
        LIVE_PILOT_KILL_SWITCH_ENV: "0",
    }
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


# --------------------------------------------------------------------------- #
# 1. Kill switch fails closed
# --------------------------------------------------------------------------- #

# (value, expected_engaged). ``None`` means the env var is absent entirely.
KILL_SWITCH_CASES = [
    ("0", False),
    ("false", False),
    ("FALSE", False),
    ("  false  ", False),
    ("no", False),
    ("n", False),
    ("off", False),
    (None, True),          # unset -> engaged
    ("", True),            # empty -> engaged
    ("   ", True),         # whitespace -> engaged
    ("garbage", True),     # unrecognized -> engaged
    ("disable", True),     # looks like intent to disarm but not recognized -> engaged
    ("1", True),           # explicit on -> engaged
    ("true", True),        # explicit on -> engaged
    ("on", True),          # explicit on -> engaged
    ("2", True),           # unparseable -> engaged
]


@pytest.mark.parametrize(("value", "expected_engaged"), KILL_SWITCH_CASES)
def test_kill_switch_engaged_parses_fail_closed(value: str | None, expected_engaged: bool) -> None:
    assert kill_switch_engaged(value) is expected_engaged


@pytest.mark.parametrize(("value", "expected_engaged"), KILL_SWITCH_CASES)
def test_kill_switch_gate_blocks_unless_explicit_off(value: str | None, expected_engaged: bool) -> None:
    env = _armed_env(**{LIVE_PILOT_KILL_SWITCH_ENV: value})
    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url=LIVE_HOST,
        env=env,
        order_notional=50.0,
        submission_intent=True,
    )
    if expected_engaged:
        assert result.status == "BLOCKED"
        assert result.reason_code == "live_pilot_kill_switch_enabled"
        assert result.live_orders_allowed is False
        assert result.kill_switch is True
    else:
        # An explicit off value is the ONLY way to reach a PASS/allowed gate.
        assert result.reason_code != "live_pilot_kill_switch_enabled"
        assert result.kill_switch is False
        assert result.status == "PASS"
        assert result.live_orders_allowed is True


def test_kill_switch_unset_blocks_even_when_everything_else_is_armed() -> None:
    # Regression for the July-6-class fail-open: a missing kill-switch env must not
    # silently arm live. Removing the var entirely still blocks.
    env = _armed_env(**{LIVE_PILOT_KILL_SWITCH_ENV: None})
    assert LIVE_PILOT_KILL_SWITCH_ENV not in env
    result = build_live_pilot_gate_result(
        broker_paper=False, base_url=LIVE_HOST, env=env, order_notional=10.0, submission_intent=True
    )
    assert result.status == "BLOCKED"
    assert result.reason_code == "live_pilot_kill_switch_enabled"


# --------------------------------------------------------------------------- #
# 2. Corrupt execution artifacts fail closed (RED), missing artifacts stay benign
# --------------------------------------------------------------------------- #

_TRADE_DATE = "2099-01-05"  # far-future weekday: avoids colliding with real outputs/precompute
_RUN_ID = "2099-01-05T093000-0000_failclosed"


def _read_error_invariants(report: dict) -> list[dict]:
    return [
        row
        for row in report["invariant_results"]
        if row.get("reason_code") == "execution_artifact_unreadable"
    ]


def test_corrupt_execution_artifact_is_red_not_silent_noaction(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    # Present but unparseable execution_results.json (truncated / malformed).
    (run_root / "execution_results.json").write_text("{ not valid json", encoding="utf-8")

    report = build_execution_reliability_report(
        run_root=run_root, trade_date=_TRADE_DATE, run_id=_RUN_ID
    )

    read_errors = _read_error_invariants(report)
    assert read_errors, "corrupt artifact must surface an explicit read-error invariant"
    assert read_errors[0]["status"] == "FAIL"
    assert read_errors[0]["severity"] == "critical"
    assert any(
        e["artifact"] == "execution_results"
        for e in read_errors[0]["evidence"]["artifact_read_errors"]
    )
    assert report["classification"] == RELIABILITY_RED


def test_non_object_json_artifact_is_read_error(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    # Valid JSON, but not a JSON object (a list) — previously swallowed to {}.
    (run_root / "operator_summary.json").write_text("[1, 2, 3]", encoding="utf-8")

    report = build_execution_reliability_report(
        run_root=run_root, trade_date=_TRADE_DATE, run_id=_RUN_ID
    )
    read_errors = _read_error_invariants(report)
    assert read_errors and read_errors[0]["status"] == "FAIL"


def test_missing_artifacts_are_not_read_errors(tmp_path: Path) -> None:
    # A run root with NO artifacts must not raise a read-error (missing != corrupt).
    run_root = tmp_path / "run"
    run_root.mkdir()
    report = build_execution_reliability_report(
        run_root=run_root, trade_date=_TRADE_DATE, run_id=_RUN_ID
    )
    assert _read_error_invariants(report) == []


# --------------------------------------------------------------------------- #
# 3. Account-hash match wired into the orchestrated gate result
# --------------------------------------------------------------------------- #

def test_account_id_match_allows_when_broker_matches() -> None:
    env = _armed_env(**{LIVE_PILOT_ACCOUNT_ID_ENV: "acct-123"})
    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url=LIVE_HOST,
        env=env,
        account_id="acct-123",
        enforce_account_match=True,
    )
    assert result.account_id_match is True
    assert result.account_match_reason == "account_id_match"
    assert result.reason_code != "live_pilot_account_id_mismatch"


def test_account_id_mismatch_blocks() -> None:
    env = _armed_env(**{LIVE_PILOT_ACCOUNT_ID_ENV: "acct-123"})
    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url=LIVE_HOST,
        env=env,
        account_id="acct-999-WRONG",
        enforce_account_match=True,
    )
    assert result.status == "BLOCKED"
    assert result.reason_code == "live_pilot_account_id_mismatch"
    assert result.account_id_match is False
    assert result.account_match_reason == "account_id_mismatch"
    assert result.live_orders_allowed is False


def test_missing_broker_account_id_blocks() -> None:
    # Broker did not report an id at all -> fail closed, never "allow because configured".
    env = _armed_env(**{LIVE_PILOT_ACCOUNT_ID_ENV: "acct-123"})
    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url=LIVE_HOST,
        env=env,
        account_id=None,
        enforce_account_match=True,
    )
    assert result.status == "BLOCKED"
    assert result.reason_code == "live_pilot_account_id_mismatch"
    assert result.account_id_match is False
    assert result.account_match_reason == "missing_actual_account_id"


def test_account_id_hash_fallback_matches() -> None:
    # When the broker exposes only a hash (no raw id), the env hash still matches.
    expected_hash = account_id_hash("acct-xyz")
    env = _armed_env(**{LIVE_PILOT_ACCOUNT_ID_HASH_ENV: expected_hash})
    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url=LIVE_HOST,
        env=env,
        account_id=None,
        account_id_hash=expected_hash,
        enforce_account_match=True,
    )
    assert result.account_id_match is True
    assert result.account_match_reason == "account_id_hash_match"


def test_account_match_not_enforced_when_flag_off_is_presence_only() -> None:
    # Backward-compatible default: without enforce_account_match, the gate is
    # presence-only and account_id_match stays unknown (None).
    env = _armed_env()
    result = build_live_pilot_gate_result(
        broker_paper=False,
        base_url=LIVE_HOST,
        env=env,
        order_notional=10.0,
        submission_intent=True,
    )
    assert result.account_id_match is None
    assert result.status == "PASS"
