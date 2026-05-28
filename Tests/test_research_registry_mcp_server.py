from __future__ import annotations

import json
import sys
from pathlib import Path

from research_registry.mcp_server import ToolContext, call_tool, list_tools
from research_registry.mcp_server.server import handle_jsonrpc
from scripts import research_registry_cli


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture_roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    runs_root = tmp_path / "outputs" / "runs"
    run_root = runs_root / "run-20260527-warn"
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run-20260527-warn",
            "trade_date": "2026-05-27",
            "execution_status": "EXECUTED",
            "operator_execution_status": "executed",
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
            "trades": [{"ticker": "ELV", "side": "SELL"}],
        },
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "run_id": "run-20260527-warn",
            "trade_date": "2026-05-27",
            "status": "EXECUTED",
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
            "broker_responses": [{"ticker": "ELV", "side": "SELL", "status": "ACCEPTED"}],
        },
    )
    _write_json(
        run_root / "operator_summary.json",
        {
            "run_id": "run-20260527-warn",
            "trade_date": "2026-05-27",
            "terminal_status": "warning",
            "operator_execution_status": "executed",
            "execution_integrity_status": "WARN",
            "updated_at": "2026-05-27T14:05:00Z",
        },
    )
    _write_json(
        run_root / "audit" / "execution_integrity.json",
        {
            "run_id": "run-20260527-warn",
            "trade_date": "2026-05-27",
            "status": "WARN",
            "pending_buy_count": 1,
            "missing_buy_orders": [{"ticker": "SLB", "side": "BUY"}],
            "findings": [{"code": "PENDING_BUY_WITHOUT_SUBMITTED_BUY", "severity": "WARN"}],
        },
    )

    packets_root = tmp_path / "outputs" / "research_packets"
    packet_root = packets_root / "2026-05-27"
    _write_json(
        packet_root / "packet.json",
        {
            "trade_date": "2026-05-27",
            "status": "READY",
            "confidence": "LOW",
            "source_readiness": {"news": "READY"},
            "stale_warnings": ["research digest one day old"],
        },
    )

    docs_root = tmp_path / "docs" / "governance"
    _write_text(
        docs_root / "fr_active_backlog.md",
        "\n".join(
            [
                "| FR | Phase | Status | Blast Radius |",
                "|---|---|---|---|",
                "| FR-031 stale active row | Execution Integrity | `PROMOTION_READY` | HIGH |",
                "| HOTFIX-2026-05-27 stale hotfix row | HOTFIX | `BACKLOG` | HIGH |",
            ]
        )
        + "\n",
    )
    _write_text(
        docs_root / "fr_registry.md",
        "\n".join(
            [
                "| FR | Phase | Status | Blast Radius |",
                "|---|---|---|---|",
                "| FR-031 current row | Execution Integrity | `DEPLOYED_OBSERVING` | HIGH |",
                "| HOTFIX-2026-05-27 current hotfix row | HOTFIX | `DEPLOYED_OBSERVING` | HIGH |",
            ]
        )
        + "\n",
    )
    return runs_root, packets_root, docs_root, run_root


