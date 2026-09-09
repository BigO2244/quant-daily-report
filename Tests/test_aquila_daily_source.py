from Tests.fixtures.orion_registry import orion_registry
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from core.aquila_monthly import AquilaContractError
from core.portfolio_operating_model import content_hash
from scripts.build_aquila_daily_source import build_daily_source, _monthly_state


def write(path, body, hashed=False):
    body = dict(body)
    if hashed: body["content_hash"] = content_hash(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    return body


def setup(root):
    from core.price_hydration import DEFAULT_CACHE_PATH
    as_of = "2026-09-04T23:15:00+00:00"
    write(root / "outputs/ledger/paper/ownership_latest.json", dict(as_of=as_of, opening_contract_hash="e"*64,
          account_id_hash="f"*64, positions=[], reconciliation={"status": "PASS"}), True)
    write(root / "outputs/ledger/paper/valuation_latest.json", dict(as_of=as_of, equity=10000, reconciliation={"status": "PASS"}), True)
    write(root / "outputs/aquila/rankings/2026-09-04.json", dict(accepted=True, formation_session="2026-09-04",
          formation_id="sep", captured_at="2026-09-04T21:00:00+00:00",
          issuers=[dict(issuer_id=f"i{i}", execution_symbol=f"S{i}", yahoo_symbol=f"S{i}", market_cap=100-i) for i in range(11)]), True)
    cache = root / DEFAULT_CACHE_PATH
    cache.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dict(date="2026-09-04", ticker=f"S{i}", open=100, high=100, low=100, close=100, volume=1) for i in range(10)]).to_parquet(cache)


def test_producer_uses_local_cache_and_immutable_owner_snapshot(tmp_path):
    setup(tmp_path)
    args = dict(repo_root=tmp_path, bundle_dir=tmp_path / "outputs/precompute/2026-09-08",
                trade_date="2026-09-08", generated_at="2026-09-08T11:00:00+00:00")
    path = build_daily_source(**args)
    source = json.loads(path.read_text())
    assert source["quantity_contract"]["action"] == "MONTHLY_REBALANCE"
    assert Path(source["quantity_contract"]["ownership_snapshot_path"]).exists()
    assert build_daily_source(**args).read_bytes() == path.read_bytes()


def test_missing_prior_close_fails_before_publishing_source(tmp_path):
    setup(tmp_path)
    from core.price_hydration import DEFAULT_CACHE_PATH
    (tmp_path / DEFAULT_CACHE_PATH).unlink()
    with pytest.raises(AquilaContractError, match="close prices"):
        build_daily_source(repo_root=tmp_path, bundle_dir=tmp_path / "bundle", trade_date="2026-09-08", generated_at="2026-09-08T11:00:00+00:00")
    assert not (tmp_path / "outputs/shadow_candidates/2026-09-08/caerus_aquila.json").exists()


def receipt_fixture(root, monkeypatch, *, success):
    from Tests.test_exact_execution_choice2 import _plan, _rebuild_exact
    base = _plan().to_dict()
    constraints = dict(base["constraints"])
    constraints["aquila_quantity_authority"] = {"quantity_contract": dict(action="MONTHLY_REBALANCE", formation_id="aug",
                formation_hash="b"*64, formation_session="2026-07-31")}
    plan = _rebuild_exact(base, constraints=constraints).to_dict()
    run = root / "outputs/paper_lane/runs/run"
    write(run / "execution_payload.json", dict(exact_execution_plan=plan, exact_execution_plan_hash=plan["content_hash"],
          mode="PAPER", trade_date=plan["as_of"][:10], generated_at="2026-09-01T14:00:00+00:00", run_id="run"))
    write(run / "execution_results.json", dict(status="SUBMITTED" if success else "FAILED_RECONCILIATION", mode="PAPER", run_id="run", trade_date=plan["as_of"][:10]))
    write(run / "live_pilot_operator_summary.json", dict(mode="PAPER", run_id="run", trade_date=plan["as_of"][:10], terminal_outcome="RECONCILED_SUCCESS" if success else "FAILED_RECONCILIATION",
          plan_hash_received=plan["content_hash"], plan_id_received=plan["plan_id"], plan_hash_validated=True, authorization_validated=True, dry_run=False))
    return plan


def test_only_successful_receipt_establishes_monthly_state(orion_registry, tmp_path, monkeypatch):
    plan = receipt_fixture(tmp_path, monkeypatch, success=True)
    state, refs = _monthly_state(tmp_path, dict(as_of="2026-09-04T23:15:00+00:00", account_id_hash=plan["account_id_hash"]), {"S0": 4.75}, "2026-09-08T11:00:00+00:00")
    assert state["quantities"] == {"S0": 4.75}
    assert state["monthly_plan_sha256"] == plan["content_hash"]
    assert len(refs) == 3


