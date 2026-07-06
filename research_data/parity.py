from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from research_data import api
from research_data.hydration import read_json, utc_now_iso, write_json


SCHEMA_VERSION = "sleeve_data_parity_v1"
RUNTIME_IMPACT = "read_only_sleeve_parity_no_trading_path_changes"
DEFAULT_MIGRATION_ROOT = Path("outputs/research/data_migration")
DEFAULT_LEGACY_CANDIDATES_ROOT = Path("outputs/shadow_candidates")


def build_sleeve_parity_report(
    *,
    repo_root: Path,
    as_of_date: str | None = None,
    sleeve_id: str | None = None,
    migration_readiness_path: Path | None = None,
    legacy_candidates_root: Path | None = None,
    output_root: Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    migration_path = _resolve(root, migration_readiness_path) if migration_readiness_path else _latest_migration_readiness(root)
    migration = read_json(migration_path)
    effective_as_of = as_of_date or str(migration.get("as_of_date") or date.today().isoformat())
    selected = _select_sleeve(migration.get("sleeves") or [], sleeve_id=sleeve_id)
    destination = _resolve(root, output_root or DEFAULT_MIGRATION_ROOT) / effective_as_of

    if selected is None:
        payload = _base_payload(
            root=root,
            generated_at=utc_now_iso(),
            as_of_date=effective_as_of,
            selected_sleeve=None,
            migration_path=migration_path,
            legacy_path=None,
        )
        payload.update(
            {
                "parity_status": "BLOCKED",
                "recommendation": "BLOCKED_NO_READY_OBSERVE_ONLY_SLEEVE",
                "fail_reasons": ["no READY_OBSERVE_ONLY sleeve exists in migration readiness artifact"],
                "differences": [],
            }
        )
        _write_report(root, destination, "unselected", payload)
        return payload

    legacy_root = _resolve(root, legacy_candidates_root or DEFAULT_LEGACY_CANDIDATES_ROOT)
    legacy_path = _find_legacy_candidate(legacy_root, selected["strategy_id"], effective_as_of)
    payload = _base_payload(
        root=root,
        generated_at=utc_now_iso(),
        as_of_date=effective_as_of,
        selected_sleeve=selected,
        migration_path=migration_path,
        legacy_path=legacy_path,
    )

    if legacy_path is None:
        payload.update(
            {
                "parity_status": "BLOCKED",
                "recommendation": "BLOCKED_MISSING_LEGACY_ARTIFACT",
                "fail_reasons": [f"no legacy shadow candidate found for {selected['strategy_id']} on or before {effective_as_of}"],
                "differences": [],
            }
        )
        _write_report(root, destination, str(selected["sleeve_id"]), payload)
        return payload

    legacy = read_json(legacy_path)
    holdings = _legacy_holdings(legacy)
    canonical = _load_canonical_inputs(root, effective_as_of)
    comparisons = _compare_symbols(holdings, canonical)
    freshness_missing = _missing_freshness_rows(selected["required_dataset_ids"], canonical["freshness"]["rows"])
    stale_days = _staleness_days(str(legacy.get("effective_trade_date") or legacy.get("trade_date") or ""), effective_as_of)
    differences = _differences(comparisons, freshness_missing, stale_days)
    differences.extend(_readiness_differences(selected))
    fail_reasons = [row["reason_code"] for row in differences if row["severity"] == "FAIL"]
    warn_reasons = [row["reason_code"] for row in differences if row["severity"] == "WARN"]

    input_parity_status = "PASS" if not fail_reasons else "FAIL"
    signal_result = None
    if fail_reasons:
        parity_status = "BLOCKED"
        recommendation = "BLOCKED_CANONICAL_INPUT_COVERAGE"
        signal_status = "NOT_RUN_CANONICAL_INPUT_COVERAGE_BLOCKED"
        output_status = "FAIL_INPUT_COVERAGE"
    else:
        signal_result = _run_signal_adapter(selected, legacy, canonical, _legacy_signal_date(legacy, effective_as_of))
        signal_status = str(signal_result["signal_parity_status"])
        if signal_status == "PASS":
            parity_status = "PASS" if not warn_reasons else "WARN"
            recommendation = "PARITY_PASS" if not warn_reasons else "PARITY_PASS_WITH_WARNINGS"
            output_status = "PASS"
        elif signal_status.startswith("BLOCKED"):
            parity_status = "BLOCKED"
            recommendation = signal_status
            output_status = signal_status
            fail_reasons = [signal_status]
        else:
            parity_status = "WARN"
            recommendation = "CANONICAL_SIGNAL_DIFF_REVIEW_REQUIRED"
            output_status = "DIFF"
            warn_reasons.append(signal_status)
            differences.append(
                {
                    "severity": "WARN",
                    "reason_code": signal_status,
                    "detail": signal_result.get("reason") or "Canonical signal output differs from legacy output.",
                }
            )

    output_parity = _output_parity_from_signal(signal_result, holdings, comparisons, output_status)

    payload.update(
        {
            "parity_status": parity_status,
            "recommendation": recommendation,
            "fail_reasons": fail_reasons,
            "warning_reasons": warn_reasons,
            "selected_sleeve_rationale": _selection_rationale(selected),
            "legacy_output": {
                "strategy_id": legacy.get("strategy_slug") or legacy.get("strategy_id") or selected["strategy_id"],
                "trade_date": legacy.get("trade_date"),
                "effective_trade_date": legacy.get("effective_trade_date") or legacy.get("trade_date"),
                "holding_count": len(holdings),
                "target_weight_sum": _round(sum(item["target_weight"] for item in holdings)),
                "holdings": holdings,
            },
            "fr_dh_inputs": _input_summary(canonical),
            "legacy_vs_fr_dh_inputs": {
                "legacy_symbols": [item["ticker"] for item in holdings],
                "canonical_price_symbols": canonical["prices"]["symbols"],
                "canonical_security_master_symbols": canonical["security_master"]["symbols"],
                "canonical_corporate_action_symbols": canonical["corporate_actions"]["symbols"],
                "freshness_dataset_ids": canonical["freshness"]["dataset_ids"],
                "missing_freshness_dataset_ids": freshness_missing,
            },
            "signal_parity": {
                **(signal_result or {}),
                "signal_replay_supported": bool(signal_result),
                "signal_parity_status": signal_status,
                "reason": (signal_result or {}).get("reason") or "Canonical signal replay was not run because input coverage is blocked.",
            },
            "output_parity": output_parity,
            "input_parity_status": input_parity_status,
            "per_symbol_diagnostics": comparisons,
            "differences": differences,
        }
    )
    _write_report(root, destination, str(selected["sleeve_id"]), payload)
    return payload


def render_sleeve_parity_markdown(payload: dict[str, Any]) -> str:
    selected = payload.get("selected_sleeve") or {}
    lines = [
        "# FR-DH Observe-Only Sleeve Parity",
        "",
        f"- As of date: {payload.get('as_of_date')}",
        f"- Sleeve: `{selected.get('sleeve_id')}` / `{selected.get('strategy_id')}`",
        f"- Status: {payload.get('parity_status')}",
        f"- Recommendation: {payload.get('recommendation')}",
        f"- Broker submission invoked: {str(payload.get('broker_submission_invoked')).lower()}",
        f"- Sleeve runtime invoked: {str(payload.get('sleeve_runtime_invoked')).lower()}",
        f"- Allocation mutation invoked: {str(payload.get('allocation_mutation_invoked')).lower()}",
        "",
        "## Input Summary",
        "",
        "| input | rows | symbols |",
        "|---|---:|---:|",
    ]
    for name, row in (payload.get("fr_dh_inputs") or {}).items():
        lines.append(f"| {name} | {row.get('row_count')} | {row.get('symbol_count')} |")
    lines.extend(
        [
            "",
            "## Per-Symbol Diagnostics",
            "",
            "| symbol | target_weight | price_rows | security_rows | corporate_action_rows | status |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in payload.get("per_symbol_diagnostics") or []:
        lines.append(
            f"| {row.get('ticker')} | {row.get('legacy_target_weight')} | {row.get('price_observation_count')} | "
            f"{row.get('security_master_row_count')} | {row.get('corporate_action_row_count')} | {row.get('input_parity_status')} |"
        )
    lines.extend(["", "## Differences", ""])
    differences = payload.get("differences") or []
    if not differences:
        lines.append("No input differences detected. Signal replay remains blocked until a canonical sleeve adapter exists.")
    else:
        for row in differences:
            lines.append(f"- {row.get('severity')} `{row.get('reason_code')}`: {row.get('detail')}")
    lines.extend(
        [
            "",
            "Runtime impact: read-only parity artifact only; no trading, broker, scheduler, allocation, sleeve promotion, or production data-path change.",
            "",
        ]
    )
    return "\n".join(lines)


def _base_payload(
    *,
    root: Path,
    generated_at: str,
    as_of_date: str,
    selected_sleeve: dict[str, Any] | None,
    migration_path: Path,
    legacy_path: Path | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "runtime_impact": RUNTIME_IMPACT,
        "selected_sleeve": selected_sleeve,
        "source_migration_readiness_path": _display_path(root, migration_path),
        "legacy_candidate_path": _display_path(root, legacy_path) if legacy_path else None,
        "broker_submission_invoked": False,
        "sleeve_runtime_invoked": False,
        "trading_runtime_invoked": False,
        "allocation_mutation_invoked": False,
        "production_data_path_mutation_invoked": False,
        "promotion_invoked": False,
    }


def _write_report(root: Path, destination: Path, sleeve_id: str, payload: dict[str, Any]) -> None:
    json_path = destination / f"sleeve_parity_{sleeve_id}.json"
    md_path = destination / f"sleeve_parity_{sleeve_id}.md"
    payload["json_path"] = _display_path(root, json_path)
    payload["markdown_path"] = _display_path(root, md_path)
    write_json(json_path, payload)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_sleeve_parity_markdown(payload), encoding="utf-8")


def _latest_migration_readiness(root: Path) -> Path:
    base = root / DEFAULT_MIGRATION_ROOT
    dates = sorted(path for path in base.iterdir() if path.is_dir()) if base.exists() else []
    if not dates:
        return base / "missing" / "migration_readiness.json"
    return dates[-1] / "migration_readiness.json"


def _select_sleeve(sleeves: list[dict[str, Any]], *, sleeve_id: str | None) -> dict[str, Any] | None:
    if sleeve_id:
        for row in sleeves:
            if row.get("sleeve_id") == sleeve_id:
                return dict(row)
        return None
    ready = [row for row in sleeves if row.get("migration_readiness_status") == "READY_OBSERVE_ONLY"]
    if not ready:
        return None
    return dict(
        sorted(
            ready,
            key=lambda row: (
                0 if row.get("sleeve_id") == "polaris" else 1,
                0 if row.get("lifecycle_stage") == "paper_observed" else 1,
                str(row.get("sleeve_id") or ""),
            ),
        )[0]
    )


def _selection_rationale(selected: dict[str, Any]) -> str:
    return (
        f"Selected {selected.get('sleeve_id')} because it is marked READY_OBSERVE_ONLY in the FR-DH migration "
        "readiness artifact and is the safest core-momentum baseline/control candidate."
    )


def _find_legacy_candidate(root: Path, strategy_id: str, as_of_date: str) -> Path | None:
    if not root.exists():
        return None
    candidates = []
    for dated_dir in root.iterdir():
        if not dated_dir.is_dir():
            continue
        if dated_dir.name > as_of_date:
            continue
        path = dated_dir / f"{strategy_id}.json"
        if path.exists():
            candidates.append(path)
    return sorted(candidates)[-1] if candidates else None


def _legacy_holdings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("holdings") or []
    holdings = []
    for row in rows:
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
        if not ticker:
            continue
        holdings.append(
            {
                "ticker": ticker,
                "target_weight": _float(row.get("target_weight")),
                "momentum_rank": row.get("momentum_rank"),
                "momentum_score": row.get("momentum_score"),
            }
        )
    if holdings:
        return holdings
    target_weights = payload.get("target_weights") or {}
    return [
        {"ticker": str(symbol).upper(), "target_weight": _float(weight), "momentum_rank": None, "momentum_score": None}
        for symbol, weight in sorted(target_weights.items())
    ]


def _load_canonical_inputs(root: Path, as_of_date: str) -> dict[str, dict[str, Any]]:
    prices = api.load_prices(repo_root=root, required=False, as_of_date=as_of_date)
    security_master = api.load_security_master(repo_root=root, required=False, as_of_date=as_of_date)
    corporate_actions = api.load_corporate_actions(repo_root=root, required=False, as_of_date=as_of_date)
    freshness = api.load_dataset_freshness(repo_root=root, required=False, as_of_date=as_of_date)
    return {
        "prices": _dataset_rows(prices, payload=_normalized_payload(root, "ohlcv_prices")),
        "security_master": _dataset_rows(security_master, payload=_normalized_payload(root, "security_master_pit")),
        "corporate_actions": _dataset_rows(corporate_actions, payload=_normalized_payload(root, "corporate_actions")),
        "freshness": _dataset_rows(freshness, symbol_fields=("dataset_id",), payload=_normalized_payload(root, "dataset_freshness")),
    }


def _normalized_payload(root: Path, dataset_id: str) -> dict[str, Any]:
    rel_path = api.NORMALIZED_ARTIFACTS.get(dataset_id)
    if rel_path is None:
        return {}
    path = root / rel_path
    return read_json(path) if path.exists() else {}


def _dataset_rows(rows: list[dict[str, Any]], *, symbol_fields: tuple[str, ...] = ("source_symbol", "ticker", "symbol"), payload: dict[str, Any] | None = None) -> dict[str, Any]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dataset_ids: set[str] = set()
    for row in rows:
        dataset_id = row.get("dataset_id")
        if dataset_id:
            dataset_ids.add(str(dataset_id))
        symbol = _row_symbol(row, symbol_fields)
        if symbol:
            by_symbol[symbol].append(row)
    return {
        "rows": rows,
        "row_count": len(rows),
        "symbols": sorted(by_symbol),
        "dataset_ids": sorted(dataset_ids),
        "by_symbol": dict(by_symbol),
        "coverage": (payload or {}).get("coverage") or {},
    }


def _row_symbol(row: dict[str, Any], symbol_fields: tuple[str, ...]) -> str | None:
    for field in symbol_fields:
        value = row.get(field)
        if value:
            return str(value).upper()
    security_id = str(row.get("security_id") or "")
    if security_id.startswith("YAHOO:") or security_id.startswith("SHARADAR_TICKER:"):
        return security_id.split(":", 1)[1].upper()
    return None


def _compare_symbols(holdings: list[dict[str, Any]], canonical: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons = []
    for holding in holdings:
        symbol = holding["ticker"]
        price_rows = canonical["prices"]["by_symbol"].get(symbol, [])
        security_rows = canonical["security_master"]["by_symbol"].get(symbol, [])
        action_rows = canonical["corporate_actions"]["by_symbol"].get(symbol, [])
        action_query_covered = _coverage_contains(canonical["corporate_actions"], symbol)
        missing = []
        if not price_rows and not _coverage_contains(canonical["prices"], symbol):
            missing.append("ohlcv_prices")
        if not security_rows and not _coverage_contains(canonical["security_master"], symbol):
            missing.append("security_master_pit")
        if not action_query_covered:
            missing.append("corporate_actions")
        status = "PASS" if not missing else "FAIL"
        comparisons.append(
            {
                "ticker": symbol,
                "legacy_target_weight": holding["target_weight"],
                "legacy_momentum_rank": holding.get("momentum_rank"),
                "legacy_momentum_score": holding.get("momentum_score"),
                "price_observation_count": len(price_rows),
                "security_master_row_count": len(security_rows),
                "corporate_action_row_count": len(action_rows),
                "canonical_price_available": bool(price_rows) or _coverage_contains(canonical["prices"], symbol),
                "canonical_security_master_available": bool(security_rows) or _coverage_contains(canonical["security_master"], symbol),
                "canonical_corporate_actions_query_covered": action_query_covered,
                "canonical_corporate_actions_available": bool(action_rows),
                "missing_required_inputs": missing,
                "input_parity_status": status,
            }
        )
    return comparisons


def _coverage_contains(dataset: dict[str, Any], symbol: str) -> bool:
    coverage = dataset.get("coverage") or {}
    for key in ("covered_symbols", "query_covered_symbols"):
        values = {str(value).upper() for value in coverage.get(key) or []}
        if symbol.upper() in values:
            return True
    return False


def _input_summary(canonical: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "row_count": data["row_count"],
            "symbol_count": len(data["symbols"]),
            "symbols": data["symbols"],
            "dataset_ids": data["dataset_ids"],
        }
        for name, data in canonical.items()
    }


def _run_signal_adapter(
    selected_sleeve: dict[str, Any],
    legacy: dict[str, Any],
    canonical: dict[str, dict[str, Any]],
    as_of_date: str,
) -> dict[str, Any]:
    sleeve_id = str(selected_sleeve.get("sleeve_id") or "").lower()
    strategy_id = str(selected_sleeve.get("strategy_id") or "").lower()
    if sleeve_id == "orion" or strategy_id == "caerus_orion":
        return _run_orion_rank_decay_signal_adapter(legacy, canonical, as_of_date)
    return _run_polaris_signal_adapter(legacy, canonical, as_of_date)


def _run_polaris_signal_adapter(legacy: dict[str, Any], canonical: dict[str, dict[str, Any]], as_of_date: str) -> dict[str, Any]:
    legacy_rank_rows = legacy.get("rank_table") or legacy.get("holdings") or []
    legacy_symbols = [str(row.get("ticker") or row.get("symbol") or "").upper() for row in legacy_rank_rows]
    legacy_symbols = [symbol for symbol in legacy_symbols if symbol]
    if not legacy_symbols:
        return {
            "adapter_name": "polaris_canonical_momentum_v1_observe_only",
            "signal_as_of_date": as_of_date,
            "signal_parity_status": "BLOCKED_LEGACY_RANK_TABLE_MISSING",
            "reason": "Legacy Polaris artifact has no rank_table or holdings symbols to compare.",
        }
    scores = []
    missing = []
    insufficient = []
    price_rows_by_symbol = canonical["prices"]["by_symbol"]
    for symbol in legacy_symbols:
        rows = sorted(price_rows_by_symbol.get(symbol, []), key=lambda row: str(row.get("trade_date") or ""))
        rows = [row for row in rows if str(row.get("trade_date") or "") <= as_of_date and row.get("close_adjusted", row.get("close")) is not None]
        score = _momentum_score(rows)
        if not rows:
            missing.append(symbol)
        elif score is None:
            insufficient.append(symbol)
        else:
            scores.append({"ticker": symbol, **score})
    if missing:
        return {
            "adapter_name": "polaris_canonical_momentum_v1_observe_only",
            "signal_as_of_date": as_of_date,
            "adapter_scope": "legacy_rank_table_symbols_only",
            "signal_parity_status": "BLOCKED_SPECIFIC_SYMBOL",
            "missing_price_symbols": missing,
            "reason": f"Missing canonical price rows for rank-table symbols: {', '.join(missing)}",
        }
    if insufficient:
        return {
            "adapter_name": "polaris_canonical_momentum_v1_observe_only",
            "signal_as_of_date": as_of_date,
            "adapter_scope": "legacy_rank_table_symbols_only",
            "signal_parity_status": "BLOCKED_INSUFFICIENT_PRICE_HISTORY",
            "insufficient_history_symbols": insufficient,
            "reason": f"Insufficient canonical price history for 12-1 momentum: {', '.join(insufficient)}",
        }
    ranked = sorted(scores, key=lambda row: (-float(row["momentum_score"]), row["ticker"]))
    for idx, row in enumerate(ranked, start=1):
        row["momentum_rank"] = idx
    legacy_selected = _legacy_selected_symbols(legacy)
    canonical_selected = [row["ticker"] for row in ranked[: len(legacy_selected) or 10]]
    legacy_ranked_symbols = legacy_symbols
    canonical_ranked_symbols = [row["ticker"] for row in ranked]
    selected_overlap = sorted(set(legacy_selected) & set(canonical_selected))
    exact_selected_order_match = legacy_selected == canonical_selected
    selected_set_match = set(legacy_selected) == set(canonical_selected)
    status = "PASS" if exact_selected_order_match else "DIFF"
    return {
        "adapter_name": "polaris_canonical_momentum_v1_observe_only",
        "signal_as_of_date": as_of_date,
        "adapter_scope": "legacy_rank_table_symbols_only",
        "score_formula": "momentum_score = 0.5*r12_1 + 0.3*r6_1 + 0.2*r3",
        "signal_parity_status": status,
        "reason": "Canonical ranking matches legacy selected order." if status == "PASS" else "Canonical ranking differs from legacy selected order.",
        "legacy_ranked_symbols": legacy_ranked_symbols,
        "canonical_ranked_symbols": canonical_ranked_symbols,
        "legacy_selected_symbols": legacy_selected,
        "canonical_selected_symbols": canonical_selected,
        "selected_overlap_count": len(selected_overlap),
        "selected_overlap_symbols": selected_overlap,
        "selected_set_match": selected_set_match,
        "exact_selected_order_match": exact_selected_order_match,
        "ranked_rows": ranked,
    }


def _run_orion_rank_decay_signal_adapter(legacy: dict[str, Any], canonical: dict[str, dict[str, Any]], as_of_date: str) -> dict[str, Any]:
    base = _run_polaris_signal_adapter(legacy, canonical, as_of_date)
    ranked = base.get("ranked_rows") or []
    if not ranked:
        base["adapter_name"] = "orion_rank_decay_canonical_momentum_v1_observe_only"
        return base

    legacy_selected = _legacy_selected_symbols(legacy)
    top_n = len(legacy_selected) or 5
    exit_rank_multiple = 2.0
    exit_rank_cutoff = int(top_n * exit_rank_multiple)
    rank_by_symbol = {str(row["ticker"]): int(row["momentum_rank"]) for row in ranked}
    active_symbols = _legacy_active_symbols(legacy) or legacy_selected
    keep = [symbol for symbol in active_symbols if rank_by_symbol.get(symbol, exit_rank_cutoff + 1) <= exit_rank_cutoff]
    fill = [str(row["ticker"]) for row in ranked if str(row["ticker"]) not in set(keep)]
    engine_selected = (keep + fill)[:top_n]
    engine_selected_set = set(engine_selected)
    canonical_selected = [str(row["ticker"]) for row in ranked if str(row["ticker"]) in engine_selected_set]
    selected_overlap = sorted(set(legacy_selected) & set(canonical_selected))
    exact_selected_order_match = legacy_selected == canonical_selected
    selected_set_match = set(legacy_selected) == set(canonical_selected)
    status = "PASS" if exact_selected_order_match else "DIFF"

    return {
        **base,
        "adapter_name": "orion_rank_decay_canonical_momentum_v1_observe_only",
        "adapter_scope": "legacy_rank_table_symbols_with_legacy_active_holdings_context",
        "rank_decay_exit_enabled": True,
        "rank_decay_top_n": top_n,
        "rank_decay_exit_rank_multiple": exit_rank_multiple,
        "rank_decay_exit_rank_cutoff": exit_rank_cutoff,
        "rank_decay_active_symbols": active_symbols,
        "rank_decay_kept_symbols": keep,
        "rank_decay_fill_symbols": [symbol for symbol in engine_selected if symbol not in set(keep)],
        "signal_parity_status": status,
        "reason": "Canonical Orion rank-decay selection matches legacy selected order."
        if status == "PASS"
        else "Canonical Orion rank-decay selection differs from legacy selected order.",
        "legacy_selected_symbols": legacy_selected,
        "canonical_selected_symbols": canonical_selected,
        "selected_overlap_count": len(selected_overlap),
        "selected_overlap_symbols": selected_overlap,
        "selected_set_match": selected_set_match,
        "exact_selected_order_match": exact_selected_order_match,
    }


def _momentum_score(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    closes = [_float(row.get("close_adjusted", row.get("close"))) for row in rows]
    if len(closes) <= 252 or any(value == 0 for value in (closes[-22], closes[-127], closes[-253])):
        return None
    r3 = closes[-1] / closes[-4] - 1.0 if len(closes) > 3 and closes[-4] else None
    r6_1 = closes[-22] / closes[-127] - 1.0
    r12_1 = closes[-22] / closes[-253] - 1.0
    if r3 is None:
        return None
    score = 0.5 * r12_1 + 0.3 * r6_1 + 0.2 * r3
    return {
        "r3": round(r3, 8),
        "r6_1": round(r6_1, 8),
        "r12_1": round(r12_1, 8),
        "momentum_score": round(score, 8),
    }


def _legacy_selected_symbols(legacy: dict[str, Any]) -> list[str]:
    rank_table = legacy.get("rank_table") or []
    selected = [
        str(row.get("ticker") or row.get("symbol") or "").upper()
        for row in rank_table
        if row.get("is_selected") is True and str(row.get("ticker") or row.get("symbol") or "").strip()
    ]
    if selected:
        return selected
    holdings = [
        str(row.get("ticker") or row.get("symbol") or "").upper()
        for row in legacy.get("holdings") or []
        if str(row.get("ticker") or row.get("symbol") or "").strip()
    ]
    return holdings


def _legacy_active_symbols(legacy: dict[str, Any]) -> list[str]:
    symbols = [
        str(row.get("ticker") or row.get("symbol") or "").upper()
        for row in legacy.get("holdings") or []
        if str(row.get("ticker") or row.get("symbol") or "").strip()
    ]
    seen = set()
    unique = []
    for symbol in symbols:
        if symbol in seen:
            continue
        seen.add(symbol)
        unique.append(symbol)
    return unique


def _legacy_signal_date(legacy: dict[str, Any], fallback: str) -> str:
    return str(legacy.get("effective_trade_date") or legacy.get("trade_date") or fallback)[:10]


def _output_parity_from_signal(
    signal_result: dict[str, Any] | None,
    holdings: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    output_status: str,
) -> dict[str, Any]:
    if not signal_result:
        return {
            "output_parity_status": output_status,
            "legacy_holding_count": len(holdings),
            "fr_dh_reconstructable_holding_count": sum(1 for row in comparisons if row["input_parity_status"] == "PASS"),
            "matching_target_weight_count": 0,
            "missing_symbol_count": sum(1 for row in comparisons if row["input_parity_status"] == "FAIL"),
        }
    legacy_selected = signal_result.get("legacy_selected_symbols") or []
    canonical_selected = signal_result.get("canonical_selected_symbols") or []
    legacy_weights = {row["ticker"]: row["target_weight"] for row in holdings}
    canonical_weight = round(1.0 / len(canonical_selected), 8) if canonical_selected else 0.0
    matching_weight_count = sum(
        1
        for symbol in legacy_selected
        if symbol in canonical_selected and round(float(legacy_weights.get(symbol) or 0.0), 8) == canonical_weight
    )
    return {
        "output_parity_status": output_status,
        "legacy_holding_count": len(holdings),
        "fr_dh_reconstructable_holding_count": len(canonical_selected),
        "matching_target_weight_count": matching_weight_count,
        "missing_symbol_count": sum(1 for row in comparisons if row["input_parity_status"] == "FAIL"),
        "legacy_selected_symbols": legacy_selected,
        "canonical_selected_symbols": canonical_selected,
        "selected_overlap_count": signal_result.get("selected_overlap_count"),
        "selected_set_match": signal_result.get("selected_set_match"),
        "exact_selected_order_match": signal_result.get("exact_selected_order_match"),
    }


def _missing_freshness_rows(required_dataset_ids: list[str], freshness_rows: list[dict[str, Any]]) -> list[str]:
    seen = {str(row.get("dataset_id")) for row in freshness_rows if row.get("dataset_id")}
    return sorted(dataset_id for dataset_id in required_dataset_ids if dataset_id != "dataset_freshness" and dataset_id not in seen)


def _differences(
    comparisons: list[dict[str, Any]],
    freshness_missing: list[str],
    stale_days: int | None,
) -> list[dict[str, str]]:
    differences: list[dict[str, str]] = []
    missing_symbols = [row["ticker"] for row in comparisons if row["missing_required_inputs"]]
    if missing_symbols:
        differences.append(
            {
                "severity": "FAIL",
                "reason_code": "FR_DH_INPUT_COVERAGE_INSUFFICIENT",
                "detail": (
                    f"{len(missing_symbols)} legacy holding symbols are missing required canonical OHLCV and/or "
                    f"security-master coverage: {', '.join(missing_symbols)}"
                ),
            }
        )
    if freshness_missing:
        differences.append(
            {
                "severity": "FAIL",
                "reason_code": "DATASET_FRESHNESS_DOES_NOT_COVER_REQUIRED_INPUTS",
                "detail": f"dataset_freshness lacks rows for required datasets: {', '.join(freshness_missing)}",
            }
        )
    if stale_days is not None and stale_days > 1:
        differences.append(
            {
                "severity": "WARN",
                "reason_code": "LEGACY_ARTIFACT_STALE_RELATIVE_TO_AS_OF_DATE",
                "detail": f"latest legacy candidate is {stale_days} calendar days before the parity as_of_date",
            }
        )
    if not comparisons:
        differences.append(
            {
                "severity": "FAIL",
                "reason_code": "LEGACY_OUTPUT_EMPTY",
                "detail": "legacy candidate contains no holdings to compare",
            }
        )
    return differences


def _readiness_differences(selected: dict[str, Any]) -> list[dict[str, str]]:
    differences: list[dict[str, str]] = []
    symbol_coverage = selected.get("symbol_coverage") or {}
    security_coverage = (symbol_coverage.get("coverage_by_dataset") or {}).get("security_master_pit") or {}
    if "security_master_pit" in (selected.get("warning_dataset_ids") or []):
        grade = security_coverage.get("pit_grade_status")
        if grade and grade != "PIT_GRADE":
            differences.append(
                {
                    "severity": "WARN",
                    "reason_code": "SECURITY_MASTER_PIT_NOT_PIT_GRADE",
                    "detail": f"security_master_pit coverage is {grade}; PIT_GRADE is required for clean observe-only readiness.",
                }
            )
    return differences


def _staleness_days(legacy_date: str, as_of_date: str) -> int | None:
    if not legacy_date:
        return None
    try:
        return (date.fromisoformat(as_of_date[:10]) - date.fromisoformat(legacy_date[:10])).days
    except ValueError:
        return None


def _round(value: float) -> float:
    return round(float(value), 8)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _resolve(repo_root: Path, path: Path | None) -> Path:
    if path is None:
        return repo_root
    return path if path.is_absolute() else repo_root / path


def _display_path(repo_root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)
