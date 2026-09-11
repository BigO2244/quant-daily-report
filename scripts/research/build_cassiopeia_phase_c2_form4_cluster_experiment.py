#!/usr/bin/env python3
"""Build Cassiopeia Phase C2 cluster-aware Form 4 research evidence.

Research-only. Consumes an existing Phase C Form 4 event-tape JSON artifact,
clusters PIT-valid insider filings by ticker/transaction type/tradable-date
window, and measures whether purchase clusters remain interesting after role,
transaction-value, filing-delay, and cost filters. It does not fetch SEC data,
generate signals, change allocations, touch broker state, modify risk controls,
or update cron/runtime behavior.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "caerus_cassiopeia_phase_c2_form4_cluster_experiment_v1"
GOVERNANCE_LABEL = "RESEARCH_ONLY"
EXECUTION_IMPACT = "NON_EXECUTIONAL"
DEFAULT_OUTPUT_DATE = "2026-06-29"
DEFAULT_CLUSTER_WINDOW_DAYS = 5
DEFAULT_MAX_SOURCE_EVENTS = 500
DEFAULT_MIN_PURCHASE_VALUE = 100_000.0
DEFAULT_MIN_CLUSTER_COUNT = 30
DEFAULT_COST_ROUND_TRIPS = 2.0
HORIZONS = (1, 5, 20, 60)
HIGH_QUALITY_ROLES = {"ceo", "cfo", "president", "director", "ten_percent_owner"}


def _finite(value: Any, *, allow_zero: bool = False) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    if out == 0 and allow_zero:
        return out
    return out if out > 0 else None


def _numeric(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _round(value: float | None, digits: int = 10) -> float | None:
    return None if value is None else round(float(value), digits)


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "hit_rate": None, "min": None, "max": None, "t_stat": None}
    series = pd.Series(values, dtype="float64")
    std = float(series.std(ddof=1)) if len(series) > 1 else 0.0
    t_stat = (float(series.mean()) / (std / math.sqrt(len(series)))) if std > 0 else None
    return {
        "count": int(len(series)),
        "mean": _round(float(series.mean())),
        "median": _round(float(series.median())),
        "hit_rate": _round(float((series > 0).mean())),
        "min": _round(float(series.min())),
        "max": _round(float(series.max())),
        "t_stat": _round(t_stat),
    }


def _parse_date(value: Any) -> pd.Timestamp | None:
    if value in {None, ""}:
        return None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.normalize()


def _transaction_date(event: dict[str, Any]) -> pd.Timestamp | None:
    tx_dates = []
    for tx in event.get("transactions") or []:
        parsed = _parse_date(tx.get("transaction_date"))
        if parsed is not None:
            tx_dates.append(parsed)
    if tx_dates:
        return min(tx_dates)
    return _parse_date(event.get("period_of_report"))


def _filing_delay_days(event: dict[str, Any]) -> int | None:
    tx_date = _transaction_date(event)
    tradable_date = _parse_date(event.get("tradable_date"))
    if tx_date is None or tradable_date is None:
        return None
    return int((tradable_date - tx_date).days)


def _event_excess_return(event: dict[str, Any], horizon: int) -> float | None:
    direct = _numeric(event.get(f"excess_return_vs_spy_{horizon}d"))
    if direct is not None:
        return direct
    forward = _numeric(event.get(f"forward_return_{horizon}d"))
    spy = _numeric(event.get(f"spy_forward_return_{horizon}d"))
    if forward is None or spy is None:
        return None
    return forward - spy


def _event_sort_key(event: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(event.get("ticker") or ""),
        str(event.get("transaction_type") or ""),
        str(event.get("tradable_date") or ""),
        str(event.get("acceptance_datetime_utc") or ""),
        str(event.get("accession_number") or ""),
    )


def _bounded_pit_events(payload: dict[str, Any], max_source_events: int | None) -> list[dict[str, Any]]:
    events = [e for e in payload.get("event_tape", {}).get("events", []) if e.get("pit_validity_flag")]
    events = sorted(events, key=_event_sort_key)
    return events[:max_source_events] if max_source_events is not None else events


def _cluster_source_events(events: list[dict[str, Any]], *, cluster_window_days: int, cost_round_trips: float) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[(str(event.get("ticker") or ""), str(event.get("transaction_type") or ""))].append(event)

    clusters: list[dict[str, Any]] = []
    for (ticker, transaction_type), rows in sorted(grouped.items()):
        current: list[dict[str, Any]] = []
        last_date: pd.Timestamp | None = None
        for event in sorted(rows, key=_event_sort_key):
            tradable_date = _parse_date(event.get("tradable_date"))
            if not current or last_date is None or tradable_date is None or int((tradable_date - last_date).days) <= cluster_window_days:
                current.append(event)
            else:
                clusters.append(_build_cluster(ticker, transaction_type, current, len(clusters) + 1, cost_round_trips))
                current = [event]
            if tradable_date is not None:
                last_date = tradable_date
        if current:
            clusters.append(_build_cluster(ticker, transaction_type, current, len(clusters) + 1, cost_round_trips))
    return clusters


def _build_cluster(ticker: str, transaction_type: str, events: list[dict[str, Any]], idx: int, cost_round_trips: float) -> dict[str, Any]:
    ordered = sorted(events, key=_event_sort_key)
    anchor = ordered[0]
    dates = [str(e.get("tradable_date") or "") for e in ordered if e.get("tradable_date")]
    role_weights = [_finite(e.get("role_weight"), allow_zero=True) or 0.0 for e in ordered]
    max_role_weight = max(role_weights) if role_weights else 0.0
    role_counts = Counter(str(e.get("insider_role") or "missing") for e in ordered)
    top_role = sorted(role_counts.items(), key=lambda item: (-item[1], item[0]))[0][0] if role_counts else "missing"
    purchase_value = sum(_numeric(e.get("purchase_value")) or 0.0 for e in ordered)
    sale_value = sum(_numeric(e.get("sale_value")) or 0.0 for e in ordered)
    filing_delays = [delay for delay in (_filing_delay_days(e) for e in ordered) if delay is not None]
    cost_bps = _numeric(anchor.get("implementation_shortfall_proxy_bps")) or 0.0
    cluster = {
        "cluster_id": f"form4_c2_{idx:05d}",
        "ticker": ticker,
        "transaction_type": transaction_type,
        "sector": str(anchor.get("sector") or "missing"),
        "first_tradable_date": min(dates) if dates else None,
        "last_tradable_date": max(dates) if dates else None,
        "raw_event_count": len(ordered),
        "accession_numbers": [e.get("accession_number") for e in ordered if e.get("accession_number")],
        "anchor_accession_number": anchor.get("accession_number"),
        "anchor_acceptance_datetime_utc": anchor.get("acceptance_datetime_utc"),
        "purchase_value": _round(purchase_value),
        "sale_value": _round(sale_value),
        "net_transaction_value": _round(purchase_value - sale_value),
        "insider_role_counts": dict(sorted(role_counts.items())),
        "top_insider_role": top_role,
        "max_role_weight": _round(max_role_weight),
        "role_quality_pass": bool(top_role in HIGH_QUALITY_ROLES and max_role_weight >= 1.0),
        "filing_delay_days": {
            "count": len(filing_delays),
            "min": min(filing_delays) if filing_delays else None,
            "max": max(filing_delays) if filing_delays else None,
            "median": _round(float(pd.Series(filing_delays).median())) if filing_delays else None,
        },
        "implementation_shortfall_proxy_bps": _round(cost_bps),
        "cost_round_trips": cost_round_trips,
    }
    for horizon in HORIZONS:
        excess = _event_excess_return(anchor, horizon)
        cluster[f"excess_return_vs_spy_{horizon}d"] = _round(excess)
        cluster[f"net_excess_return_vs_spy_{horizon}d"] = _round(excess - (cost_round_trips * cost_bps / 10_000.0)) if excess is not None else None
    return cluster


def _forward_summary(clusters: list[dict[str, Any]], *, field_prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for horizon in HORIZONS:
        values = [_numeric(c.get(f"{field_prefix}_{horizon}d")) for c in clusters]
        out[f"{horizon}d"] = _summary([v for v in values if v is not None])
    return out


def _cohort_summary(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"by_transaction_type": {}, "by_role_quality": {}, "by_sector": {}}
    groups: dict[str, dict[str, list[dict[str, Any]]]] = {
        "by_transaction_type": defaultdict(list),
        "by_role_quality": defaultdict(list),
        "by_sector": defaultdict(list),
    }
    for cluster in clusters:
        groups["by_transaction_type"][str(cluster.get("transaction_type") or "missing")].append(cluster)
        groups["by_role_quality"]["pass" if cluster.get("role_quality_pass") else "fail"].append(cluster)
        groups["by_sector"][str(cluster.get("sector") or "missing")].append(cluster)
    for cohort_name, cohort_groups in groups.items():
        for key, rows in sorted(cohort_groups.items()):
            out[cohort_name][key] = {
                "cluster_count": len(rows),
                "net_excess_return": _forward_summary(rows, field_prefix="net_excess_return_vs_spy"),
            }
    return out


def _classification(
    *,
    source_events: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    purchase_clusters: list[dict[str, Any]],
    eligible_purchase_clusters: list[dict[str, Any]],
    min_cluster_count: int,
) -> dict[str, Any]:
    if not source_events:
        return {"classification": "CASSIOPEIA_PHASE_C2_BLOCKED_DATA", "reason_codes": ["no_pit_valid_form4_source_events"]}
    if not clusters:
        return {"classification": "CASSIOPEIA_PHASE_C2_BLOCKED_DATA", "reason_codes": ["no_clusters_built"]}
    if not purchase_clusters:
        return {"classification": "CASSIOPEIA_PHASE_C2_NEEDS_DEEPER_EVIDENCE", "reason_codes": ["no_purchase_clusters", "sale_only_sample"]}
    if len(eligible_purchase_clusters) < min_cluster_count:
        return {
            "classification": "CASSIOPEIA_PHASE_C2_NEEDS_DEEPER_EVIDENCE",
            "reason_codes": ["eligible_purchase_cluster_sample_below_minimum", "continue_bounded_research"],
        }
    forward = _forward_summary(eligible_purchase_clusters, field_prefix="net_excess_return_vs_spy")
    p20 = forward["20d"]
    p60 = forward["60d"]
    if (
        p20["mean"] is not None
        and p20["mean"] > 0
        and p60["mean"] is not None
        and p60["mean"] > 0
        and (p20["hit_rate"] or 0) >= 0.52
    ):
        return {"classification": "CASSIOPEIA_PHASE_C2_PROMISING", "reason_codes": ["clustered_purchase_net_returns_positive", "role_and_value_filters_pass"]}
    return {
        "classification": "CASSIOPEIA_PHASE_C2_NEEDS_DEEPER_EVIDENCE",
        "reason_codes": ["purchase_cluster_net_returns_not_decision_grade"],
    }


def build_cluster_experiment(
    form4_payload: dict[str, Any],
    *,
    output_date: str,
    cluster_window_days: int = DEFAULT_CLUSTER_WINDOW_DAYS,
    max_source_events: int | None = DEFAULT_MAX_SOURCE_EVENTS,
    min_purchase_value: float = DEFAULT_MIN_PURCHASE_VALUE,
    min_cluster_count: int = DEFAULT_MIN_CLUSTER_COUNT,
    cost_round_trips: float = DEFAULT_COST_ROUND_TRIPS,
) -> dict[str, Any]:
    source_events = _bounded_pit_events(form4_payload, max_source_events)
    clusters = _cluster_source_events(source_events, cluster_window_days=cluster_window_days, cost_round_trips=cost_round_trips)
    purchase_clusters = [c for c in clusters if c.get("transaction_type") == "purchase"]
    eligible_purchase_clusters = [
        c
        for c in purchase_clusters
        if c.get("role_quality_pass") and (_numeric(c.get("purchase_value")) or 0.0) >= min_purchase_value
    ]
    rejection_counts = Counter()
    for cluster in purchase_clusters:
        if not cluster.get("role_quality_pass"):
            rejection_counts["role_quality_failed"] += 1
        if (_numeric(cluster.get("purchase_value")) or 0.0) < min_purchase_value:
            rejection_counts["purchase_value_below_threshold"] += 1

    classification = _classification(
        source_events=source_events,
        clusters=clusters,
        purchase_clusters=purchase_clusters,
        eligible_purchase_clusters=eligible_purchase_clusters,
        min_cluster_count=min_cluster_count,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "artifact_date": output_date,
        "strategy_id": "caerus_cassiopeia",
        "sleeve_id": "cassiopeia",
        "governance_label": GOVERNANCE_LABEL,
        "execution_impact": EXECUTION_IMPACT,
        "production_impact": "research_only",
        "research_only": True,
        "runtime_change": False,
        "source_schema_version": form4_payload.get("schema_version"),
        "source_artifact_date": form4_payload.get("artifact_date"),
        "event_family": "form4_insider_activity_cluster_experiment",
        "generation_bounds": {
            "cluster_window_days": cluster_window_days,
            "max_source_events": max_source_events,
            "source_event_count": len(source_events),
            "source_bounded": max_source_events is not None and len(form4_payload.get("event_tape", {}).get("events", [])) > len(source_events),
            "min_purchase_value": min_purchase_value,
            "min_cluster_count": min_cluster_count,
            "cost_round_trips": cost_round_trips,
        },
        "cluster_tape": {
            "cluster_count": len(clusters),
            "purchase_cluster_count": len(purchase_clusters),
            "eligible_purchase_cluster_count": len(eligible_purchase_clusters),
            "transaction_type_distribution": dict(Counter(c.get("transaction_type") for c in clusters)),
            "purchase_filter_rejections": dict(sorted(rejection_counts.items())),
            "clusters": clusters,
        },
        "cluster_forward_evidence": {
            "all_clusters": _forward_summary(clusters, field_prefix="net_excess_return_vs_spy"),
            "purchase_clusters": _forward_summary(purchase_clusters, field_prefix="net_excess_return_vs_spy"),
            "eligible_purchase_clusters": _forward_summary(eligible_purchase_clusters, field_prefix="net_excess_return_vs_spy"),
        },
        "cohort_evidence": _cohort_summary(clusters),
        "filing_delay_diagnostics": {
            "all_clusters": _summary([
                float(c["filing_delay_days"]["median"])
                for c in clusters
                if c.get("filing_delay_days", {}).get("median") is not None
            ]),
            "eligible_purchase_clusters": _summary([
                float(c["filing_delay_days"]["median"])
                for c in eligible_purchase_clusters
                if c.get("filing_delay_days", {}).get("median") is not None
            ]),
        },
        "overlap_correlation": {
            "classification": "NOT_COMPUTED_NO_CANDIDATE_HOLDINGS",
            "reason": "C2 clusters are event-study evidence only; sleeve overlap should be measured after a candidate holding-generation rule exists.",
        },
        "pit_validity": {
            "pit_safe": bool(form4_payload.get("pit_validity", {}).get("pit_safe")) and all(c.get("first_tradable_date") for c in clusters),
            "availability_rule": "Cluster anchors use the earliest PIT-valid Form 4 tradable_date in each ticker/transaction-type cluster.",
        },
        "classification": classification,
        "non_goals": [
            "no Cassiopeia activation",
            "no live signals",
            "no allocation changes",
            "no execution changes",
            "no broker behavior changes",
            "no risk-control changes",
            "no promotion-threshold changes",
            "no cron changes",
        ],
    }


def _latest_phase_c_artifact(repo_root: Path) -> Path:
    out_dir = repo_root / "outputs" / "research" / "cassiopeia"
    candidates = sorted(out_dir.glob("cassiopeia_phase_c_form4_event_tape_*.json"))
    if not candidates:
        raise FileNotFoundError(f"No Phase C Form 4 event-tape JSON found under {out_dir}")
    return candidates[-1]


def write_artifacts(repo_root: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = repo_root / "outputs" / "research" / "cassiopeia"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = payload["artifact_date"]
    json_path = out_dir / f"cassiopeia_phase_c2_form4_cluster_experiment_{date}.json"
    md_path = out_dir / f"cassiopeia_phase_c2_form4_cluster_experiment_{date}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_markdown_summary(payload), encoding="utf-8")
    return json_path, md_path


def _markdown_summary(payload: dict[str, Any]) -> str:
    cluster = payload["cluster_tape"]
    lines = [
        "# Cassiopeia Phase C2 Form 4 Cluster Experiment",
        "",
        "RESEARCH_ONLY",
        "NO_RUNTIME_CHANGE",
        "",
        f"Classification: `{payload['classification']['classification']}`",
        f"Source event count: `{payload['generation_bounds']['source_event_count']}`",
        f"Cluster count: `{cluster['cluster_count']}`",
        f"Purchase cluster count: `{cluster['purchase_cluster_count']}`",
        f"Eligible purchase cluster count: `{cluster['eligible_purchase_cluster_count']}`",
        f"PIT safe: `{payload['pit_validity']['pit_safe']}`",
        "",
        "## Eligible Purchase Cluster Net SPY-Relative Returns",
        "",
    ]
    for horizon in HORIZONS:
        stats = payload["cluster_forward_evidence"]["eligible_purchase_clusters"][f"{horizon}d"]
        lines.append(
            f"- {horizon}D: count `{stats['count']}`, mean `{stats['mean']}`, "
            f"median `{stats['median']}`, hit rate `{stats['hit_rate']}`, t-stat `{stats['t_stat']}`"
        )
    lines += [
        "",
        "## Purchase Filter Rejections",
        "",
        "```json",
        json.dumps(cluster["purchase_filter_rejections"], indent=2, sort_keys=True),
        "```",
        "",
        "## Interpretation",
        "",
        "C2 clusters same-ticker same-transaction-type Form 4 filings by bounded tradable-date windows and anchors returns to the first PIT-valid tradable date in the cluster.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--input-json", type=Path, default=None, help="Phase C Form 4 event-tape JSON. Defaults to the latest matching artifact.")
    parser.add_argument("--date", default=DEFAULT_OUTPUT_DATE)
    parser.add_argument("--cluster-window-days", type=int, default=DEFAULT_CLUSTER_WINDOW_DAYS)
    parser.add_argument("--max-source-events", type=int, default=DEFAULT_MAX_SOURCE_EVENTS)
    parser.add_argument("--min-purchase-value", type=float, default=DEFAULT_MIN_PURCHASE_VALUE)
    parser.add_argument("--min-cluster-count", type=int, default=DEFAULT_MIN_CLUSTER_COUNT)
    parser.add_argument("--cost-round-trips", type=float, default=DEFAULT_COST_ROUND_TRIPS)
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    input_json = args.input_json.resolve() if args.input_json else _latest_phase_c_artifact(repo_root)
    source_payload = json.loads(input_json.read_text(encoding="utf-8"))
    payload = build_cluster_experiment(
        source_payload,
        output_date=args.date,
        cluster_window_days=args.cluster_window_days,
        max_source_events=args.max_source_events,
        min_purchase_value=args.min_purchase_value,
        min_cluster_count=args.min_cluster_count,
        cost_round_trips=args.cost_round_trips,
    )
    payload["source_artifact_path"] = str(input_json)
    json_path, md_path = write_artifacts(repo_root, payload)
    print(json.dumps({
        "status": "OK",
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "classification": payload["classification"]["classification"],
        "cluster_count": payload["cluster_tape"]["cluster_count"],
        "eligible_purchase_cluster_count": payload["cluster_tape"]["eligible_purchase_cluster_count"],
        "pit_safe": payload["pit_validity"]["pit_safe"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
