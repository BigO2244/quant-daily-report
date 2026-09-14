"""Build Aquila's precompute source exclusively from local immutable evidence."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.aquila_monthly import AquilaContractError, build_aquila_source
from core.aquila_recovery_closure import (
    AquilaRecoveryClosureError,
    verified_recovery_closure,
)
from core.portfolio_operating_model import content_hash, file_hash
from core.price_hydration import DEFAULT_CACHE_PATH
from paper.trading_calendar import prev_trading_day


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise AquilaContractError(f"object required: {path}")
    return value


def _verified(path: Path) -> dict[str, Any]:
    value = _read(path)
    body = dict(value)
    if body.pop("content_hash", None) != content_hash(body):
        raise AquilaContractError(f"content hash mismatch: {path}")
    return value


def _time(value: str) -> dt.datetime:
    result = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise AquilaContractError("source timestamp needs timezone")
    return result


def _immutable(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise AquilaContractError(f"immutable source already differs: {path}")
        return
    with path.open("xb") as handle:
        handle.write(raw)


def _monthly_state(root: Path, ownership: dict, quantities: dict, generated_at: str) -> tuple[dict | None, list[dict]]:
    """Only completed execution receipts can establish an Aquila formation."""
    from authority.exact_plan import exact_execution_plan_from_dict

    successes = []
    attempts = []
    for payload_path in sorted((root / "outputs/paper_lane/runs").glob("*/execution_payload.json")):
        payload = _read(payload_path)
        plan = payload.get("exact_execution_plan") or {}
        qa = (plan.get("constraints") or {}).get("aquila_quantity_authority")
        if not qa:
            continue
        exact = exact_execution_plan_from_dict(plan, expected_account_scope="PAPER")
        if exact.account_id_hash != ownership.get("account_id_hash"):
            raise AquilaContractError("Aquila execution belongs to a different account")
        if payload.get("exact_execution_plan_hash") != exact.content_hash or payload.get("mode") != "PAPER" or payload.get("trade_date") != exact.trade_date:
            raise AquilaContractError("execution payload plan identity mismatch")
        stamp = _time(payload["generated_at"])
        if stamp > _time(generated_at):
            raise AquilaContractError("future Aquila execution evidence")
        receipt_path = payload_path.with_name("execution_results.json")
        summary_path = payload_path.with_name("live_pilot_operator_summary.json")
        receipt, summary = _read(receipt_path), _read(summary_path)
        success = (
            receipt.get("status") in {"SUBMITTED", "AUTHORIZED_NO_TRADE"}
            and summary.get("terminal_outcome") in {"RECONCILED_SUCCESS", "AUTHORIZED_NO_TRADE"}
            and summary.get("plan_hash_received") == exact.content_hash
            and summary.get("plan_id_received") == exact.plan_id
            and summary.get("plan_hash_validated") is True
            and summary.get("authorization_validated") is True
            and summary.get("dry_run") is False
            and summary.get("mode") == "PAPER"
            and summary.get("run_id") == payload.get("run_id")
            and summary.get("trade_date") == exact.trade_date
            and receipt.get("mode") == "PAPER"
            and receipt.get("run_id") == payload.get("run_id")
            and receipt.get("trade_date") == exact.trade_date
        )
        item = dict(timestamp=stamp, trade_date=exact.trade_date, plan=plan, qa=qa, paths=[payload_path, receipt_path, summary_path])
        attempts.append((exact.content_hash, success, stamp))
        if success:
            successes.append(item)
    completed = {item["plan"]["content_hash"] for item in successes}
    closed_by_successor = {}
    closure_evidence = []
    for item in successes:
        try:
            closure = verified_recovery_closure(
                    repo_root=root,
                    successful_plan=item["plan"],
                    trade_date=item["trade_date"],
            )
            if closure is not None:
                for digest in closure.parent_plan_hashes:
                    prior = closed_by_successor.get(digest)
                    closed_by_successor[digest] = (
                        item["timestamp"] if prior is None else min(prior, item["timestamp"])
                    )
                closure_evidence.extend(closure.evidence_paths)
        except AquilaRecoveryClosureError as exc:
            # A malformed claimed recovery is not an ordinary historical
            # failure.  It must be investigated rather than ignored.
            raise AquilaContractError("invalid governed Aquila recovery closure") from exc
    if any(
        not ok
        and digest not in completed
        and (digest not in closed_by_successor or stamp >= closed_by_successor[digest])
        for digest, ok, stamp in attempts
    ):
        raise AquilaContractError("unresolved Aquila execution requires explicit recovery")
    if not successes:
        if quantities:
            raise AquilaContractError("Aquila ownership has no successful formation receipt")
        return None, []
    receipts_by_time = {}
    for item in successes:
        receipts_by_time.setdefault(item["timestamp"], set()).add(item["plan"]["content_hash"])
    if any(len(hashes) > 1 for hashes in receipts_by_time.values()):
        raise AquilaContractError("ambiguous simultaneous Aquila execution receipts")
    latest = max(successes, key=lambda item: item["timestamp"])
    if _time(ownership["as_of"]) < latest["timestamp"]:
        raise AquilaContractError("ownership predates latest reconciled execution")
    formations = [item for item in successes if item["qa"]["quantity_contract"]["action"] == "MONTHLY_REBALANCE"]
    if not formations:
        raise AquilaContractError("no executed monthly formation")
    formation = max(formations, key=lambda item: item["timestamp"])
    c = formation["qa"]["quantity_contract"]
    latest_c = latest["qa"]["quantity_contract"]
    if latest_c["action"] == "HOLD_NO_REBALANCE" and latest_c.get("monthly_plan_sha256") != formation["plan"]["content_hash"]:
        raise AquilaContractError("hold receipt diverges from originating formation")
    evidence = [{"path": str(path.relative_to(root)), "sha256": file_hash(path)}
                for path in dict.fromkeys(formation["paths"] + latest["paths"] + closure_evidence)]
    return dict(status="RECONCILED", formation_month=formation["trade_date"][:7],
                formation_id=c["formation_id"], formation_hash=c["formation_hash"],
                formation_session=c["formation_session"], monthly_plan_sha256=formation["plan"]["content_hash"],
                quantities=quantities), evidence


def build_daily_source(*, repo_root: Path, bundle_dir: Path, trade_date: str, generated_at: str | None = None, capture_missing_ranking: bool = False) -> Path:
    from research.flow_detection.data import load_local_price_panel

    root = repo_root.resolve()
    destination = root / f"outputs/shadow_candidates/{trade_date}/caerus_aquila.json"
    if generated_at is None and destination.exists():
        generated_at = _read(destination).get("generated_at_utc")
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc).isoformat()
    previous = prev_trading_day(trade_date)
    book_path = root / "outputs/ledger/paper/ownership_latest.json"
    value_path = root / "outputs/ledger/paper/valuation_latest.json"
    book, valuation = _verified(book_path), _verified(value_path)
    if not book.get("opening_contract_hash") or not book.get("account_id_hash"):
        raise AquilaContractError("prospective ownership opening required")
    if book.get("as_of") != valuation.get("as_of") or any((x.get("reconciliation") or {}).get("status") != "PASS" for x in (book, valuation)):
        raise AquilaContractError("account valuation and ownership do not reconcile at one timestamp")
    as_of = _time(book["as_of"])
    if as_of.date().isoformat() < previous or as_of > _time(generated_at):
        raise AquilaContractError("ownership snapshot stale or future")
    quantities = {}
    for row in book.get("positions") or []:
        if row.get("sleeve_id") == "caerus_aquila" and float(row["quantity"]) != 0:
            if row["symbol"] in quantities:
                raise AquilaContractError("duplicate Aquila ownership")
            quantities[row["symbol"]] = float(row["quantity"])
    state, receipts = _monthly_state(root, book, quantities, generated_at)
    monthly = state is None or previous[:7] != trade_date[:7]
    ranking = None
    if monthly:
        ranking_path = root / f"outputs/aquila/rankings/{previous}.json"
        if not ranking_path.exists() and capture_missing_ranking:
            try:
                subprocess.run([sys.executable, str(root / "scripts/capture_aquila_ranking.py"),
                                "--output-root", str(root / "outputs/aquila/rankings"),
                                "--previous-session", previous, "--execution-session", trade_date],
                               cwd=root, timeout=920, check=True, capture_output=True, text=True)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                # Child output can contain provider URLs; point to sanitized
                # immutable receipts instead of echoing raw stdout/stderr.
                raise AquilaContractError(
                    "aquila_ranking_capture_failed: inspect immutable failure.json under "
                    + str(root / "outputs/aquila/rankings")
                    + "; no formation published (" + type(exc).__name__ + ")"
                ) from None
            # Provider capture must predate source generation, including the
            # first runtime capture that began after this producer started.
            generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
        ranking = _verified(ranking_path)
        symbols = [r["execution_symbol"] for r in ranking["issuers"][:10]]
    else:
        symbols = sorted(quantities)
        ranking_path = root / f"outputs/aquila/rankings/{state['formation_session']}.json"
        origin_ranking = _verified(ranking_path)
        if origin_ranking["content_hash"] != state["formation_hash"]:
            raise AquilaContractError("HOLD issuer map differs from executed formation")
    alias_ranking = ranking if monthly else origin_ranking
    aliases = {}
    for issuer in alias_ranking["issuers"]:
        symbol = str(issuer["execution_symbol"])
        if symbol not in symbols:
            continue
        cache_symbol = str(issuer.get("yahoo_symbol") or "")
        if not cache_symbol or cache_symbol != symbol.replace(".", "-") or symbol in aliases:
            raise AquilaContractError("invalid or duplicate execution/cache symbol alias")
        aliases[symbol] = cache_symbol
    if set(aliases) != set(symbols) or len(set(aliases.values())) != len(aliases):
        raise AquilaContractError("incomplete or colliding execution/cache aliases")
    panel = load_local_price_panel(symbols=list(aliases.values()), start_date=previous, end_date=previous, paths=[root / DEFAULT_CACHE_PATH])
    reverse_aliases = {cache_symbol: symbol for symbol, cache_symbol in aliases.items()}
    marks = {reverse_aliases[str(row["ticker"])]: float(row["close"]) for _, row in panel.iterrows()}
    if set(marks) != set(symbols):
        raise AquilaContractError("canonical prior-session close prices incomplete")
    valuation_file = bundle_dir / f"aquila_valuation_{file_hash(value_path)}.json"
    _immutable(valuation_file, value_path.read_bytes())
    ownership_file = bundle_dir / f"aquila_ownership_{file_hash(book_path)}.json"
    _immutable(ownership_file, book_path.read_bytes())
    ownership = dict(trade_date=trade_date, reconciliation_status="PASS", content_hash=book["content_hash"],
                     quantities=quantities, source_path=str(ownership_file.resolve()), source_sha256=file_hash(ownership_file))
    close = dt.datetime.combine(dt.date.fromisoformat(previous), dt.time(16), tzinfo=ZoneInfo("America/New_York"))
    source = build_aquila_source(trade_date=trade_date, previous_session=previous, generated_at=generated_at,
                                 account_equity=valuation["equity"], marks=marks, marks_as_of=close.isoformat(),
                                 ownership=ownership, ranking=ranking, monthly_state=state,
                                 holding_issuer_map={r["execution_symbol"]: r["issuer_id"] for r in alias_ranking["issuers"]})
    source["producer_lineage"] = dict(valuation_sha256=file_hash(valuation_file), valuation_path=str(valuation_file.resolve()), monthly_execution_receipts=receipts,
                                       price_cache_path=str(DEFAULT_CACHE_PATH), price_cache_sha256=file_hash(root / DEFAULT_CACHE_PATH))
    source["producer_lineage"]["ranking_sha256"] = file_hash(ranking_path)
    source["producer_lineage"]["ranking_path"] = str(ranking_path)
    source["producer_lineage"]["execution_to_cache_symbols"] = aliases
    destination = root / f"outputs/shadow_candidates/{trade_date}/caerus_aquila.json"
    _immutable(destination, (json.dumps(source, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
    return destination
