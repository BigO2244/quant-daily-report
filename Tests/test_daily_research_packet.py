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
            "return_convention": "weights_as_of_t",
            "strategies": {
                "caerus_polaris": {"daily_return": 0.004, "nav": 1.004, "weights_count": 2},
                "caerus_orion": {"daily_return": 0.018, "nav": 1.018, "weights_count": 2},
                "caerus_lyra": {"daily_return": 0.012, "nav": 1.012, "weights_count": 3},
            },
        },
    )
    build_research_clarity_wave(repo, "2026-05-22", shadow_dir, clarity_dir)
    return repo, shadow_dir, clarity_dir, packet_dir


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_daily_research_packet_generates_operator_outputs(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)

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
    assert packet["operational_trust_summary"]["shadow_confidence_floor"] == "LOW"
    assert "FR-028 timing semantics remain unresolved" in packet["key_risks"][0]

    leader = packet["strategy_comparison"][0]
    assert leader["strategy_id"] == "caerus_orion"
    assert leader["daily_rank"] == 1
    assert packet["exposure_concentration_review"]["high_concentration_strategy_count"] >= 1
    assert packet["regime_interpretation"]["regime"]["risk"] == "risk_on"


def test_daily_research_packet_renders_concise_markdown_and_html(tmp_path):
    repo, shadow_dir, clarity_dir, packet_dir = _fixture_sources(tmp_path)

    build_daily_research_packet(repo, "2026-05-22", shadow_dir, clarity_dir, packet_dir)

    markdown = (packet_dir / "packet.md").read_text(encoding="utf-8")
    html = (packet_dir / "packet.html").read_text(encoding="utf-8")
    summary = _read_json(packet_dir / "summary.json")

    assert "## Executive Summary" in markdown
    assert "## Operator Takeaway" in markdown
    assert "## How To Read This Packet" in markdown
    assert "## Operational Trust Summary" in markdown
    assert "## Confidence + Freshness Caveats" in markdown
    assert "### #1 Caerus Orion" in markdown
    assert "Concentration: top-3 100.00%; max position 50.00%" in markdown
    assert "Sector exposure: max sector 100.00%" in markdown
    assert "Regime evidence:" not in markdown
    assert '{"risk":' not in markdown
    assert "{&quot;risk&quot;" not in html
    assert "Available regime evidence indicates risk is risk on" in markdown
    assert "Operational shadow NAV confidence remains LOW" in markdown
    assert "<h1>Daily Research Packet - 2026-05-22</h1>" in html
    assert "<h2>Operator Takeaway</h2>" in html
    assert summary["advisory_only"] is True
    assert summary["confidence_floor"] == "LOW"
    assert summary["leader"]["strategy_id"] == "caerus_orion"
    assert summary["leader"]["top3_concentration"] == 1.0
    assert summary["leader"]["max_position_weight"] == 0.5
    assert summary["leader"]["max_sector_exposure"] == 1.0


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
    assert "Exposure data incomplete: missing" in leader["main_risk_caveat"]
    assert "unavailable" in markdown
    assert "Exposure data incomplete" in markdown
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
