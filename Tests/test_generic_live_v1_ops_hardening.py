from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from authority.lane_exact_plan import canonical_json
from core.generic_live_v1_activation import (
    GenericLiveV1ActivationError,
    validate_generic_live_v1_activation_preflight,
)
from core.generic_live_v1_ops import (
    GenericLiveV1OpsError,
    install_config_with_backup,
    reject_sensitive_payload,
    restore_config_backup,
    secure_path,
)
from core.generic_live_v1_submission import (
    GenericLiveV1SubmissionError,
    ensure_generic_live_v1_rearmed_after_failure,
    execute_generic_live_v1_session,
)
from scripts.manage_generic_live_v1_cron import render_cron_line, update_crontab
from scripts.run_generic_live_v1_session import _require_exact_env, _require_source_pins
from Tests.test_generic_live_v1_activation import (
    EXPECTED, OBSERVATION, OWNER, _capture, _decision, _proofs,
    _raw_source_recompute,
)
from Tests.test_generic_live_v1_submission import Broker, _disarm, _ready


ROOT = Path(__file__).resolve().parents[1]


def _reseal(payload: dict) -> dict:
    body = copy.deepcopy(payload)
    body.pop("content_hash", None)
    payload["content_hash"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    return payload


def _protected_file(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)


def test_resealed_activation_with_arbitrary_gate_key_is_rejected() -> None:
    preflight, _ = _ready()
    forged = copy.deepcopy(preflight)
    forged["gate_results"]["invented_green_gate"] = True
    _reseal(forged)
    with pytest.raises(GenericLiveV1ActivationError, match="canonical activation contract"):
        validate_generic_live_v1_activation_preflight(forged)


def test_resealed_activation_with_inconsistent_reason_is_rejected() -> None:
    preflight, _ = _ready()
    forged = copy.deepcopy(preflight)
    forged["gate_results"]["accounting_pipeline_green"] = False
    forged["status"] = "BLOCKED"
    forged["reason_codes"] = ["ALL_OWNER_APPROVED_LIVE_V1_GATES_GREEN"]
    _reseal(forged)
    with pytest.raises(GenericLiveV1ActivationError, match="reason_codes"):
        validate_generic_live_v1_activation_preflight(forged)


@pytest.mark.parametrize("initial", (None, "not-json", '{"status":"DISARMED"}'))
def test_missing_or_malformed_gate_is_emergency_rearmed(tmp_path: Path, initial: str | None) -> None:
    state = tmp_path / "state" / "gate.json"
    if initial is not None:
        state.parent.mkdir(mode=0o700)
        _protected_file(state, initial)
    payload = ensure_generic_live_v1_rearmed_after_failure(
        state_path=state,
        preflight_hash=None,
        plan_hash=None,
        rearmed_at="2026-08-25T13:30:00+00:00",
    )
    assert payload["status"] == "ARMED"
    assert payload["preflight_hash"] == "0" * 64
    assert payload["plan_hash"] == "0" * 64
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.parent.stat().st_mode) == 0o700


