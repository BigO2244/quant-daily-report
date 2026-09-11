import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from scripts.send_workflow_failure_email import notify

ROOT = Path(__file__).resolve().parents[1]


def args(tmp_path):
    return dict(repo_root=tmp_path, workflow="precompute", report_date="2026-09-11",
                stage="seal", reason_code="workflow_failed", exit_code=7,
                log_path=str(tmp_path / "logs/precompute_2026-09-11.log"))


def test_acceptance_dedupes_and_never_claims_inbox_delivery(tmp_path):
    sent = []
    first = notify(**args(tmp_path), sender=lambda **kw: sent.append(kw))
    second = notify(**args(tmp_path), sender=lambda **kw: pytest.fail("duplicate email"))
    assert first["status"] == "SMTP_ACCEPTED"
    assert second["status"] == "ALREADY_ACCEPTED"
    assert not first["inbox_delivery_verified"]
    assert "seal" in sent[0]["body_text"]
    assert len(sent) == 1


@pytest.mark.parametrize("returned", [False, {}, {"recipient": "refused"}, "sent"])
def test_nonconfirming_sender_return_is_failure_and_retryable(tmp_path, returned):
    failed = notify(**args(tmp_path), sender=lambda **kw: returned)
    assert failed["status"] == "FAILED"
    assert not failed["smtp_accepted"]
    assert notify(**args(tmp_path), sender=lambda **kw: None)["status"] == "SMTP_ACCEPTED"


def test_exception_and_log_contents_cannot_leak(tmp_path):
    log = tmp_path / "logs/precompute_2026-09-11.log"
    log.parent.mkdir()
    log.write_text("SECRET_FROM_LOG")
    sent = []
    def fail(**kw):
        sent.append(kw)
        raise RuntimeError("SECRET_FROM_SMTP")
    result = notify(**args(tmp_path), sender=fail)
    assert result["error_type"] == "RuntimeError"
    assert "SECRET" not in json.dumps(result) + json.dumps(sent)
    notify(**args(tmp_path), sender=lambda **kw: None)
    alternate = args(tmp_path)
    alternate["stage"] = "planner"
    notify(**alternate, sender=lambda **kw: None)
    assert notify(**args(tmp_path), sender=lambda **kw: pytest.fail("lost dedupe"))["status"] == "ALREADY_ACCEPTED"


@pytest.mark.parametrize("field,value", [("stage", "SECRET"), ("reason_code", "password=SECRET"), ("report_date", "../secret"), ("log_path", "https://user:secret@example.com/log")])
def test_rejects_unbounded_alert_metadata(tmp_path, field, value):
    values = args(tmp_path)
    values[field] = value
    with pytest.raises(ValueError):
        notify(**values, sender=lambda **kw: pytest.fail("invalid input sent"))


def wrapper_fixture(tmp_path, *, missing_env=False, self_heal=False, fail_module="", fail_code=7):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/cron_precompute.sh", scripts)
    (scripts / "runtime_env.sh").write_text("activate_runtime_venv() { return 0; }\n")
    if not missing_env:
        (tmp_path / ".env").write_text("CAERUS_PRICE_SHADOW=0\nEXECUTION_READINESS_CERTIFICATION_ENABLED=1\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "python3"
    stub.write_text('''#!/usr/bin/env bash
if [[ "$*" == *scripts.send_workflow_failure_email* ]]; then
    printf '%s\\n' "$*" >> "$CALL_LOG"
    exit "${ALERT_EXIT:-0}"
fi
if [[ "$*" == *"$FAIL_MODULE"* ]] && [[ -n "$FAIL_MODULE" ]]; then exit "$FAIL_CODE"; fi
exit 0
''')
    stub.chmod(0o755)
    (scripts / "run_shadow_candidates_daily.sh").write_text("exit 0\n")
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALL_LOG": str(tmp_path / "calls"),
            "REPORT_DATE": "", "SELF_HEAL_PRECOMPUTE_ONLY": "1" if self_heal else "0",
            "FAIL_MODULE": fail_module, "FAIL_CODE": str(fail_code)}