def _build_registry(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    runs_root, packets_root, docs_root, run_root = _fixture_roots(tmp_path)
    db_path = tmp_path / "registry.db"
    payload = call_tool(
        "build_caerus_registry",
        {
            "db_path": str(db_path),
            "runs_root": str(runs_root),
            "packets_root": str(packets_root),
            "docs_root": str(docs_root),
            "limit": 10,
        },
    )
    assert payload["status"] == "OK"
    assert payload["db_path"] == str(db_path)
    return db_path, runs_root, packets_root, docs_root


def test_mcp_server_imports_cleanly_and_lists_expected_tools() -> None:
    names = {tool["name"] for tool in list_tools()}
    assert {
        "build_caerus_registry",
        "latest_runs",
        "run_health",
        "integrity_findings",
        "governance_open",
        "research_packet_status",
        "registry_summary",
        "query_registry",
        "lineage",
        "daily_operator_brief",
        "artifact_status",
        "operator_daily_summary",
        "artifact_drilldown",
    }.issubset(names)


def test_mcp_build_uses_fixture_roots_and_writes_only_db(tmp_path: Path) -> None:
    runs_root, packets_root, docs_root, _ = _fixture_roots(tmp_path)
    source_files = [path for root in [runs_root, packets_root, docs_root] for path in root.rglob("*") if path.is_file()]
    before = {path: path.read_bytes() for path in source_files}
    db_path = tmp_path / "registry.db"

    payload = call_tool(
        "build_caerus_registry",
        {
            "db_path": str(db_path),
            "runs_root": str(runs_root),
            "packets_root": str(packets_root),
            "docs_root": str(docs_root),
            "limit": 10,
        },
    )

    assert payload["status"] == "OK"
    assert db_path.exists()
    assert {path: path.read_bytes() for path in source_files} == before


def test_mcp_operator_tools_return_fixture_objects(tmp_path: Path) -> None:
    db_path, _, _, _ = _build_registry(tmp_path)

    latest = call_tool("latest_runs", {"db_path": str(db_path)})
    assert latest["status"] == "OK"
    assert latest["runs"][0]["run_id"] == "run-20260527-warn"
    assert latest["runs"][0]["integrity_status"] == "WARN"

    health = call_tool("run_health", {"db_path": str(db_path), "run_id": "run-20260527-warn"})
    assert health["status"] == "FOUND"
    assert health["execution_payload"]["trade_count"] == 1
    assert health["execution_integrity"]["status"] == "WARN"

    findings = call_tool("integrity_findings", {"db_path": str(db_path)})
    assert findings["finding_object_count"] == 1
    assert findings["integrity_findings"][0]["findings"][0]["code"] == "PENDING_BUY_WITHOUT_SUBMITTED_BUY"

    packets = call_tool("research_packet_status", {"db_path": str(db_path)})
    assert packets["packet_count"] == 1
    assert packets["packets"][0]["stale_warnings"] == ["research digest one day old"]


def test_mcp_governance_query_and_lineage_are_deterministic(tmp_path: Path) -> None:
    db_path, _, _, _ = _build_registry(tmp_path)

    governance = call_tool("governance_open", {"db_path": str(db_path)})
    items = {item["fr_id"]: item for item in governance["items"]}
    assert governance["mode"] == "deduped_current_state"
    assert items["FR-031"]["status"] == "DEPLOYED_OBSERVING"
    assert items["FR-031"]["suppressed_statuses"] == ["PROMOTION_READY"]
    assert items["HOTFIX-2026-05-27"]["status"] == "DEPLOYED_OBSERVING"

    raw = call_tool("governance_open", {"db_path": str(db_path), "show_duplicates": True})
    assert raw["mode"] == "raw_duplicates"
    assert raw["open_count"] > governance["open_count"]

    queried = call_tool("query_registry", {"db_path": str(db_path), "artifact_type": "GovernanceFR", "limit": 1})
    object_id = queried["objects"][0]["object_id"]
    lineage = call_tool("lineage", {"db_path": str(db_path), "object_id": object_id})
    assert lineage["status"] == "OK"
    assert lineage["lineage"]["object_id"] == object_id
    assert len(lineage["lineage"]["parents"]) == 1


def test_mcp_registry_summary_and_query_filters(tmp_path: Path) -> None:
    db_path, _, _, _ = _build_registry(tmp_path)

    summary = call_tool("registry_summary", {"db_path": str(db_path)})
    assert summary["status"] == "OK"
    assert summary["summary"]["object_count"] >= 1

    queried = call_tool("query_registry", {"db_path": str(db_path), "data_artifact_type": "execution_run"})
    assert queried["object_count"] == 1
    assert queried["objects"][0]["data"]["run_id"] == "run-20260527-warn"


def test_mcp_server_jsonrpc_tools_call(tmp_path: Path) -> None:
    db_path, _, _, _ = _build_registry(tmp_path)
    context = ToolContext(db_path=db_path)
    response = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "latest_runs", "arguments": {}}},
        context,
    )
    assert response["id"] == 1
    assert response["result"]["isError"] is False
    assert "run-20260527-warn" in response["result"]["content"][0]["text"]


