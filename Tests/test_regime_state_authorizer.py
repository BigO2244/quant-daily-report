from __future__ import annotations

import json
from pathlib import Path

import pytest

from authority.contracts import AuthorityContractError
from authority.exact_plan import exact_execution_plan_from_dict
from core.live_pilot_guardrails import account_id_hash
from core.regime_state_store import (
    RegimeAuthorityEvent,
    RegimePersistenceResult,
    commit_prepared_regime_authority,
    load_regime_history,
)
from scripts.authorize_exact_execution_plan import (
    authorize_exact_execution_plan,
    finalize_regime_committed_handoff,
    main as authorize_main,
)
from Tests.test_exact_execution_choice2 import (
    TrackingPaperBroker,
    _env,
    _write_authority_chain,
    _write_orion_sleeve_authority,
)


def _authorized_plan(
    tmp_path: Path,
    *,
    risk_controls: dict,
    outer: dict | None = None,
    include_market_state_id: bool = True,
) -> tuple[dict, Path]:
    sleeve_path, sleeve_hash = _write_orion_sleeve_authority(tmp_path)
    target_rows = [{"symbol": "AAPL", "target_weight": 0.1, "price": 50.0}]
    approved_package, authority_paths = _write_authority_chain(
        tmp_path,
        target_rows=target_rows,
        sleeve_hash=sleeve_hash,
        constraints=risk_controls,
        include_market_state_id=include_market_state_id,
    )
    plan_path = tmp_path / "target-plan.json"
    plan_path.write_text("{}\n", encoding="utf-8")
    plan = {
        "trade_date": "2026-08-12",
        "execution_lane": "paper",
        "approved_sleeve": "caerus_orion",
        "allow_fractional": False,
        "target_portfolio": target_rows,
        "approved_execution_package": approved_package,
        "authority_package_paths": authority_paths,
        "cash_target_weight": 0.0,
        "risk_controls": risk_controls,
        "source_precompute_payload": (
            "outputs/precompute/2026-08-12/planned_execution_payload.json"
        ),
        "source_signals": "outputs/precompute/2026-08-12/signals.json",
        "source_sleeve_evaluations": sleeve_path,
        "source_sleeve_evaluations_sha256": sleeve_hash,
    }
    plan.update(outer or {})
    return plan, plan_path


def _commit_result(state_root: Path, result: dict) -> RegimePersistenceResult:
    metadata = result["regime_authority_event"]
    event = RegimeAuthorityEvent.from_dict(metadata["event"])
    return commit_prepared_regime_authority(
        state_root,
        RegimePersistenceResult(
            event=event,
            event_path=Path(metadata["path"]),
            created=False,
            committed=False,
        ),
    )