def test_partial_failure_cannot_create_a_new_formation(orion_registry, tmp_path, monkeypatch):
    plan = receipt_fixture(tmp_path, monkeypatch, success=False)
    with pytest.raises(AquilaContractError, match="explicit recovery"):
        _monthly_state(tmp_path, dict(as_of="2026-09-04T23:15:00+00:00", account_id_hash=plan["account_id_hash"]), {}, "2026-09-08T11:00:00+00:00")


def test_failed_attempt_then_same_plan_successful_recovery_is_accepted(orion_registry, tmp_path, monkeypatch):
    import shutil
    plan = receipt_fixture(tmp_path, monkeypatch, success=False)
    run = tmp_path / "outputs/paper_lane/runs/run"
    failed = run.with_name("failed")
    shutil.copytree(run, failed)
    receipt_fixture(tmp_path, monkeypatch, success=True)
    state, refs = _monthly_state(tmp_path, dict(as_of="2026-09-04T23:15:00+00:00", account_id_hash=plan["account_id_hash"]), {"S0": 4}, "2026-09-08T11:00:00+00:00")
    assert state["monthly_plan_sha256"] == plan["content_hash"]
    assert json.loads((failed / "execution_results.json").read_text())["status"] == "FAILED_RECONCILIATION"


def test_origin_reads_actual_executor_no_trade_receipts(orion_registry, tmp_path, monkeypatch):
    import scripts.live_pilot_execute as executor
    from Tests.test_exact_execution_choice2 import _plan, _rebuild_exact, _handoff, _env, TrackingPaperBroker, run_live_pilot

    base = _plan(no_trade=True).to_dict()
    constraints = dict(base["constraints"])
    constraints["aquila_quantity_authority"] = {"quantity_contract": dict(action="MONTHLY_REBALANCE",
        formation_id="aug", formation_hash="b"*64, formation_session="2026-07-31")}
    exact = _rebuild_exact(base, constraints=constraints)
    monkeypatch.setattr(executor, "_now_utc", lambda: "2026-08-12T14:00:00+00:00")
    result = run_live_pilot(plan=_handoff(exact), broker=TrackingPaperBroker(), env=_env(),
                            run_id="aquila-receipt", output_root=tmp_path / "outputs/paper_lane")
    assert result["terminal_status"] == "AUTHORIZED_NO_TRADE"
    state, refs = _monthly_state(tmp_path, dict(as_of="2026-08-12T23:00:00+00:00", account_id_hash=exact.account_id_hash),
                                 {"OLD": 1}, "2026-08-13T11:00:00+00:00")
    assert state["monthly_plan_sha256"] == exact.content_hash
    assert len(refs) == 3


def test_missing_rankings_do_not_fetch_without_explicit_runtime_flag(tmp_path, monkeypatch):
    import scripts.build_aquila_daily_source as producer
    setup(tmp_path)
    (tmp_path / "outputs/aquila/rankings/2026-09-04.json").unlink()
    calls = []
    monkeypatch.setattr(producer.subprocess, "run", lambda *a, **k: calls.append(a))
    with pytest.raises(FileNotFoundError):
        build_daily_source(repo_root=tmp_path, bundle_dir=tmp_path / "bundle", trade_date="2026-09-08", generated_at="2026-09-08T11:00:00+00:00")
    assert calls == []


def test_runtime_missing_ranking_uses_bounded_subprocess(tmp_path, monkeypatch):
    import scripts.build_aquila_daily_source as producer
    setup(tmp_path)
    path = tmp_path / "outputs/aquila/rankings/2026-09-04.json"
    raw = path.read_bytes()
    path.unlink()
    calls = []
    def capture(command, **kwargs):
        calls.append((command, kwargs))
        path.write_bytes(raw)
    monkeypatch.setattr(producer.subprocess, "run", capture)
    original = producer.dt.datetime
    class FixedClock(original):
        @classmethod
        def now(cls, tz=None):
            return original.fromisoformat("2026-09-08T11:01:00+00:00")
    monkeypatch.setattr(producer, "dt", SimpleNamespace(datetime=FixedClock, date=producer.dt.date, time=producer.dt.time, timezone=producer.dt.timezone))
    source = build_daily_source(repo_root=tmp_path, bundle_dir=tmp_path / "bundle", trade_date="2026-09-08",
                                generated_at="2026-09-08T11:00:00+00:00", capture_missing_ranking=True)
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 920
    assert "--previous-session" in calls[0][0]
    assert json.loads(source.read_text())["generated_at_utc"] == "2026-09-08T11:01:00+00:00"


