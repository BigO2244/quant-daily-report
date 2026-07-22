from pathlib import Path


SCRIPT = Path("scripts/ops/run_vm_validation.sh")


def test_vm_validation_helper_uses_canonical_venv_and_targeted_tests() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "/home/brettolson/.venvs/quant-daily-report/bin/python" in text
    assert "/home/brettolson/.venvs/quant-daily-report/bin/pytest" in text
    assert "scripts/operational_validation.py" in text
    assert "Tests/test_sleeve_manifest.py" in text
    assert "Tests/test_sleeve_evidence.py" in text
    assert "Tests/test_governance_hygiene_agent.py" in text
    assert "Tests/test_sleeve_numeric_diagnostics.py" in text
    assert "Tests/test_target_attainment.py" in text
    assert "git status --short" in text
    assert "scripts/live_pilot_sha_guard.py" in text
    assert "CAERUS_DEPLOY_CANDIDATE_SHA" in text
    assert "CAERUS_DEPLOY_INTERNAL" in text
    assert "candidate mode is restricted to the detached deployment worktree" in text
    assert "deployment_attestation=verified" in text
    assert "[VM_VALIDATION][PASS]" in text


def test_vm_validation_helper_does_not_run_trading_or_broker_paths() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    forbidden_terms = [
        "run_precomputed_alpaca_execution",
        "execute_alpaca_orders",
        "submit_order",
        "cron_execute.sh",
        "cron_precompute.sh",
        "export_alpaca_broker_snapshot",
        "ALPACA_SECRET_KEY",
        "ALPACA_API_KEY",
    ]
    for term in forbidden_terms:
        assert term not in text
