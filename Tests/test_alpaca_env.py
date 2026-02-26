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
