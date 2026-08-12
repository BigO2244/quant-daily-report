from pathlib import Path

from scripts import build_broker_truth_ledger


def test_paper_broker_truth_uses_execution_credential_authority() -> None:
    assert build_broker_truth_ledger.ACCOUNT_ENV_FILES["paper"] == (
        build_broker_truth_ledger.REPO_ROOT / ".env"
    )
    assert build_broker_truth_ledger.ACCOUNT_ENV_FILES["paper"] != (
        Path.home() / ".caerus" / "alpaca.env"
    )