@pytest.mark.parametrize("module,stage,code", [
    ("scripts.workflow_time_guard", "time_guard", 1),
    ("core.orion_precompute_guard", "dependency_guard", 1),
    ("daily_quant_report.py", "planner", 7),
    ("scripts.seal_paper_precompute_target", "seal", 1),
    ("core.precompute_bundle_validation", "bundle_validation", 1),
    ("scripts.certify_execution_readiness", "certification", 1),
])
def test_exit_trap_covers_failures_and_preserves_status(tmp_path, module, stage, code):
    env = wrapper_fixture(tmp_path, fail_module=module)
    env["ALERT_EXIT"] = "3"
    result = subprocess.run(["bash", str(tmp_path / "scripts/cron_precompute.sh")], env=env, capture_output=True, text=True)
    assert result.returncode == code, result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert len(calls) == 1
    assert f"--stage {stage}" in calls[0]
    assert f"--exit-code {code}" in calls[0]


@pytest.mark.parametrize("self_heal", [False, True])
def test_early_credentials_failure_and_self_heal_suppression(tmp_path, self_heal):
    env = wrapper_fixture(tmp_path, missing_env=True, self_heal=self_heal)
    result = subprocess.run(["bash", str(tmp_path / "scripts/cron_precompute.sh")], env=env, capture_output=True)
    assert result.returncode == 1
    assert (tmp_path / "calls").exists() is (not self_heal)


def test_success_does_not_send_failure_email(tmp_path):
    env = wrapper_fixture(tmp_path)
    result = subprocess.run(["bash", str(tmp_path / "scripts/cron_precompute.sh")], env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "calls").exists()


def test_broker_ledger_uses_existing_aggregate_log(tmp_path):
    values = args(tmp_path)
    values.update(workflow="broker_ledger", stage="capture", log_path=str(tmp_path / "logs/cron_broker_ledger.log"))
    result = notify(**values, sender=lambda **kw: None)
    assert result["log_path"] == "logs/cron_broker_ledger.log"


@pytest.mark.parametrize("self_heal", [False, True])
def test_early_runtime_failure_preserves_status(tmp_path, self_heal):
    env = wrapper_fixture(tmp_path, self_heal=self_heal)
    (tmp_path / "scripts/runtime_env.sh").write_text("activate_runtime_venv() { return 9; }\n")
    result = subprocess.run(["bash", str(tmp_path / "scripts/cron_precompute.sh")], env=env, capture_output=True)
    assert result.returncode == 1
    if not self_heal:
        assert "--stage runtime" in (tmp_path / "calls").read_text()
    else:
        assert not (tmp_path / "calls").exists()


def test_missing_sender_dependency_writes_failed_receipt(tmp_path, monkeypatch):
    import sys
    import types
    module = types.ModuleType("core.quant_report")
    # Simulate runtime unavailable before importing existing sender. Receipt
    # remains accurate even though notification transport cannot initialize.
    monkeypatch.setitem(sys.modules, "core.quant_report", module)
    result = notify(**args(tmp_path))
    receipt = json.loads((tmp_path / "outputs/workflow/2026-09-11/precompute_failure_email.json").read_text())
    assert result == receipt
    assert receipt["status"] == "FAILED"
    assert receipt["error_type"] == "ImportError"
    assert not receipt["smtp_accepted"]


def broker_wrapper_fixture(tmp_path, failed_command="", failed_status=17):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts/cron_broker_ledger.sh", scripts)
    (scripts / "runtime_env.sh").write_text("activate_runtime_venv() { return 0; }\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "python3"
    # Capture all subprocess intentions while replacing every producer/sender.
    stub.write_text('''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CALL_LOG"
if [[ "$*" == *scripts.send_workflow_failure_email* ]]; then exit 29; fi
if [[ "$*" == "$FAILED_COMMAND" ]]; then exit "$FAILED_STATUS"; fi
exit 0
''')
    stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALL_LOG": str(tmp_path / "calls"),
            "FAILED_COMMAND": failed_command, "FAILED_STATUS": str(failed_status)}