def test_mcp_safety_boundaries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "SHOULD_NOT_APPEAR")
    db_path, _, _, _ = _build_registry(tmp_path)

    for forbidden in [
        "brokers.alpaca_broker",
        "scripts.run_precomputed_alpaca_execution",
        "scripts.cron_execute",
    ]:
        assert forbidden not in sys.modules

    output_db = Path.cwd() / "outputs" / "mcp-server-test.db"
    denied = call_tool("build_caerus_registry", {"db_path": str(output_db)})
    assert denied["status"] == "ERROR"
    assert not output_db.exists()

    payload = call_tool("registry_summary", {"db_path": str(db_path)})
    assert "SHOULD_NOT_APPEAR" not in json.dumps(payload, sort_keys=True)
    assert payload["db_path"] == str(db_path)


def test_daily_operator_brief_returns_compact_operator_summary(tmp_path: Path) -> None:
    db_path, _, _, _ = _build_registry(tmp_path)

    brief = call_tool("daily_operator_brief", {"db_path": str(db_path)})

    assert brief["status"] == "OK"
    assert brief["latest_run"]["run_id"] == "run-20260527-warn"
    assert brief["latest_run"]["trade_date"] == "2026-05-27"
    assert brief["latest_run"]["integrity_status"] == "WARN"
    assert brief["execution_integrity"]["status"] == "WARN_FAIL_PRESENT"
    assert brief["execution_integrity"]["latest_warn_fail_findings"][0]["run_id"] == "run-20260527-warn"
    assert "WARN/FAIL execution integrity findings present" in brief["warnings"]
    assert brief["governance"]["open_count"] >= 2
    assert brief["governance"]["high_blast_radius_count"] >= 2
    assert brief["governance"]["deployed_observing_count"] == 2
    key_ids = {item["fr_id"] for item in brief["governance"]["key_items"]}
    assert {"FR-031", "HOTFIX-2026-05-27"}.issubset(key_ids)
    assert brief["research_packet"]["packet_date"] == "2026-05-27"
    assert brief["research_packet"]["status"] == "READY"
    assert brief["registry_summary"]["object_count"] >= 1
    assert brief["registry_summary"]["edge_count"] >= 1
    assert brief["registry_summary"]["orphan_count"] == 0
    assert brief["registry_summary"]["surface_conflict_count"] == 0


def test_daily_operator_brief_handles_missing_research_packet(tmp_path: Path) -> None:
    runs_root, _, docs_root, _ = _fixture_roots(tmp_path)
    db_path = tmp_path / "registry.db"
    payload = call_tool(
        "build_caerus_registry",
        {
            "db_path": str(db_path),
            "runs_root": str(runs_root),
            "packets_root": str(tmp_path / "missing-packets"),
            "docs_root": str(docs_root),
            "limit": 10,
        },
    )
    assert payload["status"] == "OK"

    brief = call_tool("daily_operator_brief", {"db_path": str(db_path)})

    assert brief["research_packet"] is None
    assert "missing research packet" in brief["warnings"]


