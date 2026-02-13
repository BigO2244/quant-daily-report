import pytest

from core import quant_report


class _DummySMTP:
    def __init__(self, host, port):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        return None

    def login(self, user, password):
        self.user = user
        self.password = password

    def sendmail(self, from_addr, to_addrs, msg):
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.msg = msg
        return {}


def test_send_email_requires_email_secrets(monkeypatch):
    monkeypatch.delenv("EMAIL_SENDER", raising=False)
    monkeypatch.delenv("EMAIL_APP_PASSWORD", raising=False)
    monkeypatch.delenv("EMAIL_RECIPIENT", raising=False)

    with pytest.raises(RuntimeError, match="Missing required email env vars"):
        quant_report.send_email(subject="subj", body_text="hello")


def test_send_email_normalizes_smtp_env_from_email_secrets(monkeypatch):
    monkeypatch.setenv("EMAIL_SENDER", "sender@example.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "app-pass")
    monkeypatch.setenv("EMAIL_RECIPIENT", "recipient@example.com")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("REPORT_TO_EMAIL", raising=False)

    monkeypatch.setattr(quant_report.smtplib, "SMTP", _DummySMTP)

    quant_report.send_email(subject="subj", body_text="hello")

    assert quant_report.os.environ["SMTP_HOST"] == "smtp.gmail.com"
    assert quant_report.os.environ["SMTP_PORT"] == "587"
    assert quant_report.os.environ["SMTP_USER"] == "sender@example.com"
    assert quant_report.os.environ["SMTP_PASSWORD"] == "app-pass"
    assert quant_report.os.environ["REPORT_TO_EMAIL"] == "recipient@example.com"


def test_send_email_accepts_legacy_smtp_env_without_email_vars(monkeypatch):
    monkeypatch.delenv("EMAIL_SENDER", raising=False)
    monkeypatch.delenv("EMAIL_APP_PASSWORD", raising=False)
    monkeypatch.delenv("EMAIL_RECIPIENT", raising=False)
    monkeypatch.setenv("SMTP_USER", "legacy-sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "legacy-pass")
    monkeypatch.setenv("REPORT_TO_EMAIL", "legacy-recipient@example.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")

    monkeypatch.setattr(quant_report.smtplib, "SMTP", _DummySMTP)

    quant_report.send_email(subject="subj", body_text="hello")

    assert quant_report.os.environ["SMTP_HOST"] == "smtp.example.com"
    assert quant_report.os.environ["SMTP_PORT"] == "2525"
    assert quant_report.os.environ["SMTP_USER"] == "legacy-sender@example.com"
    assert quant_report.os.environ["SMTP_PASSWORD"] == "legacy-pass"
    assert quant_report.os.environ["REPORT_TO_EMAIL"] == "legacy-recipient@example.com"
