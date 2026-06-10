from __future__ import annotations

from pathlib import Path

from core.portfolio_history_escalation import (
    BROKEN_SUBJECT_PREFIX,
    evaluate_nav_escalation,
    send_nav_escalation,
)


def _seed_nav(repo: Path, *dates: str) -> None:
    out = repo / "outputs" / "portfolio_history"
    out.mkdir(parents=True, exist_ok=True)
    lines = ["date,equity,source"] + [f"{d},10000,x" for d in dates]
    (out / "nav.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_fresh_nav_is_ok_and_no_email(tmp_path: Path) -> None:
    _seed_nav(tmp_path, "2026-06-08", "2026-06-09", "2026-06-10")
    payload = evaluate_nav_escalation(trade_date="2026-06-10", repo_root=tmp_path)
    assert payload["status"] == "OK"
    assert payload["should_email"] is False
    assert payload["gap_trading_days"] == 0
    assert payload["reason_codes"] == ["ok"]


def test_gap_emits_nav_gap_reason(tmp_path: Path) -> None:
    # latest row 06-08; expected 06-10 -> 06-09 + 06-10 = 2 trading days > 1
    _seed_nav(tmp_path, "2026-06-05", "2026-06-08")
    payload = evaluate_nav_escalation(trade_date="2026-06-10", repo_root=tmp_path)
    assert payload["status"] == "NAV_GAP"
    assert "NAV_GAP" in payload["reason_codes"]
    assert payload["should_email"] is True
    assert payload["consecutive_failures"] == 1
    assert not payload["subject"].startswith(BROKEN_SUBJECT_PREFIX)


def test_two_consecutive_failures_escalate_subject(tmp_path: Path) -> None:
    _seed_nav(tmp_path, "2026-06-08")
    first = evaluate_nav_escalation(trade_date="2026-06-10", repo_root=tmp_path)
    assert first["consecutive_failures"] == 1
    second = evaluate_nav_escalation(trade_date="2026-06-10", repo_root=tmp_path)
    assert second["consecutive_failures"] == 2
    assert second["escalated"] is True
    assert second["subject"].startswith(BROKEN_SUBJECT_PREFIX)


def test_recovery_resets_failure_counter(tmp_path: Path) -> None:
    _seed_nav(tmp_path, "2026-06-08")
    evaluate_nav_escalation(trade_date="2026-06-10", repo_root=tmp_path)
    _seed_nav(tmp_path, "2026-06-08", "2026-06-09", "2026-06-10")
    recovered = evaluate_nav_escalation(trade_date="2026-06-10", repo_root=tmp_path)
    assert recovered["status"] == "OK"
    assert recovered["consecutive_failures"] == 0


def test_missing_nav_is_failure(tmp_path: Path) -> None:
    payload = evaluate_nav_escalation(trade_date="2026-06-10", repo_root=tmp_path)
    assert payload["status"] == "MISSING"
    assert "NAV_ARTIFACT_MISSING" in payload["reason_codes"]
    assert payload["should_email"] is True


def test_fr059_auth_failed_integration(tmp_path: Path) -> None:
    _seed_nav(tmp_path, "2026-06-08", "2026-06-09", "2026-06-10")
    payload = evaluate_nav_escalation(
        trade_date="2026-06-10",
        repo_root=tmp_path,
        live_status_reason_codes=["alpaca_auth_failed"],
    )
    assert payload["status"] == "BROKER_UNAVAILABLE"
    assert "BROKER_HISTORY_UNAVAILABLE" in payload["reason_codes"]


def test_send_uses_injected_sender(tmp_path: Path) -> None:
    _seed_nav(tmp_path, "2026-06-08")
    payload = evaluate_nav_escalation(trade_date="2026-06-10", repo_root=tmp_path)
    sent: dict[str, str] = {}

    def fake_send(*, subject: str, body_text: str) -> None:
        sent["subject"] = subject
        sent["body"] = body_text

    assert send_nav_escalation(payload, send_fn=fake_send) is True
    assert "NAV" in sent["subject"]
    assert "trade date 2026-06-10" in sent["body"]


def test_send_skipped_when_ok(tmp_path: Path) -> None:
    _seed_nav(tmp_path, "2026-06-08", "2026-06-09", "2026-06-10")
    payload = evaluate_nav_escalation(trade_date="2026-06-10", repo_root=tmp_path)
    calls: list[int] = []
    assert send_nav_escalation(payload, send_fn=lambda **_: calls.append(1)) is False
    assert calls == []