def berkshire_fixture(root):
    from core.price_hydration import DEFAULT_CACHE_PATH
    setup(root)
    ranking_path = root / "outputs/aquila/rankings/2026-09-04.json"
    ranking = json.loads(ranking_path.read_text())
    ranking.pop("content_hash")
    ranking["issuers"][0].update(execution_symbol="BRK.B", yahoo_symbol="BRK-B")
    ranking = write(ranking_path, ranking, True)
    cache_path = root / DEFAULT_CACHE_PATH
    panel = pd.read_parquet(cache_path)
    panel.loc[panel.ticker == "S0", "ticker"] = "BRK-B"
    panel.to_parquet(cache_path)
    return ranking


def test_berkshire_initial_formation_uses_explicit_cache_alias(tmp_path):
    berkshire_fixture(tmp_path)
    path = build_daily_source(repo_root=tmp_path, bundle_dir=tmp_path / "bundle", trade_date="2026-09-08", generated_at="2026-09-08T11:00:00+00:00")
    source = json.loads(path.read_text())
    assert source["quantity_contract"]["marks"]["BRK.B"] == 100
    assert source["producer_lineage"]["execution_to_cache_symbols"]["BRK.B"] == "BRK-B"


def test_berkshire_hold_reuses_executed_issuer_map(tmp_path, monkeypatch):
    import scripts.build_aquila_daily_source as producer
    ranking = berkshire_fixture(tmp_path)
    book_path = tmp_path / "outputs/ledger/paper/ownership_latest.json"
    book = json.loads(book_path.read_text())
    book.pop("content_hash")
    book["positions"] = [dict(symbol="BRK.B", sleeve_id="caerus_aquila", quantity=5)]
    write(book_path, book, True)
    state = dict(status="RECONCILED", formation_month="2026-09", formation_id=ranking["formation_id"],
        formation_hash=ranking["content_hash"], formation_session="2026-09-04", monthly_plan_sha256="c"*64, quantities={"BRK.B": 5})
    monkeypatch.setattr(producer, "_monthly_state", lambda *args: (state, []))
    path = build_daily_source(repo_root=tmp_path, bundle_dir=tmp_path / "bundle", trade_date="2026-09-08", generated_at="2026-09-08T11:00:00+00:00")
    source = json.loads(path.read_text())
    assert source["quantity_contract"]["action"] == "HOLD_NO_REBALANCE"
    assert source["quantity_contract"]["target_quantities"] == {"BRK.B": 5}
    assert source["producer_lineage"]["execution_to_cache_symbols"] == {"BRK.B": "BRK-B"}


def test_colliding_execution_cache_aliases_fail_closed(tmp_path):
    berkshire_fixture(tmp_path)
    path = tmp_path / "outputs/aquila/rankings/2026-09-04.json"
    ranking = json.loads(path.read_text())
    ranking.pop("content_hash")
    ranking["issuers"][1].update(execution_symbol="BRK-B", yahoo_symbol="BRK-B")
    write(path, ranking, True)
    with pytest.raises(AquilaContractError, match="colliding"):
        build_daily_source(repo_root=tmp_path, bundle_dir=tmp_path / "bundle", trade_date="2026-09-08", generated_at="2026-09-08T11:00:00+00:00")


def test_capture_failure_points_to_receipt_without_provider_secrets(tmp_path, monkeypatch):
    import scripts.build_aquila_daily_source as producer
    setup(tmp_path)
    (tmp_path / 'outputs/aquila/rankings/2026-09-04.json').unlink()
    def fail(*args, **kwargs):
        raise producer.subprocess.CalledProcessError(1, ['collector'], stderr='crumb=DO_NOT_RETAIN')
    monkeypatch.setattr(producer.subprocess, 'run', fail)
    with pytest.raises(Exception, match='aquila_ranking_capture_failed') as caught:
        build_daily_source(repo_root=tmp_path, bundle_dir=tmp_path / 'bundle', trade_date='2026-09-08', generated_at='2026-09-08T11:00:00+00:00', capture_missing_ranking=True)
    assert 'failure.json' in str(caught.value)
    assert 'DO_NOT_RETAIN' not in str(caught.value)
    assert not (tmp_path / 'outputs/shadow_candidates/2026-09-08/caerus_aquila.json').exists()