def test_runner_rearms_even_when_input_preread_is_missing(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    state_root = tmp_path / "state"
    inputs.mkdir(mode=0o700)
    state_root.mkdir(mode=0o700)
    gate = state_root / "gate.json"
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "CAERUS_GENERIC_LIVE_INPUT_ROOT": str(inputs),
            "CAERUS_GENERIC_LIVE_STATE_ROOT": str(state_root),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_generic_live_v1_session.py"),
            "--preflight", str(inputs / "missing-preflight.json"),
            "--exact-plan", str(inputs / "missing-plan.json"),
            "--executed-at", "2026-08-25T13:31:00+00:00",
            "--wal-directory", str(state_root / "wal"),
            "--session-gate-path", str(gate),
            "--result-path", str(state_root / "result.json"),
            "--submit-exact-session",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert json.loads(gate.read_text())["status"] == "ARMED"


def test_result_persistence_failure_leaves_gate_armed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.generic_live_v1_submission as module

    preflight, plan = _ready()
    state = tmp_path / "state" / "gate.json"
    state.parent.mkdir(mode=0o700)
    _disarm(state, preflight, plan)
    state.chmod(0o600)
    original = module._write_exclusive

    def fail_result(path: Path, payload: dict) -> None:
        if path.name == "result.json":
            raise OSError("simulated result fsync crash")
        original(path, payload)

    monkeypatch.setattr(module, "_write_exclusive", fail_result)
    with pytest.raises(OSError, match="result fsync crash"):
        execute_generic_live_v1_session(
            activation_preflight=preflight,
            exact_plan=plan,
            lyra_decision=_decision(),
            lyra_capture_result=_capture(),
            lyra_raw_source_recompute=_raw_source_recompute(),
            executed_at="2026-08-25T13:31:00+00:00",
            submit_enabled=True,
            broker=Broker(),
            wal_directory=tmp_path / "state" / "wal",
            rearm_state_path=state,
            result_path=tmp_path / "state" / "result.json",
        )
    assert json.loads(state.read_text())["status"] == "ARMED"


def test_transient_rearm_persistence_failure_is_retried_before_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.generic_live_v1_submission as module

    preflight, plan = _ready()
    state = tmp_path / "state" / "gate.json"
    state.parent.mkdir(mode=0o700)
    _disarm(state, preflight, plan)
    original = module._atomic_rearm
    calls = 0

    def fail_once(path: Path, payload: dict) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated rearm fsync interruption")
        original(path, payload)

    monkeypatch.setattr(module, "_atomic_rearm", fail_once)
    with pytest.raises(OSError, match="rearm fsync interruption"):
        execute_generic_live_v1_session(
            activation_preflight=preflight,
            exact_plan=plan,
            lyra_decision=_decision(),
            lyra_capture_result=_capture(),
            lyra_raw_source_recompute=_raw_source_recompute(),
            executed_at="2026-08-25T13:31:00+00:00",
            submit_enabled=True,
            broker=Broker(),
            wal_directory=tmp_path / "state" / "wal",
            rearm_state_path=state,
            result_path=tmp_path / "state" / "result.json",
        )
    assert calls >= 2
    assert json.loads(state.read_text())["status"] == "ARMED"


def test_secure_paths_reject_relative_outside_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    target = root / "target.json"
    _protected_file(target, "{}")
    link = root / "link.json"
    link.symlink_to(target)
    with pytest.raises(GenericLiveV1OpsError, match="absolute"):
        secure_path(Path("relative.json"), allowed_roots=[root], must_exist=False, kind="file")
    with pytest.raises(GenericLiveV1OpsError, match="broad system"):
        secure_path(target, allowed_roots=[Path("/")], must_exist=True, kind="file")
    with pytest.raises(GenericLiveV1OpsError, match="outside"):
        secure_path(tmp_path / "other.json", allowed_roots=[root], must_exist=False, kind="file")
    with pytest.raises(GenericLiveV1OpsError, match="symlink"):
        secure_path(link, allowed_roots=[root], must_exist=True, kind="file")


def test_atomic_config_backup_install_and_rollback(tmp_path: Path) -> None:
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    candidate = root / "candidate.env"
    active = root / "active.env"
    backup = root / "backup.env"
    _protected_file(
        candidate,
        "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\n"
        "CAERUS_GENERIC_LIVE_CAPITAL_CEILING_USD=460\n",
    )
    _protected_file(active, "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\nOLD=1\n")
    installed = install_config_with_backup(
        candidate_path=candidate, active_path=active, backup_path=backup,
        allowed_roots=[root],
        expected_candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
    )
    assert installed["backup_created"] is True
    assert "CAERUS_GENERIC_LIVE_CAPITAL_CEILING_USD=460" in active.read_text()
    assert "OLD=1" in backup.read_text()
    restore_config_backup(active_path=active, backup_path=backup, allowed_roots=[root])
    assert "OLD=1" in active.read_text()
    assert stat.S_IMODE(active.stat().st_mode) == 0o600


def test_generic_config_rejects_secret_and_raw_account_sentinels(tmp_path: Path) -> None:
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    active = root / "active.env"
    for key in ("APCA_API_SECRET_KEY", "CAERUS_GENERIC_LIVE_ACCOUNT_ID"):
        candidate = root / f"{key}.env"
        _protected_file(
            candidate,
            f"CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\n{key}=SENTINEL_DO_NOT_PERSIST\n",
        )
        with pytest.raises(GenericLiveV1OpsError, match="credentials or a raw account"):
            install_config_with_backup(
                candidate_path=candidate,
                active_path=active,
                backup_path=root / f"{key}.backup",
                allowed_roots=[root],
                expected_candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
            )
        assert not active.exists()


@pytest.mark.parametrize(
    ("body", "message"),
    (
        (
            "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\nCAERUS_DUPLICATE=one\nCAERUS_DUPLICATE=two\n",
            "duplicate key",
        ),
        (
            "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\nCAERUS_COMMAND=$(touch /tmp/forbidden)\n",
            "command-free literal",
        ),
        (
            "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\nCAERUS_COMMAND=`id`\n",
            "command-free literal",
        ),
        (
            "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\nexport VALUE=one\n",
            "command-free literal",
        ),
        (
            "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\nPATH=/tmp/attacker\n",
            "command-free literal",
        ),
    ),
)
def test_generic_config_requires_unique_command_free_literal_assignments(
    tmp_path: Path, body: str, message: str,
) -> None:
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    candidate = root / "candidate.env"
    _protected_file(candidate, body)
    with pytest.raises(GenericLiveV1OpsError, match=message):
        install_config_with_backup(
            candidate_path=candidate,
            active_path=root / "active.env",
            backup_path=root / "backup.env",
            allowed_roots=[root],
            expected_candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )


def test_generic_config_candidate_hash_is_verified_before_install(tmp_path: Path) -> None:
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    candidate = root / "candidate.env"
    active = root / "active.env"
    _protected_file(
        candidate,
        "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\nCAERUS_VALUE=literal\n",
    )
    with pytest.raises(GenericLiveV1OpsError, match="byte hash differs"):
        install_config_with_backup(
            candidate_path=candidate, active_path=active,
            backup_path=root / "backup.env", allowed_roots=[root],
            expected_candidate_sha256="0" * 64,
        )
    assert not active.exists()


def test_generic_config_template_satisfies_literal_install_grammar(tmp_path: Path) -> None:
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    candidate = root / "candidate.env"
    template = (ROOT / "config/templates/generic_live_v1.env.example").read_text()
    template = template.replace(
        "REPLACE_WITH_APPROVED_EFFECTIVE_SESSION", "2026-08-25"
    )
    template = re.sub(r"REPLACE_WITH_[A-Z0-9_]+", "a" * 64, template)
    candidate.write_text(template)
    candidate.chmod(0o600)
    active = root / "active.env"
    result = install_config_with_backup(
        candidate_path=candidate, active_path=active,
        backup_path=root / "backup.env", allowed_roots=[root],
        expected_candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
    )
    assert result["candidate_sha256"] == hashlib.sha256(active.read_bytes()).hexdigest()
    assert active.read_bytes() == candidate.read_bytes()


def test_generic_config_rejects_unresolved_template_token(tmp_path: Path) -> None:
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    candidate = root / "candidate.env"
    _protected_file(
        candidate,
        "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0\n"
        "CAERUS_GENERIC_LIVE_EFFECTIVE_SESSION=REPLACE_WITH_APPROVED_EFFECTIVE_SESSION\n",
    )
    with pytest.raises(GenericLiveV1OpsError, match="unresolved template token"):
        install_config_with_backup(
            candidate_path=candidate, active_path=root / "active.env",
            backup_path=root / "backup.env", allowed_roots=[root],
            expected_candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )


def test_cron_entry_is_date_bound_duplicate_free_and_conflict_closed(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    wrapper = root / "generic_live_v1_bootstrap_guard.sh"
    _protected_file(wrapper, "#!/usr/bin/env bash\n")
    wrapper.chmod(0o700)
    line = render_cron_line(
        effective_session="2026-08-25",
        wrapper_path=wrapper,
        log_path=root / "generic.log",
        allowed_roots=[root],
    )
    installed = update_crontab(f"MAILTO=x\n{line}\n{line}\n", exact_line=line, install=True)
    assert installed.count(line) == 1
    assert "25 8 *" in line
    assert "--effective-session 2026-08-25" in line
    assert update_crontab(installed, exact_line=line, install=False) == (
        "MAILTO=x\nCRON_TZ=America/New_York\n"
    )
    conflicting = line.replace("2026-08-25", "2026-08-20")
    with pytest.raises(GenericLiveV1OpsError, match="different generic Live"):
        update_crontab(conflicting + "\n", exact_line=line, install=True)


def test_runtime_pin_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    preflight, _ = _ready()
    expected = {
        "CAERUS_GENERIC_LIVE_ACCOUNT_ID_HASH": preflight["account_id_hash"],
        "CAERUS_GENERIC_LIVE_CAPITAL_CEILING_USD": "460",
        "CAERUS_GENERIC_LIVE_MINIMUM_TRADE_USD": "100",
        "CAERUS_GENERIC_LIVE_MAX_ORDERS": "1",
        "CAERUS_GENERIC_LIVE_MAXIMUM_GROSS_FRACTION": "0.95",
        "CAERUS_GENERIC_LIVE_EFFECTIVE_SESSION": preflight["effective_session"],
        "CAERUS_GENERIC_LIVE_ADAPTER_CONTRACT": "CAERUS_GENERIC_LANE_V4",
        "CAERUS_GENERIC_LIVE_ELIGIBLE_SLEEVE": "caerus_lyra",
        "CAERUS_GENERIC_LIVE_OWNER_DECISION_HASH": preflight["owner_decision_hash"],
        "CAERUS_GENERIC_LIVE_PREFLIGHT_HASH": preflight["content_hash"],
        "CAERUS_GENERIC_LIVE_POSTTRADE_OBSERVATION_ENABLED": "1",
        "CAERUS_GENERIC_LIVE_INPUT_ROOT": "/home/brettolson/.caerus/generic_live_v1_inputs",
        "CAERUS_GENERIC_LIVE_STATE_ROOT": "/home/brettolson/.caerus/generic_live_v1_state",
        "CAERUS_GENERIC_LIVE_SESSION_GATE_PATH": "/home/brettolson/.caerus/generic_live_v1_state/session_gate.json",
        "CAERUS_GENERIC_LIVE_OWNER_APPROVED": "1",
        "CAERUS_GENERIC_LIVE_SUBMIT_APPROVED": "1",
        "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED": "1",
        "CAERUS_GENERIC_PAPER_CUTOVER": "0",
        "CAERUS_LEGACY_LIVE_EXECUTOR_ENABLED": "0",
        "ALPACA_PAPER": "0",
        "ALPACA_BASE_URL": "https://api.alpaca.markets",
        "CAERUS_GENERIC_LIVE_REPO_ROOT": str(ROOT),
        "CAERUS_GENERIC_LIVE_PYTHON_BIN": "/wrong/python",
    }
    for key, value in expected.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="runtime path pins"):
        _require_exact_env(preflight, submit=True)


def test_protected_source_hash_pin_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    preflight, plan = _ready()
    capture = _capture()
    decision = capture["decision"]
    proofs = _proofs(
        deployed_sha=EXPECTED, generic_schedule_installed=True,
        generic_submission_adapter_deployed=True, rollback_rearm_proven=True,
        order_lifecycle_pipeline_green=True, reconciliation_pipeline_green=True,
        accounting_pipeline_green=True, reporting_pipeline_green=True,
    )
    monkeypatch.setenv("CAERUS_GENERIC_LIVE_OWNER_DECISION_HASH", OWNER["content_hash"])
    monkeypatch.setenv("CAERUS_GENERIC_LIVE_ACCOUNT_OBSERVATION_HASH", OBSERVATION["content_hash"])
    monkeypatch.setenv("CAERUS_GENERIC_LIVE_LYRA_DECISION_HASH", decision["content_hash"])
    monkeypatch.setenv("CAERUS_GENERIC_LIVE_LYRA_CAPTURE_HASH", capture["content_hash"])
    raw_proof = _raw_source_recompute(capture)
    monkeypatch.setenv(
        "CAERUS_GENERIC_LIVE_LYRA_RAW_SOURCE_RECOMPUTE_HASH",
        raw_proof["content_hash"],
    )
    monkeypatch.setenv("CAERUS_GENERIC_LIVE_OPERATIONAL_PROOFS_HASH", "f" * 64)
    monkeypatch.setenv("CAERUS_GENERIC_LIVE_PLAN_HASH", plan["content_hash"])
    with pytest.raises(RuntimeError, match="protected source pins mismatch"):
        _require_source_pins(
            owner_decision=OWNER, account_observation=OBSERVATION,
            lyra_decision=decision, lyra_capture_result=capture,
            lyra_raw_source_recompute=raw_proof,
            operational_proofs=proofs, plan=plan,
        )


@pytest.mark.parametrize(
    "payload",
    ({"APCA_API_SECRET_KEY": "SENTINEL_SECRET"}, {"account_id": "SENTINEL_RAW_ACCOUNT"}),
)
def test_secret_and_raw_account_sentinels_are_rejected(payload: dict) -> None:
    with pytest.raises(GenericLiveV1OpsError, match="sensitive field"):
        reject_sensitive_payload(payload)


def test_forced_secret_value_is_rejected_in_any_payload_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAERUS_SECRET_SENTINEL", "SENTINEL_SECRET_VALUE")
    with pytest.raises(GenericLiveV1OpsError, match="sensitive value"):
        reject_sensitive_payload({"status": "broker said SENTINEL_SECRET_VALUE"})


def test_templates_remain_disabled_and_runtime_pinned() -> None:
    env_template = (ROOT / "config/templates/generic_live_v1.env.example").read_text()
    cron_wrapper = ROOT / "scripts/cron_generic_live_v1.sh"
    cron_text = cron_wrapper.read_text()
    assert "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0" in env_template
    assert "CAERUS_GENERIC_LIVE_POSTTRADE_OBSERVATION_ENABLED=0" in env_template
    assert "CAERUS_GENERIC_LIVE_REPO_ROOT=/home/brettolson/quant-daily-report" in env_template
    assert "--effective-session" in cron_text
    assert "CAERUS_GENERIC_LIVE_POSTTRADE_OBSERVATION_ENABLED:-0" in cron_text
    assert "finalize_generic_live_v1_posttrade.py" in cron_text
    assert "--collect-from-broker" in cron_text
    assert "CAERUS_GENERIC_LIVE_BOOTSTRAP_GUARD:-0" in cron_text
    assert cron_text.index("scripts/run_generic_live_v1_session.py") < cron_text.index(
        "scripts/finalize_generic_live_v1_posttrade.py"
    ) < cron_text.rindex("trap - ERR INT TERM HUP")
    assert os.access(cron_wrapper, os.X_OK)
