from __future__ import annotations

import json
from pathlib import Path

from core.operating_truth import (
    compile_operating_truth,
    content_hash,
    load_lane_registry,
    render_operating_state,
)


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _seed(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    decision = {
        "decision": "APPROVE",
        "decision_id": "lyra-live-owner-decision:20260819",
    }
    decision["content_hash"] = content_hash(decision)
    _write(root / "docs/decision.json", decision)
    _write(
        root / "config/research/strategy_registry.json",
        {
            "strategies": [
                {
                    "strategy_id": "caerus_polaris",
                    "shadow_tracking": {"enabled": True},
                },
                {
                    "strategy_id": "caerus_orion",
                    "shadow_tracking": {"enabled": True},
                    "paper_execution": {
                        "enabled": True,
                        "approval_scope": "PAPER_ONLY",
                        "owner_approved_at": "2026-08-08",
                    },
                },
                {
                    "strategy_id": "caerus_lyra",
                    "shadow_tracking": {"enabled": True},
                },
            ]
        },
    )
    lanes = [
        {
            "lane_id": "shadow_comparison",
            "lane_kind": "SHADOW",
            "declared_state": "ACTIVE",
            "strategy_ids": ["caerus_lyra", "caerus_orion", "caerus_polaris"],
            "authority": {"kind": "strategy_registry_shadow"},
            "schedule": {"marker": "shadow-job"},
            "performance_surface": "outputs/shadow.json",
        },
        {
            "lane_id": "orion_paper",
            "lane_kind": "PAPER",
            "declared_state": "ACTIVE",
            "strategy_ids": ["caerus_orion"],
            "authority": {
                "kind": "strategy_registry_paper",
                "approval_scope": "PAPER_ONLY",
                "owner_approved_at": "2026-08-08",
            },
            "schedule": {"marker": "paper-job"},
            "performance_surface": "outputs/ledger/paper/performance.json",
            "broker_manifest": "outputs/ledger/paper/manifest.json",
        },
        {
            "lane_id": "lyra_live",
            "lane_kind": "LIVE",
            "declared_state": "ACTIVE",
            "strategy_ids": ["caerus_lyra"],
            "authority": {
                "kind": "owner_decision",
                "path": "docs/decision.json",
                "decision_id": decision["decision_id"],
                "content_hash": decision["content_hash"],
            },
            "schedule": {"marker": "lyra-job"},
            "runtime": {
                "env_path": ".caerus/lyra.env",
                "required_gates": {"LYRA_ENABLED": "1"},
                "state_root": ".caerus/lyra_state",
            },
            "performance_surface": "outputs/ledger/live/performance.json",
            "broker_manifest": "outputs/ledger/live/manifest.json",
        },
        {
            "lane_id": "legacy_live",
            "lane_kind": "LIVE",
            "declared_state": "DISABLED",
            "strategy_ids": [],
            "authority": {"kind": "legacy_runtime_gates"},
            "schedule": {"marker": "legacy-job"},
            "runtime": {
                "env_path": ".caerus/legacy.env",
                "disabled_when_any": {"LEGACY_KILL": "1"},
            },
            "performance_surface": "outputs/legacy.csv",
        },
    ]
    registry = {
        "schema_version": "caerus.operating_lane_registry.v1",
        "registry_id": "test-registry",
        "effective_at": "2026-08-29T10:00:00-04:00",
        "owner": "Brett Olson",
        "truth_precedence": ["owner", "broker", "narrative"],
        "lanes": lanes,
        "narrative_surfaces": [
            {
                "path": "AGENTS.md",
                "required_claims": ["Lyra Live active"],
                "forbidden_claims": ["Live blocked"],
            }
        ],
    }
    registry["content_hash"] = content_hash(registry)
    _write(root / "config/operations/operating_lane_registry.json", registry)
    _write(root / "AGENTS.md", "Lyra Live active\n")
    _write(root / "outputs/shadow.json", "{}")
    for lane in ("paper", "live"):
        _write(
            root / f"outputs/ledger/{lane}/manifest.json",
            {"pass": True, "last_pull": "2026-08-28T23:15:00Z"},
        )
    _write(home / ".caerus/lyra.env", "LYRA_ENABLED=1\nSECRET=do-not-read\n")
    _write(home / ".caerus/legacy.env", "LEGACY_KILL=1\n")
    completed = {
        "execution_session": "2026-08-20",
        "status": "COMPLETE",
        "broker_write_performed": True,
        "posttrade_reconciliation": {"status": "ALIGNED"},
    }
    _write(home / ".caerus/lyra_state/2026-08-20/result.json", completed)
    cron = "shadow-job\npaper-job\nlyra-job\nlegacy-job\n"
    return root, home, cron


def test_lane_registry_is_hash_bound(tmp_path: Path) -> None:
    root, _, _ = _seed(tmp_path)
    payload = load_lane_registry(root / "config/operations/operating_lane_registry.json")
    assert payload["registry_id"] == "test-registry"
    payload["owner"] = "someone else"
    _write(root / "config/operations/operating_lane_registry.json", payload)
    try:
        load_lane_registry(root / "config/operations/operating_lane_registry.json")
    except Exception as exc:
        assert "content hash" in str(exc)
    else:
        raise AssertionError("mutated registry passed")


def test_three_concurrent_lanes_and_disabled_legacy_are_distinct(tmp_path: Path) -> None:
    root, home, cron = _seed(tmp_path)
    result = compile_operating_truth(
        repo_root=root,
        home=home,
        crontab_text=cron,
        observed_at="2026-08-29T14:00:00+00:00",
    )
    lanes = {row["lane_id"]: row for row in result["lanes"]}
    assert lanes["shadow_comparison"]["operating_status"] == "ACTIVE"
    assert lanes["orion_paper"]["operating_status"] == "ACTIVE"
    assert lanes["lyra_live"]["operating_status"] == "ACTIVE"
    assert lanes["legacy_live"]["operating_status"] == "DISABLED"
    assert result["context_integrity"]["status"] == "PASS"
    assert lanes["lyra_live"]["runtime_gates"]["observed_keys"] == ["LYRA_ENABLED"]


def test_shadow_status_cannot_negate_live_authority(tmp_path: Path) -> None:
    root, home, cron = _seed(tmp_path)
    result = compile_operating_truth(
        repo_root=root, home=home, crontab_text=cron,
        observed_at="2026-08-29T14:00:00+00:00",
    )
    lyra = next(row for row in result["lanes"] if row["lane_id"] == "lyra_live")
    assert lyra["authority"]["status"] == "PROVED"
    assert lyra["operating_status"] == "ACTIVE"


def test_latest_blocked_attempt_is_active_exception_not_global_halt(tmp_path: Path) -> None:
    root, home, cron = _seed(tmp_path)
    blocked = {
        "execution_session": "2026-08-25",
        "status": "BLOCKED",
        "reason_code": "target_effective_date_stale",
        "broker_write_performed": False,
    }
    _write(home / ".caerus/lyra_state/2026-08-25/blocked_attempts/x.json", blocked)
    result = compile_operating_truth(
        repo_root=root, home=home, crontab_text=cron,
        observed_at="2026-08-29T14:00:00+00:00",
    )
    lanes = {row["lane_id"]: row for row in result["lanes"]}
    assert lanes["lyra_live"]["operating_status"] == "ACTIVE_WITH_EXCEPTION"
    assert lanes["orion_paper"]["operating_status"] == "ACTIVE"
    assert lanes["lyra_live"]["latest_execution"]["broker_write_performed"] is True


def test_narrative_conflict_fails_context_not_lane_authority(tmp_path: Path) -> None:
    root, home, cron = _seed(tmp_path)
    _write(root / "AGENTS.md", "Live blocked\n")
    result = compile_operating_truth(
        repo_root=root, home=home, crontab_text=cron,
        observed_at="2026-08-29T14:00:00+00:00",
    )
    assert result["context_integrity"]["status"] == "CONTEXT_CONFLICT"
    assert len(result["context_integrity"]["conflicts"]) == 2


def test_generated_state_is_deterministic_and_lane_specific(tmp_path: Path) -> None:
    root, home, cron = _seed(tmp_path)
    result = compile_operating_truth(
        repo_root=root, home=home, crontab_text=cron,
        observed_at="2026-08-29T14:00:00+00:00",
    )
    first = render_operating_state(result)
    second = render_operating_state(result)
    assert first == second
    assert "| LIVE | Lyra | ACTIVE | PROVED |" in first
    assert "| PAPER | Orion | ACTIVE | PROVED |" in first
    assert "disabled legacy FR-104 lane does not disable Lyra Live" in first