def test_authorizer_prepares_then_reuses_committed_observation_across_run_ids(
    tmp_path: Path,
):
    state_root = tmp_path / "regime-state"
    controls = {
        "regime_authority": {
            "observed_state": "LOW",
            "confidence": 0.9,
            "acute_risk": False,
            "market_state_id": "market:governed",
        }
    }
    plan, plan_path = _authorized_plan(tmp_path, risk_controls=controls)
    kwargs = {
        "plan": plan,
        "broker": TrackingPaperBroker(),
        "env": {**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        "run_id": "authority-persisted-regime",
        "plan_path": plan_path,
        "created_at": "2026-08-12T13:35:01+00:00",
        "regime_state_root": state_root,
    }

    first = authorize_exact_execution_plan(**kwargs)
    repeated_before_commit = authorize_exact_execution_plan(**kwargs)

    assert first["regime_authority_event"]["created"] is False
    assert first["regime_authority_event"]["committed_at_evaluation"] is False
    assert first["regime_authority_event"]["commit_required_before_pointer"] is True
    with pytest.raises(AuthorityContractError, match="not durably committed"):
        exact_execution_plan_from_dict(first["exact_execution_plan"])
    assert (
        first["regime_authority_event"]["content_hash"]
        == repeated_before_commit["regime_authority_event"]["content_hash"]
    )
    committed = _commit_result(state_root, first)
    assert committed.created is True
    finalized = finalize_regime_committed_handoff(first, committed)
    assert exact_execution_plan_from_dict(finalized["exact_execution_plan"])

    retried = authorize_exact_execution_plan(
        **{
            **kwargs,
            "run_id": "authority-persisted-regime-retry",
            "created_at": "2026-08-12T13:35:02+00:00",
        }
    )
    assert retried["regime_authority_event"]["committed_at_evaluation"] is True
    assert retried["regime_authority_event"]["commit_required_before_pointer"] is False
    assert (
        retried["regime_authority_event"]["content_hash"]
        == first["regime_authority_event"]["content_hash"]
    )
    regime_state = first["exact_execution_plan"]["regime_state"]
    assert regime_state["action"] == "BOOTSTRAP"
    assert regime_state["market_state_id"] == "market:governed"
    assert len(
        load_regime_history(
            state_root,
            account_scope="PAPER",
            account_id=account_id_hash("paper-account"),
            sleeve_id="caerus_orion",
        )
    ) == 1


def test_authorizer_rejects_mutable_outer_regime_confidence_and_counters(
    tmp_path: Path,
):
    controls = {
        "regime_authority": {
            "observed_state": "LOW",
            "confidence": 0.7,
            "acute_risk": False,
        }
    }
    plan, plan_path = _authorized_plan(
        tmp_path,
        risk_controls=controls,
        outer={"regime_confidence": 0.99},
    )
    with pytest.raises(RuntimeError, match="outer regime confidence diverges"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=TrackingPaperBroker(),
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authority-outer-tamper",
            plan_path=plan_path,
            created_at="2026-08-12T13:35:01+00:00",
            regime_state_root=tmp_path / "regime-state",
        )
    plan.pop("regime_confidence")
    plan["regime_bars_in_state"] = 99
    with pytest.raises(RuntimeError, match="outer regime persistence state is forbidden"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=TrackingPaperBroker(),
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authority-outer-counter-tamper",
            plan_path=plan_path,
            created_at="2026-08-12T13:35:01+00:00",
            regime_state_root=tmp_path / "regime-state",
        )

    plan.pop("regime_bars_in_state")
    plan["observed_regime"] = "CRISIS"
    with pytest.raises(RuntimeError, match="outer regime observation diverges"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=TrackingPaperBroker(),
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authority-outer-observation-tamper",
            plan_path=plan_path,
            created_at="2026-08-12T13:35:01+00:00",
            regime_state_root=tmp_path / "regime-state",
        )


def test_authorizer_requires_stable_governed_market_source_bar(tmp_path: Path):
    controls = {
        "regime_authority": {
            "observed_state": "LOW",
            "confidence": 0.9,
            "acute_risk": False,
        }
    }
    plan, plan_path = _authorized_plan(
        tmp_path,
        risk_controls=controls,
        include_market_state_id=False,
    )
    with pytest.raises(RuntimeError, match="stable market_state_id source bar"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=TrackingPaperBroker(),
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authority-missing-source-bar",
            plan_path=plan_path,
            created_at="2026-08-12T13:35:01+00:00",
            regime_state_root=tmp_path / "regime-state",
        )


def test_governed_emergency_veto_persists_after_acute_signal_clears(
    tmp_path: Path,
):
    state_root = tmp_path / "regime-state"
    emergency_controls = {
        "regime_authority": {
            "observed_state": "CRISIS",
            "confidence": 1.0,
            "acute_risk": True,
            "market_state_id": "market:emergency-source-bar",
        }
    }
    emergency_plan, plan_path = _authorized_plan(
        tmp_path,
        risk_controls=emergency_controls,
    )
    with pytest.raises(RuntimeError, match="vetoes new buy exposure"):
        authorize_exact_execution_plan(
            plan=emergency_plan,
            broker=TrackingPaperBroker(),
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authority-emergency-1",
            plan_path=plan_path,
            created_at="2026-08-12T13:35:01+00:00",
            regime_state_root=state_root,
        )

    # A retry with a different run ID but the exact same governed emergency
    # observation reuses the immediate event rather than incrementing counters.
    with pytest.raises(RuntimeError, match="vetoes new buy exposure"):
        authorize_exact_execution_plan(
            plan=emergency_plan,
            broker=TrackingPaperBroker(),
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authority-emergency-1-retry",
            plan_path=plan_path,
            created_at="2026-08-12T13:35:02+00:00",
            regime_state_root=state_root,
        )
    assert len(
        load_regime_history(
            state_root,
            account_scope="PAPER",
            account_id=account_id_hash("paper-account"),
            sleeve_id="caerus_orion",
        )
    ) == 1

    normal_controls = {
        "regime_authority": {
            "observed_state": "LOW",
            "confidence": 0.9,
            "acute_risk": False,
            "market_state_id": "market:next-source-bar",
        }
    }
    normal_plan, plan_path = _authorized_plan(
        tmp_path,
        risk_controls=normal_controls,
    )
    with pytest.raises(RuntimeError, match="vetoes new buy exposure"):
        authorize_exact_execution_plan(
            plan=normal_plan,
            broker=TrackingPaperBroker(),
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authority-emergency-2",
            plan_path=plan_path,
            created_at="2026-08-12T13:36:01+00:00",
            regime_state_root=state_root,
        )

    history = load_regime_history(
        state_root,
        account_scope="PAPER",
        account_id=account_id_hash("paper-account"),
        sleeve_id="caerus_orion",
    )
    assert len(history) == 2
    assert history[0].action == "EMERGENCY_RISK_RESPONSE"
    assert history[1].action == "PERSIST"
    assert history[1].effective_state == "EMERGENCY_RISK_OFF"
    assert history[1].risk_veto_buys is True


def test_authorizer_fails_closed_on_corrupt_persisted_regime_history(
    tmp_path: Path,
):
    state_root = tmp_path / "regime-state"
    controls = {
        "regime_authority": {
            "observed_state": "LOW",
            "confidence": 0.9,
            "acute_risk": False,
        }
    }
    plan, plan_path = _authorized_plan(tmp_path, risk_controls=controls)
    first = authorize_exact_execution_plan(
        plan=plan,
        broker=TrackingPaperBroker(),
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        run_id="authority-corruption-1",
        plan_path=plan_path,
        created_at="2026-08-12T13:35:01+00:00",
        regime_state_root=state_root,
    )
    _commit_result(state_root, first)
    event_path = Path(first["regime_authority_event"]["path"])
    event = json.loads(event_path.read_text())
    event["bars_in_effective_state"] = 999
    event_path.write_text(json.dumps(event), encoding="utf-8")

    with pytest.raises(RuntimeError, match="regime event content hash mismatch"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=TrackingPaperBroker(),
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authority-corruption-2",
            plan_path=plan_path,
            created_at="2026-08-12T13:36:01+00:00",
            regime_state_root=state_root,
        )


def test_failed_exact_build_does_not_commit_normal_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scripts.authorize_exact_execution_plan as authorizer

    state_root = tmp_path / "regime-state"
    controls = {
        "regime_authority": {
            "observed_state": "LOW",
            "confidence": 0.9,
            "acute_risk": False,
        }
    }
    plan, plan_path = _authorized_plan(tmp_path, risk_controls=controls)
    monkeypatch.setattr(
        authorizer,
        "build_exact_execution_plan",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("exact build failed")),
    )

    with pytest.raises(RuntimeError, match="exact build failed"):
        authorize_exact_execution_plan(
            plan=plan,
            broker=TrackingPaperBroker(),
            env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
            run_id="authority-build-failure",
            plan_path=plan_path,
            created_at="2026-08-12T13:35:01+00:00",
            regime_state_root=state_root,
        )
    assert load_regime_history(
        state_root,
        account_scope="PAPER",
        account_id=account_id_hash("paper-account"),
        sleeve_id="caerus_orion",
    ) == []