def test_daily_operator_brief_cli_and_no_source_mutation(tmp_path: Path, capsys) -> None:
    runs_root, packets_root, docs_root, _ = _fixture_roots(tmp_path)
    source_files = [path for root in [runs_root, packets_root, docs_root] for path in root.rglob("*") if path.is_file()]
    before = {path: path.read_bytes() for path in source_files}
    db_path = tmp_path / "registry.db"
    assert research_registry_cli.main(
        [
            "build-caerus-registry",
            "--db",
            str(db_path),
            "--runs-root",
            str(runs_root),
            "--packets-root",
            str(packets_root),
            "--docs-root",
            str(docs_root),
            "--limit",
            "10",
        ]
    ) == 0
    capsys.readouterr()

    assert research_registry_cli.main(["daily-operator-brief", "--db", str(db_path)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["latest_run"]["run_id"] == "run-20260527-warn"
    assert payload["execution_integrity"]["status"] == "WARN_FAIL_PRESENT"
    assert {path: path.read_bytes() for path in source_files} == before


def _artifact_status_fixture(tmp_path: Path) -> Path:
    outputs = tmp_path / "outputs"
    precompute = outputs / "precompute" / "2026-05-28"
    _write_json(precompute / "contract.json", {"trade_date": "2026-05-28", "status": "READY", "run_id": "precompute-run"})
    _write_json(precompute / "daily_snapshot.json", {"trade_date": "2026-05-28"})
    _write_json(precompute / "signals.json", {"trade_date": "2026-05-28"})
    _write_json(precompute / "planned_execution_payload.json", {"trade_date": "2026-05-28"})

    run = outputs / "runs" / "2026-05-28T093500-0400_phase6"
    _write_json(run / "execution_payload.json", {"run_id": "phase6-run", "trade_date": "2026-05-28", "execution_status": "EXECUTED"})
    _write_json(run / "execution_results.json", {"run_id": "phase6-run", "trade_date": "2026-05-28", "status": "EXECUTED"})
    _write_json(run / "operator_summary.json", {"run_id": "phase6-run", "trade_date": "2026-05-28", "terminal_status": "success"})
    _write_json(run / "audit" / "execution_integrity.json", {"run_id": "phase6-run", "trade_date": "2026-05-28", "status": "OK"})

    broker = outputs / "broker"
    _write_json(broker / "broker_snapshot_latest.json", {"trade_date": "2026-05-28"})
    _write_json(broker / "recon_posttrade_2026-05-28.json", {"trade_date": "2026-05-28", "status": "OK"})

    shadow = outputs / "shadow_candidates" / "2026-05-28"
    _write_json(shadow / "comparison.json", {"trade_date": "2026-05-28"})
    _write_text(shadow / "comparison.md", "# Shadow\n")
    workflow = outputs / "workflow" / "2026-05-28"
    _write_json(workflow / "shadow.json", {"trade_date": "2026-05-28", "status": "OK"})
    _write_json(workflow / "shadow_generate.json", {"trade_date": "2026-05-28", "status": "OK"})
    _write_json(workflow / "shadow_latest.json", {"trade_date": "2026-05-28", "status": "OK"})
    _write_json(workflow / "shadow_reconciliation.json", {"trade_date": "2026-05-28", "status": "OK"})

    packet = outputs / "research_packets" / "2026-05-28"
    _write_json(packet / "packet.json", {"trade_date": "2026-05-28", "status": "READY", "confidence": "LOW"})
    _write_json(packet / "summary.json", {"trade_date": "2026-05-28", "status": "READY"})
    _write_json(outputs / "overnight_signals" / "2026-05-28.json", {"trade_date": "2026-05-28"})
    return outputs


def test_artifact_status_discovers_latest_artifacts_read_only(tmp_path: Path) -> None:
    outputs = _artifact_status_fixture(tmp_path)
    source_files = [path for path in outputs.rglob("*") if path.is_file()]
    before = {path: path.read_bytes() for path in source_files}

    payload = call_tool("artifact_status", {"outputs_root": str(outputs), "limit": 3})

    assert payload["status"] == "OK"
    assert payload["warnings"] == []
    assert payload["latest_precompute"]["trade_date"] == "2026-05-28"
    assert payload["latest_precompute"]["missing_required"] == []
    assert payload["latest_execution"]["run_id"] == "phase6-run"
    assert payload["latest_execution"]["integrity_status"] == "OK"
    assert payload["latest_broker_confirmation"]["status"] == "OK"
    assert payload["latest_shadow"]["trade_date"] == "2026-05-28"
    assert payload["latest_research_packet"]["packet_status"] == "READY"
    families = {family["family"]: family for family in payload["artifact_families"]}
    assert families["precompute"]["count"] == 1
    assert families["overnight_signals"]["count"] == 1
    assert {path: path.read_bytes() for path in source_files} == before


def test_artifact_status_cli_markdown_and_missing_artifacts(tmp_path: Path, capsys) -> None:
    outputs = tmp_path / "outputs"
    (outputs / "precompute").mkdir(parents=True)

    assert research_registry_cli.main(["artifact-status", "--outputs-root", str(outputs), "--markdown"]) == 0
    markdown = capsys.readouterr().out

    assert "# MCP Artifact Status" in markdown
    assert "NEEDS_OPERATOR" in markdown
    assert "precompute: NEEDS_OPERATOR" in markdown


def test_operator_daily_summary_ok_state_and_cli_formats(tmp_path: Path, capsys) -> None:
    outputs = _artifact_status_fixture(tmp_path)

    payload = call_tool("operator_daily_summary", {"outputs_root": str(outputs), "trade_date": "2026-05-28"})
    assert payload["status"] == "OK"
    assert payload["warnings"] == []
    happened = payload["summary"]["what_happened_today"]
    assert happened["precompute_ran"] is True
    assert happened["execution_ran"] is True
    assert happened["broker_recon_present"] is True
    assert happened["shadow_ran"] is True
    assert happened["research_packet_current"] is True

    assert research_registry_cli.main(["daily-summary", "--outputs-root", str(outputs), "--trade-date", "2026-05-28", "--json"]) == 0
    json_payload = json.loads(capsys.readouterr().out)
    assert json_payload["status"] == "OK"
    assert json_payload["summary"]["what_happened_today"]["execution_ran"] is True

    assert research_registry_cli.main(["daily-summary", "--outputs-root", str(outputs), "--trade-date", "2026-05-28", "--markdown"]) == 0
    markdown = capsys.readouterr().out
    assert "# MCP Daily Summary" in markdown
    assert "Precompute ran: `True`" in markdown


def test_operator_daily_summary_missing_artifact_needs_operator(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    (outputs / "precompute").mkdir(parents=True)

    payload = call_tool("operator_daily_summary", {"outputs_root": str(outputs), "trade_date": "2026-05-28"})

    assert payload["status"] == "NEEDS_OPERATOR"
    assert "precompute: NEEDS_OPERATOR" in payload["warnings"]
    assert "execution is not current for 2026-05-28" in payload["warnings"]
    assert payload["summary"]["what_happened_today"]["execution_ran"] is False


def test_operator_daily_summary_requires_execution_results_and_integrity(tmp_path: Path) -> None:
    outputs = _artifact_status_fixture(tmp_path)
    (outputs / "runs" / "2026-05-28T093500-0400_phase6" / "execution_results.json").unlink()
    (outputs / "runs" / "2026-05-28T093500-0400_phase6" / "audit" / "execution_integrity.json").unlink()

    payload = call_tool("operator_daily_summary", {"outputs_root": str(outputs), "trade_date": "2026-05-28"})

    assert payload["status"] == "NEEDS_OPERATOR"
    assert payload["summary"]["what_happened_today"]["execution_ran"] is False
    assert "execution_results artifact is missing for 2026-05-28" in payload["warnings"]
    assert "execution integrity artifact is missing for 2026-05-28" in payload["warnings"]


def test_artifact_drilldown_omits_raw_large_payloads_and_is_read_only(tmp_path: Path, capsys) -> None:
    outputs = _artifact_status_fixture(tmp_path)
    secret = "DO_NOT_DUMP_PHASE6_SECRET"
    contract = outputs / "precompute" / "2026-05-28" / "contract.json"
    _write_json(contract, {"trade_date": "2026-05-28", "status": "READY", "secret": secret, "large": "x" * 10000})
    source_files = [path for path in outputs.rglob("*") if path.is_file()]
    before = {path: path.read_bytes() for path in source_files}

    payload = call_tool("artifact_drilldown", {"outputs_root": str(outputs), "family": "precompute"})
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "OK"
    assert payload["drilldown"]["precompute"]["files"]["contract.json"]["exists"] is True
    assert secret not in serialized
    assert "xxxxxxxxxxxxxxxxxxxxxxxx" not in serialized
    assert {path: path.read_bytes() for path in source_files} == before

    assert research_registry_cli.main(["artifact-drilldown", "--outputs-root", str(outputs), "--family", "precompute", "--markdown"]) == 0
    markdown = capsys.readouterr().out
    assert "# MCP Artifact Drilldown" in markdown
    assert secret not in markdown
