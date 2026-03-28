from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "weekly_model_review.py"
    spec = importlib.util.spec_from_file_location("weekly_model_review", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _criterion_score(rubric, label: str) -> float:
    for criterion_label, score, _reason in rubric:
        if criterion_label == label:
            return float(score)
    raise AssertionError(f"criterion not found: {label}")


def test_infrastructure_detects_existing_test_suite():
    review = _load_module()
    score = _criterion_score(
        review._rubric_infrastructure(),
        "Automated test suite (unit / integration tests)",
    )
    assert score == 1.0


def test_risk_management_detects_drawdown_circuit_breaker_implementation():
    review = _load_module()
    score = _criterion_score(
        review._rubric_risk_management(),
        "Drawdown circuit breaker with equity persistence",
    )
    assert score == 1.0


def test_send_skips_when_email_credentials_missing(monkeypatch, capsys):
    review = _load_module()

    for key in (
        "EMAIL_SENDER",
        "EMAIL_APP_PASSWORD",
        "EMAIL_RECIPIENT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "REPORT_TO_EMAIL",
    ):
        monkeypatch.delenv(key, raising=False)

    review._send("subject", "body", "<p>body</p>")
    captured = capsys.readouterr()
    assert "skipping email" in captured.err.lower()
