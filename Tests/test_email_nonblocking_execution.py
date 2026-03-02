from __future__ import annotations

import pytest

import daily_quant_report as dqr


def test_try_send_email_non_strict_does_not_raise(monkeypatch) -> None:
    monkeypatch.setenv("EMAIL_STRICT", "0")
    monkeypatch.delenv("EMAIL_DRY_RUN", raising=False)

    def _raise(**kwargs):
        raise RuntimeError("smtp refused")

    monkeypatch.setattr(dqr, "send_email", _raise)

    sent = dqr._try_send_email(
        subject="s",
        body_html="<p>x</p>",
        body_text="x",
        label="execution",
    )

    assert sent is False


def test_try_send_email_strict_raises(monkeypatch) -> None:
    monkeypatch.setenv("EMAIL_STRICT", "1")
    monkeypatch.delenv("EMAIL_DRY_RUN", raising=False)

    def _raise(**kwargs):
        raise RuntimeError("smtp refused")

    monkeypatch.setattr(dqr, "send_email", _raise)

    with pytest.raises(RuntimeError):
        dqr._try_send_email(
            subject="s",
            body_html="<p>x</p>",
            body_text="x",
            label="execution",
        )


def test_send_report_emails_non_strict_main_path_continues(monkeypatch) -> None:
    monkeypatch.setenv("EMAIL_STRICT", "0")
    monkeypatch.delenv("EMAIL_DRY_RUN", raising=False)

    def _raise(**kwargs):
        raise RuntimeError("smtp refused")

    monkeypatch.setattr(dqr, "send_email", _raise)

    execution_ok, snapshot_ok = dqr._send_report_emails(
        exec_subject="exec",
        exec_body_html="<p>exec</p>",
        exec_body_text="exec",
        snapshot_subject="snapshot",
        snapshot_body_html="<p>snapshot</p>",
        snapshot_body_text="snapshot",
    )

    assert execution_ok is False
    assert snapshot_ok is False


def test_try_send_email_dry_run_short_circuits_send(monkeypatch) -> None:
    monkeypatch.setenv("EMAIL_STRICT", "1")
    monkeypatch.setenv("EMAIL_DRY_RUN", "1")

    called = {"value": False}

    def _track(**kwargs):
        called["value"] = True
        raise RuntimeError("should not be called in dry run")

    monkeypatch.setattr(dqr, "send_email", _track)

    sent = dqr._try_send_email(
        subject="s",
        body_html="<p>x</p>",
        body_text="x",
        label="snapshot",
    )

    assert sent is True
    assert called["value"] is False
