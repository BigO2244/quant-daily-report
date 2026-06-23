from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd


SCHEMA_VERSION = "shadow_concentration_v1"
DEFAULT_SHADOW_CANDIDATE_ROOT = Path("outputs/shadow_candidates")
DEFAULT_PRICE_PANEL_PATH = Path("outputs/research/flow_detection_v1/price_panel.parquet")
DEFAULT_OUTPUT_ROOT = Path("outputs/research/shadow_concentration")


@dataclass(frozen=True)
class ShadowVariantDefinition:
    variant_name: str
    sleeve_slug: str
    sleeve_name: str
    top_n: int
    max_position_weight: float
    weighting_method: str
    deployment_status: str = "RESEARCH_SHADOW_ONLY"
    capital_impact: str = "NONE"


VARIANT_DEFINITIONS: tuple[ShadowVariantDefinition, ...] = (
    ShadowVariantDefinition(
        variant_name="polaris_concentrated_shadow",
        sleeve_slug="caerus_polaris",
        sleeve_name="Caerus Polaris",
        top_n=4,
        max_position_weight=0.20,
        weighting_method="equal",
    ),
    ShadowVariantDefinition(
        variant_name="orion_concentrated_shadow",
        sleeve_slug="caerus_orion",
        sleeve_name="Caerus Orion",
        top_n=3,
        max_position_weight=0.25,
        weighting_method="equal",
    ),
)

CURRENT_PORTFOLIO_NAMES = {
    "caerus_polaris": "current_polaris",
    "caerus_orion": "current_orion",
}


def latest_available_shadow_date(shadow_candidate_root: Path = DEFAULT_SHADOW_CANDIDATE_ROOT) -> str:
    candidates: list[str] = []
    if not shadow_candidate_root.exists():
        raise FileNotFoundError(f"Shadow candidate root not found: {shadow_candidate_root}")
    for child in shadow_candidate_root.iterdir():
        if not child.is_dir():
            continue
        try:
            normalized = pd.Timestamp(child.name).strftime("%Y-%m-%d")
        except Exception:
            continue
        if normalized != child.name:
            continue
        if all((child / f"{definition.sleeve_slug}.json").exists() for definition in VARIANT_DEFINITIONS):
            candidates.append(child.name)
    if not candidates:
        raise FileNotFoundError(f"No Polaris/Orion shadow candidate dates found under {shadow_candidate_root}")
    return max(candidates)


