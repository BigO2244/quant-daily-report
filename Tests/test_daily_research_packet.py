import json
from pathlib import Path

from scripts.research.build_daily_research_packet import build_daily_research_packet
from scripts.research.build_research_clarity_wave import build_research_clarity_wave


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _fixture_sources(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repo = tmp_path / "repo"
    shadow_dir = repo / "outputs" / "shadow_candidates" / "2026-05-22"
    clarity_dir = repo / "outputs" / "research_clarity" / "2026-05-22"
    packet_dir = repo / "outputs" / "research_packets" / "2026-05-22"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "universe.csv").write_text(
        "ticker,sector\nAAPL,Information Technology\nMSFT,Information Technology\nJPM,Financials\n",
        encoding="utf-8",
    )
    strategies = {
        "caerus_polaris": {
            "strategy_name": "Caerus Polaris",
            "holdings": [
                {"ticker": "AAPL", "target_weight": 0.4, "momentum_score": 1.2},
                {"ticker": "JPM", "target_weight": 0.4, "momentum_score": 0.2},
            ],
            "expected_turnover": 0.1,
        },
        "caerus_orion": {
            "strategy_name": "Caerus Orion",
            "holdings": [
                {"ticker": "AAPL", "target_weight": 0.5, "momentum_score": 2.2},
                {"ticker": "MSFT", "target_weight": 0.5, "momentum_score": 1.8},
            ],
            "expected_turnover": 0.02,
        },
        "caerus_lyra": {
            "strategy_name": "Caerus Lyra",
            "holdings": [
                {"ticker": "AAPL", "target_weight": 0.34, "momentum_score": 2.0},
                {"ticker": "MSFT", "target_weight": 0.33, "momentum_score": 1.7},
                {"ticker": "JPM", "target_weight": 0.33, "momentum_score": 0.4},
            ],
            "expected_turnover": 0.14,
        },
    }
    for strategy_id, payload in strategies.items():
        _write_json(shadow_dir / f"{strategy_id}.json", {"trade_date": "2026-05-22", **payload})
    _write_json(
        shadow_dir / "comparison.json",
        {
            "trade_date": "2026-05-22",
            "status": "OK",
            "regime": {"risk": "risk_on", "volatility": "calm", "trend": "trending"},
            "strategies": strategies,
        },
    )
    _write_json(
        shadow_dir / "shadow_performance.json",
        {
            "trade_date": "2026-05-22",
            "previous_trade_date": "2026-05-21",
            "status": "OK",
            "data_status": "OK",
            "data_reason": None,
            "return_convention": "weights_as_of_t",
            "strategies": {
                "caerus_polaris": {"daily_return": 0.004, "nav": 1.004, "weights_count": 2},
                "caerus_orion": {"daily_return": 0.018, "nav": 1.018, "weights_count": 2},
                "caerus_lyra": {"daily_return": 0.012, "nav": 1.012, "weights_count": 3},
            },
        },
    )
    _write_json(
        repo / "outputs" / "price_hydration" / "2026-05-22" / "status.json",
        {
            "as_of_date": "2026-05-22",
            "max_cache_date": "2026-05-22",
            "status": "OK",
        },
    )
    build_research_clarity_wave(repo, "2026-05-22", shadow_dir, clarity_dir)
    return repo, shadow_dir, clarity_dir, packet_dir


