import pytest

from brokers.alpaca_broker import load_alpaca_env


def _clear_alpaca_env(monkeypatch):
    for name in (
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "ALPACA_KEY_ID",
        "ALPACA_SECRET_KEY",
        "ALPACA_PAPER",
        "ALPACA_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_load_alpaca_env_prefers_new_names(monkeypatch):
    _clear_alpaca_env(monkeypatch)
    monkeypatch.setenv("ALPACA_API_KEY_ID", "new-key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "new-secret")
    monkeypatch.setenv("ALPACA_KEY_ID", "legacy-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "legacy-secret")
    monkeypatch.setenv("ALPACA_PAPER", "true")

    cfg = load_alpaca_env()

    assert cfg.key_id == "new-key"
    assert cfg.secret_key == "new-secret"
    assert cfg.paper is True
    assert cfg.base_url == "https://paper-api.alpaca.markets"


def test_load_alpaca_env_uses_legacy_names(monkeypatch):
    _clear_alpaca_env(monkeypatch)
    monkeypatch.setenv("ALPACA_KEY_ID", "legacy-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "legacy-secret")
    monkeypatch.setenv("ALPACA_PAPER", "0")

    cfg = load_alpaca_env()

    assert cfg.key_id == "legacy-key"
    assert cfg.secret_key == "legacy-secret"
    assert cfg.paper is False
    assert cfg.base_url == "https://api.alpaca.markets"


def test_load_alpaca_env_respects_base_url_override(monkeypatch):
    _clear_alpaca_env(monkeypatch)
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://example.test/v2")

    cfg = load_alpaca_env()

    assert cfg.base_url == "https://example.test"


def test_load_alpaca_env_missing_credentials(monkeypatch):
    _clear_alpaca_env(monkeypatch)

    with pytest.raises(RuntimeError, match="Missing Alpaca credentials"):
        load_alpaca_env()


def test_load_alpaca_env_missing_key_logs_set_missing_status(monkeypatch, caplog):
    """Verify error message displays SET/MISSING status without revealing secrets."""
    _clear_alpaca_env(monkeypatch)
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret-value")
    # Deliberately omit ALPACA_API_KEY_ID to trigger error

    with pytest.raises(RuntimeError) as exc_info:
        load_alpaca_env()

    # Verify error message shows SET/MISSING, not the actual secret value
    error_msg = str(exc_info.value)
    assert "key_id=MISSING" in error_msg
    assert "secret_key=SET" in error_msg
    assert "secret-value" not in error_msg  # Ensure secret not in message
    assert "[ALPACA_LOAD_ENV]" in caplog.text  # Log entry should appear


def test_load_alpaca_env_missing_secret_logs_set_missing_status(monkeypatch, caplog):
    """Verify error message displays SET/MISSING status when secret is missing."""
    _clear_alpaca_env(monkeypatch)
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key-value")
    # Deliberately omit ALPACA_API_SECRET_KEY to trigger error

    with pytest.raises(RuntimeError) as exc_info:
        load_alpaca_env()

    # Verify error message shows SET/MISSING, not the actual key value
    error_msg = str(exc_info.value)
    assert "key_id=SET" in error_msg
    assert "secret_key=MISSING" in error_msg
    assert "key-value" not in error_msg  # Ensure key not in message
    assert "[ALPACA_LOAD_ENV]" in caplog.text  # Log entry should appear