def build_shadow_concentration_artifact(
    *,
    trade_date: str | None = None,
    shadow_candidate_root: Path = DEFAULT_SHADOW_CANDIDATE_ROOT,
    price_panel_path: Path = DEFAULT_PRICE_PANEL_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    trade_date = trade_date or latest_available_shadow_date(shadow_candidate_root)
    trade_date = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
    generated_at_utc = generated_at_utc or f"{trade_date}T00:00:00Z"
    dated_input_dir = shadow_candidate_root / trade_date
    dated_output_dir = output_root / trade_date
    dated_output_dir.mkdir(parents=True, exist_ok=True)

    current_payloads = {
        definition.sleeve_slug: _read_json(dated_input_dir / f"{definition.sleeve_slug}.json")
        for definition in VARIANT_DEFINITIONS
    }
    current_snapshots = {
        sleeve_slug: build_current_snapshot(sleeve_slug=sleeve_slug, payload=payload)
        for sleeve_slug, payload in current_payloads.items()
    }
    variants = {
        definition.variant_name: build_concentrated_variant(
            definition=definition,
            current_payload=current_payloads[definition.sleeve_slug],
            current_snapshot=current_snapshots[definition.sleeve_slug],
        )
        for definition in VARIANT_DEFINITIONS
    }
    comparisons = {
        definition.sleeve_slug: compare_current_and_shadow(
            current=current_snapshots[definition.sleeve_slug],
            shadow=variants[definition.variant_name],
        )
        for definition in VARIANT_DEFINITIONS
    }

    portfolios = {
        current_snapshots["caerus_polaris"]["portfolio_name"]: current_snapshots["caerus_polaris"],
        variants["polaris_concentrated_shadow"]["portfolio_name"]: variants["polaris_concentrated_shadow"],
        current_snapshots["caerus_orion"]["portfolio_name"]: current_snapshots["caerus_orion"],
        variants["orion_concentrated_shadow"]["portfolio_name"]: variants["orion_concentrated_shadow"],
    }
    performance = build_performance_payload(
        trade_date=trade_date,
        portfolios=portfolios,
        price_panel_path=price_panel_path,
        output_root=output_root,
    )
    comparability = build_comparability_payload(
        dated_input_dir=dated_input_dir,
        current_payloads=current_payloads,
    )
    artifact_paths = {
        "root": str(dated_output_dir),
        "main_json": str(dated_output_dir / "shadow_concentration.json"),
        "dashboard": str(dated_output_dir / "shadow_concentration_dashboard.md"),
        "holdings_csv": str(dated_output_dir / "shadow_concentration_holdings.csv"),
        "comparison_csv": str(dated_output_dir / "shadow_concentration_comparison.csv"),
        "performance_json": str(dated_output_dir / "shadow_concentration_performance.json"),
        "performance_csv": str(dated_output_dir / "shadow_concentration_performance.csv"),
    }
    lineage = build_lineage_payload(
        dated_input_dir=dated_input_dir,
        price_panel_path=price_panel_path,
        current_payloads=current_payloads,
        output_root=output_root,
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc,
        "trade_date": trade_date,
        "governance_label": "RESEARCH_SHADOW_ONLY",
        "warning": "Research shadow artifact only. It is non-capital, non-executing, and does not alter live, paper, allocator, broker, scheduler, or production sleeve behavior.",
        "execution_impact": "NON_EXECUTIONAL",
        "capital_impact": "NONE",
        "runtime_behavior_changed": False,
        "methodology": {
            "source": "Existing daily shadow candidate artifacts",
            "decision_time_inputs": "Current sleeve rank_table, holdings, scores, and weights as emitted for the trade date.",
            "selection": "Select the highest-ranked names from each current sleeve rank table.",
            "weighting": "Equal weight with a hard max-position cap; capped residual is kept as cash.",
            "return_convention": "Next available close-to-close return after the trade date when available.",
            "limitations": [
                "Historical membership remains limited by the current-scale approximation blocker from FR-068.",
                "Forward shadow evidence is observational and not decision-grade historical proof.",
                "The artifact reads model/shadow files only and does not read broker state or submit orders.",
            ],
        },
        "variant_definitions": [asdict(definition) for definition in VARIANT_DEFINITIONS],
        "current_sleeves": current_snapshots,
        "shadow_variants": variants,
        "comparisons": comparisons,
        "performance": performance,
        "paper_live_comparability": comparability,
        "lineage": lineage,
        "artifact_paths": artifact_paths,
    }
    payload["artifact_digest"] = stable_payload_digest({k: v for k, v in payload.items() if k != "artifact_digest"})

    _write_json(dated_output_dir / "shadow_concentration.json", payload)
    _write_json(dated_output_dir / "shadow_concentration_performance.json", performance)
    for snapshot in current_snapshots.values():
        _write_json(dated_output_dir / f"{snapshot['portfolio_name']}.json", snapshot)
    for variant in variants.values():
        _write_json(dated_output_dir / f"{variant['variant_name']}.json", variant)
    write_holdings_csv(dated_output_dir / "shadow_concentration_holdings.csv", portfolios)
    write_comparison_csv(dated_output_dir / "shadow_concentration_comparison.csv", comparisons)
    write_performance_csv(dated_output_dir / "shadow_concentration_performance.csv", performance)
    (dated_output_dir / "shadow_concentration_dashboard.md").write_text(
        build_dashboard_markdown(payload),
        encoding="utf-8",
    )
    _write_json(dated_output_dir / "shadow_concentration.json", payload)
    return payload


def build_current_snapshot(*, sleeve_slug: str, payload: dict[str, Any]) -> dict[str, Any]:
    portfolio_name = CURRENT_PORTFOLIO_NAMES[sleeve_slug]
    weights = clean_weights(payload.get("target_weights") or {})
    holdings_by_ticker = {str(item.get("ticker")): item for item in payload.get("holdings") or []}
    holdings: list[dict[str, Any]] = []
    for ticker, weight in weights.items():
        source = holdings_by_ticker.get(ticker) or {}
        holdings.append(
            {
                "ticker": ticker,
                "rank": _optional_float(source.get("momentum_rank")),
                "score": _optional_float(source.get("momentum_score")),
                "weight": round_float(weight),
                "source_selected": True,
            }
        )
    holdings.sort(key=lambda item: (item["rank"] is None, item["rank"] if item["rank"] is not None else 999999, item["ticker"]))
    return {
        "portfolio_name": portfolio_name,
        "sleeve": sleeve_slug,
        "sleeve_name": str(payload.get("strategy_name") or sleeve_slug),
        "variant_name": "current_sleeve_construction",
        "source_variant": payload.get("source_variant"),
        "trade_date": payload.get("trade_date"),
        "effective_trade_date": payload.get("effective_trade_date"),
        "comparison_role": "CURRENT_CONTROL",
        "deployment_status": "EXISTING_CURRENT_SLEEVE_REFERENCE_ONLY",
        "capital_impact_from_this_artifact": "NONE",
        "holdings": holdings,
        "target_weights": {ticker: round_float(weight) for ticker, weight in weights.items()},
        "cash_weight": round_float(max(0.0, 1.0 - sum(weights.values()))),
        "expected_turnover_from_source": _optional_float(payload.get("expected_turnover")),
        "concentration": concentration_metrics(weights),
    }


def build_concentrated_variant(
    *,
    definition: ShadowVariantDefinition,
    current_payload: dict[str, Any],
    current_snapshot: dict[str, Any],
) -> dict[str, Any]:
    if definition.weighting_method != "equal":
        raise ValueError(f"Unsupported shadow concentration weighting method: {definition.weighting_method}")
    selected = select_rank_table_rows(current_payload, top_n=definition.top_n)
    if len(selected) < definition.top_n:
        raise ValueError(
            f"{definition.variant_name} requires {definition.top_n} ranked securities; found {len(selected)}"
        )
    raw_weight = 1.0 / definition.top_n
    capped_weight = min(raw_weight, definition.max_position_weight)
    weights = {row["ticker"]: round_float(capped_weight) for row in selected}
    holdings = [
        {
            "ticker": row["ticker"],
            "rank": _optional_float(row.get("momentum_rank")),
            "score": _optional_float(row.get("momentum_score")),
            "weight": round_float(weights[row["ticker"]]),
            "source_selected": bool(row.get("is_selected")),
        }
        for row in selected
    ]
    cash_weight = round_float(max(0.0, 1.0 - sum(weights.values())))
    return {
        "portfolio_name": definition.variant_name,
        "variant_name": definition.variant_name,
        "sleeve": definition.sleeve_slug,
        "sleeve_name": definition.sleeve_name,
        "trade_date": current_payload.get("trade_date"),
        "effective_trade_date": current_payload.get("effective_trade_date"),
        "source_current_portfolio": current_snapshot["portfolio_name"],
        "rank_source_variant": current_payload.get("source_variant"),
        "top_n": definition.top_n,
        "max_position_weight": round_float(definition.max_position_weight),
        "weighting_method": definition.weighting_method,
        "score_squared_used": False,
        "deployment_status": definition.deployment_status,
        "shadow_only": True,
        "capital_impact": definition.capital_impact,
        "selection_rule": "highest_ranked_names_from_current_sleeve_rank_table",
        "holdings": holdings,
        "target_weights": weights,
        "cash_weight": cash_weight,
        "expected_cash_residual": cash_weight,
        "concentration": concentration_metrics(weights),
    }


def select_rank_table_rows(current_payload: dict[str, Any], *, top_n: int) -> list[dict[str, Any]]:
    rows = []
    for row in current_payload.get("rank_table") or []:
        ticker = row.get("ticker")
        rank = _optional_float(row.get("momentum_rank"))
        if ticker is None or rank is None:
            continue
        rows.append(
            {
                "ticker": str(ticker),
                "momentum_rank": rank,
                "momentum_score": _optional_float(row.get("momentum_score")),
                "is_selected": bool(row.get("is_selected")),
            }
        )
    rows.sort(key=lambda item: (item["momentum_rank"], item["ticker"]))
    return rows[:top_n]


def concentration_metrics(weights: dict[str, float]) -> dict[str, Any]:
    values = [float(value) for value in weights.values() if float(value) > 0.0]
    values.sort(reverse=True)
    gross_exposure = round_float(sum(values))
    cash_weight = round_float(max(0.0, 1.0 - gross_exposure))
    deployed = [value / gross_exposure for value in values] if gross_exposure > 0 else []
    hhi = sum(value * value for value in deployed)
    capital_hhi = sum(value * value for value in values)
    return {
        "holdings_count": len(values),
        "gross_exposure": gross_exposure,
        "cash_weight": cash_weight,
        "max_weight": round_float(max(values) if values else 0.0),
        "min_weight": round_float(min(values) if values else 0.0),
        "top3_concentration": round_float(sum(values[:3])),
        "top5_concentration": round_float(sum(values[:5])),
        "hhi": round_float(hhi),
        "effective_n": round_float((1.0 / hhi) if hhi > 0 else 0.0),
        "capital_hhi": round_float(capital_hhi),
        "capital_effective_n": round_float((1.0 / capital_hhi) if capital_hhi > 0 else 0.0),
    }


def compare_current_and_shadow(*, current: dict[str, Any], shadow: dict[str, Any]) -> dict[str, Any]:
    current_weights = clean_weights(current.get("target_weights") or {})
    shadow_weights = clean_weights(shadow.get("target_weights") or {})
    tickers = sorted(set(current_weights) | set(shadow_weights))
    diffs = []
    for ticker in tickers:
        current_weight = current_weights.get(ticker, 0.0)
        shadow_weight = shadow_weights.get(ticker, 0.0)
        diffs.append(
            {
                "ticker": ticker,
                "current_weight": round_float(current_weight),
                "shadow_weight": round_float(shadow_weight),
                "delta_weight": round_float(shadow_weight - current_weight),
            }
        )
    overlap_names = sorted(set(current_weights) & set(shadow_weights))
    overlap_weight = sum(min(current_weights[ticker], shadow_weights[ticker]) for ticker in overlap_names)
    turnover = 0.5 * sum(abs(item["delta_weight"]) for item in diffs)
    current_concentration = current["concentration"]
    shadow_concentration = shadow["concentration"]
    return {
        "sleeve": current["sleeve"],
        "current_portfolio": current["portfolio_name"],
        "shadow_variant": shadow["variant_name"],
        "trade_date": current.get("trade_date"),
        "overlap_names": overlap_names,
        "overlap_count": len(overlap_names),
        "current_only": sorted(set(current_weights) - set(shadow_weights)),
        "shadow_only": sorted(set(shadow_weights) - set(current_weights)),
        "overlap_weight": round_float(overlap_weight),
        "transition_turnover_vs_current": round_float(turnover),
        "weight_diffs": diffs,
        "metric_deltas": {
            "holdings_count_delta": shadow_concentration["holdings_count"] - current_concentration["holdings_count"],
            "gross_exposure_delta": round_float(shadow_concentration["gross_exposure"] - current_concentration["gross_exposure"]),
            "cash_weight_delta": round_float(shadow_concentration["cash_weight"] - current_concentration["cash_weight"]),
            "max_weight_delta": round_float(shadow_concentration["max_weight"] - current_concentration["max_weight"]),
            "hhi_delta": round_float(shadow_concentration["hhi"] - current_concentration["hhi"]),
            "effective_n_delta": round_float(shadow_concentration["effective_n"] - current_concentration["effective_n"]),
        },
    }


def build_performance_payload(
    *,
    trade_date: str,
    portfolios: dict[str, dict[str, Any]],
    price_panel_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    forward = compute_next_day_returns(price_panel_path=price_panel_path, trade_date=trade_date)
    previous_date, prior_performance = load_prior_concentration_performance(output_root=output_root, trade_date=trade_date)
    previous_weights = load_previous_weights(output_root=output_root, previous_date=previous_date)
    portfolio_results: dict[str, dict[str, Any]] = {}
    for portfolio_name, portfolio in portfolios.items():
        weights = clean_weights(portfolio.get("target_weights") or {})
        prior = ((prior_performance or {}).get("portfolios") or {}).get(portfolio_name) or {}
        previous_nav = _optional_float(prior.get("nav"))
        if previous_nav is None:
            previous_nav = 1.0
        previous_peak = _optional_float(prior.get("peak_nav"))
        if previous_peak is None:
            previous_peak = previous_nav
        daily_return = None
        if forward["status"] == "OK":
            daily_return = round_float(sum(weights.get(ticker, 0.0) * forward["ticker_returns"].get(ticker, 0.0) for ticker in weights))
        nav = round_float(previous_nav * (1.0 + daily_return)) if daily_return is not None else round_float(previous_nav)
        peak_nav = round_float(max(previous_peak, nav))
        drawdown = round_float((nav / peak_nav) - 1.0) if peak_nav > 0 else None
        prior_weights = previous_weights.get(portfolio_name)
        turnover = one_way_turnover(prior_weights, weights) if prior_weights is not None else None
        portfolio_results[portfolio_name] = {
            "portfolio_name": portfolio_name,
            "sleeve": portfolio.get("sleeve"),
            "variant_name": portfolio.get("variant_name"),
            "daily_return": daily_return,
            "next_day_return": daily_return,
            "return_status": forward["status"],
            "return_reason": forward.get("reason"),
            "return_date": forward.get("return_date"),
            "next_trade_date": forward.get("next_trade_date"),
            "previous_nav": round_float(previous_nav),
            "nav": nav,
            "peak_nav": peak_nav,
            "cumulative_return": round_float(nav - 1.0),
            "drawdown": drawdown,
            "turnover": turnover,
            "turnover_status": "OK" if turnover is not None else "NO_PRIOR_SHADOW_CONCENTRATION_ARTIFACT",
            "weights_count": len(weights),
            "gross_exposure": concentration_metrics(weights)["gross_exposure"],
            "cash_weight": concentration_metrics(weights)["cash_weight"],
        }
    pair_deltas = build_performance_deltas(portfolio_results)
    return {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date,
        "previous_shadow_concentration_date": previous_date,
        "status": "OK" if forward["status"] == "OK" else "INITIALIZING_OR_AWAITING_NEXT_DAY_RETURN",
        "return_convention": "weights_as_of_trade_date_applied_to_next_available_close_to_close_return",
        "price_panel_path": str(price_panel_path),
        "next_day_return_context": forward,
        "portfolios": portfolio_results,
        "current_vs_shadow_delta": pair_deltas,
    }


def compute_next_day_returns(*, price_panel_path: Path, trade_date: str) -> dict[str, Any]:
    if not price_panel_path.exists():
        return {
            "status": "NO_PRICE_PANEL",
            "reason": "PRICE_PANEL_MISSING",
            "ticker_returns": {},
            "return_date": None,
            "next_trade_date": None,
        }
    frame = pd.read_parquet(price_panel_path)
    required = {"date", "ticker", "close"}
    if not required.issubset(frame.columns):
        return {
            "status": "INVALID_PRICE_PANEL",
            "reason": "PRICE_PANEL_MISSING_REQUIRED_COLUMNS",
            "ticker_returns": {},
            "return_date": None,
            "next_trade_date": None,
        }
    frame = frame[list(required)].copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    decision_date = pd.Timestamp(trade_date).normalize()
    dates = pd.DatetimeIndex(frame["date"].dropna().unique()).sort_values()
    if decision_date not in dates:
        return {
            "status": "NO_DECISION_DATE_PRICE",
            "reason": "TRADE_DATE_NOT_IN_PRICE_PANEL",
            "ticker_returns": {},
            "return_date": None,
            "next_trade_date": None,
        }
    later_dates = dates[dates > decision_date]
    if len(later_dates) == 0:
        return {
            "status": "RETURN_NOT_AVAILABLE",
            "reason": "NO_PRICE_DATE_AFTER_TRADE_DATE",
            "ticker_returns": {},
            "return_date": None,
            "next_trade_date": None,
        }
    next_date = pd.Timestamp(later_dates[0]).normalize()
    current = frame[frame["date"] == decision_date][["ticker", "close"]].copy()
    next_frame = frame[frame["date"] == next_date][["ticker", "close"]].copy()
    current["ticker"] = current["ticker"].astype(str)
    next_frame["ticker"] = next_frame["ticker"].astype(str)
    merged = current.merge(next_frame, on="ticker", how="inner", suffixes=("_current", "_next"))
    merged = merged[(merged["close_current"] > 0) & merged["close_next"].notna()].copy()
    merged["return"] = merged["close_next"] / merged["close_current"] - 1.0
    return {
        "status": "OK",
        "reason": None,
        "ticker_returns": {
            str(row.ticker): round_float(float(row.return_))
            for row in merged.rename(columns={"return": "return_"}).itertuples(index=False)
        },
        "return_date": next_date.strftime("%Y-%m-%d"),
        "next_trade_date": next_date.strftime("%Y-%m-%d"),
    }


def build_performance_deltas(portfolios: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    pairs = {
        "caerus_polaris": ("current_polaris", "polaris_concentrated_shadow"),
        "caerus_orion": ("current_orion", "orion_concentrated_shadow"),
    }
    deltas: dict[str, dict[str, Any]] = {}
    for sleeve, (current_name, shadow_name) in pairs.items():
        current = portfolios.get(current_name) or {}
        shadow = portfolios.get(shadow_name) or {}
        deltas[sleeve] = {
            "current_portfolio": current_name,
            "shadow_variant": shadow_name,
            "daily_return_delta": diff_or_none(shadow.get("daily_return"), current.get("daily_return")),
            "cumulative_return_delta": diff_or_none(shadow.get("cumulative_return"), current.get("cumulative_return")),
            "drawdown_delta": diff_or_none(shadow.get("drawdown"), current.get("drawdown")),
            "nav_delta": diff_or_none(shadow.get("nav"), current.get("nav")),
        }
    return deltas


def build_comparability_payload(*, dated_input_dir: Path, current_payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    performance_path = dated_input_dir / "shadow_performance.json"
    performance = _read_json(performance_path) if performance_path.exists() else {}
    strategies = performance.get("strategies") or {}
    return {
        "status": "MODEL_ARTIFACT_ONLY",
        "source_shadow_performance_path": str(performance_path) if performance_path.exists() else None,
        "source_shadow_performance_status": performance.get("status"),
        "current_sleeve_shadow_candidate_performance": {
            sleeve_slug: strategies.get(sleeve_slug)
            for sleeve_slug in current_payloads
            if strategies.get(sleeve_slug) is not None
        },
        "broker_data_used": False,
        "orders_submitted": False,
        "live_or_paper_execution_impact": "NONE",
        "note": "Comparability is limited to existing model/shadow artifacts. Broker, live, and paper execution state are not read or modified by this builder.",
    }


def build_lineage_payload(
    *,
    dated_input_dir: Path,
    price_panel_path: Path,
    current_payloads: dict[str, dict[str, Any]],
    output_root: Path,
) -> dict[str, Any]:
    input_files = {
        f"{sleeve_slug}_source": dated_input_dir / f"{sleeve_slug}.json"
        for sleeve_slug in current_payloads
    }
    if (dated_input_dir / "shadow_performance.json").exists():
        input_files["source_shadow_performance"] = dated_input_dir / "shadow_performance.json"
    if price_panel_path.exists():
        input_files["price_panel"] = price_panel_path
    return {
        "input_files": {
            name: {
                "path": str(path),
                "sha256": sha256_file(path),
            }
            for name, path in sorted(input_files.items())
        },
        "reproducibility": {
            "builder": "research.shadow_concentration.build_shadow_concentration_artifact",
            "output_root": str(output_root),
            "uses_broker_state": False,
            "uses_order_submission": False,
            "uses_allocator": False,
            "uses_scheduler": False,
        },
    }


def load_prior_concentration_performance(*, output_root: Path, trade_date: str) -> tuple[str | None, dict[str, Any] | None]:
    previous_date = find_previous_output_date(output_root=output_root, trade_date=trade_date)
    if previous_date is None:
        return None, None
    path = output_root / previous_date / "shadow_concentration_performance.json"
    if not path.exists():
        return previous_date, None
    return previous_date, _read_json(path)


def load_previous_weights(*, output_root: Path, previous_date: str | None) -> dict[str, dict[str, float]]:
    if previous_date is None:
        return {}
    artifact_path = output_root / previous_date / "shadow_concentration.json"
    if not artifact_path.exists():
        return {}
    artifact = _read_json(artifact_path)
    portfolios: dict[str, dict[str, float]] = {}
    for section in ("current_sleeves", "shadow_variants"):
        for portfolio in (artifact.get(section) or {}).values():
            portfolio_name = portfolio.get("portfolio_name")
            if portfolio_name:
                portfolios[str(portfolio_name)] = clean_weights(portfolio.get("target_weights") or {})
    return portfolios


def find_previous_output_date(*, output_root: Path, trade_date: str) -> str | None:
    if not output_root.exists():
        return None
    candidates = []
    for child in output_root.iterdir():
        if not child.is_dir():
            continue
        try:
            normalized = pd.Timestamp(child.name).strftime("%Y-%m-%d")
        except Exception:
            continue
        if normalized == child.name and child.name < trade_date:
            candidates.append(child.name)
    return max(candidates) if candidates else None


def write_holdings_csv(path: Path, portfolios: dict[str, dict[str, Any]]) -> None:
    rows = []
    for portfolio_name, portfolio in portfolios.items():
        for holding in portfolio.get("holdings") or []:
            rows.append(
                {
                    "portfolio_name": portfolio_name,
                    "sleeve": portfolio.get("sleeve"),
                    "variant_name": portfolio.get("variant_name"),
                    "ticker": holding.get("ticker"),
                    "rank": holding.get("rank"),
                    "score": holding.get("score"),
                    "weight": holding.get("weight"),
                    "cash_weight": portfolio.get("cash_weight"),
                }
            )
    write_csv(path, rows)


def write_comparison_csv(path: Path, comparisons: dict[str, dict[str, Any]]) -> None:
    rows = []
    for sleeve, comparison in comparisons.items():
        for diff in comparison.get("weight_diffs") or []:
            rows.append(
                {
                    "sleeve": sleeve,
                    "current_portfolio": comparison.get("current_portfolio"),
                    "shadow_variant": comparison.get("shadow_variant"),
                    "ticker": diff.get("ticker"),
                    "current_weight": diff.get("current_weight"),
                    "shadow_weight": diff.get("shadow_weight"),
                    "delta_weight": diff.get("delta_weight"),
                    "overlap_weight": comparison.get("overlap_weight"),
                    "transition_turnover_vs_current": comparison.get("transition_turnover_vs_current"),
                }
            )
    write_csv(path, rows)


def write_performance_csv(path: Path, performance: dict[str, Any]) -> None:
    rows = []
    for portfolio_name, item in (performance.get("portfolios") or {}).items():
        rows.append(
            {
                "portfolio_name": portfolio_name,
                "sleeve": item.get("sleeve"),
                "variant_name": item.get("variant_name"),
                "daily_return": item.get("daily_return"),
                "cumulative_return": item.get("cumulative_return"),
                "drawdown": item.get("drawdown"),
                "turnover": item.get("turnover"),
                "nav": item.get("nav"),
                "return_status": item.get("return_status"),
            }
        )
    write_csv(path, rows)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def build_dashboard_markdown(payload: dict[str, Any]) -> str:
    performance = payload["performance"]
    lines = [
        "# Shadow Concentration Dashboard",
        "",
        "**RESEARCH SHADOW ONLY: non-capital, non-executing, no live/paper/allocator/broker/scheduler changes.**",
        "",
        f"- Trade date: {payload['trade_date']}",
        f"- Return status: {performance['next_day_return_context']['status']}",
        f"- Return date: {performance['next_day_return_context'].get('return_date') or 'N/A'}",
        "",
        "## Current vs Concentrated Polaris",
    ]
    lines.extend(dashboard_pair_lines(payload, sleeve="caerus_polaris"))
    lines.extend(["", "## Current vs Concentrated Orion"])
    lines.extend(dashboard_pair_lines(payload, sleeve="caerus_orion"))
    lines.extend(["", "## Portfolio Performance"])
    lines.extend(
        [
            "| Portfolio | Daily Return | Cumulative Return | Drawdown | Turnover | Gross | Cash |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, item in performance["portfolios"].items():
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    fmt_pct(item.get("daily_return")),
                    fmt_pct(item.get("cumulative_return")),
                    fmt_pct(item.get("drawdown")),
                    fmt_pct(item.get("turnover")),
                    fmt_pct(item.get("gross_exposure")),
                    fmt_pct(item.get("cash_weight")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Lineage",
            f"- Schema: {payload['schema_version']}",
            f"- Artifact digest: {payload['artifact_digest']}",
            "- Inputs are existing daily shadow candidate artifacts plus the research price panel for next-day return measurement.",
        ]
    )
    return "\n".join(lines) + "\n"


def dashboard_pair_lines(payload: dict[str, Any], *, sleeve: str) -> list[str]:
    comparison = payload["comparisons"][sleeve]
    current = payload["current_sleeves"][sleeve]
    shadow = payload["shadow_variants"][comparison["shadow_variant"]]
    performance = payload["performance"]["current_vs_shadow_delta"][sleeve]
    current_top = ", ".join(f"{item['ticker']} {fmt_pct(item['weight'])}" for item in current["holdings"][:10])
    shadow_top = ", ".join(f"{item['ticker']} {fmt_pct(item['weight'])}" for item in shadow["holdings"])
    return [
        f"- Current holdings: {current_top}",
        f"- Shadow holdings: {shadow_top}",
        f"- Overlap weight: {fmt_pct(comparison['overlap_weight'])}",
        f"- Transition turnover vs current: {fmt_pct(comparison['transition_turnover_vs_current'])}",
        f"- Current HHI/effective N: {current['concentration']['hhi']} / {current['concentration']['effective_n']}",
        f"- Shadow HHI/effective N: {shadow['concentration']['hhi']} / {shadow['concentration']['effective_n']}",
        f"- Daily return delta: {fmt_pct(performance.get('daily_return_delta'))}",
        f"- Cumulative return delta: {fmt_pct(performance.get('cumulative_return_delta'))}",
    ]


def clean_weights(raw: dict[str, Any]) -> dict[str, float]:
    weights = {str(ticker): float(value) for ticker, value in raw.items() if value is not None and float(value) > 0.0}
    return dict(sorted(weights.items()))


def one_way_turnover(previous: dict[str, float] | None, current: dict[str, float]) -> float | None:
    if previous is None:
        return None
    tickers = set(previous) | set(current)
    return round_float(0.5 * sum(abs(current.get(ticker, 0.0) - previous.get(ticker, 0.0)) for ticker in tickers))


def round_float(value: float | int | None, digits: int = 10) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def diff_or_none(left: Any, right: Any) -> float | None:
    left_number = _optional_float(left)
    right_number = _optional_float(right)
    if left_number is None or right_number is None:
        return None
    return round_float(left_number - right_number)


def fmt_pct(value: Any) -> str:
    number = _optional_float(value)
    if number is None:
        return "N/A"
    return f"{number:.2%}"


def stable_payload_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return round_float(number)
