from pathlib import Path


def test_orion_launcher_blocks_incomplete_sources_by_default():
    script = Path("scripts/open_shadow_comparison_latest.command").read_text(encoding="utf-8")

    assert "Post-close research source is not ready; packet would be incomplete." in script
    assert "ORION_ALLOW_INCOMPLETE_PACKET=1" in script
    assert "exit 24" in script
    assert "shadow_data_status" in script
    assert "shadow_data_reason" in script
    assert "comparison_status" in script
    assert "strategy_count" in script
    assert "scripts.research.check_research_source_readiness" in script
    assert "price_hydration_status_path" in script
    assert "stale_days" in script
    assert "symbols_missing_count" in script
    assert "cache_lag_interpretation" in script


def test_orion_launcher_has_explicit_incomplete_override_and_no_auto_refresh():
    script = Path("scripts/open_shadow_comparison_latest.command").read_text(encoding="utf-8")

    assert "ORION_ALLOW_INCOMPLETE_PACKET" in script
    assert "building packet with INCOMPLETE source readiness" in script
    assert "ORION_REFRESH_BEFORE_PACKET" in script
    assert "Refresh-before-packet is not implemented yet; run the approved hydration workflow separately." in script
    assert "scripts.hydrate_price_cache_only" not in script