def _write_prior_research_clarity(repo: Path) -> None:
    prior_dir = repo / "outputs" / "research_clarity" / "2026-05-21"
    _write_json(
        prior_dir / "weights_snapshot.json",
        {
            "trade_date": "2026-05-21",
            "strategies": {
                "caerus_polaris": {"target_weights": {"AAPL": 0.38, "JPM": 0.42}},
                "caerus_orion": {"target_weights": {"AAPL": 0.30, "JPM": 0.30, "MSFT": 0.40}},
                "caerus_lyra": {"target_weights": {"AAPL": 0.34, "MSFT": 0.33, "JPM": 0.33}},
            },
        },
    )
    _write_json(
        prior_dir / "exposures_snapshot.json",
        {
            "trade_date": "2026-05-21",
            "strategies": {
                "caerus_polaris": {
                    "top3_concentration": 0.80,
                    "max_sector_exposure": 0.42,
                    "sector_exposure": {"Information Technology": 0.38, "Financials": 0.42},
                },
                "caerus_orion": {
                    "top3_concentration": 1.00,
                    "max_sector_exposure": 0.70,
                    "sector_exposure": {"Information Technology": 0.70, "Financials": 0.30},
                },
                "caerus_lyra": {
                    "top3_concentration": 1.00,
                    "max_sector_exposure": 0.67,
                    "sector_exposure": {"Information Technology": 0.67, "Financials": 0.33},
                },
            },
        },
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _no_data_sources(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repo = tmp_path / "repo"
    shadow_dir = repo / "outputs" / "shadow_candidates" / "2026-05-22"
    clarity_dir = repo / "outputs" / "research_clarity" / "2026-05-22"
    packet_dir = repo / "outputs" / "research_packets" / "2026-05-22"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "universe.csv").write_text("ticker,sector\nAAPL,Information Technology\n", encoding="utf-8")
    _write_json(
        shadow_dir / "comparison.json",
        {
            "trade_date": "2026-05-22",
            "status": "NO_DATA",
            "reason_code": "PRICE_CACHE_STALE",
            "strategies": {},
        },
    )
    _write_json(
        shadow_dir / "shadow_performance.json",
        {
            "trade_date": "2026-05-22",
            "previous_trade_date": "2026-05-21",
            "status": "OK",
            "data_status": "NO_DATA",
            "data_reason": "PRICE_CACHE_STALE",
            "return_convention": "weights_as_of_t",
            "strategies": {
                "caerus_polaris": {"daily_return": 0.0, "nav": 1.1, "weights_count": 0},
                "caerus_orion": {"daily_return": 0.0, "nav": 1.2, "weights_count": 0},
                "caerus_lyra": {"daily_return": 0.0, "nav": 1.3, "weights_count": 0},
            },
        },
    )
    build_research_clarity_wave(repo, "2026-05-22", shadow_dir, clarity_dir)
    return repo, shadow_dir, clarity_dir, packet_dir


def test_daily_research_packet_generates_operator_outputs(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)
    _write_prior_research_clarity(repo)

    result = build_daily_research_packet(repo, "2026-05-22", shadow_dir, clarity_dir, packet_dir)

    assert result["artifacts"] == ["packet.html", "packet.json", "packet.md", "summary.json"]
    assert all((packet_dir / artifact).exists() for artifact in result["artifacts"])

    packet = _read_json(packet_dir / "packet.json")
    assert packet["packet_scope"] == "FR_030_RESEARCH_ONLY"
    assert packet["advisory_only"] is True
    assert packet["execution_behavior_changed"] is False
    assert packet["accounting_semantics_changed"] is False
    assert packet["timing_semantics_changed"] is False
    assert packet["promotion_logic_changed"] is False
    assert packet["source_readiness"] == "READY"
    assert packet["shadow_data_status"] == "OK"
    assert packet["comparison_status"] == "OK"
    assert packet["strategy_count"] == 3
    assert packet["price_hydration_status"] == "OK"
    assert packet["operational_trust_summary"]["shadow_confidence_floor"] == "LOW"
    assert "FR-028 timing semantics remain unresolved" in packet["key_risks"][0]

    leader = packet["strategy_comparison"][0]
    assert leader["strategy_id"] == "caerus_orion"
    assert leader["daily_rank"] == 1
    assert packet["exposure_concentration_review"]["high_concentration_strategy_count"] >= 1
    assert packet["regime_interpretation"]["regime"]["risk"] == "risk_on"
    assert packet["research_intelligence"]["status"] == "ASSESSABLE"
    assert packet["research_intelligence"]["previous_trade_date"] == "2026-05-21"


def test_daily_research_packet_renders_concise_markdown_and_html(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)
    _write_prior_research_clarity(repo)

    build_daily_research_packet(repo, "2026-05-22", shadow_dir, clarity_dir, packet_dir)

    markdown = (packet_dir / "packet.md").read_text(encoding="utf-8")
    html = (packet_dir / "packet.html").read_text(encoding="utf-8")
    summary = _read_json(packet_dir / "summary.json")

    assert "## Executive Notes" in markdown
    assert "## Top Dashboard" in markdown
    assert "## Can I Use This Today?" in markdown
    assert "## Operator Takeaway" in markdown
    assert "## How To Read This Packet" in markdown
    assert "## Data Completeness" in markdown
    assert "## Operational Trust Summary" in markdown
    assert "## Confidence + Freshness Caveats" in markdown
    assert "| 1 | Caerus Orion | 1.0180 | 1.80% | top-3 100.0%; max 50.0% | max sector 100.0% |" in markdown
    assert "Regime evidence:" not in markdown
    assert '{"risk":' not in markdown
    assert "{&quot;risk&quot;" not in html
    assert "Available regime evidence indicates risk is risk on" in markdown
    assert "Operational shadow NAV confidence remains LOW" in markdown
    assert "## Research Intelligence" in markdown
    assert "Research attention flags" in markdown
    assert "Turnover increased materially vs prior day." in markdown
    assert "JPM change -30.0%" in markdown
    assert "Composition rotation is material" in markdown
    assert "<h1>Daily Research Packet - 2026-05-22</h1>" in html
    assert "<h2>Operator Takeaway</h2>" in html
    assert "<h2>Research Intelligence</h2>" in html
    assert "<table>" in html
    assert "`LOW`" not in html
    assert summary["advisory_only"] is True
    assert summary["confidence_floor"] == "LOW"
    assert summary["source_readiness"] == "READY"
    assert summary["leader"]["strategy_id"] == "caerus_orion"
    assert summary["leader"]["top3_concentration"] == 1.0
    assert summary["leader"]["max_position_weight"] == 0.5
    assert summary["leader"]["max_sector_exposure"] == 1.0
    assert summary["exposure_data_status"] == "complete"
    assert summary["exposure_risk_assessable"] is True
    assert summary["research_intelligence_status"] == "ASSESSABLE"
    assert summary["research_attention_flag_count"] > 0


def test_daily_research_packet_research_intelligence_flags_material_drift(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)
    _write_prior_research_clarity(repo)

    build_daily_research_packet(repo, "2026-05-22", shadow_dir, clarity_dir, packet_dir)

    packet = _read_json(packet_dir / "packet.json")
    intelligence = packet["research_intelligence"]
    orion = next(row for row in intelligence["strategy_change_summaries"] if row["strategy_id"] == "caerus_orion")
    flag_names = {flag["flag"] for flag in intelligence["attention_flags"]}

    assert intelligence["status"] == "ASSESSABLE"
    assert intelligence["material_vs_noise"] in {"material", "mixed"}
    assert orion["material_vs_noise"] == "material"
    assert orion["largest_removals"][0]["ticker"] == "JPM"
    assert orion["largest_weight_increases"][0]["ticker"] == "AAPL"
    assert {row["sector"] for row in orion["sector_exposure_drift"][:2]} == {"Information Technology", "Financials"}
    assert "SUDDEN_COMPOSITION_ROTATION" in flag_names
    assert "CHALLENGER_INSTABILITY" in flag_names


def test_daily_research_packet_surfaces_missing_freshness_inputs(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)
    (clarity_dir / "factor_risk_flags.json").unlink()

    build_daily_research_packet(repo, "2026-05-22", shadow_dir, clarity_dir, packet_dir)

    packet = _read_json(packet_dir / "packet.json")
    assert packet["operational_trust_summary"]["status"] == "PARTIAL"
    assert "factor_risk_flags.json" in packet["operational_trust_summary"]["missing_artifacts"]
    assert any(
        item["artifact"] == "factor_risk_flags.json" and item["status"] == "MISSING"
        for item in packet["confidence_freshness_caveats"]["freshness"]
    )


def test_daily_research_packet_missing_exposure_fields_are_explained(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)
    (clarity_dir / "exposures_snapshot.json").unlink()
    (clarity_dir / "exposure_summary.json").unlink()
    (clarity_dir / "concentration_monitor.json").unlink()
    (clarity_dir / "regime_exposure_matrix.json").unlink()

    build_daily_research_packet(repo, "2026-05-22", shadow_dir, clarity_dir, packet_dir)

    packet = _read_json(packet_dir / "packet.json")
    markdown = (packet_dir / "packet.md").read_text(encoding="utf-8")
    leader = packet["strategy_comparison"][0]

    assert leader["top3_concentration"] is None
    assert leader["missing_exposure_sources"]
    assert "Required exposure fields are absent" in leader["main_risk_caveat"]
    assert packet["data_completeness"]["exposure_data_status"] == "missing"
    assert "Strategy Briefs" in packet["data_completeness"]["impacted_sections"]
    assert packet["exposure_concentration_review"]["risk_assessable"] is False
    assert "not assessable" in markdown
    assert "Required exposure fields are absent" in markdown
    assert "Exposure risk not assessable because required exposure artifacts are incomplete." in markdown
    assert markdown.count("Exposure risk not assessable because required exposure artifacts are incomplete.") == 1
    assert "No exposure risk flags fired" not in markdown
    assert "Performance ranking is available, but exposure-adjusted interpretation is not yet available." in markdown
    assert "Do not compare strategy quality until exposure data is present." in markdown
    assert "## Data Completeness" in markdown
    assert "Field Diagnostics" in markdown
    assert "field missing" in markdown
    assert "exposures_snapshot.json" in markdown
    assert "regime_exposure_matrix.json" in markdown
    assert "n/a" not in markdown


def test_daily_research_packet_confidence_floor_is_consistently_low(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)

    build_daily_research_packet(repo, "2026-05-22", shadow_dir, clarity_dir, packet_dir)

    packet = _read_json(packet_dir / "packet.json")
    summary = _read_json(packet_dir / "summary.json")
    markdown = (packet_dir / "packet.md").read_text(encoding="utf-8")

    assert packet["operational_trust_summary"]["shadow_confidence_floor"] == "LOW"
    assert packet["confidence_freshness_caveats"]["confidence_floor"] == "LOW"
    assert summary["confidence_floor"] == "LOW"
    assert "Shadow confidence floor: `LOW`" in markdown
    assert "Shadow confidence floor: `UNKNOWN`" not in markdown


def test_daily_research_packet_zero_daily_returns_rank_by_nav(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)
    shadow_performance = _read_json(shadow_dir / "shadow_performance.json")
    shadow_performance["strategies"]["caerus_polaris"]["daily_return"] = 0.0
    shadow_performance["strategies"]["caerus_orion"]["daily_return"] = 0.0
    shadow_performance["strategies"]["caerus_lyra"]["daily_return"] = 0.0
    _write_json(shadow_dir / "shadow_performance.json", shadow_performance)

    build_daily_research_packet(repo, "2026-05-22", shadow_dir, clarity_dir, packet_dir)

    packet = _read_json(packet_dir / "packet.json")
    markdown = (packet_dir / "packet.md").read_text(encoding="utf-8")

    assert packet["strategy_comparison"][0]["strategy_id"] == "caerus_orion"
    assert packet["strategy_comparison"][0]["ranking_basis"] == "cumulative_nav"
    assert "No meaningful daily return ranking available; cumulative NAV shown for context." in markdown
    assert "daily shadow return ranking" not in markdown


def test_daily_research_packet_html_is_dashboard_style_and_utf8_clean(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)

    build_daily_research_packet(repo, "2026-05-22", shadow_dir, clarity_dir, packet_dir)

    html_bytes = (packet_dir / "packet.html").read_bytes()
    html_text = html_bytes.decode("utf-8")

    assert "<style>" in html_text
    assert "Top Dashboard" in html_text
    assert "class=\"card metric\"" in html_text
    assert "<table>" in html_text
    assert "`" not in html_text
    assert '{"risk":' not in html_text
    assert "{&quot;risk&quot;" not in html_text


def test_daily_research_packet_marks_price_cache_stale_source_incomplete(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _no_data_sources(tmp_path)

    build_daily_research_packet(repo, "2026-05-22", shadow_dir, clarity_dir, packet_dir)

    packet = _read_json(packet_dir / "packet.json")
    summary = _read_json(packet_dir / "summary.json")
    markdown = (packet_dir / "packet.md").read_text(encoding="utf-8")
    html = (packet_dir / "packet.html").read_text(encoding="utf-8")

    assert packet["source_readiness"] == "INCOMPLETE"
    assert packet["research_intelligence"]["status"] == "NOT_ASSESSABLE"
    assert packet["research_intelligence"]["attention_flags"][0]["flag"] == "MISSING_ATTRIBUTION_EVIDENCE"
    assert packet["shadow_data_status"] == "NO_DATA"
    assert packet["shadow_data_reason"] == "PRICE_CACHE_STALE"
    assert packet["comparison_status"] == "NO_DATA"
    assert packet["strategy_count"] == 0
    assert packet["price_hydration_status"] == "MISSING"
    assert summary["source_readiness"] == "INCOMPLETE"
    assert summary["shadow_data_status"] == "NO_DATA"
    assert "Source readiness | INCOMPLETE" in markdown
    assert "## Why This Is Incomplete" in markdown
    assert "Shadow data status: `NO_DATA`" in markdown
    assert "Shadow data reason: `PRICE_CACHE_STALE`" in markdown
    assert "Comparison status: `NO_DATA`" in markdown
    assert "Price hydration status: `MISSING`" in markdown
    assert "Strategy count: `0`" in markdown
    assert "## Strategy Briefs - Context Only" in markdown
    assert "Strategy ordering is not analytically meaningful" in markdown
    assert "Wait for post-close hydration and shadow artifact refresh, then rerun Orion.command. Do not force an incomplete packet unless diagnosing source readiness." in markdown
    assert "Do not use this packet for strategy interpretation until post-close hydration and shadow artifacts are complete." in markdown
    assert "Required exposure fields are absent despite source artifacts being present, likely because upstream shadow artifacts were generated from a stale/no-data price source." in markdown
    assert '{"risk":' not in markdown
    assert "Source Readiness" in html
    assert "Why This Is Incomplete" in html
    assert "Strategy Briefs - Context Only" in html
    assert "INCOMPLETE" in html
    assert "LOW" in markdown


def test_daily_research_packet_uses_vix_regime_fallback_when_shadow_regime_missing(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)
    comparison = _read_json(shadow_dir / "comparison.json")
    comparison.pop("regime")
    _write_json(shadow_dir / "comparison.json", comparison)
    fallback_clarity_dir = repo / "outputs" / "research_clarity_fallback" / "2026-05-22"
    _write_json(
        repo / "outputs" / "vix_regime" / "regime_current.json",
        {"as_of": "2026-05-22", "regime": "ELEVATED", "vix": 23.456},
    )
    build_research_clarity_wave(repo, "2026-05-22", shadow_dir, fallback_clarity_dir)

    build_daily_research_packet(repo, "2026-05-22", shadow_dir, fallback_clarity_dir, packet_dir)

    packet = _read_json(packet_dir / "packet.json")
    markdown = (packet_dir / "packet.md").read_text(encoding="utf-8")

    assert packet["regime_interpretation"]["regime"]["regime_source"] == "vix_regime_current_fallback"
    assert packet["regime_interpretation"]["confidence_classification"] == "LOW"
    assert "VIX fallback indicates volatility is elevated with VIX 23.46" in markdown