@pytest.mark.parametrize("failed_command,stage,expected_producers", [
    ("scripts/build_broker_truth_ledger.py --account paper", "capture", [
        "scripts/build_broker_truth_ledger.py --account paper"]),
    ("scripts/build_causal_paper_ledger.py", "ownership", [
        "scripts/build_broker_truth_ledger.py --account paper",
        "scripts/build_causal_paper_ledger.py"]),
    ("scripts/build_broker_truth_ledger.py --account live", "capture", [
        "scripts/build_broker_truth_ledger.py --account paper",
        "scripts/build_causal_paper_ledger.py",
        "scripts/build_broker_truth_ledger.py --account live"]),
    ("scripts/broker_ledger_report.py", "accounting", [
        "scripts/build_broker_truth_ledger.py --account paper",
        "scripts/build_causal_paper_ledger.py",
        "scripts/build_broker_truth_ledger.py --account live",
        "scripts/broker_ledger_report.py"]),
])
def test_broker_failure_stops_downstream_and_keeps_first_status(tmp_path, failed_command, stage, expected_producers):
    env = broker_wrapper_fixture(tmp_path, failed_command)
    result = subprocess.run(["bash", str(tmp_path / "scripts/cron_broker_ledger.sh")], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 17, result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls[:-1] == expected_producers
    alert = calls[-1]
    assert "scripts.send_workflow_failure_email" in alert
    assert "--workflow broker_ledger" in alert
    assert f"--stage {stage}" in alert
    assert "--exit-code 17" in alert
    assert "failure notification unsuccessful" in result.stderr
    assert "done rc=17" in result.stdout


def test_broker_success_refreshes_paper_before_live_and_sends_no_failure(tmp_path):
    env = broker_wrapper_fixture(tmp_path)
    result = subprocess.run(["bash", str(tmp_path / "scripts/cron_broker_ledger.sh")], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls[:4] == ["scripts/build_broker_truth_ledger.py --account paper",
                         "scripts/build_causal_paper_ledger.py",
                         "scripts/build_broker_truth_ledger.py --account live",
                         "scripts/broker_ledger_report.py"]
    assert calls[4].startswith("scripts/run_operational_drag_analysis.py --date ")
    assert calls[5:] == ["scripts/build_tca.py --account both"]


def test_real_sender_path_loads_only_email_env_without_execution_or_interpolation(tmp_path, monkeypatch):
    import sys
    import types
    from scripts.send_workflow_failure_email import EMAIL_ENV_KEYS
    from core.email_env import resolve_email_env
    monkeypatch.setitem(sys.modules, "dotenv", None)
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for key in EMAIL_ENV_KEYS | {"ALPACA_API_KEY", "EMAIL_SIDE_EFFECT"}:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SMTP_HOST", "already-configured.example")
    (tmp_path / ".env").write_text(
        'export EMAIL_SENDER="operator@example.com" # SMTP identity\n'
        'EMAIL_RECIPIENT="owner@example.com"\n'
        "EMAIL_APP_PASSWORD='literal${ALPACA_API_KEY}$(touch SHOULD_NOT_EXIST)'\n"
        'SMTP_HOST="file-host.example"\n'
        'ALPACA_API_KEY="broker-secret\n'
        'EMAIL_SIDE_EFFECT="not-allowlisted"\n'
    )
    observed = []
    module = types.ModuleType("core.quant_report")
    def fake_real_sender(**kwargs):
        observed.append(resolve_email_env())
    module.send_email = fake_real_sender
    monkeypatch.setitem(sys.modules, "core.quant_report", module)
    result = notify(**args(tmp_path))
    assert result["status"] == "SMTP_ACCEPTED"
    assert observed[0]["missing"] == []
    assert observed[0]["sender"] == "operator@example.com"
    assert observed[0]["recipient"] == "owner@example.com"
    assert observed[0]["smtp_host"] == "already-configured.example"
    assert observed[0]["password"] == "literal${ALPACA_API_KEY}$(touch SHOULD_NOT_EXIST)"
    assert "ALPACA_API_KEY" not in os.environ
    assert "EMAIL_SIDE_EFFECT" not in os.environ
    assert "literal" not in json.dumps(result)
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()
