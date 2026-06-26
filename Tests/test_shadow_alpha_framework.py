from __future__ import annotations

from pathlib import Path

from research.fr105_shadow_alpha_framework import build_shadow_alpha_chase_framework, write_shadow_alpha_chase_framework


def test_shadow_alpha_framework_is_default_off_and_non_trading(tmp_path: Path) -> None:
    path, payload = write_shadow_alpha_chase_framework(
        repo_root=tmp_path,
        generated_at="2026-06-26T20:00:00Z",
    )

    assert path == tmp_path / "outputs" / "research" / "fr_105" / "shadow_alpha_chase_framework.json"
    assert payload["metadata"]["enabled"] is False
    assert payload["metadata"]["default_off"] is True
    assert payload["metadata"]["trading_behavior_changed"] is False
    assert payload["metadata"]["optimizer_behavior_changed"] is False
    assert payload["metadata"]["broker_behavior_changed"] is False
    assert payload["metadata"]["sizing_behavior_changed"] is False
    assert payload["evaluation_status"]["paper_or_live_influence_allowed"] is False
    assert "target_weight" in payload["score_policy"]["prohibited_score_sources"]
    assert "allocation_weight" in payload["score_policy"]["prohibited_score_sources"]

    first = path.read_text(encoding="utf-8")
    write_shadow_alpha_chase_framework(
        repo_root=tmp_path,
        generated_at="2026-06-26T20:00:00Z",
    )
    assert path.read_text(encoding="utf-8") == first


def test_shadow_alpha_framework_builder_has_no_enabled_alpha_chase_mode() -> None:
    payload = build_shadow_alpha_chase_framework(generated_at="2026-06-26T20:00:00Z")

    enabled_trading_modes = [
        row
        for row in payload["supported_modes"]
        if row.get("enabled") and row.get("trading_influence")
    ]
    assert enabled_trading_modes == []
    assert payload["evaluation_status"]["alpha_chase_recommendations_allowed"] is False
