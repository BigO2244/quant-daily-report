import sys

import pytest

import scripts.email_alpha_report as email_script


class FakeSMTP:
    instances = []

    def __init__(self, *args, **kwargs):
        _ = args
        self.kwargs = kwargs
        self.calls = []
        FakeSMTP.instances.append(self)

    def connect(self, host, port):
        self.calls.append(("connect", host, port))
        return 220, b"ok"

    def ehlo(self):
        self.calls.append(("ehlo",))
        return 250, b"ok"

    def starttls(self):
        self.calls.append(("starttls",))
        return 220, b"ready"

    def login(self, user, password):
        self.calls.append(("login", user, password))
        return 235, b"ok"

    def send_message(self, msg):
        self.calls.append(("send_message", msg))
        return {}

    def quit(self):
        self.calls.append(("quit",))
        return 221, b"bye"

    def __enter__(self):
        self.calls.append(("__enter__",))
        return self

    def __exit__(self, exc_type, exc, tb):
        _ = exc_type, exc, tb
        self.calls.append(("__exit__",))
        self.quit()
        return False


def _clear_email_env(monkeypatch):
    for name in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "REPORT_TO_EMAIL",
        "EMAIL_DRY_RUN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_missing_env_vars_fail_fast(monkeypatch):
    _clear_email_env(monkeypatch)

    with pytest.raises(RuntimeError, match="Missing required env vars:"):
        email_script._required_email_env()

    try:
        email_script._required_email_env()
    except RuntimeError as exc:
        msg = str(exc)
    else:
        pytest.fail("Expected RuntimeError for missing env vars")

    for key in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "REPORT_TO_EMAIL",
    ):
        assert key in msg


def test_smtp_session_calls_connect_before_starttls(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(email_script.smtplib, "SMTP", FakeSMTP)

    smtp = email_script._smtp_session("smtp.gmail.com", 587)
    call_names = [c[0] for c in smtp.calls]

    assert call_names[:4] == ["connect", "ehlo", "starttls", "ehlo"]
    assert call_names.index("connect") < call_names.index("starttls")
    smtp.quit()


def test_dry_run_skips_login_and_send(monkeypatch, tmp_path, capsys):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(email_script.smtplib, "SMTP", FakeSMTP)
    _clear_email_env(monkeypatch)

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-pass")
    monkeypatch.setenv("REPORT_TO_EMAIL", "recipient@example.com")
    monkeypatch.setenv("EMAIL_DRY_RUN", "1")

    report_dir = tmp_path / "alpha_report"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "alpha_report.html").write_text("<html><body>ok</body></html>", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["email_alpha_report.py", "--report-dir", str(report_dir)])

    email_script.main()
    out = capsys.readouterr().out

    assert "[EMAIL] DRY_RUN complete." in out
    assert "version=" in out

    assert FakeSMTP.instances, "Expected at least one SMTP session instance"
    all_calls = [call[0] for inst in FakeSMTP.instances for call in inst.calls]
    assert "connect" in all_calls
    assert "starttls" in all_calls
    assert "login" not in all_calls
    assert "send_message" not in all_calls