def test_failed_exact_publication_does_not_commit_normal_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scripts.authorize_exact_execution_plan as authorizer

    controls = {
        "regime_authority": {
            "observed_state": "LOW",
            "confidence": 0.9,
            "acute_risk": False,
        }
    }
    plan, plan_path = _authorized_plan(tmp_path, risk_controls=controls)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    output = tmp_path / "paper_lane" / "plans" / "exact.json"
    monkeypatch.setattr(authorizer.AlpacaBroker, "from_env", lambda: TrackingPaperBroker())
    monkeypatch.setattr(
        authorizer, "_now", lambda: "2026-08-12T13:35:01+00:00"
    )
    monkeypatch.setattr(
        "scripts.live_pilot_execute._now_utc",
        lambda: "2026-08-12T13:35:01+00:00",
    )
    monkeypatch.setattr(
        authorizer,
        "safe_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("publication failed")),
    )
    for key, value in {
        **_env(),
        "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000",
        "CAERUS_AUTHORIZATION_QUOTE_MAX_AGE_SECONDS": "1000000000",
    }.items():
        monkeypatch.setenv(key, value)

    assert authorize_main(
        ["--plan", str(plan_path), "--run-id", "publication-fail", "--output", str(output)]
    ) == 1

    assert load_regime_history(
        tmp_path / "paper_lane" / "state" / "regime_authority",
        account_scope="PAPER",
        account_id=account_id_hash("paper-account"),
        sleeve_id="caerus_orion",
    ) == []


def test_successful_exact_publication_commits_before_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import scripts.authorize_exact_execution_plan as authorizer

    controls = {
        "regime_authority": {
            "observed_state": "LOW",
            "confidence": 0.9,
            "acute_risk": False,
        }
    }
    plan, plan_path = _authorized_plan(tmp_path, risk_controls=controls)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    output = tmp_path / "paper_lane" / "plans" / "exact.json"
    monkeypatch.setattr(authorizer.AlpacaBroker, "from_env", lambda: TrackingPaperBroker())
    monkeypatch.setattr(
        authorizer, "_now", lambda: "2026-08-12T13:35:01+00:00"
    )
    monkeypatch.setattr(
        "scripts.live_pilot_execute._now_utc",
        lambda: "2026-08-12T13:35:01+00:00",
    )
    for key, value in {
        **_env(),
        "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000",
        "CAERUS_AUTHORIZATION_QUOTE_MAX_AGE_SECONDS": "1000000000",
    }.items():
        monkeypatch.setenv(key, value)

    assert authorize_main(
        ["--plan", str(plan_path), "--run-id", "publication-success", "--output", str(output)]
    ) == 0
    history = load_regime_history(
        tmp_path / "paper_lane" / "state" / "regime_authority",
        account_scope="PAPER",
        account_id=account_id_hash("paper-account"),
        sleeve_id="caerus_orion",
    )
    assert len(history) == 1
    assert output.exists()
    pointer = json.loads(output.read_text(encoding="utf-8"))
    published = json.loads(Path(pointer["json_path"]).read_text(encoding="utf-8"))
    exact = exact_execution_plan_from_dict(published["exact_execution_plan"])
    assert exact.regime_state["state_committed_at_evaluation"] is True
    assert exact.regime_state["state_commit_required_before_pointer"] is False
    assert exact.regime_state["state_event_hash"] == history[0].content_hash


def test_missing_or_tampered_committed_event_blocks_exact_reader(tmp_path: Path):
    state_root = tmp_path / "regime-state"
    controls = {
        "regime_authority": {
            "observed_state": "LOW",
            "confidence": 0.9,
            "acute_risk": False,
        }
    }
    plan, plan_path = _authorized_plan(tmp_path, risk_controls=controls)
    prepared = authorize_exact_execution_plan(
        plan=plan,
        broker=TrackingPaperBroker(),
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        run_id="authority-event-integrity",
        plan_path=plan_path,
        created_at="2026-08-12T13:35:01+00:00",
        regime_state_root=state_root,
    )
    committed = _commit_result(state_root, prepared)
    finalized = finalize_regime_committed_handoff(prepared, committed)
    event_text = committed.event_path.read_text()
    committed.event_path.unlink()
    with pytest.raises(AuthorityContractError, match="event is missing"):
        exact_execution_plan_from_dict(finalized["exact_execution_plan"])

    committed.event_path.write_text(event_text, encoding="utf-8")
    tampered = json.loads(event_text)
    tampered["effective_state"] = "TAMPERED"
    committed.event_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(AuthorityContractError, match="event is invalid"):
        exact_execution_plan_from_dict(finalized["exact_execution_plan"])
